from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.envelope import ResponseEnvelope
from app.schemas.expense import ExpenseResponse
from app.schemas.friend import (
    CreateShadowProfileRequest,
    CreateShadowProfileResponse,
    FriendBalanceResponse,
    FriendExpenseCreate,
    FriendExpenseFeedResponse,
    FriendExpenseUpdate,
    FriendProfileResponse,
    FriendRequestCreate,
    FriendWithProfileResponse,
    MergeShadowProfileRequest,
    PendingShadowProfileResponse,
)
from app.schemas.user import ProfileResponse
from app.services.expense_service import (
    create_friend_expense,
    delete_friend_expense,
    get_friend_balances,
    get_friend_expenses_feed,
    update_friend_expense,
)
from app.services.friend_service import (
    accept_friend_request,
    create_shadow_profile,
    delete_friendship,
    merge_shadow_profile,
    reject_friend_request,
    send_friend_request,
)
from app.services.user_service import (
    check_friendship_or_shadow_access,
    get_friend_profile,
    get_pending_shadow_profiles,
    get_user_friends_with_profiles,
)

router = APIRouter()


def _format_friend_response(friend, profile) -> FriendWithProfileResponse:
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
    return FriendWithProfileResponse(
        id=friend.id,
        user_id=friend.user_id,
        friend_id=friend.friend_id,
        status=friend.status,
        created_at=friend.created_at,
        updated_at=friend.updated_at,
        profile=profile_resp,
    )


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

    data = [_format_friend_response(f, p) for f, p in friends_with_profiles]

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


@router.post("/shadow", response_model=ResponseEnvelope[CreateShadowProfileResponse], status_code=status.HTTP_201_CREATED)
async def create_shadow_endpoint(
    payload: CreateShadowProfileRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[CreateShadowProfileResponse]:
    """
    Creates a shadow profile contact and an auto-accepted friendship record.
    """
    profile, shadow_id = await create_shadow_profile(
        db, current_user.id, payload.display_name, payload.email
    )
    return ResponseEnvelope(
        data=CreateShadowProfileResponse(
            profile=FriendProfileResponse.model_validate(profile),
            shadow_user_id=shadow_id,
        ),
        error=None,
        meta={},
    )


@router.post("/shadow/merge", response_model=ResponseEnvelope[dict])
async def merge_shadow_endpoint(
    payload: MergeShadowProfileRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[dict]:
    """
    Safely merges a shadow profile into the authenticated real user in an atomic transaction.
    """
    result = await merge_shadow_profile(db, current_user.id, payload.shadow_user_id)
    return ResponseEnvelope(data=result, error=None, meta={})



@router.post("/request", response_model=ResponseEnvelope[FriendWithProfileResponse], status_code=status.HTTP_201_CREATED)
async def send_request_endpoint(
    payload: FriendRequestCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[FriendWithProfileResponse]:
    """
    Sends a friend request to a target user by email or user_id.
    Rejects duplicate pending requests and requests to existing friends (409).
    """
    friend, profile = await send_friend_request(db, current_user.id, payload)
    data = _format_friend_response(friend, profile)
    return ResponseEnvelope(data=data, error=None, meta={})


@router.post("/{id}/accept", response_model=ResponseEnvelope[FriendWithProfileResponse])
async def accept_request_endpoint(
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[FriendWithProfileResponse]:
    """
    Accepts a pending friend request. Only the recipient may accept.
    Idempotent on repeated calls.
    """
    friend, profile = await accept_friend_request(db, current_user.id, id)
    data = _format_friend_response(friend, profile)
    return ResponseEnvelope(data=data, error=None, meta={})


@router.post("/{id}/reject", response_model=ResponseEnvelope[FriendWithProfileResponse])
async def reject_request_endpoint(
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[FriendWithProfileResponse]:
    """
    Rejects a pending friend request. Only the recipient may reject.
    Idempotent on repeated calls.
    """
    friend, profile = await reject_friend_request(db, current_user.id, id)
    data = _format_friend_response(friend, profile)
    return ResponseEnvelope(data=data, error=None, meta={})


@router.delete("/{id}", response_model=ResponseEnvelope[dict])
async def delete_friendship_endpoint(
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[dict]:
    """
    Cancels a pending sent request, or removes an existing friendship.
    Only either party to the friendship may perform this action.
    """
    await delete_friendship(db, current_user.id, id)
    return ResponseEnvelope(data={"id": str(id)}, error=None, meta={})


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


@router.post("/{friend_id}/expenses", response_model=ResponseEnvelope[ExpenseResponse], status_code=status.HTTP_201_CREATED)
async def create_friend_expense_endpoint(
    friend_id: UUID,
    payload: FriendExpenseCreate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[ExpenseResponse]:
    """
    Creates a 1:1 shared non-group expense between current user and friend_id.
    Requires an active friendship or contact access.
    """
    expense = await create_friend_expense(db, current_user.id, friend_id, payload)
    return ResponseEnvelope(
        data=ExpenseResponse.model_validate(expense),
        error=None,
        meta={},
    )


@router.patch("/{friend_id}/expenses/{id}", response_model=ResponseEnvelope[ExpenseResponse])
async def update_friend_expense_endpoint(
    friend_id: UUID,
    id: UUID,
    payload: FriendExpenseUpdate,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[ExpenseResponse]:
    """
    Updates a 1:1 shared non-group expense between current user and friend_id.
    Scoped to expenses where current user is payer or split owner.
    """
    expense = await update_friend_expense(db, current_user.id, friend_id, id, payload)
    return ResponseEnvelope(
        data=ExpenseResponse.model_validate(expense),
        error=None,
        meta={},
    )


@router.delete("/{friend_id}/expenses/{id}", response_model=ResponseEnvelope[dict])
async def delete_friend_expense_endpoint(
    friend_id: UUID,
    id: UUID,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[dict]:
    """
    Deletes a 1:1 shared non-group expense between current user and friend_id.
    Scoped to expenses where current user is payer or split owner.
    """
    await delete_friend_expense(db, current_user.id, friend_id, id)
    return ResponseEnvelope(
        data={"id": str(id)},
        error=None,
        meta={},
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
