from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.envelope import ResponseEnvelope
from app.schemas.friend import (
    FriendBalanceResponse,
    FriendExpenseFeedResponse,
    FriendProfileResponse,
    FriendWithProfileResponse,
    PendingShadowProfileResponse,
)
from app.schemas.user import ProfileResponse
from app.services.expense_service import (
    get_friend_balances,
    get_friend_expenses_feed,
)
from app.services.user_service import (
    check_friendship_or_shadow_access,
    get_friend_profile,
    get_pending_shadow_profiles,
    get_user_friends_with_profiles,
)

router = APIRouter()


@router.get("/", response_model=ResponseEnvelope[list[FriendWithProfileResponse]])
async def list_friends(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str, Query(description="Friendship status filter")] = "accepted",
) -> ResponseEnvelope[list[FriendWithProfileResponse]]:
    """
    Authenticated endpoint returning friends enriched with counterpart profile metadata.
    """
    friends_with_profiles = await get_user_friends_with_profiles(
        db, current_user.id, status=status
    )

    data = []
    for friend, profile in friends_with_profiles:
        profile_resp = (
            FriendProfileResponse(
                user_id=profile.user_id,
                display_name=profile.display_name,
                email=profile.email,
                avatar_url=profile.avatar_url,
                is_shadow=profile.is_shadow,
            )
            if profile
            else None
        )
        data.append(
            FriendWithProfileResponse(
                id=friend.id,
                user_id=friend.user_id,
                friend_id=friend.friend_id,
                status=friend.status,
                created_at=friend.created_at,
                updated_at=friend.updated_at,
                profile=profile_resp,
            )
        )

    return ResponseEnvelope(
        data=data,
        error=None,
        meta={"count": len(data)},
    )


@router.get("/balances", response_model=ResponseEnvelope[list[FriendBalanceResponse]])
async def list_friend_balances(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[FriendBalanceResponse]]:
    """
    Authenticated endpoint calculating pairwise running net balances between current user and friends.
    """
    balances = await get_friend_balances(db, current_user.id)
    return ResponseEnvelope(
        data=[FriendBalanceResponse(**b) for b in balances],
        error=None,
        meta={"count": len(balances)},
    )


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


@router.get("/{friend_id}/expenses", response_model=ResponseEnvelope[list[FriendExpenseFeedResponse]])
async def get_friend_expenses(
    friend_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[list[FriendExpenseFeedResponse]]:
    """
    Authenticated endpoint returning 1-on-1 shared non-group expenses between current user and friend_id.
    Strictly verifies that current user has an active friendship or shared shadow profile with friend_id.
    """
    has_access = await check_friendship_or_shadow_access(db, current_user.id, friend_id)
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this friend's expenses",
        )

    expenses = await get_friend_expenses_feed(db, current_user.id, friend_id)
    return ResponseEnvelope(
        data=[FriendExpenseFeedResponse.model_validate(e) for e in expenses],
        error=None,
        meta={"count": len(expenses)},
    )


@router.get("/{friend_id}/profile", response_model=ResponseEnvelope[ProfileResponse])
async def get_friend_profile_endpoint(
    friend_id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[ProfileResponse]:
    """
    Authenticated endpoint returning counterpart profile for friend_id.
    Strictly verifies that current user has an active friendship or shared shadow profile with friend_id.
    """
    has_access = await check_friendship_or_shadow_access(db, current_user.id, friend_id)
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this friend's profile",
        )

    profile = await get_friend_profile(db, current_user.id, friend_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Friend profile not found",
        )

    return ResponseEnvelope(
        data=ProfileResponse.model_validate(profile),
        error=None,
        meta={},
    )

