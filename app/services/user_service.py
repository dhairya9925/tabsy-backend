from typing import Sequence
from uuid import UUID
from sqlalchemy import delete, func, or_, select
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

    # Determine accepted friends to avoid showing pending/sent requests for existing friends
    accepted_ids: set[UUID] = set()
    accepted_emails: set[str] = set()
    if status_lower in ("pending", "sent"):
        accepted_stmt = select(Friend).where(
            (Friend.user_id == user_id) | (Friend.friend_id == user_id),
            Friend.status == "accepted",
        )
        accepted_records = (await db.execute(accepted_stmt)).scalars().all()
        accepted_ids = {
            f.friend_id if f.user_id == user_id else f.user_id
            for f in accepted_records
        }
        if accepted_ids:
            acc_prof_stmt = select(Profile.email).where(
                Profile.user_id.in_(accepted_ids),
                Profile.email.is_not(None),
            )
            acc_emails_res = await db.execute(acc_prof_stmt)
            accepted_emails = {
                e.strip().lower() for e in acc_emails_res.scalars().all() if e
            }

    valid_results: list[tuple[Friend, Profile | None]] = []
    stale_friend_ids: list[UUID] = []

    for f in friends:
        cid = f.friend_id if f.user_id == user_id else f.user_id
        profile = profiles_map.get(cid)

        if status_lower in ("pending", "sent"):
            # If counterpart profile does not exist, request is orphaned
            if profile is None:
                stale_friend_ids.append(f.id)
                continue
            # If user is already accepted friends with this counterpart ID
            if cid in accepted_ids:
                stale_friend_ids.append(f.id)
                continue
            # If user is already accepted friends with this counterpart email
            if profile.email and profile.email.strip().lower() in accepted_emails:
                stale_friend_ids.append(f.id)
                continue
        elif status_lower == "accepted":
            if profile is None:
                continue

        valid_results.append((f, profile))

    if stale_friend_ids:
        await db.execute(delete(Friend).where(Friend.id.in_(stale_friend_ids)))
        try:
            await db.commit()
        except Exception:
            await db.flush()

    return valid_results


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
