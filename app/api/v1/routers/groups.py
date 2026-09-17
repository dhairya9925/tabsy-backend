from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.models.group import GroupMember
from app.models.profile import Profile
from app.schemas.envelope import ResponseEnvelope
from app.schemas.expense import ExpenseResponse
from app.schemas.group import (
    GroupBalanceResponse,
    GroupCreate,
    GroupExpenseBulkCreate,
    GroupExpenseCreate,
    GroupExpenseResponse,
    GroupExpenseSplitResponse,
    GroupExpenseUpdate,
    GroupMemberAdd,
    GroupMemberResponse,
    GroupMemberRoleUpdate,
    GroupResponse,
    GroupSettleUpRequest,
    GroupUpdate,
    MemberProfileResponse,
    MonthlyLedgerContributionRecord,
    MonthlyLedgerResponse,
)
from app.schemas.settlement import (
    MemberMonthlyExclusionResponse,
    MemberMonthlyExclusionWrite,
    MemberMonthlyStatusCreate,
    MemberMonthlyStatusResponse,
    MonthlySettlementCreate,
    MonthlySettlementResponse,
)
from app.services.group_service import (
    add_group_member,
    bulk_create_group_expenses,
    bulk_reimburse_group_expenses,
    create_group,
    create_group_expense,
    create_or_get_monthly_settlement,
    delete_group,
    delete_group_expense,
    finalize_monthly_settlement,
    get_group_balances,
    get_group_detail,
    get_group_expenses,
    get_group_members,
    get_member_monthly_exclusions,
    get_member_monthly_statuses,
    get_monthly_group_expenses,
    get_monthly_ledger,
    get_monthly_settlement,
    get_multi_month_settlements,
    get_user_groups,
    join_group,
    record_monthly_ledger_contribution,
    remove_group_member,
    set_member_monthly_exclusion,
    settle_group_expenses,
    update_group,
    update_group_expense,
    update_member_monthly_status,
    update_member_role,
)

router = APIRouter()


async def _member_response(db: AsyncSession, member: GroupMember) -> GroupMemberResponse:
    profile = await db.scalar(select(Profile).where(Profile.user_id == member.user_id))
    return GroupMemberResponse(
        id=member.id,
        group_id=member.group_id,
        user_id=member.user_id,
        role=member.role,
        joined_at=member.joined_at,
        profile=MemberProfileResponse(
            display_name=profile.display_name if profile else None,
            email=profile.email if profile else None,
            avatar_url=profile.avatar_url if profile else None,
            is_shadow=profile.is_shadow if profile else False,
        )
        if profile
        else None,
    )


