from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence
from uuid import UUID
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from app.models.expense import Expense, ExpenseSplit
from app.models.category import UserCategory
from app.models.friend import Friend
from app.models.profile import Profile
from app.schemas.expense import PersonalExpenseCreate, PersonalExpenseUpdate, ExpenseSplitWrite
from app.schemas.friend import FriendExpenseCreate, FriendExpenseUpdate
from app.services.category_service import DEFAULT_CATEGORY_IDS
from app.services.user_service import check_friendship_or_shadow_access

CATEGORY_NAME_MAP = {
    "food & dining": "food",
    "food": "food",
    "dining": "food",
    "transport": "transport",
    "travel": "transport",
    "transportation": "transport",
    "shopping": "shopping",
    "shop": "shopping",
    "bills & utilities": "bills",
    "bills": "bills",
    "utilities": "bills",
    "other": "other",
    "system": "system",
}


def normalize_category_slug(category: str) -> str:
    cleaned = (category or "").strip().lower()
    return CATEGORY_NAME_MAP.get(cleaned, cleaned)


async def _validate_category(db: AsyncSession, user_id: UUID, category: str) -> str:
    slug = normalize_category_slug(category)
    if slug in DEFAULT_CATEGORY_IDS:
        return slug
    owned = await db.scalar(select(UserCategory.id).where(
        UserCategory.user_id == user_id, UserCategory.slug == slug,
    ).with_for_update(read=True))
    if owned is not None:
        return slug
    owned_by_name = await db.scalar(select(UserCategory.slug).where(
        UserCategory.user_id == user_id, func.lower(UserCategory.name) == (category or "").strip().lower(),
    ).with_for_update(read=True))
    if owned_by_name is not None:
        return owned_by_name
    raise HTTPException(422, "Category must be a default category or one of your custom categories")


async def _validate_allocation(
    db: AsyncSession, user_id: UUID, amount: Decimal, payer_id: UUID,
    splits: list[ExpenseSplitWrite],
) -> None:
    ids = [s.user_id for s in splits]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Each participant may have only one split")
    if splits:
        if user_id not in ids or payer_id not in ids:
            raise HTTPException(422, "Splits must include the current user and payer")
        if sum((s.amount for s in splits), Decimal("0")) != amount:
            raise HTTPException(422, "Split amounts must add up to the expense amount")
    elif payer_id != user_id:
        raise HTTPException(422, "An expense paid by someone else requires splits")

    external_ids = set(ids) - {user_id}
    if not external_ids:
        return
    relationships = (await db.execute(select(Friend).where(
        or_(Friend.user_id == user_id, Friend.friend_id == user_id), Friend.status == "accepted",
    ))).scalars().all()
    allowed = {f.friend_id if f.user_id == user_id else f.user_id for f in relationships}
    shadow_ids = (await db.execute(select(Profile.user_id).where(
        Profile.is_shadow.is_(True), Profile.shadow_created_by == user_id,
    ))).scalars().all()
    allowed.update(shadow_ids)
    if external_ids - allowed:
        raise HTTPException(403, "Splits may only include yourself, accepted friends or your shadow contacts")


async def create_personal_expense(db: AsyncSession, user_id: UUID, payload: PersonalExpenseCreate) -> Expense:
    async with db.begin():
        valid_category = await _validate_category(db, user_id, payload.category)
        payer_id = payload.paid_by or user_id
        await _validate_allocation(db, user_id, payload.amount, payer_id, payload.splits)
        expense = Expense(
            user_id=user_id, group_id=None, paid_by=payer_id,
            amount=payload.amount, category=valid_category, note=payload.note,
            expense_date=payload.expense_date,
            splits=[ExpenseSplit(user_id=s.user_id, amount=s.amount, is_settled=False) for s in payload.splits],
        )
        db.add(expense)
        await db.flush()
    return expense


async def _owned_non_group_expense(db: AsyncSession, user_id: UUID, expense_id: UUID) -> Expense:
    expense = (await db.execute(select(Expense).where(
        Expense.id == expense_id, Expense.user_id == user_id, Expense.group_id.is_(None),
    ).options(selectinload(Expense.splits)).with_for_update())).scalar_one_or_none()
    if expense is None:
        raise HTTPException(404, "Personal expense not found")
    # Lock splits too: another client can independently mark a split settled.
    await db.execute(select(ExpenseSplit).where(ExpenseSplit.expense_id == expense_id)
                     .with_for_update().execution_options(populate_existing=True))
    return expense


