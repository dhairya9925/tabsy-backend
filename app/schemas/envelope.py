from typing import Any, Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class ResponseEnvelope(BaseModel, Generic[T]):
    """
    Standard response envelope for all API v1 endpoints per Guardrail #3:
    { "data": ..., "error": ..., "meta": ... }
    """
    data: T | None = None
    error: str | None = None
    meta: dict[str, Any] | None = None
