from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class MonthlySettlementResponse(BaseModel):
    id: UUID
    group_id: UUID
    month: int
    year: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemberMonthlyStatusResponse(BaseModel):
    id: UUID
    settlement_id: UUID
    user_id: UUID
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemberMonthlyExclusionResponse(BaseModel):
    id: UUID
    group_id: UUID
    user_id: UUID
    month: int
    year: int
    exclusion_type: str
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
