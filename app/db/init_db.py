import logging
from sqlalchemy import text
from app.db.session import engine
from app.models.base import Base
# Import all models so they are registered on Base.metadata
import app.models  # noqa: F401
from app.services.group_service import generate_invite_code

logger = logging.getLogger("app.db.init")


async def init_db() -> None:
    """
    Ensures all database tables defined in SQLAlchemy models exist in the target database.
    Idempotent: creates missing tables, adds missing columns, and backfills data.
    """
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            # Ensure invite_code column exists on groups
            await conn.execute(text("ALTER TABLE groups ADD COLUMN IF NOT EXISTS invite_code TEXT;"))
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_groups_invite_code ON groups (invite_code);"))

            # Backfill any groups missing invite_code
            result = await conn.execute(text("SELECT id FROM groups WHERE invite_code IS NULL;"))
            missing_groups = result.fetchall()
            for row in missing_groups:
                code = generate_invite_code(10)
                await conn.execute(
                    text("UPDATE groups SET invite_code = :code WHERE id = :id;"),
                    {"code": code, "id": row[0]}
                )

        logger.info("Database schema initialized successfully.")
    except Exception as e:
        logger.warning(f"Database schema auto-creation skipped or failed: {e}")

