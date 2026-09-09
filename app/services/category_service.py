from typing import Sequence
from uuid import UUID
import re
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import UserCategory
from app.models.expense import Expense
from app.models.profile import Profile
from app.schemas.category import CategoryCreate, CategoryUpdate

# Mirrors src/lib/categories.ts. Hidden system categories cannot be user-created.
DEFAULT_CATEGORY_IDS = frozenset({"food", "transport", "shopping", "bills", "system", "other"})


async def create_category(db: AsyncSession, user_id: UUID, payload: CategoryCreate) -> UserCategory:
    slug = re.sub(r"[^a-z0-9]+", "_", payload.name.lower()).strip("_")
    if len(slug) < 2 or slug in DEFAULT_CATEGORY_IDS:
        raise HTTPException(422, "Use a category name with at least two letters/digits that is not a default category")
    try:
        async with db.begin():
            # Serialize a user's category creation to keep the existing 20-category limit.
            await db.execute(select(Profile.id).where(Profile.user_id == user_id).with_for_update())
            count = await db.scalar(select(func.count()).select_from(UserCategory).where(UserCategory.user_id == user_id))
            if count >= 20:
                raise HTTPException(409, "Maximum 20 custom categories allowed")
            category = UserCategory(user_id=user_id, name=payload.name, slug=slug, color_index=count % 10)
            db.add(category)
            await db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "A category with this name already exists") from exc
    return category


async def _owned_category(db: AsyncSession, user_id: UUID, category_id: UUID) -> UserCategory:
    category = (await db.execute(select(UserCategory).where(
        UserCategory.id == category_id, UserCategory.user_id == user_id,
    ).with_for_update())).scalar_one_or_none()
    if category is None:
        raise HTTPException(404, "Category not found")
    return category


async def update_category(db: AsyncSession, user_id: UUID, category_id: UUID, payload: CategoryUpdate) -> UserCategory:
    async with db.begin():
        category = await _owned_category(db, user_id, category_id)
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(category, key, value)
        await db.flush()
    return category


async def delete_category(db: AsyncSession, user_id: UUID, category_id: UUID) -> int:
    async with db.begin():
        category = await _owned_category(db, user_id, category_id)
        references = await db.scalar(select(func.count()).select_from(Expense).where(
            Expense.user_id == user_id, Expense.category == category.slug,
        ))
        # Preserve Phase 3.0 behavior: hard delete, retain expense slugs and UI fallback.
        # Slugs are user-scoped, so another owner's matching slug is not this category.
        await db.delete(category)
        await db.flush()
    return references


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