@router.post("/", response_model=ResponseEnvelope[GroupResponse], status_code=201)
async def post_group(
    payload: GroupCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[GroupResponse]:
    """Create a new group. Creator is automatically added as admin."""
    group = await create_group(db, current_user.id, payload)
    return ResponseEnvelope(data=GroupResponse.model_validate(group))


@router.get("/", response_model=ResponseEnvelope[list[GroupResponse]])
async def list_groups(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[GroupResponse]]:
    """
    Authenticated endpoint returning all groups where the current user is a member.
    """
    groups = await get_user_groups(db, current_user.id)
    return ResponseEnvelope(
        data=[GroupResponse.model_validate(g) for g in groups],
        error=None,
        meta={"count": len(groups)},
    )


@router.get("/{id}", response_model=ResponseEnvelope[GroupResponse])
async def get_group(
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[GroupResponse]:
    """
    Authenticated endpoint returning group details.
    Rejects with 403 Forbidden if the user is not a member of the group.
    """
    group = await get_group_detail(db, id, current_user.id)
    return ResponseEnvelope(
        data=GroupResponse.model_validate(group),
        error=None,
        meta=None,
    )


@router.patch("/{id}", response_model=ResponseEnvelope[GroupResponse])
async def patch_group(
    id: UUID,
    payload: GroupUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[GroupResponse]:
    """Update group settings. Only group admins can update."""
    group = await update_group(db, id, current_user.id, payload)
    return ResponseEnvelope(data=GroupResponse.model_validate(group))


@router.delete("/{id}", response_model=ResponseEnvelope[None])
async def remove_group(
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[None]:
    """Delete a group. Only group admins can delete."""
    await delete_group(db, id, current_user.id)
    return ResponseEnvelope()


@router.post("/{id}/members", response_model=ResponseEnvelope[GroupMemberResponse], status_code=201)
async def post_group_member(
    id: UUID,
    payload: GroupMemberAdd,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[GroupMemberResponse]:
    """Add a member to a group by email or user_id. Only group admins can add members."""
    member = await add_group_member(db, id, current_user.id, payload)
    resp = await _member_response(db, member)
    return ResponseEnvelope(data=resp)


@router.post("/{id}/join", response_model=ResponseEnvelope[GroupMemberResponse], status_code=201)
async def post_join_group(
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[GroupMemberResponse]:
    """Join a group using invite ID. Authenticated user is added as a member."""
    member = await join_group(db, id, current_user.id)
    resp = await _member_response(db, member)
    return ResponseEnvelope(data=resp)


@router.delete("/{id}/members/{user_id}", response_model=ResponseEnvelope[None])
async def delete_group_member_endpoint(
    id: UUID,
    user_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[None]:
    """Remove a member from the group. Admins can remove members; members can remove themselves."""
    await remove_group_member(db, id, current_user.id, user_id)
    return ResponseEnvelope()


@router.patch("/{id}/members/{user_id}", response_model=ResponseEnvelope[GroupMemberResponse])
async def patch_member_role(
    id: UUID,
    user_id: UUID,
    payload: GroupMemberRoleUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[GroupMemberResponse]:
    """Update a group member's role. Only admins can update roles; members cannot modify own role."""
    member = await update_member_role(db, id, current_user.id, user_id, payload)
    resp = await _member_response(db, member)
    return ResponseEnvelope(data=resp)


@router.get("/{id}/members", response_model=ResponseEnvelope[list[GroupMemberResponse]])
async def get_members(
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[GroupMemberResponse]]:
    """
    Authenticated endpoint returning members of a group with joined profile metadata.
    Rejects with 403 Forbidden if the user is not a member of the group.
    """
    rows = await get_group_members(db, id, current_user.id)

    data = []
    for member, profile in rows:
        member_resp = GroupMemberResponse(
            id=member.id,
            group_id=member.group_id,
            user_id=member.user_id,
            role=member.role,
            joined_at=member.joined_at,
            profile=MemberProfileResponse(
                display_name=profile.display_name if profile else None,
                email=profile.email if profile else None,
                avatar_url=profile.avatar_url if profile else None,
                is_shadow=profile.is_shadow if profile else False,
            )
            if profile
            else None,
        )
        data.append(member_resp)

    return ResponseEnvelope(
        data=data,
        error=None,
        meta={"count": len(data)},
    )


@router.get("/{id}/expenses", response_model=ResponseEnvelope[list[GroupExpenseResponse]])
async def list_group_expenses(
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[GroupExpenseResponse]]:
    """
    Authenticated endpoint returning group expenses with splits and profiles.
    Member-only authorization enforced.
    """
    expenses = await get_group_expenses(db, id, current_user.id)
    return ResponseEnvelope(
        data=[GroupExpenseResponse.model_validate(e) for e in expenses],
        error=None,
        meta={"count": len(expenses)},
    )


@router.get("/{id}/balances", response_model=ResponseEnvelope[list[GroupBalanceResponse]])
async def list_group_balances(
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[GroupBalanceResponse]]:
    """
    Authenticated endpoint returning simplified peer-to-peer or sponsor group balances.
    Member-only authorization enforced.
    """
    balances = await get_group_balances(db, id, current_user.id)
    return ResponseEnvelope(
        data=balances,
        error=None,
        meta={"count": len(balances)},
    )


@router.get("/{id}/settlements/multi", response_model=ResponseEnvelope[list[MonthlySettlementResponse]])
async def get_multi_settlements(
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(description="Maximum settlements to return", ge=1, le=100)] = 12,
) -> ResponseEnvelope[list[MonthlySettlementResponse]]:
    """
    Authenticated endpoint returning multi-month settlements.
    Member-only authorization enforced.
    """
    settlements = await get_multi_month_settlements(db, id, current_user.id, limit=limit)
    return ResponseEnvelope(
        data=[MonthlySettlementResponse.model_validate(s) for s in settlements],
        error=None,
        meta={"count": len(settlements)},
    )


@router.get("/{id}/settlements/{settlement_id}/member-status", response_model=ResponseEnvelope[list[MemberMonthlyStatusResponse]])
async def get_settlement_member_status(
    id: UUID,
    settlement_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[MemberMonthlyStatusResponse]]:
    """
    Authenticated endpoint returning member status for a specific settlement.
    Member-only authorization enforced.
    """
    statuses = await get_member_monthly_statuses(db, id, current_user.id, settlement_id)
    return ResponseEnvelope(
        data=[MemberMonthlyStatusResponse.model_validate(s) for s in statuses],
        error=None,
        meta={"count": len(statuses)},
    )


@router.post("/{id}/settlements/{settlement_id}/member-status", response_model=ResponseEnvelope[MemberMonthlyStatusResponse], status_code=201)
async def post_settlement_member_status(
    id: UUID,
    settlement_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: MemberMonthlyStatusCreate | None = None,
) -> ResponseEnvelope[MemberMonthlyStatusResponse]:
    """Mark a member's expenses as complete for the settlement cycle. Self or group admin."""
    body = payload or MemberMonthlyStatusCreate()
    status_row = await update_member_monthly_status(db, id, settlement_id, current_user.id, body)
    return ResponseEnvelope(data=MemberMonthlyStatusResponse.model_validate(status_row))


@router.get("/{id}/settlements/{month:int}/{year:int}/expenses", response_model=ResponseEnvelope[list[ExpenseResponse]])
async def get_settlement_expenses(
    id: UUID,
    month: int,
    year: int,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[ExpenseResponse]]:
    """
    Authenticated endpoint returning group expenses for a specific month and year.
    Member-only authorization enforced.
    """
    expenses = await get_monthly_group_expenses(db, id, current_user.id, month, year)
    return ResponseEnvelope(
        data=[ExpenseResponse.model_validate(e) for e in expenses],
        error=None,
        meta={"count": len(expenses)},
    )


@router.get("/{id}/settlements/{month:int}/{year:int}", response_model=ResponseEnvelope[MonthlySettlementResponse | None])
async def get_settlement(
    id: UUID,
    month: int,
    year: int,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[MonthlySettlementResponse | None]:
    """
    Authenticated endpoint returning a monthly settlement record if it exists.
    Member-only authorization enforced.
    """
    settlement = await get_monthly_settlement(db, id, current_user.id, month, year)
    return ResponseEnvelope(
        data=MonthlySettlementResponse.model_validate(settlement) if settlement else None,
        error=None,
        meta=None,
    )


@router.post("/{id}/settlements/{month:int}/{year:int}", response_model=ResponseEnvelope[MonthlySettlementResponse], status_code=201)
async def post_monthly_settlement(
    id: UUID,
    month: int,
    year: int,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: MonthlySettlementCreate | None = None,
) -> ResponseEnvelope[MonthlySettlementResponse]:
    """Create or get an open monthly settlement cycle."""
    status_val = payload.status if payload else "open"
    settlement = await create_or_get_monthly_settlement(db, id, current_user.id, month, year, status_val=status_val)
    return ResponseEnvelope(data=MonthlySettlementResponse.model_validate(settlement))


@router.post("/{id}/settlements/{month:int}/{year:int}/finalize", response_model=ResponseEnvelope[MonthlySettlementResponse])
async def post_finalize_monthly_settlement(
    id: UUID,
    month: int,
    year: int,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[MonthlySettlementResponse]:
    """Finalize/lock a monthly settlement cycle. Idempotent against repeat submissions."""
    settlement = await finalize_monthly_settlement(db, id, current_user.id, month, year)
    return ResponseEnvelope(data=MonthlySettlementResponse.model_validate(settlement))


@router.get("/{id}/exclusions/{month:int}/{year:int}", response_model=ResponseEnvelope[list[MemberMonthlyExclusionResponse]])
async def get_exclusions(
    id: UUID,
    month: int,
    year: int,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[MemberMonthlyExclusionResponse]]:
    """
    Authenticated endpoint returning member monthly exclusions for a specific month and year.
    Member-only authorization enforced.
    """
    exclusions = await get_member_monthly_exclusions(db, id, current_user.id, month, year)
    return ResponseEnvelope(
        data=[MemberMonthlyExclusionResponse.model_validate(e) for e in exclusions],
        error=None,
        meta={"count": len(exclusions)},
    )


@router.post("/{id}/exclusions/{month:int}/{year:int}", response_model=ResponseEnvelope[MemberMonthlyExclusionResponse | None])
@router.patch("/{id}/exclusions/{month:int}/{year:int}", response_model=ResponseEnvelope[MemberMonthlyExclusionResponse | None])
async def post_member_exclusion(
    id: UUID,
    month: int,
    year: int,
    payload: MemberMonthlyExclusionWrite,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[MemberMonthlyExclusionResponse | None]:
    """Create or update a member's monthly exclusion. Self or group admin."""
    res = await set_member_monthly_exclusion(db, id, month, year, current_user.id, payload)
    return ResponseEnvelope(data=MemberMonthlyExclusionResponse.model_validate(res) if res else None)


@router.delete("/{id}/exclusions/{month:int}/{year:int}/{user_id}", response_model=ResponseEnvelope[None])
async def delete_member_exclusion(
    id: UUID,
    month: int,
    year: int,
    user_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[None]:
    """Remove a member's monthly exclusion."""
    await set_member_monthly_exclusion(
        db, id, month, year, current_user.id, MemberMonthlyExclusionWrite(user_id=user_id, exclusion_type="none")
    )
    return ResponseEnvelope()


@router.post("/{id}/expenses", response_model=ResponseEnvelope[GroupExpenseResponse], status_code=201)
async def post_group_expense(
    id: UUID,
    payload: GroupExpenseCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[GroupExpenseResponse]:
    """Create a new group expense with splits in a single transaction."""
    expense_data = await create_group_expense(db, id, current_user.id, payload)
    return ResponseEnvelope(data=GroupExpenseResponse.model_validate(expense_data))


@router.post("/{id}/expenses/bulk", response_model=ResponseEnvelope[list[GroupExpenseResponse]], status_code=201)
async def post_bulk_group_expenses(
    id: UUID,
    payload: GroupExpenseBulkCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[GroupExpenseResponse]]:
    """Bulk create group expenses with splits in a single transaction."""
    expenses_data = await bulk_create_group_expenses(db, id, current_user.id, payload)
    return ResponseEnvelope(
        data=[GroupExpenseResponse.model_validate(e) for e in expenses_data],
        meta={"count": len(expenses_data)},
    )


@router.patch("/{id}/expenses/{expense_id}", response_model=ResponseEnvelope[GroupExpenseResponse])
async def patch_group_expense(
    id: UUID,
    expense_id: UUID,
    payload: GroupExpenseUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[GroupExpenseResponse]:
    """Update a group expense. Creator, payer, or group admin only."""
    expense_data = await update_group_expense(db, id, expense_id, current_user.id, payload)
    return ResponseEnvelope(data=GroupExpenseResponse.model_validate(expense_data))


@router.delete("/{id}/expenses/{expense_id}", response_model=ResponseEnvelope[None])
async def delete_group_expense_endpoint(
    id: UUID,
    expense_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[None]:
    """Delete a group expense and its splits. Creator, payer, or group admin only."""
    await delete_group_expense(db, id, expense_id, current_user.id)
    return ResponseEnvelope()


@router.post("/{id}/settle", response_model=ResponseEnvelope[dict])
async def post_settle_up(
    id: UUID,
    payload: GroupSettleUpRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[dict]:
    """Settle balances between two group members."""
    count = await settle_group_expenses(db, id, current_user.id, payload)
    return ResponseEnvelope(data={"settled_count": count})


@router.post("/{id}/reimburse-bulk", response_model=ResponseEnvelope[dict])
async def post_reimburse_bulk(
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[dict]:
    """Mark all approved group expenses as reimbursed and splits as settled."""
    count = await bulk_reimburse_group_expenses(db, id, current_user.id)
    return ResponseEnvelope(data={"reimbursed_count": count})


@router.get("/{id}/monthly-ledger", response_model=ResponseEnvelope[MonthlyLedgerResponse])
async def get_group_monthly_ledger_endpoint(
    id: UUID,
    month: Annotated[int, Query(ge=1, le=12)],
    year: Annotated[int, Query(ge=2020)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[MonthlyLedgerResponse]:
    """
    Computes the complete Monthly Household Ledger for the group:
    - Member Obligation = Rent Share (ceil-rounded whole rupees) + Shared Expenses + Adjustments
    - Paid = Out-of-pocket fronted expenses + verified contributions
    - Balance = Obligation - Paid
    - Returns full ledger, summary, my_summary, and coordinator_summary.
    """
    ledger = await get_monthly_ledger(db, id, current_user.id, month, year)
    return ResponseEnvelope(data=ledger)


@router.post("/{id}/monthly-ledger/contributions", response_model=ResponseEnvelope[MemberMonthlyStatusResponse])
async def post_monthly_ledger_contribution(
    id: UUID,
    month: Annotated[int, Query(ge=1, le=12)],
    year: Annotated[int, Query(ge=2020)],
    payload: MonthlyLedgerContributionRecord,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[MemberMonthlyStatusResponse]:
    """Record a member's payment contribution for the monthly ledger cycle."""
    status_obj = await record_monthly_ledger_contribution(db, id, month, year, current_user.id, payload)
    return ResponseEnvelope(data=MemberMonthlyStatusResponse.model_validate(status_obj))


