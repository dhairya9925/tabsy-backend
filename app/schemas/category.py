from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=30)


class CategoryUpdate(BaseModel):
    """The slug is a stable expense reference; renaming only changes the label."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=2, max_length=30)
    color_index: int | None = Field(default=None, ge=0, le=9)

    @field_validator("name", "color_index")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Field cannot be null")
        return value


class UserCategoryResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    slug: str
    color_index: int
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
