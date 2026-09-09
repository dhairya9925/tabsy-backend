from uuid import UUID
from pydantic import BaseModel, ConfigDict


class PendingShadowProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    display_name: str | None = None
    email: str | None = None
    shadow_created_by: UUID | None = None

    model_config = ConfigDict(from_attributes=True)
