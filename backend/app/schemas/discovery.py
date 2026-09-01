from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .job import JobOut


class DiscoveryCreate(BaseModel):
    name: str | None = None
    brief: str
    num_leads: int = 10

    @model_validator(mode="after")
    def _require_brief(self):
        if not self.brief or not self.brief.strip():
            raise ValueError("Provide a natural-language brief describing the leads to discover")
        return self


class DiscoveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    brief: str
    num_leads: int
    created_at: datetime


class DiscoveryListItem(DiscoveryOut):
    latest_job: JobOut | None = None