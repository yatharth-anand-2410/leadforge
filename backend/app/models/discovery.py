from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, utcnow


class Discovery(Base):
    __tablename__ = "discoveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    lead_type: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(200), nullable=True)
    company_size_min: Mapped[int | None] = mapped_column(nullable=True)
    company_size_max: Mapped[int | None] = mapped_column(nullable=True)
    target_roles: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    keywords: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    exclude_keywords: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    num_leads: Mapped[int] = mapped_column(default=10, nullable=False)

    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
