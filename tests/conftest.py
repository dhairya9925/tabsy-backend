import pytest
import pytest_asyncio
from app.core.rate_limiter import rate_limiter


@pytest_asyncio.fixture(autouse=True)
async def reset_rate_limit():
    await rate_limiter.reset()
    yield
    await rate_limiter.reset()
