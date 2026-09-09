"""Comprehensive transaction rollback tests for all multi-step writes.

These tests simulate mid-flight exceptions during multi-step database operations
and assert that the entire transaction rolls back cleanly with ZERO partial writes.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import jwt
import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.models.expense import Expense, ExpenseSplit
from app.models.friend import Friend
from app.models.group import Group, GroupMember
from app.models.profile import Profile
from app.models.settlement import MonthlySettlement
from app.schemas.expense import ExpenseSplitWrite, PersonalExpenseCreate
from app.schemas.friend import FriendExpenseCreate, FriendExpenseUpdate
from app.schemas.group import GroupCreate, GroupExpenseBulkCreate, GroupExpenseCreate, GroupUpdate
from app.services import expense_service, friend_service, group_service

USER_A = uuid.UUID("aaaaaaaa-1111-4111-8111-111111111111")
USER_B = uuid.UUID("bbbbbbbb-2222-4222-8222-222222222222")
GROUP_ID = uuid.UUID("dddddddd-4444-4444-8444-444444444444")
TEST_SECRET = "rollback-test-secret-at-least-32-chars-long-12345"


@pytest_asyncio.fixture
async def test_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", TEST_SECRET)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rollbacks.sqlite'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([
            Profile(user_id=USER_A, display_name="Alice", email="alice@example.test"),
            Profile(user_id=USER_B, display_name="Bob", email="bob@example.test"),
            Friend(user_id=USER_A, friend_id=USER_B, status="accepted"),
            Group(id=GROUP_ID, name="Test Group", created_by=USER_A),
            GroupMember(group_id=GROUP_ID, user_id=USER_A, role="admin"),
            GroupMember(group_id=GROUP_ID, user_id=USER_B, role="member"),
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
async def test_rollback_create_personal_expense_with_splits(test_engine):
    """If split insertion fails during personal expense creation, the expense must not persist."""
    payload = PersonalExpenseCreate(
        amount=Decimal("100.00"),
        note="Personal Shared Dinner",
        category="food",
        expense_date=date(2026, 9, 10),
        splits=[
            ExpenseSplitWrite(user_id=USER_A, amount=Decimal("50.00")),
            ExpenseSplitWrite(user_id=USER_B, amount=Decimal("50.00")),
        ],
    )

    async with test_engine() as db:
        with patch.object(db, "flush", side_effect=RuntimeError("Simulated split flush failure")):
            with pytest.raises(RuntimeError, match="Simulated split flush failure"):
                await expense_service.create_personal_expense(db, USER_A, payload)

    # Verify that NO expense and NO splits were created
    async with test_engine() as verify_db:
        expenses = (await verify_db.execute(select(Expense))).scalars().all()
        splits = (await verify_db.execute(select(ExpenseSplit))).scalars().all()
        assert len(expenses) == 0
        assert len(splits) == 0


@pytest.mark.asyncio
async def test_rollback_create_group_membership_failure(test_engine):
    """If creator admin membership fails during group creation, the group must not persist."""
    payload = GroupCreate(name="Orphan Group", type="general")

    async with test_engine() as db:
        orig_add = db.add
        call_count = 0

        def failing_add(obj):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("Simulated admin membership crash")
            orig_add(obj)

        with patch.object(db, "add", side_effect=failing_add):
            with pytest.raises(RuntimeError, match="Simulated admin membership crash"):
                await group_service.create_group(db, USER_A, payload)

    # Verify that NO orphan group was created
    async with test_engine() as verify_db:
        groups = (await verify_db.execute(select(Group).where(Group.name == "Orphan Group"))).scalars().all()
        assert len(groups) == 0


@pytest.mark.asyncio
async def test_rollback_group_rent_sync_failure(test_engine):
    """If rent expense split synchronization fails, the group update must roll back."""
    payload = GroupUpdate(monthly_rent=Decimal("1200.00"))

    async with test_engine() as db:
        with patch.object(db, "flush", side_effect=RuntimeError("Simulated rent sync crash")):
            with pytest.raises(RuntimeError, match="Simulated rent sync crash"):
                await group_service.update_group(db, GROUP_ID, USER_A, payload)

    # Verify that group rent was NOT updated
    async with test_engine() as verify_db:
        group = (await verify_db.execute(select(Group).where(Group.id == GROUP_ID))).scalar_one()
        assert group.monthly_rent != Decimal("1200.00")
        rent_expenses = (await verify_db.execute(select(Expense).where(Expense.category == "bills"))).scalars().all()
        assert len(rent_expenses) == 0


@pytest.mark.asyncio
async def test_rollback_create_friend_expense_split_failure(test_engine):
    """If split insertion fails during 1:1 friend expense creation, the expense must not persist."""
    payload = FriendExpenseCreate(
        amount=Decimal("60.00"),
        note="Movie tickets",
        category="other",
        expense_date=date(2026, 9, 10),
        split_type="equal",
    )

    async with test_engine() as db:
        with patch.object(db, "flush", side_effect=RuntimeError("Simulated friend split crash")):
            with pytest.raises(RuntimeError, match="Simulated friend split crash"):
                await expense_service.create_friend_expense(db, USER_A, USER_B, payload)

    # Verify that NO expense and NO splits were created
    async with test_engine() as verify_db:
        expenses = (await verify_db.execute(select(Expense).where(Expense.note == "Movie tickets"))).scalars().all()
        assert len(expenses) == 0


@pytest.mark.asyncio
async def test_rollback_update_friend_expense_split_failure(test_engine):
    """If split recreation fails during friend expense update, the update must roll back."""
    # First, create a valid friend expense
    async with test_engine() as db:
        create_payload = FriendExpenseCreate(
            amount=Decimal("40.00"),
            note="Initial Lunch",
            category="food",
            expense_date=date(2026, 9, 10),
            split_type="equal",
        )
        expense = await expense_service.create_friend_expense(db, USER_A, USER_B, create_payload)
        expense_id = expense.id

    # Now attempt an update with simulated failure during flush
    async with test_engine() as db:
        update_payload = FriendExpenseUpdate(
            amount=Decimal("80.00"),
            note="Updated Lunch",
            split_type="equal",
        )
        with patch.object(db, "flush", side_effect=RuntimeError("Simulated split update crash")):
            with pytest.raises(RuntimeError, match="Simulated split update crash"):
                await expense_service.update_friend_expense(db, USER_A, USER_B, expense_id, update_payload)

    # Verify that original expense amount and note are preserved
    async with test_engine() as verify_db:
        exp = (await verify_db.execute(select(Expense).where(Expense.id == expense_id))).scalar_one()
        assert exp.amount == Decimal("40.00")
        assert exp.note == "Initial Lunch"
        splits = (await verify_db.execute(select(ExpenseSplit).where(ExpenseSplit.expense_id == expense_id))).scalars().all()
        assert len(splits) == 2


@pytest.mark.asyncio
async def test_rollback_create_group_expense_split_failure(test_engine):
    """If split insertion fails during group expense creation, the expense must not persist."""
    payload = GroupExpenseCreate(
        amount=Decimal("90.00"),
        note="Group Supplies",
        category="shopping",
        expense_date=date(2026, 9, 10),
    )

    async with test_engine() as db:
        with patch.object(db, "flush", side_effect=RuntimeError("Simulated group split crash")):
            with pytest.raises(RuntimeError, match="Simulated group split crash"):
                await group_service.create_group_expense(db, GROUP_ID, USER_A, payload)

    # Verify no expense was created
    async with test_engine() as verify_db:
        expenses = (await verify_db.execute(select(Expense).where(Expense.note == "Group Supplies"))).scalars().all()
        assert len(expenses) == 0


@pytest.mark.asyncio
async def test_rollback_update_group_expense_split_failure(test_engine):
    """If split replacement fails during group expense update, the update must roll back."""
    from app.schemas.group import GroupExpenseSplitWrite, GroupExpenseUpdate

    # Create initial expense
    async with test_engine() as db:
        exp = await group_service.create_group_expense(
            db, GROUP_ID, USER_A,
            GroupExpenseCreate(amount=Decimal("100.00"), note="Initial Group Meal", category="food")
        )
        exp_id = exp["id"] if isinstance(exp["id"], uuid.UUID) else uuid.UUID(str(exp["id"]))

    # Attempt update with simulated failure during flush
    async with test_engine() as db:
        update_payload = GroupExpenseUpdate(
            amount=Decimal("150.00"),
            note="Updated Group Meal",
            splits=[
                GroupExpenseSplitWrite(user_id=USER_A, amount=Decimal("75.00")),
                GroupExpenseSplitWrite(user_id=USER_B, amount=Decimal("75.00")),
            ],
        )
        with patch.object(db, "flush", side_effect=RuntimeError("Simulated group split update crash")):
            with pytest.raises(RuntimeError, match="Simulated group split update crash"):
                await group_service.update_group_expense(db, GROUP_ID, exp_id, USER_A, update_payload)

    # Verify original amount and note are preserved
    async with test_engine() as verify_db:
        exp_row = (await verify_db.execute(select(Expense).where(Expense.id == exp_id))).scalar_one()
        assert exp_row.amount == Decimal("100.00")
        assert exp_row.note == "Initial Group Meal"



@pytest.mark.asyncio
async def test_rollback_bulk_create_group_expenses_failure(test_engine):
    """If any item fails during bulk group expense creation, all items must roll back."""
    items = [
        GroupExpenseCreate(amount=Decimal("20.00"), note="Item 1", category="food"),
        GroupExpenseCreate(amount=Decimal("30.00"), note="Item 2", category="transport"),
    ]
    payload = GroupExpenseBulkCreate(expenses=items)

    async with test_engine() as db:
        with patch.object(db, "flush", side_effect=RuntimeError("Simulated bulk item crash")):
            with pytest.raises(RuntimeError, match="Simulated bulk item crash"):
                await group_service.bulk_create_group_expenses(db, GROUP_ID, USER_A, payload)

    # Verify that neither Item 1 nor Item 2 was created
    async with test_engine() as verify_db:
        expenses = (await verify_db.execute(select(Expense).where(Expense.group_id == GROUP_ID))).scalars().all()
        assert len(expenses) == 0


@pytest.mark.asyncio
async def test_rollback_bulk_reimburse_failure(test_engine):
    """If marking splits settled fails during bulk reimburse, expense status must roll back."""
    # Create approved expense
    async with test_engine() as db:
        exp = await group_service.create_group_expense(
            db, GROUP_ID, USER_A,
            GroupExpenseCreate(amount=Decimal("50.00"), note="Reimbursable", category="other")
        )
        exp_id = exp["id"] if isinstance(exp["id"], uuid.UUID) else uuid.UUID(str(exp["id"]))
        exp_row = (await db.execute(select(Expense).where(Expense.id == exp_id))).scalar_one()
        exp_row.status = "approved"
        await db.commit()

    # Attempt bulk reimburse with simulated failure during flush
    async with test_engine() as db:
        with patch.object(db, "flush", side_effect=RuntimeError("Simulated split settle flush failure")):
            with pytest.raises(RuntimeError, match="Simulated split settle flush failure"):
                await group_service.bulk_reimburse_group_expenses(db, GROUP_ID, USER_A)

    # Verify expense status remains 'approved', NOT 'reimbursed'
    async with test_engine() as verify_db:
        exp = (await verify_db.execute(select(Expense).where(Expense.id == exp_id))).scalar_one()
        assert exp.status == "approved"
        unsettled = (await verify_db.execute(select(ExpenseSplit).where(ExpenseSplit.expense_id == exp_id, ExpenseSplit.is_settled.is_(False)))).scalars().all()
        assert len(unsettled) == 2


@pytest.mark.asyncio
async def test_rollback_create_shadow_profile_failure(test_engine):
    """If friendship insertion fails during shadow profile creation, the shadow profile must not persist."""
    async with test_engine() as db:
        orig_add = db.add
        call_count = 0

        def failing_add(obj):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("Simulated shadow friendship failure")
            orig_add(obj)

        with patch.object(db, "add", side_effect=failing_add):
            with pytest.raises(RuntimeError, match="Simulated shadow friendship failure"):
                await friend_service.create_shadow_profile(db, USER_A, "Ghost User", "ghost@example.test")

    # Verify that NO ghost profile exists
    async with test_engine() as verify_db:
        profiles = (await verify_db.execute(select(Profile).where(Profile.email == "ghost@example.test"))).scalars().all()
        assert len(profiles) == 0
