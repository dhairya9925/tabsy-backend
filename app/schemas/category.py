from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class UserCategoryResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    slug: str
    color_index: int
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
