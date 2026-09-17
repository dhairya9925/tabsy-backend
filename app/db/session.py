from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# Configure async engine with SSL if connecting to Supabase cloud
connect_args = {}
if "supabase.com" in settings.async_database_url or "sslmode=require" in settings.async_database_url:
    connect_args["ssl"] = "require"
    connect_args["statement_cache_size"] = 0

engine = create_async_engine(
    settings.async_database_url,
    echo=(settings.ENVIRONMENT == "development_debug"),
    pool_pre_ping=True,
    connect_args=connect_args,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency yielding an async SQLAlchemy database session.
    Transactions are scoped per request. Does NOT run any Drizzle migrations.
    """
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
