from datetime import datetime
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str
    environment: str
    timestamp: datetime
