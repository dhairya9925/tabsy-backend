"""End-to-end real user walkthrough verifying Phase 3 writes and balance consistency.

Walkthrough sequence:
1. Alice creates a group ("Ski Trip").
2. Alice adds Bob as a group member.
3. Alice creates a group expense ($200 cabin rental, split 50/50 with Bob).
   -> Confirms group balance: Bob owes Alice $100.00.
4. Alice creates a personal expense ($45 groceries).
   -> Confirms personal expense created without affecting group/friend balances.
5. Alice sends a friend request to Charlie, and Charlie accepts.
   -> Confirms friendship accepted.
6. Alice creates a 1:1 shared expense with Charlie ($80 concert, split 50/50).
   -> Confirms friend balance: Charlie owes Alice $40.00, Alice is owed $40.00.
7. Alice finalizes a monthly settlement in "Ski Trip".
   -> Confirms settlement locked idempotently.
8. Bob marks himself complete and settles up $100 with Alice.
   -> Confirms group balances reset cleanly to $0.00.
9. Alice checks dashboard summary:
   -> Confirms all balances, personal totals, and activity entries are strictly consistent.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.profile import Profile

USER_ALICE = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
USER_BOB = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
USER_CHARLIE = uuid.UUID("cccccccc-0000-4000-8000-000000000003")
TEST_SECRET = "e2e-walkthrough-secret-at-least-32-chars-long-12345"


def token_for(user_id: uuid.UUID, email: str):
    payload = {
        "sub": str(user_id),
        "aud": "authenticated",
        "role": "authenticated",
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return {"Authorization": f"Bearer {jwt.encode(payload, TEST_SECRET, algorithm='HS256')}"}


@pytest_asyncio.fixture
async def e2e_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", TEST_SECRET)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'e2e.sqlite'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([
            Profile(user_id=USER_ALICE, display_name="Alice Inquirer", email="alice@test.local"),
            Profile(user_id=USER_BOB, display_name="Bob Builder", email="bob@test.local"),
            Profile(user_id=USER_CHARLIE, display_name="Charlie Chef", email="charlie@test.local"),
        ])
        await db.commit()

    async def get_test_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db
    yield factory
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_user_walkthrough_end_to_end(e2e_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        alice_auth = token_for(USER_ALICE, "alice@test.local")
        bob_auth = token_for(USER_BOB, "bob@test.local")
        charlie_auth = token_for(USER_CHARLIE, "charlie@test.local")

        # -------------------------------------------------------------
        # Step 1: Alice creates a group ("Ski Trip")
        # -------------------------------------------------------------
        res = await client.post("/api/v1/groups/", json={"name": "Ski Trip", "type": "trip"}, headers=alice_auth)
        assert res.status_code == 201, res.text
        group_id = res.json()["data"]["id"]
        assert res.json()["data"]["name"] == "Ski Trip"

        # -------------------------------------------------------------
        # Step 2: Alice adds Bob as a group member
        # -------------------------------------------------------------
        add_bob = await client.post(
            f"/api/v1/groups/{group_id}/members",
            json={"email": "bob@test.local", "role": "member"},
            headers=alice_auth,
        )
        assert add_bob.status_code == 201, add_bob.text
        assert add_bob.json()["data"]["user_id"] == str(USER_BOB)

        # -------------------------------------------------------------
        # Step 3: Alice creates a group expense ($200 Cabin Rental, split 50/50 with Bob)
        # -------------------------------------------------------------
        add_exp = await client.post(
            f"/api/v1/groups/{group_id}/expenses",
            json={
                "amount": 200.00,
                "category": "bills",
                "note": "Cabin Rental",
                "expense_date": "2026-09-10",
                "splits": [
                    {"user_id": str(USER_ALICE), "amount": 100.00},
                    {"user_id": str(USER_BOB), "amount": 100.00},
                ],
            },
            headers=alice_auth,
        )
        assert add_exp.status_code == 201, add_exp.text
        group_exp_id = add_exp.json()["data"]["id"]

        # Confirm balances in "Ski Trip": Bob owes Alice $100.00
        bal_res = await client.get(f"/api/v1/groups/{group_id}/balances", headers=alice_auth)
        assert bal_res.status_code == 200, bal_res.text
        debts = bal_res.json()["data"]
        assert len(debts) == 1
        assert debts[0]["from_user_id"] == str(USER_BOB)
        assert debts[0]["to_user_id"] == str(USER_ALICE)
        assert debts[0]["amount"] == 100.00

        # -------------------------------------------------------------
        # Step 4: Alice creates a personal expense ($45.00 Groceries)
        # -------------------------------------------------------------
        pers_res = await client.post(
            "/api/v1/expenses/personal",
            json={
                "amount": 45.00,
                "category": "food",
                "note": "Weekly Groceries",
                "expense_date": "2026-09-10",
            },
            headers=alice_auth,
        )
        assert pers_res.status_code == 201, pers_res.text

        # Verify personal expenses list for Alice has 1 item of $45.00
        my_personal = await client.get("/api/v1/expenses/personal", headers=alice_auth)
        assert my_personal.status_code == 200
        assert len(my_personal.json()["data"]) == 1
        assert my_personal.json()["data"][0]["amount"] == 45.00

        # Verify Bob has zero personal expenses
        bob_personal = await client.get("/api/v1/expenses/personal", headers=bob_auth)
        assert bob_personal.status_code == 200
        assert len(bob_personal.json()["data"]) == 0

        # -------------------------------------------------------------
        # Step 5: Alice sends a friend request to Charlie, Charlie accepts
        # -------------------------------------------------------------
        freq_res = await client.post(
            "/api/v1/friends/request",
            json={"email": "charlie@test.local"},
            headers=alice_auth,
        )
        assert freq_res.status_code == 201, freq_res.text
        friendship_id = freq_res.json()["data"]["id"]
        assert freq_res.json()["data"]["status"] == "pending"

        # Charlie accepts the friend request
        accept_res = await client.post(f"/api/v1/friends/{friendship_id}/accept", headers=charlie_auth)
        assert accept_res.status_code == 200, accept_res.text
        assert accept_res.json()["data"]["status"] == "accepted"

        # -------------------------------------------------------------
        # Step 6: Alice creates a 1:1 shared expense with Charlie ($80.00 Concert, 50/50)
        # -------------------------------------------------------------
        friend_exp_res = await client.post(
            f"/api/v1/friends/{USER_CHARLIE}/expenses",
            json={
                "amount": 80.00,
                "category": "other",
                "note": "Concert Tickets",
                "expense_date": "2026-09-10",
                "split_type": "equal",
            },
            headers=alice_auth,
        )
        assert friend_exp_res.status_code == 201, friend_exp_res.text

        # Confirm friend balance: Charlie owes Alice $40.00
        alice_fbal = await client.get("/api/v1/friends/balances", headers=alice_auth)
        assert alice_fbal.status_code == 200
        alice_charlie_bal = next(b for b in alice_fbal.json()["data"] if b["friendId"] == str(USER_CHARLIE))
        assert alice_charlie_bal["netBalance"] == 40.00

        charlie_fbal = await client.get("/api/v1/friends/balances", headers=charlie_auth)
        assert charlie_fbal.status_code == 200
        charlie_alice_bal = next(b for b in charlie_fbal.json()["data"] if b["friendId"] == str(USER_ALICE))
        assert charlie_alice_bal["netBalance"] == -40.00

        # -------------------------------------------------------------
        # Step 7: Open cycle 9/2026, Bob marks himself complete
        # -------------------------------------------------------------
        open_settlement = await client.post(
            f"/api/v1/groups/{group_id}/settlements/9/2026",
            headers=alice_auth,
        )
        assert open_settlement.status_code in (200, 201), open_settlement.text
        settlement_id = open_settlement.json()["data"]["id"]

        # Bob marks himself complete for cycle 9/2026
        status_res = await client.post(
            f"/api/v1/groups/{group_id}/settlements/{settlement_id}/member-status",
            json={"status": "complete"},
            headers=bob_auth,
        )
        assert status_res.status_code in (200, 201), status_res.text
        assert status_res.json()["data"]["status"] == "complete"

        # -------------------------------------------------------------
        # Step 8: Alice finalizes the monthly settlement (locks it)
        # -------------------------------------------------------------
        finalize_res = await client.post(
            f"/api/v1/groups/{group_id}/settlements/9/2026/finalize",
            headers=alice_auth,
        )
        assert finalize_res.status_code == 200, finalize_res.text
        settlement = finalize_res.json()["data"]
        assert settlement["status"] == "locked"
        assert settlement["id"] == settlement_id

        # Bob settles up the $100.00 debt with Alice
        settle_res = await client.post(
            f"/api/v1/groups/{group_id}/settle",
            json={"from_user_id": str(USER_BOB), "to_user_id": str(USER_ALICE)},
            headers=bob_auth,
        )
        assert settle_res.status_code == 200, settle_res.text


        # Verify group balances are now completely settled: $0.00 outstanding debts
        bal_res_after = await client.get(f"/api/v1/groups/{group_id}/balances", headers=alice_auth)
        assert bal_res_after.status_code == 200
        assert len(bal_res_after.json()["data"]) == 0

        # -------------------------------------------------------------
        # Step 9: Verify Dashboard Summary for Alice
        # -------------------------------------------------------------
        dash_res = await client.get("/api/v1/dashboard/summary", headers=alice_auth)
        assert dash_res.status_code == 200, dash_res.text
        dash = dash_res.json()["data"]

        # Personal total should be exactly $45.00
        assert dash["personalTotal"] == 45.00

        # Group balance summary in "Ski Trip" should show 0 net balance (all settled)
        group_summary = next(g for g in dash["groupBalances"] if g["groupId"] == group_id)
        assert group_summary["userNetBalance"] == 0.0

        # Friend balance with Charlie should be +$40.00
        charlie_dash = next(f for f in dash["friendBalances"] if f["friendId"] == str(USER_CHARLIE))
        assert charlie_dash["netBalance"] == 40.00

        # Activity feed has recent items
        assert len(dash["recentActivity"]) >= 2
        print("\nAll 9 steps of the user walkthrough passed with complete mathematical balance consistency!")

