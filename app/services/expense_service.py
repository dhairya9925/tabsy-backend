from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence
from uuid import UUID
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.expense import Expense, ExpenseSplit


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

