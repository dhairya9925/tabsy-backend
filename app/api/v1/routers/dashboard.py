from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.dashboard import DashboardDataResponse
from app.schemas.envelope import ResponseEnvelope
from app.services.dashboard_service import get_dashboard_summary

router = APIRouter()


@router.get("/summary", response_model=ResponseEnvelope[DashboardDataResponse])
async def get_dashboard_summary_endpoint(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[DashboardDataResponse]:
    """
    Consolidated dashboard summary endpoint.
    Computes server-side:
    - Personal monthly total and previous month total
    - User's group net balances and group share totals
    - Friend net balances
    - Unified total spending and month-over-month %
    - Net debt owed, net debt owes, unsettled counts
    - Unified recent activity feed
    """
    summary = await get_dashboard_summary(db, current_user.id)
    return ResponseEnvelope(
        data=summary,
        error=None,
        meta=None,
    )
