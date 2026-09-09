from datetime import datetime
from uuid import UUID
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.schemas.expense import ExpenseResponse


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

