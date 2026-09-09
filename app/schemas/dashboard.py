from uuid import UUID
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.schemas.expense import ExpenseResponse
from app.schemas.group import GroupResponse


class GroupBalanceSummaryResponse(BaseModel):
    groupId: UUID = Field(validation_alias=AliasChoices("groupId", "group_id"))
    groupName: str = Field(validation_alias=AliasChoices("groupName", "group_name"))
    groupType: str = Field(validation_alias=AliasChoices("groupType", "group_type"))
    memberCount: int = Field(validation_alias=AliasChoices("memberCount", "member_count"))
    userNetBalance: float = Field(validation_alias=AliasChoices("userNetBalance", "user_net_balance"))

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class GroupUserSplitItemResponse(BaseModel):
    id: UUID
    expense_date: str
    category: str
    amount: float
    group_id: UUID

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class DashboardActivityItemResponse(BaseModel):
    id: str
    type: str  # 'personal_expense' | 'group_expense' | 'settlement'
    title: str
    subText: str = Field(validation_alias=AliasChoices("subText", "sub_text"))
    amount: float
    date: str
    category: str
    link: str

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class DashboardFriendBalanceResponse(BaseModel):
    friendId: UUID = Field(validation_alias=AliasChoices("friendId", "friend_id"))
    netBalance: float = Field(validation_alias=AliasChoices("netBalance", "net_balance"))
    friendName: str | None = Field(None, validation_alias=AliasChoices("friendName", "friend_name"))

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class DashboardDataResponse(BaseModel):
    personalTotal: float = Field(validation_alias=AliasChoices("personalTotal", "personal_total"))
    personalPrevMonthTotal: float = Field(validation_alias=AliasChoices("personalPrevMonthTotal", "personal_prev_month_total"))
    personalExpenseCount: int = Field(validation_alias=AliasChoices("personalExpenseCount", "personal_expense_count"))
    personalExpenses: list[ExpenseResponse] = Field(validation_alias=AliasChoices("personalExpenses", "personal_expenses"))

    groups: list[GroupResponse]
    groupShareTotal: float = Field(validation_alias=AliasChoices("groupShareTotal", "group_share_total"))
    groupSharePrevMonthTotal: float = Field(validation_alias=AliasChoices("groupSharePrevMonthTotal", "group_share_prev_month_total"))
    groupBalances: list[GroupBalanceSummaryResponse] = Field(validation_alias=AliasChoices("groupBalances", "group_balances"))
    groupUserSplits: list[GroupUserSplitItemResponse] = Field(validation_alias=AliasChoices("groupUserSplits", "group_user_splits"))

    friendBalances: list[DashboardFriendBalanceResponse] = Field(validation_alias=AliasChoices("friendBalances", "friend_balances"))

    unifiedTotal: float = Field(validation_alias=AliasChoices("unifiedTotal", "unified_total"))
    unifiedPrevMonthTotal: float = Field(validation_alias=AliasChoices("unifiedPrevMonthTotal", "unified_prev_month_total"))
    monthOverMonthPct: int | None = Field(None, validation_alias=AliasChoices("monthOverMonthPct", "month_over_month_pct"))

    netOwed: float = Field(validation_alias=AliasChoices("netOwed", "net_owed"))
    netOwes: float = Field(validation_alias=AliasChoices("netOwes", "net_owes"))
    netBalance: float = Field(validation_alias=AliasChoices("netBalance", "net_balance"))
    unsettledCount: int = Field(validation_alias=AliasChoices("unsettledCount", "unsettled_count"))

    monthlyTransactionCount: int = Field(validation_alias=AliasChoices("monthlyTransactionCount", "monthly_transaction_count"))
    recentActivity: list[DashboardActivityItemResponse] = Field(validation_alias=AliasChoices("recentActivity", "recent_activity"))
    lastExpenseDate: str | None = Field(None, validation_alias=AliasChoices("lastExpenseDate", "last_expense_date"))

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
