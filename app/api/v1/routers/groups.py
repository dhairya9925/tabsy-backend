from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.envelope import ResponseEnvelope
from app.schemas.expense import ExpenseResponse
from app.schemas.group import (
    GroupBalanceResponse,
    GroupExpenseResponse,
    GroupMemberResponse,
    GroupResponse,
    MemberProfileResponse,
)
from app.schemas.settlement import (
    MemberMonthlyExclusionResponse,
    MemberMonthlyStatusResponse,
    MonthlySettlementResponse,
)
from app.services.group_service import (
    get_group_balances,
    get_group_detail,
    get_group_expenses,
    get_group_members,
    get_member_monthly_exclusions,
    get_member_monthly_statuses,
    get_monthly_group_expenses,
    get_monthly_settlement,
    get_multi_month_settlements,
    get_user_groups,
)

router = APIRouter()


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


@router.get("/{id}/settlements/{month}/{year}/expenses", response_model=ResponseEnvelope[list[ExpenseResponse]])
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


@router.get("/{id}/settlements/{month}/{year}", response_model=ResponseEnvelope[MonthlySettlementResponse | None])
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


@router.get("/{id}/exclusions/{month}/{year}", response_model=ResponseEnvelope[list[MemberMonthlyExclusionResponse]])
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

