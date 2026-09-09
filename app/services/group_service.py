import calendar
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
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
from app.schemas.group import (
    GroupBalanceResponse,
    GroupCreate,
    GroupExpenseBulkCreate,
    GroupExpenseCreate,
    GroupExpenseUpdate,
    GroupMemberAdd,
    GroupMemberRoleUpdate,
    GroupSettleUpRequest,
    GroupUpdate,
)
from app.schemas.settlement import (
    MemberMonthlyExclusionWrite,
    MemberMonthlyStatusCreate,
)
from app.services.expense_service import _validate_category
from app.services.user_service import lookup_user_by_email


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


async def _assert_group_admin(
    db: AsyncSession, group_id: UUID, user_id: UUID, operation_desc: str = "perform this action"
) -> Group:
    """Verifies that user_id is an admin of group_id or the group creator.
    Raises 404 if group doesn't exist, 403 if user is not a member or not an admin.
    """
    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")

    member = (await db.execute(select(GroupMember).where(
        GroupMember.group_id == group_id, GroupMember.user_id == user_id
    ))).scalar_one_or_none()

    if not member:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not a member of this group")

    if member.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Only group admins can {operation_desc}")

    return group


async def _sync_monthly_rent(
    db: AsyncSession, group_id: UUID, user_id: UUID, monthly_rent: Decimal | None
) -> None:
    today = date.today()
    start_of_month = date(today.year, today.month, 1)
    last_day = calendar.monthrange(today.year, today.month)[1]
    end_of_month = date(today.year, today.month, last_day)

    stmt = (
        select(Expense)
        .options(selectinload(Expense.splits))
        .where(
            Expense.group_id == group_id,
            Expense.category == "rent",
            Expense.expense_date >= start_of_month,
            Expense.expense_date <= end_of_month,
            Expense.note == "Automated Monthly Rent",
        )
    )
    existing_rent = (await db.execute(stmt)).scalar_one_or_none()

    if monthly_rent and monthly_rent > 0:
        members_stmt = select(GroupMember.user_id).where(GroupMember.group_id == group_id)
        member_ids = (await db.execute(members_stmt)).scalars().all()
        if not member_ids:
            return

        split_count = len(member_ids)
        base_amount = (monthly_rent / Decimal(split_count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_allocated = base_amount * split_count
        difference = monthly_rent - total_allocated

        splits = []
        for i, m_id in enumerate(member_ids):
            allocated = base_amount + (difference if i == 0 else Decimal("0.00"))
            splits.append(ExpenseSplit(user_id=m_id, amount=allocated, is_settled=False))

        if existing_rent:
            existing_rent.amount = monthly_rent
            existing_rent.splits = splits
        else:
            new_rent = Expense(
                group_id=group_id,
                user_id=user_id,
                paid_by=user_id,
                amount=monthly_rent,
                category="rent",
                note="Automated Monthly Rent",
                expense_date=today,
                splits=splits,
            )
            db.add(new_rent)
    elif existing_rent:
        await db.delete(existing_rent)


async def create_group(db: AsyncSession, user_id: UUID, payload: GroupCreate) -> Group:
    async with db.begin():
        group = Group(
            name=payload.name,
            description=payload.description,
            type=payload.type,
            monthly_rent=payload.monthly_rent or Decimal("0.00"),
            sponsor_id=payload.sponsor_id,
            created_by=user_id,
        )
        db.add(group)
        await db.flush()

        creator_member = GroupMember(
            group_id=group.id,
            user_id=user_id,
            role="admin",
        )
        db.add(creator_member)
        await db.flush()

        if payload.monthly_rent and payload.monthly_rent > 0:
            await _sync_monthly_rent(db, group.id, user_id, payload.monthly_rent)

    return group


async def update_group(
    db: AsyncSession, group_id: UUID, user_id: UUID, payload: GroupUpdate
) -> Group:
    async with db.begin():
        group = await _assert_group_admin(db, group_id, user_id, "modify group settings")

        updates = payload.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(group, key, value)

        if "monthly_rent" in payload.model_fields_set:
            await _sync_monthly_rent(db, group.id, user_id, payload.monthly_rent)

        await db.flush()
    return group


async def delete_group(db: AsyncSession, group_id: UUID, user_id: UUID) -> None:
    async with db.begin():
        group = await _assert_group_admin(db, group_id, user_id, "delete the group")
        await db.delete(group)
        await db.flush()


async def add_group_member(
    db: AsyncSession, group_id: UUID, caller_id: UUID, payload: GroupMemberAdd
) -> GroupMember:
    async with db.begin():
        group = await _assert_group_admin(db, group_id, caller_id, "add members")

        target_user_id = payload.user_id
        if target_user_id is None and payload.email:
            profile = await lookup_user_by_email(db, payload.email)
            if not profile:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found with that email")
            target_user_id = profile.user_id

        if target_user_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Target user could not be determined")

        existing = (await db.execute(select(GroupMember).where(
            GroupMember.group_id == group_id, GroupMember.user_id == target_user_id
        ))).scalar_one_or_none()
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "User is already a member")

        member = GroupMember(
            group_id=group_id,
            user_id=target_user_id,
            role=payload.role,
        )
        db.add(member)
        await db.flush()

        if group.monthly_rent and group.monthly_rent > 0:
            await _sync_monthly_rent(db, group.id, caller_id, group.monthly_rent)

    return member


async def join_group(db: AsyncSession, group_id: UUID, user_id: UUID) -> GroupMember:
    async with db.begin():
        group = await db.get(Group, group_id)
        if not group:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")

        existing = (await db.execute(select(GroupMember).where(
            GroupMember.group_id == group_id, GroupMember.user_id == user_id
        ))).scalar_one_or_none()
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "You are already a member of this group")

        member = GroupMember(
            group_id=group_id,
            user_id=user_id,
            role="member",
        )
        db.add(member)
        await db.flush()

        if group.monthly_rent and group.monthly_rent > 0:
            await _sync_monthly_rent(db, group_id, user_id, group.monthly_rent)

    return member


