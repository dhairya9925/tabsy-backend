import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.expense import Expense, ExpenseSplit
from app.models.group import Group, GroupMember
from app.models.profile import Profile
from app.models.settlement import (
    MemberMonthlyExclusion,
    MemberMonthlyStatus,
    MonthlySettlement,
)
from app.schemas.group import GroupBalanceResponse


async def get_user_groups(db: AsyncSession, user_id: UUID) -> Sequence[Group]:
    """
    Retrieves all groups where the specified user is a member, ordered by created_at desc.
    """
    stmt = (
        select(Group)
        .join(GroupMember, Group.id == GroupMember.group_id)
        .where(GroupMember.user_id == user_id)
        .order_by(Group.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_group_detail(db: AsyncSession, group_id: UUID, user_id: UUID) -> Group:
    """
    Retrieves a group by its ID. Enforces that user_id is a member of the group.
    Raises 404 if group does not exist, or 403 if user is not a member.
    """
    # Check if group exists
    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Check membership
    member_stmt = select(GroupMember).where(
        GroupMember.group_id == group_id,
        GroupMember.user_id == user_id,
    )
    member_result = await db.execute(member_stmt)
    if not member_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this group",
        )

    return group


async def get_group_members(
    db: AsyncSession, group_id: UUID, user_id: UUID
) -> Sequence[tuple[GroupMember, Profile | None]]:
    """
    Retrieves all members of a group with their joined profile metadata in a single query.
    Enforces membership authorization for user_id.
    """
    # First verify group and user membership
    await get_group_detail(db, group_id, user_id)

    # Single-query join between group_members and profiles
    stmt = (
        select(GroupMember, Profile)
        .outerjoin(Profile, GroupMember.user_id == Profile.user_id)
        .where(GroupMember.group_id == group_id)
        .order_by(GroupMember.joined_at.asc())
    )
    result = await db.execute(stmt)
    return result.all()


async def get_group_expenses(
    db: AsyncSession, group_id: UUID, user_id: UUID
) -> list[dict]:
    """
    Retrieves all group expenses (excluding 'Automated Monthly Rent') with eager-loaded splits
    and enriched with payer_name and member_name from profiles.
    Enforces group membership authorization for user_id.
    """
    await get_group_detail(db, group_id, user_id)

    stmt = (
        select(Expense)
        .options(selectinload(Expense.splits))
        .where(
            Expense.group_id == group_id,
            or_(Expense.note.is_(None), Expense.note != "Automated Monthly Rent"),
        )
        .order_by(Expense.expense_date.desc(), Expense.created_at.desc())
    )
    result = await db.execute(stmt)
    expenses = result.scalars().all()

    if not expenses:
        return []

    # Collect all user IDs from expenses and splits
    user_ids = set()
    for e in expenses:
        payer = e.paid_by or e.user_id
        if payer:
            user_ids.add(payer)
        for s in e.splits:
            user_ids.add(s.user_id)

    # Fetch profile display names
    profiles_stmt = select(Profile).where(Profile.user_id.in_(list(user_ids)))
    profiles_result = await db.execute(profiles_stmt)
    profiles_map = {
        p.user_id: (p.display_name or p.email or "Unknown")
        for p in profiles_result.scalars().all()
    }

    data = []
    for e in expenses:
        payer = e.paid_by or e.user_id
        payer_name = profiles_map.get(payer, "Unknown")

        splits_data = [
            {
                "id": s.id,
                "expense_id": s.expense_id,
                "user_id": s.user_id,
                "amount": float(s.amount),
                "is_settled": s.is_settled,
                "created_at": s.created_at,
                "member_name": profiles_map.get(s.user_id, "Unknown"),
            }
            for s in e.splits
        ]

        data.append(
            {
                "id": e.id,
                "amount": float(e.amount),
                "category": e.category,
                "note": e.note,
                "expense_date": e.expense_date,
                "user_id": e.user_id,
                "group_id": e.group_id,
                "paid_by": e.paid_by,
                "status": e.status,
                "receipt_url": e.receipt_url,
                "created_at": e.created_at,
                "updated_at": e.updated_at,
                "edited_at": e.edited_at,
                "payer_name": payer_name,
                "splits": splits_data,
            }
        )

    return data


async def get_group_balances(
    db: AsyncSession, group_id: UUID, user_id: UUID
) -> list[GroupBalanceResponse]:
    """
    Calculates simplified peer-to-peer or sponsor debt balances for a group.
    Replicates exact logic from src/hooks/useGroupExpenses.ts:
    - Expenses where group_id == group_id, note != 'Automated Monthly Rent', category != 'system'
    - Unsettled splits only
    - Standard vs Reimbursable logic
    - Greedy debt simplification
    """
    group = await get_group_detail(db, group_id, user_id)

    # 1. Fetch group members
    members_stmt = select(GroupMember.user_id).where(GroupMember.group_id == group_id)
    members_res = await db.execute(members_stmt)
    all_user_ids = list(members_res.scalars().all())

    is_reimbursable = (group.type == "reimbursable")
    sponsor_uuid: UUID | None = None
    if group.sponsor_id:
        try:
            sponsor_uuid = UUID(str(group.sponsor_id))
        except (ValueError, TypeError):
            sponsor_uuid = None

    if sponsor_uuid and sponsor_uuid not in all_user_ids:
        all_user_ids.append(sponsor_uuid)

    # 2. Fetch profiles for all members
    profiles_stmt = select(Profile).where(Profile.user_id.in_(all_user_ids))
    profiles_res = await db.execute(profiles_stmt)
    profile_map = {
        p.user_id: (p.display_name or p.email or "Unknown")
        for p in profiles_res.scalars().all()
    }

    # 3. Fetch expenses with splits
    expenses_stmt = (
        select(Expense)
        .options(selectinload(Expense.splits))
        .where(
            Expense.group_id == group_id,
            or_(Expense.note.is_(None), Expense.note != "Automated Monthly Rent"),
            or_(Expense.category.is_(None), Expense.category != "system"),
        )
    )
    expenses_res = await db.execute(expenses_stmt)
    expenses = expenses_res.scalars().all()

    # 4. Calculate net balances
    net_balance: dict[UUID, Decimal] = {uid: Decimal("0.00") for uid in all_user_ids}

    for exp in expenses:
        payer_id = exp.paid_by or exp.user_id
        if not payer_id:
            continue

        unsettled_splits = [s for s in exp.splits if not s.is_settled]

        if is_reimbursable and sponsor_uuid:
            # Reimbursable: sponsor owes payer for all unsettled splits
            if payer_id != sponsor_uuid:
                total_amount = sum(Decimal(str(s.amount)) for s in unsettled_splits)
                if total_amount > Decimal("0.01"):
                    net_balance[payer_id] = net_balance.get(payer_id, Decimal("0.00")) + total_amount
                    net_balance[sponsor_uuid] = net_balance.get(sponsor_uuid, Decimal("0.00")) - total_amount
        else:
            # Standard peer-to-peer
            for split in unsettled_splits:
                if split.user_id != payer_id:
                    split_amt = Decimal(str(split.amount))
                    net_balance[payer_id] = net_balance.get(payer_id, Decimal("0.00")) + split_amt
                    net_balance[split.user_id] = net_balance.get(split.user_id, Decimal("0.00")) - split_amt

    # 5. Greedy debt simplification
    creditors = [
        {"id": uid, "amount": amt}
        for uid, amt in net_balance.items()
        if amt > Decimal("0.01")
    ]
    debtors = [
        {"id": uid, "amount": -amt}
        for uid, amt in net_balance.items()
        if amt < Decimal("-0.01")
    ]

    creditors.sort(key=lambda x: x["amount"], reverse=True)
    debtors.sort(key=lambda x: x["amount"], reverse=True)

    balances: list[GroupBalanceResponse] = []
    ci = 0
    di = 0

    two_dp = Decimal("0.01")
    while ci < len(creditors) and di < len(debtors):
        settle = min(creditors[ci]["amount"], debtors[di]["amount"])
        if settle > Decimal("0.01"):
            balances.append(
                GroupBalanceResponse(
                    from_user_id=debtors[di]["id"],
                    from_name=profile_map.get(debtors[di]["id"], "Unknown"),
                    to_user_id=creditors[ci]["id"],
                    to_name=profile_map.get(creditors[ci]["id"], "Unknown"),
                    amount=float(settle.quantize(two_dp, rounding=ROUND_HALF_UP)),
                )
            )
        creditors[ci]["amount"] -= settle
        debtors[di]["amount"] -= settle
        if creditors[ci]["amount"] < Decimal("0.01"):
            ci += 1
        if debtors[di]["amount"] < Decimal("0.01"):
            di += 1

    return balances


async def get_monthly_settlement(
    db: AsyncSession, group_id: UUID, user_id: UUID, month: int, year: int
) -> MonthlySettlement | None:
    await get_group_detail(db, group_id, user_id)

    stmt = select(MonthlySettlement).where(
        MonthlySettlement.group_id == group_id,
        MonthlySettlement.month == month,
        MonthlySettlement.year == year,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_multi_month_settlements(
    db: AsyncSession, group_id: UUID, user_id: UUID, limit: int = 12
) -> Sequence[MonthlySettlement]:
    await get_group_detail(db, group_id, user_id)

    stmt = (
        select(MonthlySettlement)
        .where(MonthlySettlement.group_id == group_id)
        .order_by(MonthlySettlement.year.desc(), MonthlySettlement.month.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_member_monthly_statuses(
    db: AsyncSession, group_id: UUID, user_id: UUID, settlement_id: UUID
) -> Sequence[MemberMonthlyStatus]:
    await get_group_detail(db, group_id, user_id)

    settlement = await db.get(MonthlySettlement, settlement_id)
    if not settlement or settlement.group_id != group_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Settlement not found for this group",
        )

    stmt = select(MemberMonthlyStatus).where(
        MemberMonthlyStatus.settlement_id == settlement_id
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_monthly_group_expenses(
    db: AsyncSession, group_id: UUID, user_id: UUID, month: int, year: int
) -> Sequence[Expense]:
    await get_group_detail(db, group_id, user_id)

    start_date = date(year, month, 1)
    _, last_day = calendar.monthrange(year, month)
    end_date = date(year, month, last_day)

    stmt = (
        select(Expense)
        .options(selectinload(Expense.splits))
        .where(
            Expense.group_id == group_id,
            Expense.expense_date >= start_date,
            Expense.expense_date <= end_date,
        )
        .order_by(Expense.expense_date.desc(), Expense.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_member_monthly_exclusions(
    db: AsyncSession, group_id: UUID, user_id: UUID, month: int, year: int
) -> Sequence[MemberMonthlyExclusion]:
    await get_group_detail(db, group_id, user_id)

    stmt = select(MemberMonthlyExclusion).where(
        MemberMonthlyExclusion.group_id == group_id,
        MemberMonthlyExclusion.month == month,
        MemberMonthlyExclusion.year == year,
    )
    result = await db.execute(stmt)
    return result.scalars().all()