async def update_personal_expense(db: AsyncSession, user_id: UUID, expense_id: UUID, payload: PersonalExpenseUpdate) -> Expense:
    async with db.begin():
        expense = await _owned_non_group_expense(db, user_id, expense_id)
        updates = payload.model_dump(exclude_unset=True, exclude={"splits"})
        # A deleted category remains a valid unchanged legacy reference.
        if "category" in updates and updates["category"] != expense.category:
            updates["category"] = await _validate_category(db, user_id, updates["category"])
        amount = updates.get("amount", expense.amount)
        payer_id = updates.get("paid_by", expense.paid_by) or user_id
        splits = payload.splits if "splits" in payload.model_fields_set else [
            ExpenseSplitWrite(user_id=s.user_id, amount=s.amount) for s in expense.splits
        ]
        if {"amount", "paid_by", "splits"} & payload.model_fields_set:
            await _validate_allocation(db, user_id, amount, payer_id, splits)
        existing = {s.user_id: s for s in expense.splits}
        desired = {s.user_id: s.amount for s in splits}
        if any(s.is_settled and (desired.get(s.user_id) != s.amount or payer_id != (expense.paid_by or user_id)) for s in expense.splits):
            raise HTTPException(409, "Settled allocations cannot be changed")
        for key, value in updates.items():
            setattr(expense, key, value)
        if "splits" in payload.model_fields_set:
            replacement = []
            for split in splits:
                row = existing.get(split.user_id)
                if row is None:
                    row = ExpenseSplit(user_id=split.user_id, amount=split.amount, is_settled=False)
                else:
                    row.amount = split.amount
                replacement.append(row)
            expense.splits = replacement
        expense.edited_at = datetime.now(timezone.utc)
        await db.flush()
    return expense


async def delete_personal_expense(db: AsyncSession, user_id: UUID, expense_id: UUID) -> None:
    async with db.begin():
        expense = await _owned_non_group_expense(db, user_id, expense_id)
        # ORM cascade deletes loaded splits before the expense in this transaction.
        # This does not depend on Supabase RLS or a deployed cascade being present.
        await db.delete(expense)
        await db.flush()


async def get_personal_expenses(
    db: AsyncSession,
    user_id: UUID,
    category: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    search: str | None = None,
) -> Sequence[Expense]:
    """
    Retrieves personal (non-group) expenses strictly owned by user_id.
    Excludes any expense that has external debtor splits (split.user_id != user_id).
    Eager-loads splits.
    Supports filtering by category, date range, and note substring search.
    """
    # Exclude expenses with splits for other users (external debtors / shared friend expenses)
    external_split_subquery = (
        select(1)
        .where(
            ExpenseSplit.expense_id == Expense.id,
            ExpenseSplit.user_id != user_id,
        )
        .exists()
    )

    stmt = (
        select(Expense)
        .options(selectinload(Expense.splits))
        .where(
            Expense.user_id == user_id,
            Expense.group_id.is_(None),
            ~external_split_subquery,
        )
    )

    if category and category.strip() and category.strip().lower() != "all":
        stmt = stmt.where(Expense.category == category.strip())

    if start_date is not None:
        stmt = stmt.where(Expense.expense_date >= start_date)

    if end_date is not None:
        stmt = stmt.where(Expense.expense_date <= end_date)

    if search and search.strip():
        stmt = stmt.where(Expense.note.ilike(f"%{search.strip()}%"))

    stmt = stmt.order_by(Expense.expense_date.desc(), Expense.created_at.desc())

    result = await db.execute(stmt)
    expenses = result.scalars().all()

    # Double-check Python-side invariant: no splits belonging to other users
    return [
        exp
        for exp in expenses
        if not exp.splits or all(s.user_id == user_id for s in exp.splits)
    ]


async def get_friend_balances(
    db: AsyncSession, current_user_id: UUID
) -> list[dict]:
    """
    Calculates pairwise running net balances between current_user_id and all friends.
    Replicates client getFriendBalances() logic:
    - Non-group expenses (group_id IS NULL) involving current_user_id
    - Unsettled splits (is_settled == False)
    - Payer is user & split is friend -> friend owes user (+split.amount)
    - Payer is friend & split is user -> user owes friend (-split.amount)
    """
    user_has_split = (
        select(1)
        .where(
            ExpenseSplit.expense_id == Expense.id,
            ExpenseSplit.user_id == current_user_id,
        )
        .exists()
    )

    stmt = (
        select(Expense)
        .options(selectinload(Expense.splits))
        .where(
            Expense.group_id.is_(None),
            or_(
                Expense.paid_by == current_user_id,
                Expense.user_id == current_user_id,
                user_has_split,
            ),
        )
    )

    result = await db.execute(stmt)
    expenses = result.scalars().all()

    balances_map: dict[UUID, Decimal] = {}

    for expense in expenses:
        payer_id = expense.paid_by or expense.user_id
        is_user_payer = (payer_id == current_user_id)

        for split in expense.splits:
            if split.is_settled:
                continue

            is_user_split = (split.user_id == current_user_id)
            split_amount = Decimal(str(split.amount))

            if is_user_payer and not is_user_split:
                # User paid for friend -> friend owes user (+)
                friend_id = split.user_id
                balances_map[friend_id] = balances_map.get(friend_id, Decimal("0.00")) + split_amount
            elif not is_user_payer and is_user_split:
                # Friend paid for user -> user owes friend (-)
                friend_id = payer_id
                if friend_id:
                    balances_map[friend_id] = balances_map.get(friend_id, Decimal("0.00")) - split_amount

    two_dp = Decimal("0.01")
    return [
        {
            "friendId": fid,
            "netBalance": float(net.quantize(two_dp, rounding=ROUND_HALF_UP)),
        }
        for fid, net in balances_map.items()
    ]


