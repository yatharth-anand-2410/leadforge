from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    discovery_id: int
    user_id: int
    status: str
    progress: dict
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
