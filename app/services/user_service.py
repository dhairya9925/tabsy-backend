from typing import Sequence
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import Profile


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
