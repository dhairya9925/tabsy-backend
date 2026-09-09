from datetime import date, datetime
import re
from uuid import UUID
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.expense import ExpenseResponse, ExpenseSplitWrite, PositiveMoney


class PendingShadowProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    display_name: str | None = None
    email: str | None = None
    shadow_created_by: UUID | None = None

    model_config = ConfigDict(from_attributes=True)


class FriendProfileResponse(BaseModel):
    user_id: UUID
    display_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    is_shadow: bool = False

    model_config = ConfigDict(from_attributes=True)


class FriendWithProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    friend_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
    profile: FriendProfileResponse | None = None

    model_config = ConfigDict(from_attributes=True)


class FriendBalanceResponse(BaseModel):
    friendId: UUID = Field(validation_alias=AliasChoices("friendId", "friend_id"))
    netBalance: float = Field(validation_alias=AliasChoices("netBalance", "net_balance"))

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


FriendExpenseFeedResponse = ExpenseResponse


class FriendRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str | None = None
    user_id: UUID | None = None

    @model_validator(mode="after")
    def validate_target(self) -> "FriendRequestCreate":
        if not self.email and not self.user_id:
            raise ValueError("Either email or user_id must be provided")
        return self


class FriendExpenseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount: PositiveMoney
    category: str = Field(default="food", min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    expense_date: date = Field(default_factory=date.today)
    paid_by: UUID | None = None
    splits: list[ExpenseSplitWrite] = Field(default_factory=list, max_length=2)
    split_type: str | None = Field(default=None, pattern="^(equal|full)$")

    @field_validator("expense_date", mode="before")
    @classmethod
    def iso_date_only(cls, value):
        if not isinstance(value, date) and (not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            raise ValueError("Use an ISO date (YYYY-MM-DD)")
        return value


class FriendExpenseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount: PositiveMoney | None = None
    category: str | None = Field(default=None, min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    expense_date: date | None = None
    paid_by: UUID | None = None
    splits: list[ExpenseSplitWrite] | None = Field(default=None, max_length=2)
    split_type: str | None = Field(default=None, pattern="^(equal|full)$")

    @field_validator("amount", "category", "expense_date", "splits")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Field cannot be null")
        return value

    @field_validator("expense_date", mode="before")
    @classmethod
    def iso_date_only(cls, value):
        if value is not None and not isinstance(value, date) and (not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            raise ValueError("Use an ISO date (YYYY-MM-DD)")
        return value


class MergeShadowProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shadow_user_id: UUID


class CreateShadowProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    display_name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=255)


class CreateShadowProfileResponse(BaseModel):
    profile: FriendProfileResponse
    shadow_user_id: UUID