async def get_friend_expenses_feed(
    db: AsyncSession, current_user_id: UUID, friend_id: UUID
) -> Sequence[Expense]:
    """
    Retrieves non-group expenses shared linearly between current_user_id and friend_id.
    Matches:
    - (Payer is current_user AND friend has a split)
    - OR (Payer is friend AND current_user has a split)
    Eager-loads splits and orders by expense_date desc, created_at desc.
    """
    friend_in_splits = (
        select(1)
        .where(
            ExpenseSplit.expense_id == Expense.id,
            ExpenseSplit.user_id == friend_id,
        )
        .exists()
    )

    user_in_splits = (
        select(1)
        .where(
            ExpenseSplit.expense_id == Expense.id,
            ExpenseSplit.user_id == current_user_id,
        )
        .exists()
    )

    user_is_payer = or_(
        Expense.paid_by == current_user_id,
        and_(Expense.paid_by.is_(None), Expense.user_id == current_user_id),
    )

    friend_is_payer = or_(
        Expense.paid_by == friend_id,
        and_(Expense.paid_by.is_(None), Expense.user_id == friend_id),
    )

    stmt = (
        select(Expense)
        .options(selectinload(Expense.splits))
        .where(
            Expense.group_id.is_(None),
            or_(
                and_(user_is_payer, friend_in_splits),
                and_(friend_is_payer, user_in_splits),
            ),
        )
        .order_by(Expense.expense_date.desc(), Expense.created_at.desc())
    )

    result = await db.execute(stmt)
    return result.scalars().all()


async def create_friend_expense(
    db: AsyncSession,
    user_id: UUID,
    friend_id: UUID,
    payload: FriendExpenseCreate,
) -> Expense:
    """
    Creates a 1:1 shared non-group expense between user_id and friend_id.
    - Rejects sharing an expense with yourself (400).
    - Verifies active friendship or shadow access between users (403).
    - Validates category (default or owned custom category).
    - Payer must be user_id or friend_id.
    - Creates expense and two splits atomically in a single SQL transaction.
    """
    if user_id == friend_id:
        raise HTTPException(400, "Cannot share expense with yourself")

    payer_id = payload.paid_by or user_id
    if payer_id != user_id and payer_id != friend_id:
        raise HTTPException(422, "Payer must be either yourself or your friend")

    async with db.begin():
        has_access = await check_friendship_or_shadow_access(db, user_id, friend_id)
        if not has_access:
            raise HTTPException(403, "You can only share expenses with accepted friends or contacts")

        valid_category = await _validate_category(db, user_id, payload.category)

        two_dp = Decimal("0.01")
        if payload.splits:
            ids = [s.user_id for s in payload.splits]
            if set(ids) != {user_id, friend_id}:
                raise HTTPException(422, "Splits must include exactly yourself and your friend")
            if sum((s.amount for s in payload.splits), Decimal("0")) != payload.amount:
                raise HTTPException(422, "Split amounts must add up to the expense amount")
            splits_to_create = [
                ExpenseSplit(user_id=s.user_id, amount=s.amount, is_settled=False)
                for s in payload.splits
            ]
        else:
            if payload.split_type == "full":
                other_id = friend_id if payer_id == user_id else user_id
                splits_to_create = [
                    ExpenseSplit(user_id=payer_id, amount=Decimal("0.00"), is_settled=False),
                    ExpenseSplit(user_id=other_id, amount=payload.amount, is_settled=False),
                ]
            else:
                half = (payload.amount / Decimal("2")).quantize(two_dp, rounding=ROUND_HALF_UP)
                remainder = payload.amount - half
                splits_to_create = [
                    ExpenseSplit(user_id=user_id, amount=half, is_settled=False),
                    ExpenseSplit(user_id=friend_id, amount=remainder, is_settled=False),
                ]

        expense = Expense(
            user_id=user_id,
            group_id=None,
            paid_by=payer_id,
            amount=payload.amount,
            category=valid_category,
            note=payload.note,
            expense_date=payload.expense_date,
            splits=splits_to_create,
        )
        db.add(expense)
        await db.flush()

    return expense


