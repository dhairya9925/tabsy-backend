from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class ProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    display_name: str | None = None
    avatar_url: str | None = None
    email: str | None = None
    is_shadow: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProfileLookupResponse(BaseModel):
    id: UUID
    user_id: UUID
    display_name: str | None = None
    avatar_url: str | None = None
    email: str | None = None

    model_config = ConfigDict(from_attributes=True)
