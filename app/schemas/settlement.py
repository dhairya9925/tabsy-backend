from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


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


class MonthlySettlementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(default="open", pattern=r"^(open|locked)$")


class MemberMonthlyStatusCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID | None = None
    status: str = Field(default="complete", pattern=r"^(complete)$")


class MemberMonthlyExclusionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID | None = None
    exclusion_type: str = Field(..., pattern=r"^(none|partial|full)$")
