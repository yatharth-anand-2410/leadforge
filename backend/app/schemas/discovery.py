from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .job import JobOut


class DiscoveryCreate(BaseModel):
    name: str | None = None
    lead_type: str
    location: str
    industry: str | None = None
    company_size_min: int | None = None
    company_size_max: int | None = None
    target_roles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    num_leads: int = 10


class DiscoveryOut(DiscoveryCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    created_at: datetime


class DiscoveryListItem(DiscoveryOut):
    latest_job: JobOut | None = None
