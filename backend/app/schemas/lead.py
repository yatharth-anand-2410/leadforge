from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    discovery_id: int
    user_id: int
    name: str
    category: str | None
    industry: str | None
    address: str | None
    city: str | None
    country: str | None
    lat: float | None
    lon: float | None
    website: str | None
    email: str | None
    phone: str | None
    contact_name: str | None
    contact_role: str | None
    source: str | None
    source_id: str | None
    score: float
    score_breakdown: dict
    fit_score: float | None
    fit_reason: str | None
    what_to_sell: str | None
    verified: bool
    verified_method: str | None
    status: str
    dedupe_key: str | None
    created_at: datetime
