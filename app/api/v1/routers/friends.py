from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.envelope import ResponseEnvelope
from app.schemas.friend import PendingShadowProfileResponse
from app.services.user_service import get_pending_shadow_profiles

router = APIRouter()


@router.get("/shadow/pending", response_model=ResponseEnvelope[list[PendingShadowProfileResponse]])
async def get_pending_shadows(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[PendingShadowProfileResponse]]:
    """
    Authenticated endpoint querying pending shadow profiles where
    email == current_user.email AND is_shadow == True.
    """
    if not current_user.email:
        return ResponseEnvelope(data=[], error=None, meta={"count": 0})

    profiles = await get_pending_shadow_profiles(db, current_user.email)
    return ResponseEnvelope(
        data=[PendingShadowProfileResponse.model_validate(p) for p in profiles],
        error=None,
        meta={"count": len(profiles)},
    )
