from datetime import date, datetime
from uuid import UUID
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


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
