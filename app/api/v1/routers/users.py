from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.envelope import ResponseEnvelope
from app.schemas.user import ProfileLookupResponse, ProfileResponse, ProfileUpdate
from app.services.user_service import get_profile_by_user_id, lookup_user_by_email, update_profile

router = APIRouter()


@router.patch("/me", response_model=ResponseEnvelope[ProfileResponse])
async def patch_current_user_profile(
    payload: ProfileUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[ProfileResponse]:
    """Update own display name/avatar. Identity, email and shadow fields are rejected."""
    profile = await update_profile(db, current_user.id, payload)
    return ResponseEnvelope(data=ProfileResponse.model_validate(profile))


@router.get("/me", response_model=ResponseEnvelope[ProfileResponse])
async def get_current_user_profile(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[ProfileResponse]:
    """
    Authenticated endpoint returning the current user's profile read directly
    from Postgres via SQLAlchemy using the verified Supabase JWT token.
    """
    profile = await get_profile_by_user_id(db, current_user.id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile for user {current_user.id} not found",
        )

    return ResponseEnvelope(
        data=ProfileResponse.model_validate(profile),
        error=None,
        meta={"auth_email": current_user.email},
    )


@router.get("/lookup", response_model=ResponseEnvelope[ProfileLookupResponse | None])
async def lookup_profile_by_email(
    email: Annotated[str, Query(..., description="Email address to look up")],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[ProfileLookupResponse | None]:
    """
    Authenticated endpoint looking up an active user by email.
    Only returns active, non-shadow profiles (is_shadow == False).
    """
    profile = await lookup_user_by_email(db, email)
    if not profile:
        return ResponseEnvelope(
            data=None,
            error=None,
            meta={"queried_email": email},
        )

    return ResponseEnvelope(
        data=ProfileLookupResponse.model_validate(profile),
        error=None,
        meta={"queried_email": email},
    )
