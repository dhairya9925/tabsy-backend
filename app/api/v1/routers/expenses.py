from datetime import date
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.envelope import ResponseEnvelope
from app.schemas.expense import ExpenseResponse
from app.services.expense_service import get_personal_expenses

router = APIRouter()


@router.get("/personal", response_model=ResponseEnvelope[list[ExpenseResponse]])
async def list_personal_expenses(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    category: Annotated[str | None, Query(description="Filter by category")] = None,
    start_date: Annotated[date | None, Query(description="Start date (YYYY-MM-DD)")] = None,
    startDate: Annotated[date | None, Query(alias="startDate", description="Start date alias")] = None,
    end_date: Annotated[date | None, Query(description="End date (YYYY-MM-DD)")] = None,
    endDate: Annotated[date | None, Query(alias="endDate", description="End date alias")] = None,
    search: Annotated[str | None, Query(description="Substring search on note")] = None,
) -> ResponseEnvelope[list[ExpenseResponse]]:
    """
    Authenticated endpoint returning personal (non-group) expenses for the current user.
    Authorization: expenses.user_id == current_user.id AND expenses.group_id IS NULL.
    Excludes expenses that have external debtor splits.
    """
    effective_start_date = start_date or startDate
    effective_end_date = end_date or endDate

    expenses = await get_personal_expenses(
        db=db,
        user_id=current_user.id,
        category=category,
        start_date=effective_start_date,
        end_date=effective_end_date,
        search=search,
    )

    data = [ExpenseResponse.model_validate(e) for e in expenses]

    return ResponseEnvelope(
        data=data,
        error=None,
        meta={"count": len(data)},
    )
