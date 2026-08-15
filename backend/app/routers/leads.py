from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models.discovery import Discovery
from ..models.lead import Lead
from ..models.user import User
from ..schemas.lead import LeadOut

router = APIRouter(tags=["leads"])


def _owned_discovery(discovery_id: int, user: User, db: Session) -> Discovery:
    discovery = (
        db.query(Discovery)
        .filter(Discovery.id == discovery_id, Discovery.user_id == user.id)
        .first()
    )
    if discovery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Discovery not found")
    return discovery


@router.get("/discoveries/{discovery_id}/leads", response_model=list[LeadOut])
def list_leads(
    discovery_id: int,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_discovery(discovery_id, user, db)
    q = db.query(Lead).filter(Lead.discovery_id == discovery_id)
    if status:
        q = q.filter(Lead.status == status)
    return q.order_by(Lead.score.desc()).all()


@router.get("/leads/{lead_id}", response_model=LeadOut)
def get_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user.id).first()
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return lead
