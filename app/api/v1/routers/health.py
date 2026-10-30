from datetime import datetime, timezone
from fastapi import APIRouter

from app.core.config import settings
from app.schemas.envelope import ResponseEnvelope
from app.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health", response_model=ResponseEnvelope[HealthResponse])
@router.head("/health", include_in_schema=False)
async def health_check() -> ResponseEnvelope[HealthResponse]:
    """
    Unauthenticated health check endpoint returning server status.
    """
    payload = HealthResponse(
        status="healthy",
        version=settings.VERSION,
        environment=settings.ENVIRONMENT,
        timestamp=datetime.now(timezone.utc),
    )
    return ResponseEnvelope(data=payload, error=None, meta=None)