async def remove_group_member(
    db: AsyncSession, group_id: UUID, caller_id: UUID, target_user_id: UUID
) -> None:
    async with db.begin():
        group = await db.get(Group, group_id)
        if not group:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")

        target_member = (await db.execute(select(GroupMember).where(
            GroupMember.group_id == group_id, GroupMember.user_id == target_user_id
        ))).scalar_one_or_none()
        if not target_member:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found in this group")

        is_self = (caller_id == target_user_id)

        if not is_self:
            caller_member = (await db.execute(select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.user_id == caller_id
            ))).scalar_one_or_none()
            if not caller_member or (caller_member.role != "admin" and group.created_by != caller_id):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Only group admins can remove other members")

        if target_member.role == "admin":
            total_members = await db.scalar(select(func.count()).select_from(GroupMember).where(
                GroupMember.group_id == group_id
            )) or 0
            other_admins = await db.scalar(select(func.count()).select_from(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.role == "admin",
                GroupMember.user_id != target_user_id,
            )) or 0
            if total_members > 1 and other_admins == 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Cannot remove the sole admin while other members remain in the group. Promote another member to admin first.",
                )

        await db.delete(target_member)
        await db.flush()

        if group.monthly_rent and group.monthly_rent > 0:
            await _sync_monthly_rent(db, group_id, caller_id, group.monthly_rent)


async def update_member_role(
    db: AsyncSession,
    group_id: UUID,
    caller_id: UUID,
    target_user_id: UUID,
    payload: GroupMemberRoleUpdate,
) -> GroupMember:
    async with db.begin():
        group = await _assert_group_admin(db, group_id, caller_id, "change member roles")

        if caller_id == target_user_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot modify your own role")

        target_member = (await db.execute(select(GroupMember).where(
            GroupMember.group_id == group_id, GroupMember.user_id == target_user_id
        ))).scalar_one_or_none()
        if not target_member:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found in this group")

        if target_member.role == "admin" and payload.role == "member":
            other_admins = await db.scalar(select(func.count()).select_from(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.role == "admin",
                GroupMember.user_id != target_user_id,
            )) or 0
            if other_admins == 0:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot demote the sole group admin")

        target_member.role = payload.role
        await db.flush()

    return target_member


