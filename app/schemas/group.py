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
    invite_code: str | None = None

    model_config = ConfigDict(from_attributes=True)


class GroupJoinRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128, description="Group invite code or group UUID")


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


class MemberLedgerItem(BaseModel):
    user_id: UUID
    display_name: str
    email: str | None = None
    avatar_url: str | None = None
    role: str = "member"
    is_excluded: bool = False
    exclusion_type: str | None = None
    rent_share: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    expense_share: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    adjustments: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    total_expense: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    total_paid: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    balance: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    status: str = "pending"  # "pending", "confirmed", "credited"

    model_config = ConfigDict(from_attributes=True)


class MemberObligationBreakdown(BaseModel):
    rent_share: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    expense_share: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    adjustments: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    total_obligation: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    already_paid: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)


class UserLedgerActionSummary(BaseModel):
    action: str  # "pay_coordinator" | "receive_refund" | "settled"
    amount: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    coordinator_name: str | None = None
    coordinator_id: UUID | None = None
    coordinator_upi_id: str | None = None
    status: str = "pending"
    upi_uri: str | None = None
    breakdown: MemberObligationBreakdown


class CoordinatorPendingCollection(BaseModel):
    user_id: UUID
    display_name: str
    avatar_url: str | None = None
    amount: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    status: str = "pending"


class CoordinatorPendingRefund(BaseModel):
    user_id: UUID
    display_name: str
    avatar_url: str | None = None
    amount: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    refunded_amount: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    remaining_refund: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    status: str = "credited"  # "credited" | "refunded"


class CoordinatorPendingBill(BaseModel):
    category: str
    description: str
    amount: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    paid_amount: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    remaining_amount: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    status: str = "unpaid"  # "unpaid" | "partially_paid" | "cleared"


class CoordinatorChecklist(BaseModel):
    members_to_collect: list[CoordinatorPendingCollection] = Field(default_factory=list)
    total_to_collect: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    members_to_refund: list[CoordinatorPendingRefund] = Field(default_factory=list)
    total_to_refund: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    net_cash_for_bills: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    external_bills_pending: list[CoordinatorPendingBill] = Field(default_factory=list)
    total_external_bills_pending: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)


class MonthlyLedgerSummary(BaseModel):
    total_rent: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    total_shared_expenses: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    total_adjustments: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    grand_total: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    total_paid: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    total_balance: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    members_to_contribute: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    over_contributed: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    remaining_for_bills: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    collection_progress_pct: float = 0.0
    bill_progress_pct: float = 0.0
    total_disbursed: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    total_vendor_bills_paid: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    total_refunds_paid: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)


class MonthlyLedgerDisbursementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    disbursement_type: str = Field(..., pattern=r"^(vendor_bill|member_refund)$")
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    recipient_user_id: UUID | None = None
    recipient_name: str | None = Field(default=None, max_length=120)
    category: str = Field(default="rent", max_length=50)
    payment_method: str = Field(default="upi", max_length=50)
    reference_note: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_disbursement(self) -> "MonthlyLedgerDisbursementCreate":
        if self.disbursement_type == "member_refund" and not self.recipient_user_id:
            raise ValueError("recipient_user_id is required for member_refund disbursements")
        return self


class MonthlyLedgerDisbursementResponse(BaseModel):
    id: UUID
    settlement_id: UUID
    group_id: UUID
    disbursement_type: str
    amount: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    recipient_user_id: UUID | None = None
    recipient_name: str | None = None
    category: str
    payment_method: str
    reference_note: str | None = None
    recorded_by: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MonthlyLedgerLockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rollover_unclaimed_refunds: bool = True
    note: str | None = Field(default=None, max_length=255)


class MonthlyLedgerResponse(BaseModel):
    group_id: UUID
    group_name: str
    month: int
    year: int
    settlement_id: UUID | None = None
    settlement_status: str = "open"  # "open" | "locked"
    summary: MonthlyLedgerSummary
    members: list[MemberLedgerItem] = Field(default_factory=list)
    my_summary: UserLedgerActionSummary | None = None
    coordinator_summary: CoordinatorChecklist | None = None
    disbursements: list[MonthlyLedgerDisbursementResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class MonthlyLedgerContributionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    from_user_id: UUID
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    note: str | None = Field(default=None, max_length=255)


