from typing import Sequence
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import UserCategory


async def get_user_categories(db: AsyncSession, user_id: UUID) -> Sequence[UserCategory]:
    """
    Retrieves categories belonging strictly to the specified user_id,
    ordered by created_at ascending.
    """
    stmt = (
        select(UserCategory)
        .where(UserCategory.user_id == user_id)
        .order_by(UserCategory.created_at.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()
