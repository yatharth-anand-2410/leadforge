from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db import get_db
from ..models.discovery import Discovery
from ..models.job import Job
from ..models.user import User
from ..schemas.discovery import DiscoveryCreate, DiscoveryListItem, DiscoveryOut
from ..schemas.job import JobOut

router = APIRouter(prefix="/discoveries", tags=["discoveries"])


def _get_owned(discovery_id: int, user: User, db: Session) -> Discovery:
    discovery = (
        db.query(Discovery)
        .filter(Discovery.id == discovery_id, Discovery.user_id == user.id)
        .first()
    )
    if discovery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Discovery not found")
    return discovery


@router.get("", response_model=list[DiscoveryListItem])
def list_discoveries(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    discoveries = (
        db.query(Discovery)
        .filter(Discovery.user_id == user.id)
        .order_by(Discovery.created_at.desc())
        .all()
    )
    items: list[DiscoveryListItem] = []
    for d in discoveries:
        latest = (
            db.query(Job)
            .filter(Job.discovery_id == d.id)
            .order_by(Job.created_at.desc())
            .first()
        )
        item = DiscoveryListItem.model_validate(d)
        item.latest_job = JobOut.model_validate(latest) if latest else None
        items.append(item)
    return items


def _default_name(payload: DiscoveryCreate) -> str:
    if payload.name:
        return payload.name
    if payload.brief:
        return payload.brief.strip().splitlines()[0][:200] or str(payload.brief)[:200]
    return f"{payload.lead_type} in {payload.location}"


@router.post("", response_model=DiscoveryOut, status_code=status.HTTP_201_CREATED)
def create_discovery(
    payload: DiscoveryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    discovery = Discovery(
        user_id=user.id,
        name=_default_name(payload),
        brief=payload.brief,
        lead_type=payload.lead_type or "",
        location=payload.location or "",
        industry=payload.industry,
        company_size_min=payload.company_size_min,
        company_size_max=payload.company_size_max,
        target_roles=payload.target_roles,
        keywords=payload.keywords,
        exclude_keywords=payload.exclude_keywords,
        num_leads=max(1, payload.num_leads),
    )
    db.add(discovery)
    db.flush()  # assign discovery.id

    db.add(Job(discovery_id=discovery.id, user_id=user.id, status="queued"))
    db.commit()
    db.refresh(discovery)
    return discovery


@router.get("/{discovery_id}", response_model=DiscoveryOut)
def get_discovery(
    discovery_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _get_owned(discovery_id, user, db)


@router.get("/{discovery_id}/jobs", response_model=list[JobOut])
def list_jobs(
    discovery_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_owned(discovery_id, user, db)
    return (
        db.query(Job)
        .filter(Job.discovery_id == discovery_id)
        .order_by(Job.created_at.desc())
        .all()
    )


@router.post("/{discovery_id}/run", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def run_discovery(
    discovery_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    discovery = _get_owned(discovery_id, user, db)
    job = Job(discovery_id=discovery.id, user_id=user.id, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
