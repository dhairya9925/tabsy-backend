from datetime import datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    type: str = Field(default="day_to_day", max_length=50)
    monthly_rent: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    sponsor_id: str | None = Field(default=None, max_length=100)


class GroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    type: str | None = Field(default=None, max_length=50)
    monthly_rent: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    sponsor_id: str | None = Field(default=None, max_length=100)

    @field_validator("name", "type")
    @classmethod
    def reject_empty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Field cannot be empty")
        return value


class GroupMemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: UUID | None = None
    email: str | None = None
    role: str = Field(default="member", pattern=r"^(admin|member)$")

    @model_validator(mode="after")
    def require_user_or_email(self):
        if self.user_id is None and not self.email:
            raise ValueError("Either user_id or email must be provided")
        return self


class GroupMemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: str = Field(pattern=r"^(admin|member)$")


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


class GroupExpenseSplitWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class GroupExpenseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    category: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    expense_date: datetime | str | None = None
    paid_by: UUID | None = None
    status: str = Field(default="submitted", pattern=r"^(submitted|approved|reimbursed)$")
    receipt_url: str | None = Field(default=None, max_length=2000)
    splits: list[GroupExpenseSplitWrite] = Field(default_factory=list, max_length=100)


class GroupExpenseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    expense_date: datetime | str | None = None
    paid_by: UUID | None = None
    status: str | None = Field(default=None, pattern=r"^(submitted|approved|reimbursed)$")
    receipt_url: str | None = Field(default=None, max_length=2000)
    splits: list[GroupExpenseSplitWrite] | None = Field(default=None, max_length=100)


class GroupExpenseBulkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expenses: list[GroupExpenseCreate] = Field(min_length=1, max_length=100)


class GroupSettleUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_user_id: UUID
    to_user_id: UUID

