import logging
from app.db.session import engine
from app.models.base import Base
# Import all models so they are registered on Base.metadata
import app.models  # noqa: F401

logger = logging.getLogger("app.db.init")


async def init_db() -> None:
    """
    Ensures all database tables defined in SQLAlchemy models exist in the target database.
    Idempotent: only creates missing tables (e.g. user_credentials).
    """
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database schema initialized successfully.")
    except Exception as e:
        logger.warning(f"Database schema auto-creation skipped or failed: {e}")
