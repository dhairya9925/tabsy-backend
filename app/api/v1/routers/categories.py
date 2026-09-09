from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.category import UserCategoryResponse
from app.schemas.envelope import ResponseEnvelope
from app.services.category_service import get_user_categories

router = APIRouter()


@router.get("/", response_model=ResponseEnvelope[list[UserCategoryResponse]])
async def list_user_categories(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[UserCategoryResponse]]:
    """
    Authenticated endpoint returning categories belonging strictly to the current user.
    Authorization rule: user_id == current_user.id.
    """
    categories = await get_user_categories(db, current_user.id)
    return ResponseEnvelope(
        data=[UserCategoryResponse.model_validate(c) for c in categories],
        error=None,
        meta={"count": len(categories)},
    )
