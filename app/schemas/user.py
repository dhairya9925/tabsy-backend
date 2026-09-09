from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator


class ProfileUpdate(BaseModel):
    """Only public display fields are editable; identity stays with Supabase Auth."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str | None = Field(default=None, max_length=120)
    avatar_url: str | None = Field(default=None, max_length=2048)

    @field_validator("display_name", "avatar_url")
    @classmethod
    def blank_to_null(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("avatar_url")
    @classmethod
    def validate_avatar_url(cls, value: str | None) -> str | None:
        if value is not None:
            TypeAdapter(HttpUrl).validate_python(value)
        return value


class ProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    display_name: str | None = None
    avatar_url: str | None = None
    email: str | None = None
    is_shadow: bool
    created_at: datetime
    updated_at: datetime
    shadow_created_by: UUID | None = None

    model_config = ConfigDict(from_attributes=True)


class ProfileLookupResponse(BaseModel):
    id: UUID
    user_id: UUID
    display_name: str | None = None
    avatar_url: str | None = None
    email: str | None = None

    model_config = ConfigDict(from_attributes=True)
