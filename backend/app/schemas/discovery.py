from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .job import JobOut


class DiscoveryCreate(BaseModel):
    name: str | None = None
    brief: str | None = None
    lead_type: str | None = None
    location: str | None = None
    industry: str | None = None
    company_size_min: int | None = None
    company_size_max: int | None = None
    target_roles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    num_leads: int = 10

    @model_validator(mode="after")
    def _require_something(self):
        if not any([self.brief, self.lead_type, self.location]):
            raise ValueError("Provide a natural-language brief, or lead type and location")
        return self


class DiscoveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    brief: str | None = None
    lead_type: str | None = None
    location: str | None = None
    industry: str | None = None
    company_size_min: int | None = None
    company_size_max: int | None = None
    target_roles: list[str]
    keywords: list[str]
    exclude_keywords: list[str]
    num_leads: int
    created_at: datetime


class DiscoveryListItem(DiscoveryOut):
    latest_job: JobOut | None = None