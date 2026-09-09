from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class MemberProfileResponse(BaseModel):
    display_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    is_shadow: bool = False

    model_config = ConfigDict(from_attributes=True)


class GroupMemberResponse(BaseModel):
    id: UUID
    group_id: UUID
    user_id: UUID
    role: str
    joined_at: datetime
    profile: MemberProfileResponse | None = None

    model_config = ConfigDict(from_attributes=True)


class GroupResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    type: str = "day_to_day"
    monthly_rent: float | None = None
    sponsor_id: str | None = None

    model_config = ConfigDict(from_attributes=True)


class GroupExpenseSplitResponse(BaseModel):
    id: UUID
    expense_id: UUID
    user_id: UUID
    amount: float
    is_settled: bool
    created_at: datetime
    member_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class GroupExpenseResponse(BaseModel):
    id: UUID
    amount: float
    category: str
    note: str | None = None
    expense_date: datetime | str
    user_id: UUID
    group_id: UUID | None = None
    paid_by: UUID | None = None
    status: str = "submitted"
    receipt_url: str | None = None
    created_at: datetime
    updated_at: datetime
    edited_at: datetime | None = None
    payer_name: str | None = None
    splits: list[GroupExpenseSplitResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GroupBalanceResponse(BaseModel):
    from_user_id: UUID
    from_name: str
    to_user_id: UUID
    to_name: str
    amount: float

    model_config = ConfigDict(from_attributes=True)