def _parse_expense_date(val: datetime | date | str | None) -> date:
    if val is None:
        return date.today()
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val.split("T")[0])
    return date.today()


async def _format_expense_response(db: AsyncSession, e: Expense) -> dict:
    user_ids = set()
    payer = e.paid_by or e.user_id
    if payer:
        user_ids.add(payer)
    for s in e.splits:
        user_ids.add(s.user_id)

    profiles_map = {}
    if user_ids:
        profiles_stmt = select(Profile).where(Profile.user_id.in_(list(user_ids)))
        profiles_result = await db.execute(profiles_stmt)
        profiles_map = {
            p.user_id: (p.display_name or p.email or "Unknown")
            for p in profiles_result.scalars().all()
        }

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

    return {
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


async def create_group_expense(
    db: AsyncSession, group_id: UUID, caller_id: UUID, payload: GroupExpenseCreate
) -> dict:
    async with db.begin():
        await get_group_detail(db, group_id, caller_id)

        payer_id = payload.paid_by or caller_id

        payer_is_member = await db.scalar(
            select(GroupMember.id).where(
                GroupMember.group_id == group_id, GroupMember.user_id == payer_id
            )
        )
        if not payer_is_member:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Payer must be a member of this group",
            )

        exp_date = _parse_expense_date(payload.expense_date)

        locked_settlement = (
            await db.execute(
                select(MonthlySettlement).where(
                    MonthlySettlement.group_id == group_id,
                    MonthlySettlement.month == exp_date.month,
                    MonthlySettlement.year == exp_date.year,
                    MonthlySettlement.status == "locked",
                )
            )
        ).scalar_one_or_none()
        if locked_settlement:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot add expenses to a locked monthly settlement period",
            )

        await _validate_category(db, caller_id, payload.category)

        members_stmt = select(GroupMember.user_id).where(GroupMember.group_id == group_id)
        all_member_ids = set((await db.execute(members_stmt)).scalars().all())

        splits_to_insert: list[ExpenseSplit] = []
        if payload.splits:
            split_uids = [s.user_id for s in payload.splits]
            if len(split_uids) != len(set(split_uids)):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Each member may only have one split",
                )
            for uid in split_uids:
                if uid not in all_member_ids:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Splits may only include members of this group",
                    )
            total_splits = sum((Decimal(str(s.amount)) for s in payload.splits), Decimal("0.00"))
            exp_amount = Decimal(str(payload.amount))
            if abs(total_splits - exp_amount) > Decimal("0.02"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Split amounts sum to {total_splits}, which does not match expense amount {exp_amount}",
                )
            splits_to_insert = [
                ExpenseSplit(
                    user_id=s.user_id,
                    amount=Decimal(str(s.amount)),
                    is_settled=False,
                )
                for s in payload.splits
            ]
        else:
            member_list = sorted(list(all_member_ids))
            count = len(member_list)
            if count == 0:
                raise HTTPException(status_code=400, detail="Group has no members to split among")
            exp_amount = Decimal(str(payload.amount))
            base_amount = (exp_amount / Decimal(count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_allocated = base_amount * count
            difference = exp_amount - total_allocated
            splits_to_insert = [
                ExpenseSplit(
                    user_id=m_id,
                    amount=base_amount + (difference if i == 0 else Decimal("0.00")),
                    is_settled=False,
                )
                for i, m_id in enumerate(member_list)
            ]

        expense = Expense(
            group_id=group_id,
            user_id=caller_id,
            paid_by=payer_id,
            amount=Decimal(str(payload.amount)),
            category=payload.category,
            note=payload.note,
            expense_date=exp_date,
            status=payload.status or "submitted",
            receipt_url=payload.receipt_url,
            splits=splits_to_insert,
        )
        db.add(expense)
        await db.flush()

        res = await _format_expense_response(db, expense)
    return res


async def bulk_create_group_expenses(
    db: AsyncSession, group_id: UUID, caller_id: UUID, payload: GroupExpenseBulkCreate
) -> list[dict]:
    async with db.begin():
        await get_group_detail(db, group_id, caller_id)

        members_stmt = select(GroupMember.user_id).where(GroupMember.group_id == group_id)
        all_member_ids = set((await db.execute(members_stmt)).scalars().all())

        created_expenses = []
        for exp_payload in payload.expenses:
            payer_id = exp_payload.paid_by or caller_id
            if payer_id not in all_member_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Payer must be a member of this group",
                )

            exp_date = _parse_expense_date(exp_payload.expense_date)

            locked_settlement = (
                await db.execute(
                    select(MonthlySettlement).where(
                        MonthlySettlement.group_id == group_id,
                        MonthlySettlement.month == exp_date.month,
                        MonthlySettlement.year == exp_date.year,
                        MonthlySettlement.status == "locked",
                    )
                )
            ).scalar_one_or_none()
            if locked_settlement:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot add expenses to locked month {exp_date.month}/{exp_date.year}",
                )

            await _validate_category(db, caller_id, exp_payload.category)

            splits_to_insert = []
            if exp_payload.splits:
                split_uids = [s.user_id for s in exp_payload.splits]
                if len(split_uids) != len(set(split_uids)):
                    raise HTTPException(status_code=422, detail="Each member may only have one split")
                for uid in split_uids:
                    if uid not in all_member_ids:
                        raise HTTPException(status_code=403, detail="Splits may only include members of this group")
                total_splits = sum((Decimal(str(s.amount)) for s in exp_payload.splits), Decimal("0.00"))
                exp_amount = Decimal(str(exp_payload.amount))
                if abs(total_splits - exp_amount) > Decimal("0.02"):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Split amounts do not match expense amount: {total_splits} != {exp_amount}",
                    )
                splits_to_insert = [
                    ExpenseSplit(user_id=s.user_id, amount=Decimal(str(s.amount)), is_settled=False)
                    for s in exp_payload.splits
                ]
            else:
                member_list = sorted(list(all_member_ids))
                count = len(member_list)
                exp_amount = Decimal(str(exp_payload.amount))
                base_amount = (exp_amount / Decimal(count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                difference = exp_amount - (base_amount * count)
                splits_to_insert = [
                    ExpenseSplit(
                        user_id=m_id,
                        amount=base_amount + (difference if i == 0 else Decimal("0.00")),
                        is_settled=False,
                    )
                    for i, m_id in enumerate(member_list)
                ]

            expense = Expense(
                group_id=group_id,
                user_id=caller_id,
                paid_by=payer_id,
                amount=Decimal(str(exp_payload.amount)),
                category=exp_payload.category,
                note=exp_payload.note,
                expense_date=exp_date,
                status=exp_payload.status or "submitted",
                receipt_url=exp_payload.receipt_url,
                splits=splits_to_insert,
            )
            db.add(expense)
            await db.flush()
            created_expenses.append(expense)

        results = []
        for exp in created_expenses:
            res = await _format_expense_response(db, exp)
            results.append(res)
    return results


async def update_group_expense(
    db: AsyncSession, group_id: UUID, expense_id: UUID, caller_id: UUID, payload: GroupExpenseUpdate
) -> dict:
    async with db.begin():
        await get_group_detail(db, group_id, caller_id)

        expense = (
            await db.execute(
                select(Expense)
                .options(selectinload(Expense.splits))
                .where(Expense.id == expense_id, Expense.group_id == group_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

        if not expense:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Group expense not found",
            )

        caller_role = await db.scalar(
            select(GroupMember.role).where(
                GroupMember.group_id == group_id, GroupMember.user_id == caller_id
            )
        )
        is_admin = (caller_role == "admin")

        if caller_id != expense.user_id and caller_id != expense.paid_by and not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the expense creator, payer, or a group admin can edit this expense",
            )

        locked_settlement = (
            await db.execute(
                select(MonthlySettlement).where(
                    MonthlySettlement.group_id == group_id,
                    MonthlySettlement.month == expense.expense_date.month,
                    MonthlySettlement.year == expense.expense_date.year,
                    MonthlySettlement.status == "locked",
                )
            )
        ).scalar_one_or_none()
        if locked_settlement:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot edit an expense in a locked monthly settlement period",
            )

        members_stmt = select(GroupMember.user_id).where(GroupMember.group_id == group_id)
        all_member_ids = set((await db.execute(members_stmt)).scalars().all())

        if payload.paid_by is not None:
            if payload.paid_by not in all_member_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Payer must be a member of this group",
                )
            expense.paid_by = payload.paid_by

        if payload.expense_date is not None:
            new_date = _parse_expense_date(payload.expense_date)
            new_locked = (
                await db.execute(
                    select(MonthlySettlement).where(
                        MonthlySettlement.group_id == group_id,
                        MonthlySettlement.month == new_date.month,
                        MonthlySettlement.year == new_date.year,
                        MonthlySettlement.status == "locked",
                    )
                )
            ).scalar_one_or_none()
            if new_locked:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot move an expense into a locked monthly settlement period",
                )
            expense.expense_date = new_date

        if payload.category is not None and payload.category != expense.category:
            await _validate_category(db, caller_id, payload.category)
            expense.category = payload.category

        if payload.amount is not None:
            expense.amount = Decimal(str(payload.amount))

        if payload.note is not None:
            expense.note = payload.note

        if payload.status is not None:
            expense.status = payload.status

        if payload.receipt_url is not None:
            expense.receipt_url = payload.receipt_url

        if payload.splits is not None:
            split_uids = [s.user_id for s in payload.splits]
            if len(split_uids) != len(set(split_uids)):
                raise HTTPException(status_code=422, detail="Each member may only have one split")
            for uid in split_uids:
                if uid not in all_member_ids:
                    raise HTTPException(status_code=403, detail="Splits may only include members of this group")
            total_splits = sum((Decimal(str(s.amount)) for s in payload.splits), Decimal("0.00"))
            exp_amount = Decimal(str(expense.amount))
            if abs(total_splits - exp_amount) > Decimal("0.02"):
                raise HTTPException(
                    status_code=422,
                    detail=f"Split amounts sum to {total_splits}, which does not match expense amount {exp_amount}",
                )

            for s in list(expense.splits):
                await db.delete(s)
            await db.flush()

            for s in payload.splits:
                new_s = ExpenseSplit(
                    expense_id=expense_id,
                    user_id=s.user_id,
                    amount=Decimal(str(s.amount)),
                    is_settled=False,
                )
                db.add(new_s)
            await db.flush()

        expense.edited_at = datetime.utcnow()
        await db.flush()

        await db.refresh(expense, attribute_names=["splits"])
        res = await _format_expense_response(db, expense)
    return res


