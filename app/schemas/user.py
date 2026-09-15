from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator


VALID_AVATARS: set[str] = {f"avatar-{i}" for i in range(1, 16)}


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
    def validate_avatar_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Accept bare key: avatar-1 .. avatar-15
        if value in VALID_AVATARS:
            return value
        # Accept valid HTTP URLs (e.g. https://avatar.tabsy.app/avatar-1 or custom URL)
        try:
            TypeAdapter(HttpUrl).validate_python(value)
            return value
        except Exception:
            pass
        valid_list = ", ".join(sorted(VALID_AVATARS, key=lambda x: int(x.split("-")[1])))
        raise ValueError(f"avatar_url must be a valid avatar key ({valid_list}) or a valid HTTP URL")


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
