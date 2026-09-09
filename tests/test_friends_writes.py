"""Integration tests for Phase 3.4 Friends & 1-on-1 Expenses write operations.

Tests cover:
- Friend request full lifecycle: send, duplicate rejection, recipient-only accept/reject, cancel, delete.
- Idempotency on friend request sending, accepting, and rejecting.
- 1:1 shared expense create, update, and delete.
- Two-sided running balance invariant for both users across create, update, payment, and delete.
- Security, JWT authentication, and authorization barriers.
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
from app.models.expense import Expense, ExpenseSplit
from app.models.friend import Friend
from app.models.profile import Profile

USER_A = uuid.UUID("aaaaaaaa-1111-4111-8111-111111111111")
USER_B = uuid.UUID("bbbbbbbb-2222-4222-8222-222222222222")
USER_C = uuid.UUID("cccccccc-3333-4333-8333-333333333333")
TEST_SECRET = "friends-writes-test-secret-at-least-32-chars-long-12345"


def bearer(user_id=USER_A, email="user@example.test", **claims):
    payload = {
        "sub": str(user_id),
        "aud": "authenticated",
        "role": "authenticated",
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        **claims,
    }
    return {"Authorization": "Bearer " + jwt.encode(payload, TEST_SECRET, algorithm="HS256")}


@pytest_asyncio.fixture
async def friends_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", TEST_SECRET)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'friends_writes.sqlite'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([
            Profile(user_id=USER_A, display_name="Alice User", email="alice@example.test"),
            Profile(user_id=USER_B, display_name="Bob User", email="bob@example.test"),
            Profile(user_id=USER_C, display_name="Charlie User", email="charlie@example.test"),
        ])
        await db.commit()

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield factory
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_send_friend_request_lifecycle(friends_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Send to Bob by email
        res = await client.post(
            "/api/v1/friends/request",
            headers=bearer(USER_A, email="alice@example.test"),
            json={"email": "bob@example.test"},
        )
        assert res.status_code == 201, res.text
        data = res.json()["data"]
        assert data["status"] == "pending"
        assert data["user_id"] == str(USER_A)
        assert data["friend_id"] == str(USER_B)
        assert data["profile"]["display_name"] == "Bob User"
        friendship_id = data["id"]

        # 2. Duplicate sending by Alice returns 409 Conflict
        res_dup = await client.post(
            "/api/v1/friends/request",
            headers=bearer(USER_A, email="alice@example.test"),
            json={"email": "bob@example.test"},
        )
        assert res_dup.status_code == 409
        assert "already pending" in res_dup.json()["error"]

        # 3. Reverse sending by Bob returns 409 Conflict
        res_rev = await client.post(
            "/api/v1/friends/request",
            headers=bearer(USER_B, email="bob@example.test"),
            json={"user_id": str(USER_A)},
        )
        assert res_rev.status_code == 409
        assert "already sent you a friend request" in res_rev.json()["error"]

        # 4. Self-friend request is rejected with 400
        res_self = await client.post(
            "/api/v1/friends/request",
            headers=bearer(USER_A, email="alice@example.test"),
            json={"user_id": str(USER_A)},
        )
        assert res_self.status_code == 400

        # 5. Non-existent user returns 404
        res_404 = await client.post(
            "/api/v1/friends/request",
            headers=bearer(USER_A, email="alice@example.test"),
            json={"email": "nonexistent@example.test"},
        )
        assert res_404.status_code == 404

        # 6. Alice (sender) tries to accept -> 403 Forbidden
        res_accept_forbidden = await client.post(
            f"/api/v1/friends/{friendship_id}/accept",
            headers=bearer(USER_A, email="alice@example.test"),
        )
        assert res_accept_forbidden.status_code == 403

        # 7. Charlie (unrelated) tries to accept -> 403 Forbidden
        res_charlie_accept = await client.post(
            f"/api/v1/friends/{friendship_id}/accept",
            headers=bearer(USER_C, email="charlie@example.test"),
        )
        assert res_charlie_accept.status_code == 403

        # 8. Bob (recipient) accepts -> 200 OK
        res_accept = await client.post(
            f"/api/v1/friends/{friendship_id}/accept",
            headers=bearer(USER_B, email="bob@example.test"),
        )
        assert res_accept.status_code == 200
        assert res_accept.json()["data"]["status"] == "accepted"

        # 9. Bob accepts again -> idempotent 200 OK
        res_accept_retry = await client.post(
            f"/api/v1/friends/{friendship_id}/accept",
            headers=bearer(USER_B, email="bob@example.test"),
        )
        assert res_accept_retry.status_code == 200
        assert res_accept_retry.json()["data"]["status"] == "accepted"

        # 10. Sending request to already accepted friend returns 409
        res_accepted_send = await client.post(
            "/api/v1/friends/request",
            headers=bearer(USER_A, email="alice@example.test"),
            json={"user_id": str(USER_B)},
        )
        assert res_accepted_send.status_code == 409
        assert "already friends" in res_accepted_send.json()["error"]


@pytest.mark.asyncio
async def test_reject_friend_request_and_resend(friends_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Alice sends request to Charlie
        res = await client.post(
            "/api/v1/friends/request",
            headers=bearer(USER_A, email="alice@example.test"),
            json={"user_id": str(USER_C)},
        )
        assert res.status_code == 201
        friendship_id = res.json()["data"]["id"]

        # Alice tries to reject her own request -> 403
        res_rej_sender = await client.post(
            f"/api/v1/friends/{friendship_id}/reject",
            headers=bearer(USER_A, email="alice@example.test"),
        )
        assert res_rej_sender.status_code == 403

        # Charlie rejects -> 200
        res_rej = await client.post(
            f"/api/v1/friends/{friendship_id}/reject",
            headers=bearer(USER_C, email="charlie@example.test"),
        )
        assert res_rej.status_code == 200
        assert res_rej.json()["data"]["status"] == "rejected"

        # Charlie rejects again -> idempotent 200
        res_rej_retry = await client.post(
            f"/api/v1/friends/{friendship_id}/reject",
            headers=bearer(USER_C, email="charlie@example.test"),
        )
        assert res_rej_retry.status_code == 200

        # Alice resends request to Charlie -> succeeds, resurrects row to pending
        res_resend = await client.post(
            "/api/v1/friends/request",
            headers=bearer(USER_A, email="alice@example.test"),
            json={"user_id": str(USER_C)},
        )
        assert res_resend.status_code == 201
        assert res_resend.json()["data"]["status"] == "pending"


@pytest.mark.asyncio
async def test_delete_friendship(friends_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Alice sends request to Bob
        res = await client.post(
            "/api/v1/friends/request",
            headers=bearer(USER_A, email="alice@example.test"),
            json={"user_id": str(USER_B)},
        )
        friendship_id = res.json()["data"]["id"]

        # Charlie (not involved) tries to delete -> 403
        res_del_charlie = await client.delete(
            f"/api/v1/friends/{friendship_id}",
            headers=bearer(USER_C, email="charlie@example.test"),
        )
        assert res_del_charlie.status_code == 403

        # Alice (sender) cancels pending request -> 200
        res_del_alice = await client.delete(
            f"/api/v1/friends/{friendship_id}",
            headers=bearer(USER_A, email="alice@example.test"),
        )
        assert res_del_alice.status_code == 200

        # Request is gone -> 404 on subsequent delete
        res_del_gone = await client.delete(
            f"/api/v1/friends/{friendship_id}",
            headers=bearer(USER_A, email="alice@example.test"),
        )
        assert res_del_gone.status_code == 404


@pytest.mark.asyncio
async def test_shared_1on1_expense_lifecycle_and_two_sided_balances(friends_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Establish accepted friendship between Alice and Bob
        req_res = await client.post(
            "/api/v1/friends/request",
            headers=bearer(USER_A, email="alice@example.test"),
            json={"user_id": str(USER_B)},
        )
        f_id = req_res.json()["data"]["id"]
        await client.post(
            f"/api/v1/friends/{f_id}/accept",
            headers=bearer(USER_B, email="bob@example.test"),
        )

        # 1. Non-friend Charlie cannot create expense with Alice -> 403
        charlie_exp = await client.post(
            f"/api/v1/friends/{USER_A}/expenses",
            headers=bearer(USER_C, email="charlie@example.test"),
            json={"amount": 50, "category": "food"},
        )
        assert charlie_exp.status_code == 403

        # 2. Alice creates shared expense: ₹100, equal split, paid by Alice
        create_res = await client.post(
            f"/api/v1/friends/{USER_B}/expenses",
            headers=bearer(USER_A, email="alice@example.test"),
            json={
                "amount": 100,
                "category": "food",
                "note": "Dinner at Mario's",
                "split_type": "equal",
            },
        )
        assert create_res.status_code == 201, create_res.text
        expense_data = create_res.json()["data"]
        assert expense_data["amount"] == 100.0
        assert len(expense_data["expense_splits"]) == 2
        exp_id = expense_data["id"]

        # 3. Two-sided balance verification:
        # Alice balance: Bob owes Alice +₹50.00
        alice_bal_res = await client.get("/api/v1/friends/balances", headers=bearer(USER_A, email="alice@example.test"))
        assert alice_bal_res.status_code == 200
        alice_balances = {b["friendId"]: b["netBalance"] for b in alice_bal_res.json()["data"]}
        assert alice_balances.get(str(USER_B)) == 50.0

        # Bob balance: Bob owes Alice -₹50.00
        bob_bal_res = await client.get("/api/v1/friends/balances", headers=bearer(USER_B, email="bob@example.test"))
        assert bob_bal_res.status_code == 200
        bob_balances = {b["friendId"]: b["netBalance"] for b in bob_bal_res.json()["data"]}
        assert bob_balances.get(str(USER_A)) == -50.0

        # 4. Bob updates expense to ₹160 (equal split):
        patch_res = await client.patch(
            f"/api/v1/friends/{USER_A}/expenses/{exp_id}",
            headers=bearer(USER_B, email="bob@example.test"),
            json={"amount": 160, "note": "Dinner plus dessert"},
        )
        assert patch_res.status_code == 200, patch_res.text
        assert patch_res.json()["data"]["amount"] == 160.0

        # Two-sided balance verification after edit:
        # Alice: +₹80.00, Bob: -₹80.00
        alice_bal_2 = (await client.get("/api/v1/friends/balances", headers=bearer(USER_A, email="alice@example.test"))).json()["data"]
        bob_bal_2 = (await client.get("/api/v1/friends/balances", headers=bearer(USER_B, email="bob@example.test"))).json()["data"]
        assert next(b["netBalance"] for b in alice_bal_2 if b["friendId"] == str(USER_B)) == 80.0
        assert next(b["netBalance"] for b in bob_bal_2 if b["friendId"] == str(USER_A)) == -80.0

        # 5. Bob records a settlement payment of ₹80 (Bob pays Alice full ₹80)
        pay_res = await client.post(
            f"/api/v1/friends/{USER_A}/expenses",
            headers=bearer(USER_B, email="bob@example.test"),
            json={
                "amount": 80,
                "category": "other",
                "note": "Settlement payment",
                "paid_by": str(USER_B),
                "split_type": "full",
            },
        )
        assert pay_res.status_code == 201
        pay_id = pay_res.json()["data"]["id"]

        # Two-sided balance verification after payment:
        # Net balance is now ₹0.00 (settled up)
        alice_bal_3 = (await client.get("/api/v1/friends/balances", headers=bearer(USER_A, email="alice@example.test"))).json()["data"]
        bob_bal_3 = (await client.get("/api/v1/friends/balances", headers=bearer(USER_B, email="bob@example.test"))).json()["data"]
        alice_net = next((b["netBalance"] for b in alice_bal_3 if b["friendId"] == str(USER_B)), 0.0)
        bob_net = next((b["netBalance"] for b in bob_bal_3 if b["friendId"] == str(USER_A)), 0.0)
        assert alice_net == 0.0
        assert bob_net == 0.0

        # 6. Delete payment expense
        del_pay_res = await client.delete(
            f"/api/v1/friends/{USER_B}/expenses/{pay_id}",
            headers=bearer(USER_A, email="alice@example.test"),
        )
        assert del_pay_res.status_code == 200

        # Balances revert to +80 / -80
        alice_bal_4 = (await client.get("/api/v1/friends/balances", headers=bearer(USER_A, email="alice@example.test"))).json()["data"]
        assert next(b["netBalance"] for b in alice_bal_4 if b["friendId"] == str(USER_B)) == 80.0

        # 7. Delete original expense
        del_exp_res = await client.delete(
            f"/api/v1/friends/{USER_B}/expenses/{exp_id}",
            headers=bearer(USER_A, email="alice@example.test"),
        )
        assert del_exp_res.status_code == 200

        # Balances back to 0
        alice_bal_5 = (await client.get("/api/v1/friends/balances", headers=bearer(USER_A, email="alice@example.test"))).json()["data"]
        assert next((b["netBalance"] for b in alice_bal_5 if b["friendId"] == str(USER_B)), 0.0) == 0.0


@pytest.mark.asyncio
async def test_auth_and_openapi_documentation(friends_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Check 401 without auth
        assert (await client.post("/api/v1/friends/request", json={"email": "a@b.com"})).status_code == 401
        assert (await client.post(f"/api/v1/friends/{uuid.uuid4()}/accept")).status_code == 401
        assert (await client.post(f"/api/v1/friends/{uuid.uuid4()}/reject")).status_code == 401
        assert (await client.delete(f"/api/v1/friends/{uuid.uuid4()}")).status_code == 401
        assert (await client.post(f"/api/v1/friends/{uuid.uuid4()}/expenses", json={"amount": 10})).status_code == 401
        assert (await client.patch(f"/api/v1/friends/{uuid.uuid4()}/expenses/{uuid.uuid4()}", json={"amount": 10})).status_code == 401
        assert (await client.delete(f"/api/v1/friends/{uuid.uuid4()}/expenses/{uuid.uuid4()}")).status_code == 401

        # Check OpenAPI documentation
        schema_res = await client.get("/api/v1/openapi.json")
        assert schema_res.status_code == 200
        paths = schema_res.json()["paths"]
        assert "/api/v1/friends/request" in paths
        assert "/api/v1/friends/{id}/accept" in paths
        assert "/api/v1/friends/{id}/reject" in paths
        assert "/api/v1/friends/{id}" in paths
        assert "/api/v1/friends/{friend_id}/expenses" in paths
        assert "/api/v1/friends/{friend_id}/expenses/{id}" in paths


@pytest.mark.asyncio
async def test_shadow_profile_create_and_merge(friends_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        alice_token = bearer(USER_A, email="alice@example.test")

        # 1. Create shadow profile
        create_res = await client.post(
            "/api/v1/friends/shadow",
            json={"display_name": "Shadow Bob", "email": "bob@example.test"},
            headers=alice_token,
        )
        assert create_res.status_code == 201
        data = create_res.json()["data"]
        shadow_id = uuid.UUID(data["shadow_user_id"])
        assert data["profile"]["display_name"] == "Shadow Bob"
        assert data["profile"]["is_shadow"] is True

        # Check friendship exists
        friend_res = await client.get("/api/v1/friends/", headers=alice_token)
        assert friend_res.status_code == 200
        friends_list = friend_res.json()["data"]
        assert any(f["friend_id"] == str(shadow_id) and f["status"] == "accepted" for f in friends_list)

        # 2. Bob registers with email bob@example.test and merges shadow profile
        bob_token = bearer(USER_B, email="bob@example.test")
        merge_res = await client.post(
            "/api/v1/friends/shadow/merge",
            json={"shadow_user_id": str(shadow_id)},
            headers=bob_token,
        )
        assert merge_res.status_code == 200
        assert merge_res.json()["data"]["merged"] is True

        # Verify shadow profile deleted and friendship transferred to bob
        alice_friends_after = (await client.get("/api/v1/friends/", headers=alice_token)).json()["data"]
        assert any(f["friend_id"] == str(USER_B) for f in alice_friends_after)
        assert not any(f["friend_id"] == str(shadow_id) for f in alice_friends_after)

        # Test auth rejection / mismatch
        # Another user with different email tries to merge nonexistent shadow
        charlie_token = bearer(USER_C, email="charlie@example.test")
        bad_merge = await client.post(
            "/api/v1/friends/shadow/merge",
            json={"shadow_user_id": str(shadow_id)},
            headers=charlie_token,
        )
        assert bad_merge.status_code == 404