async def delete_group_expense(
    db: AsyncSession, group_id: UUID, expense_id: UUID, caller_id: UUID
) -> None:
    async with db.begin():
        await get_group_detail(db, group_id, caller_id)

        expense = (
            await db.execute(
                select(Expense).where(Expense.id == expense_id, Expense.group_id == group_id).with_for_update()
            )
        ).scalar_one_or_none()

        if not expense:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group expense not found")

        caller_role = await db.scalar(
            select(GroupMember.role).where(
                GroupMember.group_id == group_id, GroupMember.user_id == caller_id
            )
        )
        is_admin = (caller_role == "admin")

        if caller_id != expense.user_id and caller_id != expense.paid_by and not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the expense creator, payer, or a group admin can delete this expense",
            )

        locked_settlement = (
            await db.execute(
                select(MonthlySettlement).where(
                    MonthlySettlement.group_id == group_id,
                    MonthlySettlement.month == expense.expense_date.month,
                    MonthlySettlement.year == expense.expense_date.year,
                    MonthlySettlement.status == "locked",
                )
            )
        ).scalar_one_or_none()
        if locked_settlement:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete an expense in a locked monthly settlement period",
            )

        await db.delete(expense)
        await db.flush()


async def settle_group_expenses(
    db: AsyncSession, group_id: UUID, caller_id: UUID, payload: GroupSettleUpRequest
) -> int:
    async with db.begin():
        await get_group_detail(db, group_id, caller_id)

        caller_role = await db.scalar(
            select(GroupMember.role).where(
                GroupMember.group_id == group_id, GroupMember.user_id == caller_id
            )
        )
        is_admin = (caller_role == "admin")

        if caller_id != payload.from_user_id and caller_id != payload.to_user_id and not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the debtor, creditor, or a group admin can settle balances",
            )

        exp_stmt = select(Expense.id).where(
            Expense.group_id == group_id,
            or_(
                Expense.paid_by == payload.to_user_id,
                and_(Expense.paid_by.is_(None), Expense.user_id == payload.to_user_id),
            ),
        )
        exp_ids = (await db.execute(exp_stmt)).scalars().all()

        if not exp_ids:
            return 0

        splits_stmt = (
            select(ExpenseSplit)
            .where(
                ExpenseSplit.expense_id.in_(exp_ids),
                ExpenseSplit.user_id == payload.from_user_id,
                ExpenseSplit.is_settled.is_(False),
            )
            .with_for_update()
        )
        splits = (await db.execute(splits_stmt)).scalars().all()
        for s in splits:
            s.is_settled = True
        await db.flush()
        return len(splits)


