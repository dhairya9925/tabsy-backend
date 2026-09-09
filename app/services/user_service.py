from typing import Sequence
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.models.friend import Friend
from app.models.profile import Profile
from app.schemas.user import ProfileUpdate


async def update_profile(db: AsyncSession, user_id: UUID, payload: ProfileUpdate) -> Profile:
    async with db.begin():
        profile = (await db.execute(
            select(Profile).where(Profile.user_id == user_id).with_for_update()
        )).scalar_one_or_none()
        if profile is None:
            raise HTTPException(404, "Profile not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(profile, key, value)
        await db.flush()
    return profile


async def get_profile_by_user_id(db: AsyncSession, user_id: UUID) -> Profile | None:
    """
    Retrieves a user's profile by their auth user_id directly via SQLAlchemy.
    """
    stmt = select(Profile).where(Profile.user_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def lookup_user_by_email(db: AsyncSession, email: str) -> Profile | None:
    """
    Looks up an active, non-shadow user profile by exact email (case-insensitive).
    Only returns profiles where is_shadow == False.
    """
    clean_email = email.strip().lower()
    stmt = select(Profile).where(
        func.lower(Profile.email) == clean_email,
        Profile.is_shadow.is_(False),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_pending_shadow_profiles(db: AsyncSession, email: str) -> Sequence[Profile]:
    """
    Queries shadow profiles matching the user's email where is_shadow == True.
    """
    if not email:
        return []
    clean_email = email.strip().lower()
    stmt = select(Profile).where(
        func.lower(Profile.email) == clean_email,
        Profile.is_shadow.is_(True),
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_user_friends_with_profiles(
    db: AsyncSession, user_id: UUID, status: str = "accepted"
) -> list[tuple[Friend, Profile | None]]:
    """
    Retrieves friends of user_id with counterpart profiles joined.
    Status filters:
    - 'accepted': (user_id == user_id OR friend_id == user_id) AND status == 'accepted'
    - 'pending': friend_id == user_id AND status == 'pending' (incoming)
    - 'sent': user_id == user_id AND status == 'pending' (outgoing)
    - 'all': (user_id == user_id OR friend_id == user_id)
    """
    status_lower = status.lower() if status else "accepted"

    if status_lower == "pending":
        stmt = select(Friend).where(
            Friend.friend_id == user_id,
            Friend.status == "pending",
        )
    elif status_lower == "sent":
        stmt = select(Friend).where(
            Friend.user_id == user_id,
            Friend.status == "pending",
        )
    elif status_lower == "accepted":
        stmt = select(Friend).where(
            (Friend.user_id == user_id) | (Friend.friend_id == user_id),
            Friend.status == "accepted",
        )
    elif status_lower == "all":
        stmt = select(Friend).where(
            (Friend.user_id == user_id) | (Friend.friend_id == user_id)
        )
    else:
        stmt = select(Friend).where(
            (Friend.user_id == user_id) | (Friend.friend_id == user_id),
            Friend.status == status_lower,
        )

    stmt = stmt.order_by(Friend.created_at.desc())
    friends_res = await db.execute(stmt)
    friends = friends_res.scalars().all()

    if not friends:
        return []

    counterpart_ids = [
        f.friend_id if f.user_id == user_id else f.user_id
        for f in friends
    ]

    profiles_stmt = select(Profile).where(Profile.user_id.in_(counterpart_ids))
    profiles_res = await db.execute(profiles_stmt)
    profiles_map = {p.user_id: p for p in profiles_res.scalars().all()}

    return [
        (f, profiles_map.get(f.friend_id if f.user_id == user_id else f.user_id))
        for f in friends
    ]


async def check_friendship_or_shadow_access(
    db: AsyncSession, current_user_id: UUID, friend_id: UUID
) -> bool:
    """
    Checks if current_user_id has an active (accepted) friendship with friend_id,
    or a shared shadow profile relationship.
    """
    # Active friendship check
    friend_stmt = select(Friend).where(
        (
            (Friend.user_id == current_user_id) & (Friend.friend_id == friend_id)
        ) | (
            (Friend.user_id == friend_id) & (Friend.friend_id == current_user_id)
        ),
        Friend.status == "accepted",
    )
    friend_res = await db.execute(friend_stmt)
    if friend_res.scalar_one_or_none() is not None:
        return True

    # Shared shadow profile check
    profile_stmt = select(Profile).where(
        (
            (Profile.user_id == friend_id) & (Profile.shadow_created_by == current_user_id)
        ) | (
            (Profile.user_id == current_user_id) & (Profile.shadow_created_by == friend_id)
        )
    )
    profile_res = await db.execute(profile_stmt)
    if profile_res.scalar_one_or_none() is not None:
        return True

    return False


async def get_friend_profile(
    db: AsyncSession, current_user_id: UUID, friend_id: UUID
) -> Profile | None:
    """
    Retrieves friend's profile after verifying access.
    """
    return await get_profile_by_user_id(db, friend_id)
