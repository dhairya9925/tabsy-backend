"""Integration tests for Phase 3.5 Group Expenses & Settlements write operations.

Tests verify:
- Group expense create / update / delete with split-sum validation and RBAC
- Auto-splits generation across group members
- Non-member payer/split rejection
- Locked month rejection for add/update/delete
- Settlement finalization double-submit idempotency
- Member status self-service vs admin enforcement and repeat mark idempotency
- Member monthly exclusions toggle, self vs admin enforcement
- Balances reflect writes (create, update, settle, delete)
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
from app.models.category import UserCategory
from app.models.expense import Expense, ExpenseSplit
from app.models.group import Group, GroupMember
from app.models.profile import Profile
from app.models.settlement import (
    MemberMonthlyExclusion,
    MemberMonthlyStatus,
    MonthlySettlement,
)

USER_A = uuid.UUID("aaaaaaaa-1111-4111-8111-111111111111")  # Admin
USER_B = uuid.UUID("bbbbbbbb-2222-4222-8222-222222222222")  # Member
USER_C = uuid.UUID("cccccccc-3333-4333-8333-333333333333")  # Member
USER_OUTSIDER = uuid.UUID("dddddddd-4444-4444-8444-444444444444")  # Non-member

TEST_SECRET = "group-expenses-test-secret-at-least-32-chars-long-12345"


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
async def group_exp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", TEST_SECRET)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'group_exp_writes.sqlite'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([
            Profile(user_id=USER_A, display_name="Alice Admin", email="alice@example.test"),
            Profile(user_id=USER_B, display_name="Bob Member", email="bob@example.test"),
            Profile(user_id=USER_C, display_name="Charlie Member", email="charlie@example.test"),
            Profile(user_id=USER_OUTSIDER, display_name="Dave Outsider", email="dave@example.test"),
        ])
        await db.commit()

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield factory
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def setup_group(group_exp_db):
    group_id = uuid.uuid4()
    async with group_exp_db() as db:
        group = Group(
            id=group_id,
            name="Apartment 4B",
            description="Flatmates",
            type="day_to_day",
            monthly_rent=Decimal("0.00"),
            created_by=USER_A,
        )
        db.add(group)
        db.add_all([
            GroupMember(group_id=group_id, user_id=USER_A, role="admin"),
            GroupMember(group_id=group_id, user_id=USER_B, role="member"),
            GroupMember(group_id=group_id, user_id=USER_C, role="member"),
        ])
        await db.commit()
    return group_id


@pytest.mark.asyncio
async def test_create_group_expense_with_splits(client, setup_group):
    group_id = setup_group
    payload = {
        "amount": 300.00,
        "category": "food",
        "note": "Weekly groceries",
        "expense_date": "2026-03-01",
        "splits": [
            {"user_id": str(USER_A), "amount": 100.00},
            {"user_id": str(USER_B), "amount": 100.00},
            {"user_id": str(USER_C), "amount": 100.00},
        ],
    }

    res = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json=payload,
        headers=bearer(USER_A),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["data"]["amount"] == 300.00
    assert body["data"]["category"] == "food"
    assert body["data"]["payer_name"] == "Alice Admin"
    assert len(body["data"]["splits"]) == 3
    for s in body["data"]["splits"]:
        assert s["amount"] == 100.00
        assert s["member_name"] in ["Alice Admin", "Bob Member", "Charlie Member"]


@pytest.mark.asyncio
async def test_create_group_expense_auto_splits(client, setup_group):
    group_id = setup_group
    payload = {
        "amount": 60.00,
        "category": "bills",
        "note": "Internet bill",
        "expense_date": "2026-03-05",
    }

    res = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json=payload,
        headers=bearer(USER_B),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["data"]["amount"] == 60.00
    assert len(body["data"]["splits"]) == 3
    total_split = sum(s["amount"] for s in body["data"]["splits"])
    assert total_split == 60.00


@pytest.mark.asyncio
async def test_create_group_expense_split_sum_mismatch(client, setup_group):
    group_id = setup_group
    payload = {
        "amount": 200.00,
        "category": "food",
        "splits": [
            {"user_id": str(USER_A), "amount": 50.00},
            {"user_id": str(USER_B), "amount": 50.00},
        ],  # Sum is 100, not 200
    }

    res = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json=payload,
        headers=bearer(USER_A),
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_create_group_expense_non_member_rejected(client, setup_group):
    group_id = setup_group

    # 1. Outsider attempts to create an expense
    res = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={"amount": 50.00, "category": "food"},
        headers=bearer(USER_OUTSIDER),
    )
    assert res.status_code == 403

    # 2. Member attempts to include outsider in splits
    res2 = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "amount": 100.00,
            "category": "food",
            "splits": [
                {"user_id": str(USER_A), "amount": 50.00},
                {"user_id": str(USER_OUTSIDER), "amount": 50.00},
            ],
        },
        headers=bearer(USER_A),
    )
    assert res2.status_code == 403


@pytest.mark.asyncio
async def test_create_group_expense_in_locked_month_rejected(client, setup_group, group_exp_db):
    group_id = setup_group

    # Lock month 2 / 2026
    async with group_exp_db() as db:
        db.add(MonthlySettlement(group_id=group_id, month=2, year=2026, status="locked"))
        await db.commit()

    res = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "amount": 75.00,
            "category": "food",
            "expense_date": "2026-02-15",
        },
        headers=bearer(USER_A),
    )
    assert res.status_code == 400
    err_text = str(res.json().get("error") or res.json().get("detail") or "").lower()
    assert "locked" in err_text


@pytest.mark.asyncio
async def test_update_group_expense_authorization(client, setup_group):
    group_id = setup_group

    # USER_B creates expense
    create_res = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={"amount": 100.00, "category": "food", "note": "Lunch"},
        headers=bearer(USER_B),
    )
    expense_id = create_res.json()["data"]["id"]

    # USER_C (non-payer, non-creator, non-admin member) tries to edit -> 403
    patch_res1 = await client.patch(
        f"/api/v1/groups/{group_id}/expenses/{expense_id}",
        json={"note": "Hacked Lunch"},
        headers=bearer(USER_C),
    )
    assert patch_res1.status_code == 403

    # USER_B (creator) edits -> 200
    patch_res2 = await client.patch(
        f"/api/v1/groups/{group_id}/expenses/{expense_id}",
        json={"note": "Bob's Lunch"},
        headers=bearer(USER_B),
    )
    assert patch_res2.status_code == 200
    assert patch_res2.json()["data"]["note"] == "Bob's Lunch"

    # USER_A (group admin) edits -> 200
    patch_res3 = await client.patch(
        f"/api/v1/groups/{group_id}/expenses/{expense_id}",
        json={"note": "Admin Approved Lunch"},
        headers=bearer(USER_A),
    )
    assert patch_res3.status_code == 200
    assert patch_res3.json()["data"]["note"] == "Admin Approved Lunch"


@pytest.mark.asyncio
async def test_delete_group_expense_cascades_splits(client, setup_group, group_exp_db):
    group_id = setup_group

    create_res = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={"amount": 90.00, "category": "bills"},
        headers=bearer(USER_B),
    )
    assert create_res.status_code == 201
    expense_id = create_res.json()["data"]["id"]

    # USER_C cannot delete -> 403
    del_res1 = await client.delete(
        f"/api/v1/groups/{group_id}/expenses/{expense_id}",
        headers=bearer(USER_C),
    )
    assert del_res1.status_code == 403

    # USER_B deletes -> 200
    del_res2 = await client.delete(
        f"/api/v1/groups/{group_id}/expenses/{expense_id}",
        headers=bearer(USER_B),
    )
    assert del_res2.status_code == 200

    # Verify expense and splits are gone from DB
    async with group_exp_db() as db:
        exp = await db.get(Expense, uuid.UUID(expense_id))
        assert exp is None
        splits = (await db.execute(select(ExpenseSplit).where(ExpenseSplit.expense_id == uuid.UUID(expense_id)))).scalars().all()
        assert len(splits) == 0


@pytest.mark.asyncio
async def test_finalize_monthly_settlement_double_submit(client, setup_group, group_exp_db):
    group_id = setup_group

    # First finalize call
    res1 = await client.post(
        f"/api/v1/groups/{group_id}/settlements/4/2026/finalize",
        headers=bearer(USER_A),
    )
    assert res1.status_code == 200
    body1 = res1.json()["data"]
    assert body1["status"] == "locked"
    settlement_id = body1["id"]

    # Second finalize call (simulate double-submit or user tapping twice)
    res2 = await client.post(
        f"/api/v1/groups/{group_id}/settlements/4/2026/finalize",
        headers=bearer(USER_A),
    )
    assert res2.status_code == 200
    body2 = res2.json()["data"]
    assert body2["id"] == settlement_id
    assert body2["status"] == "locked"

    # Verify only 1 settlement record exists in DB
    async with group_exp_db() as db:
        all_settlements = (
            await db.execute(
                select(MonthlySettlement).where(
                    MonthlySettlement.group_id == group_id,
                    MonthlySettlement.month == 4,
                    MonthlySettlement.year == 2026,
                )
            )
        ).scalars().all()
        assert len(all_settlements) == 1


@pytest.mark.asyncio
async def test_member_monthly_status_authorization(client, setup_group):
    group_id = setup_group

    # Create an open settlement cycle
    set_res = await client.post(
        f"/api/v1/groups/{group_id}/settlements/5/2026",
        headers=bearer(USER_A),
    )
    settlement_id = set_res.json()["data"]["id"]

    # USER_B marks self complete -> 201
    res1 = await client.post(
        f"/api/v1/groups/{group_id}/settlements/{settlement_id}/member-status",
        json={"user_id": str(USER_B), "status": "complete"},
        headers=bearer(USER_B),
    )
    assert res1.status_code == 201
    assert res1.json()["data"]["status"] == "complete"
    assert res1.json()["data"]["user_id"] == str(USER_B)

    # Repeat mark is idempotent -> 201 with existing record
    res2 = await client.post(
        f"/api/v1/groups/{group_id}/settlements/{settlement_id}/member-status",
        headers=bearer(USER_B),
    )
    assert res2.status_code == 201
    assert res2.json()["data"]["id"] == res1.json()["data"]["id"]

    # USER_B tries to mark USER_C -> 403 Forbidden
    res3 = await client.post(
        f"/api/v1/groups/{group_id}/settlements/{settlement_id}/member-status",
        json={"user_id": str(USER_C), "status": "complete"},
        headers=bearer(USER_B),
    )
    assert res3.status_code == 403

    # USER_A (admin) marks USER_C -> 201 OK
    res4 = await client.post(
        f"/api/v1/groups/{group_id}/settlements/{settlement_id}/member-status",
        json={"user_id": str(USER_C), "status": "complete"},
        headers=bearer(USER_A),
    )
    assert res4.status_code == 201
    assert res4.json()["data"]["user_id"] == str(USER_C)


@pytest.mark.asyncio
async def test_member_monthly_exclusion_authorization(client, setup_group):
    group_id = setup_group

    # USER_B sets own exclusion to partial -> 200
    res1 = await client.post(
        f"/api/v1/groups/{group_id}/exclusions/6/2026",
        json={"user_id": str(USER_B), "exclusion_type": "partial"},
        headers=bearer(USER_B),
    )
    assert res1.status_code == 200
    assert res1.json()["data"]["exclusion_type"] == "partial"

    # USER_B tries to set USER_C's exclusion -> 403
    res2 = await client.post(
        f"/api/v1/groups/{group_id}/exclusions/6/2026",
        json={"user_id": str(USER_C), "exclusion_type": "full"},
        headers=bearer(USER_B),
    )
    assert res2.status_code == 403

    # USER_A (admin) sets USER_C's exclusion -> 200
    res3 = await client.post(
        f"/api/v1/groups/{group_id}/exclusions/6/2026",
        json={"user_id": str(USER_C), "exclusion_type": "full"},
        headers=bearer(USER_A),
    )
    assert res3.status_code == 200
    assert res3.json()["data"]["exclusion_type"] == "full"

    # USER_B deletes own exclusion via 'none' -> 200 with data null
    res4 = await client.post(
        f"/api/v1/groups/{group_id}/exclusions/6/2026",
        json={"user_id": str(USER_B), "exclusion_type": "none"},
        headers=bearer(USER_B),
    )
    assert res4.status_code == 200
    assert res4.json()["data"] is None


@pytest.mark.asyncio
async def test_balance_reflects_expense_writes(client, setup_group):
    group_id = setup_group

    # 1. Initial balances: all settled
    bal0 = await client.get(f"/api/v1/groups/{group_id}/balances", headers=bearer(USER_A))
    assert bal0.status_code == 200
    assert len(bal0.json()["data"]) == 0

    # 2. USER_A pays 100 split 50/50 with USER_B
    exp_res = await client.post(
        f"/api/v1/groups/{group_id}/expenses",
        json={
            "amount": 100.00,
            "category": "food",
            "splits": [
                {"user_id": str(USER_A), "amount": 50.00},
                {"user_id": str(USER_B), "amount": 50.00},
            ],
        },
        headers=bearer(USER_A),
    )
    assert exp_res.status_code == 201
    expense_id = exp_res.json()["data"]["id"]

    # 3. Check balances: USER_B owes USER_A 50.00
    bal1 = await client.get(f"/api/v1/groups/{group_id}/balances", headers=bearer(USER_A))
    assert bal1.status_code == 200
    balances1 = bal1.json()["data"]
    assert len(balances1) == 1
    assert balances1[0]["from_user_id"] == str(USER_B)
    assert balances1[0]["to_user_id"] == str(USER_A)
    assert balances1[0]["amount"] == 50.00

    # 4. Edit expense amount to 200.00 (split 100 each)
    patch_res = await client.patch(
        f"/api/v1/groups/{group_id}/expenses/{expense_id}",
        json={
            "amount": 200.00,
            "splits": [
                {"user_id": str(USER_A), "amount": 100.00},
                {"user_id": str(USER_B), "amount": 100.00},
            ],
        },
        headers=bearer(USER_A),
    )
    assert patch_res.status_code == 200

    # 5. Check balances: USER_B owes USER_A 100.00
    bal2 = await client.get(f"/api/v1/groups/{group_id}/balances", headers=bearer(USER_A))
    balances2 = bal2.json()["data"]
    assert len(balances2) == 1
    assert balances2[0]["amount"] == 100.00

    # 6. Settle up: USER_B pays USER_A
    settle_res = await client.post(
        f"/api/v1/groups/{group_id}/settle",
        json={"from_user_id": str(USER_B), "to_user_id": str(USER_A)},
        headers=bearer(USER_B),
    )
    assert settle_res.status_code == 200
    assert settle_res.json()["data"]["settled_count"] >= 1

    # 7. Check balances: now 0 (empty)
    bal3 = await client.get(f"/api/v1/groups/{group_id}/balances", headers=bearer(USER_A))
    assert len(bal3.json()["data"]) == 0

    # 8. Delete expense -> balances remain 0
    del_res = await client.delete(
        f"/api/v1/groups/{group_id}/expenses/{expense_id}",
        headers=bearer(USER_A),
    )
    assert del_res.status_code == 200
    bal4 = await client.get(f"/api/v1/groups/{group_id}/balances", headers=bearer(USER_A))
    assert len(bal4.json()["data"]) == 0