async def bulk_reimburse_group_expenses(
    db: AsyncSession, group_id: UUID, caller_id: UUID
) -> int:
    async with db.begin():
        await get_group_detail(db, group_id, caller_id)

        stmt = (
            select(Expense)
            .where(Expense.group_id == group_id, Expense.status == "approved")
            .with_for_update()
        )
        expenses = (await db.execute(stmt)).scalars().all()
        for e in expenses:
            e.status = "reimbursed"
        await db.flush()

        all_reimbursed_ids = (
            await db.execute(
                select(Expense.id).where(Expense.group_id == group_id, Expense.status == "reimbursed")
            )
        ).scalars().all()

        if all_reimbursed_ids:
            splits_stmt = (
                select(ExpenseSplit)
                .where(
                    ExpenseSplit.expense_id.in_(all_reimbursed_ids),
                    ExpenseSplit.is_settled.is_(False),
                )
                .with_for_update()
            )
            splits = (await db.execute(splits_stmt)).scalars().all()
            for s in splits:
                s.is_settled = True
            await db.flush()

        return len(expenses)


async def create_or_get_monthly_settlement(
    db: AsyncSession, group_id: UUID, caller_id: UUID, month: int, year: int, status_val: str = "open"
) -> MonthlySettlement:
    if month < 1 or month > 12:
        raise HTTPException(status_code=422, detail="Month must be between 1 and 12")
    if year < 2020:
        raise HTTPException(status_code=422, detail="Year must be 2020 or later")

    async with db.begin():
        await get_group_detail(db, group_id, caller_id)

        stmt = (
            select(MonthlySettlement)
            .where(
                MonthlySettlement.group_id == group_id,
                MonthlySettlement.month == month,
                MonthlySettlement.year == year,
            )
            .with_for_update()
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing

        new_settlement = MonthlySettlement(
            group_id=group_id,
            month=month,
            year=year,
            status=status_val,
        )
        db.add(new_settlement)
        await db.flush()
    return new_settlement


async def finalize_monthly_settlement(
    db: AsyncSession, group_id: UUID, caller_id: UUID, month: int, year: int
) -> MonthlySettlement:
    if month < 1 or month > 12:
        raise HTTPException(status_code=422, detail="Month must be between 1 and 12")
    if year < 2020:
        raise HTTPException(status_code=422, detail="Year must be 2020 or later")

    async with db.begin():
        await get_group_detail(db, group_id, caller_id)

        stmt = (
            select(MonthlySettlement)
            .where(
                MonthlySettlement.group_id == group_id,
                MonthlySettlement.month == month,
                MonthlySettlement.year == year,
            )
            .with_for_update()
        )
        settlement = (await db.execute(stmt)).scalar_one_or_none()

        if settlement:
            if settlement.status == "locked":
                return settlement
            settlement.status = "locked"
            settlement.updated_at = datetime.utcnow()
            await db.flush()
            return settlement
        else:
            new_settlement = MonthlySettlement(
                group_id=group_id,
                month=month,
                year=year,
                status="locked",
            )
            db.add(new_settlement)
            await db.flush()
            return new_settlement


async def update_member_monthly_status(
    db: AsyncSession, group_id: UUID, settlement_id: UUID, caller_id: UUID, payload: MemberMonthlyStatusCreate
) -> MemberMonthlyStatus:
    async with db.begin():
        await get_group_detail(db, group_id, caller_id)

        settlement = await db.get(MonthlySettlement, settlement_id)
        if not settlement or settlement.group_id != group_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found")

        if settlement.status == "locked":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Settlement is already locked")

        target_user_id = payload.user_id or caller_id

        if target_user_id != caller_id:
            caller_role = await db.scalar(
                select(GroupMember.role).where(
                    GroupMember.group_id == group_id, GroupMember.user_id == caller_id
                )
            )
            if caller_role != "admin":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only group admins can update status on behalf of other members",
                )

        target_member = await db.scalar(
            select(GroupMember.id).where(
                GroupMember.group_id == group_id, GroupMember.user_id == target_user_id
            )
        )
        if not target_member:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user is not a group member")

        stmt = (
            select(MemberMonthlyStatus)
            .where(
                MemberMonthlyStatus.settlement_id == settlement_id,
                MemberMonthlyStatus.user_id == target_user_id,
            )
            .with_for_update()
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing

        status_row = MemberMonthlyStatus(
            settlement_id=settlement_id,
            user_id=target_user_id,
            status=payload.status or "complete",
        )
        db.add(status_row)
        await db.flush()
    return status_row


async def set_member_monthly_exclusion(
    db: AsyncSession, group_id: UUID, month: int, year: int, caller_id: UUID, payload: MemberMonthlyExclusionWrite
) -> MemberMonthlyExclusion | None:
    if month < 1 or month > 12:
        raise HTTPException(status_code=422, detail="Month must be between 1 and 12")
    if year < 2020:
        raise HTTPException(status_code=422, detail="Year must be 2020 or later")

    async with db.begin():
        await get_group_detail(db, group_id, caller_id)

        target_user_id = payload.user_id or caller_id

        if target_user_id != caller_id:
            caller_role = await db.scalar(
                select(GroupMember.role).where(
                    GroupMember.group_id == group_id, GroupMember.user_id == caller_id
                )
            )
            if caller_role != "admin":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only group admins can set exclusions for other members",
                )

        target_member = await db.scalar(
            select(GroupMember.id).where(
                GroupMember.group_id == group_id, GroupMember.user_id == target_user_id
            )
        )
        if not target_member:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user is not a group member")

        settlement = (
            await db.execute(
                select(MonthlySettlement).where(
                    MonthlySettlement.group_id == group_id,
                    MonthlySettlement.month == month,
                    MonthlySettlement.year == year,
                )
            )
        ).scalar_one_or_none()
        if settlement and settlement.status == "locked":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot change exclusions for a locked settlement period",
            )

        stmt = (
            select(MemberMonthlyExclusion)
            .where(
                MemberMonthlyExclusion.group_id == group_id,
                MemberMonthlyExclusion.user_id == target_user_id,
                MemberMonthlyExclusion.month == month,
                MemberMonthlyExclusion.year == year,
            )
            .with_for_update()
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()

        if payload.exclusion_type == "none":
            if existing:
                await db.delete(existing)
                await db.flush()
            return None
        else:
            if existing:
                existing.exclusion_type = payload.exclusion_type
                await db.flush()
                return existing
            else:
                new_exclusion = MemberMonthlyExclusion(
                    group_id=group_id,
                    user_id=target_user_id,
                    month=month,
                    year=year,
                    exclusion_type=payload.exclusion_type,
                )
                db.add(new_exclusion)
                await db.flush()
                return new_exclusion

