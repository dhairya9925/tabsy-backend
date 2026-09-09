from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
import re
from uuid import UUID
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
SplitMoney = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]


class ExpenseSplitWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    amount: SplitMoney


class PersonalExpenseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount: PositiveMoney
    category: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    expense_date: date = Field(default_factory=date.today)
    group_id: UUID | None = Field(default=None, description="Ignored: this endpoint always stores group_id=null")
    paid_by: UUID | None = None
    splits: list[ExpenseSplitWrite] = Field(default_factory=list, max_length=100)

    @field_validator("expense_date", mode="before")
    @classmethod
    def iso_date_only(cls, value):
        if not isinstance(value, date) and (not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            raise ValueError("Use an ISO date (YYYY-MM-DD)")
        return value


class PersonalExpenseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount: PositiveMoney | None = None
    category: str | None = Field(default=None, min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    expense_date: date | None = None
    paid_by: UUID | None = None
    splits: list[ExpenseSplitWrite] | None = Field(default=None, max_length=100)

    @field_validator("amount", "category", "expense_date", "splits")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Field cannot be null")
        return value

    @field_validator("expense_date", mode="before")
    @classmethod
    def iso_date_only(cls, value):
        return PersonalExpenseCreate.iso_date_only(value)


class ExpenseSplitResponse(BaseModel):
    id: UUID
    user_id: UUID
    amount: float
    is_settled: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExpenseResponse(BaseModel):
    id: UUID
    user_id: UUID
    amount: float
    category: str
    note: str | None = None
    expense_date: date
    group_id: UUID | None = None
    paid_by: UUID | None = None
    status: str = "submitted"
    receipt_url: str | None = None
    created_at: datetime
    updated_at: datetime
    edited_at: datetime | None = None
    expense_splits: list[ExpenseSplitResponse] = Field(
        default_factory=list,
        validation_alias=AliasChoices("splits", "expense_splits"),
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