async def _shared_friend_expense(
    db: AsyncSession,
    user_id: UUID,
    friend_id: UUID,
    expense_id: UUID,
) -> Expense:
    """
    Scopes and locks a 1:1 shared non-group expense between user_id and friend_id.
    Ensures the caller is authorized (payer, creator, or split owner).
    """
    expense = (await db.execute(
        select(Expense)
        .where(
            Expense.id == expense_id,
            Expense.group_id.is_(None),
        )
        .options(selectinload(Expense.splits))
        .with_for_update()
    )).scalar_one_or_none()

    if expense is None:
        raise HTTPException(404, "Shared expense not found")

    await db.execute(
        select(ExpenseSplit)
        .where(ExpenseSplit.expense_id == expense_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    split_user_ids = {s.user_id for s in expense.splits}
    payer_id = expense.paid_by or expense.user_id
    parties = split_user_ids | {payer_id, expense.user_id}

    if user_id not in parties or friend_id not in parties:
        raise HTTPException(404, "Shared expense not found for this friend")

    return expense


async def update_friend_expense(
    db: AsyncSession,
    user_id: UUID,
    friend_id: UUID,
    expense_id: UUID,
    payload: FriendExpenseUpdate,
) -> Expense:
    """
    Updates a 1:1 shared non-group expense and synchronizes its splits.
    - Rejects updating settled allocations (409).
    - Recalculates splits if amount, payer, or split_type/splits are provided.
    """
    async with db.begin():
        expense = await _shared_friend_expense(db, user_id, friend_id, expense_id)

        updates = payload.model_dump(exclude_unset=True, exclude={"splits", "split_type"})
        if "category" in updates and updates["category"] != expense.category:
            updates["category"] = await _validate_category(db, user_id, updates["category"])

        amount = updates.get("amount", expense.amount)
        payer_id = updates.get("paid_by", expense.paid_by) or expense.user_id
        if payer_id != user_id and payer_id != friend_id:
            raise HTTPException(422, "Payer must be either yourself or your friend")

        two_dp = Decimal("0.01")
        if payload.splits is not None:
            ids = [s.user_id for s in payload.splits]
            if set(ids) != {user_id, friend_id}:
                raise HTTPException(422, "Splits must include exactly yourself and your friend")
            if sum((s.amount for s in payload.splits), Decimal("0")) != amount:
                raise HTTPException(422, "Split amounts must add up to the expense amount")
            new_split_data = {s.user_id: s.amount for s in payload.splits}
        elif payload.split_type is not None:
            if payload.split_type == "full":
                other_id = friend_id if payer_id == user_id else user_id
                new_split_data = {payer_id: Decimal("0.00"), other_id: amount}
            else:
                half = (amount / Decimal("2")).quantize(two_dp, rounding=ROUND_HALF_UP)
                remainder = amount - half
                new_split_data = {user_id: half, friend_id: remainder}
        elif "amount" in updates:
            half = (amount / Decimal("2")).quantize(two_dp, rounding=ROUND_HALF_UP)
            remainder = amount - half
            new_split_data = {user_id: half, friend_id: remainder}
        else:
            new_split_data = None

        if any(s.is_settled for s in expense.splits):
            if new_split_data is not None or "paid_by" in updates or "amount" in updates:
                raise HTTPException(409, "Settled allocations cannot be changed")

        for key, value in updates.items():
            setattr(expense, key, value)

        if new_split_data is not None:
            existing = {s.user_id: s for s in expense.splits}
            replacement = []
            for uid, s_amount in new_split_data.items():
                row = existing.get(uid)
                if row is None:
                    row = ExpenseSplit(user_id=uid, amount=s_amount, is_settled=False)
                else:
                    row.amount = s_amount
                replacement.append(row)
            expense.splits = replacement

        expense.edited_at = datetime.now(timezone.utc)
        await db.flush()

    return expense


async def delete_friend_expense(
    db: AsyncSession,
    user_id: UUID,
    friend_id: UUID,
    expense_id: UUID,
) -> None:
    """
    Deletes a 1:1 shared non-group expense.
    Splits cascade delete within the same transaction.
    """
    async with db.begin():
        expense = await _shared_friend_expense(db, user_id, friend_id, expense_id)
        await db.delete(expense)
        await db.flush()

