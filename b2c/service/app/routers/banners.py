from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..config import settings

router = APIRouter(prefix="/api/v1", tags=["Home"])

_banners_db: list[dict] = []
_banner_events_db: list[dict] = []


def _seed_banners():
    if _banners_db:
        return
    now = datetime.now(timezone.utc)
    _banners_db.extend([
        {
            "id": str(uuid4()),
            "title": "Скидки на электронику до -30%",
            "image_url": "/cdn/banners/electronics-sale.jpg",
            "link": "/catalog?category_id=3",
            "priority": 10,
            "is_active": True,
            "start_at": None,
            "end_at": None,
            "created_at": now.isoformat(),
        },
        {
            "id": str(uuid4()),
            "title": "Новая коллекция одежды",
            "image_url": "/cdn/banners/new-collection.jpg",
            "link": "/catalog?category_id=5",
            "priority": 5,
            "is_active": True,
            "start_at": None,
            "end_at": None,
            "created_at": now.isoformat(),
        },
    ])


class BannerEventItem(BaseModel):
    banner_id: str
    event: str = Field(pattern=r"^(impression|click)$")
    timestamp: str | None = None


class BannerEventsRequest(BaseModel):
    events: list[BannerEventItem]


def _is_banner_active(banner: dict, now: datetime) -> bool:
    if not banner.get("is_active", False):
        return False
    start_at = banner.get("start_at")
    end_at = banner.get("end_at")
    if start_at:
        try:
            if datetime.fromisoformat(start_at.replace("Z", "+00:00")) > now:
                return False
        except (ValueError, TypeError):
            pass
    if end_at:
        try:
            if datetime.fromisoformat(end_at.replace("Z", "+00:00")) < now:
                return False
        except (ValueError, TypeError):
            pass
    return True


@router.get("/home/banners", status_code=status.HTTP_200_OK)
async def get_home_banners():
    now = datetime.now(timezone.utc)
    active = [b for b in _banners_db if _is_banner_active(b, now)]
    active.sort(key=lambda b: b.get("priority", 0))

    items = [
        {
            "id": b["id"],
            "title": b.get("title", ""),
            "image_url": b["image_url"],
            "link": b["link"],
            "priority": b.get("priority", 0),
        }
        for b in active
    ]

    return {
        "items": items,
        "total_count": len(items),
    }


@router.post("/banner-events", status_code=status.HTTP_202_ACCEPTED)
async def create_banner_events(body: BannerEventsRequest, request: Request):
    if not body.events:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EMPTY_EVENTS", "message": "Events list must not be empty"},
        )

    banner_ids = {b["id"] for b in _banners_db}

    for ev in body.events:
        if ev.banner_id not in banner_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "BANNER_NOT_FOUND", "message": f"Banner {ev.banner_id} not found"},
            )

    user_id = None
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        from jose import jwt, JWTError

        try:
            payload = jwt.decode(
                auth[7:],
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
            )
            user_id = payload.get("sub")
        except JWTError:
            pass

    now = datetime.now(timezone.utc)
    for ev in body.events:
        _banner_events_db.append({
            "id": str(uuid4()),
            "banner_id": ev.banner_id,
            "user_id": user_id,
            "event": ev.event,
            "timestamp": ev.timestamp or now.isoformat(),
            "created_at": now.isoformat(),
        })

    return {"accepted": len(body.events)}
