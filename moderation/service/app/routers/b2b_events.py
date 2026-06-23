import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header, Request, status
from pydantic import BaseModel

from ..config import settings
from .tickets import _tickets_db

router = APIRouter(prefix="/api/v1", tags=["B2B Events"])


class EventProductCreated(BaseModel):
    product_id: str
    seller_id: str
    json_after: dict


class EventProductEdited(BaseModel):
    product_id: str
    seller_id: str
    json_before: dict
    json_after: dict


class EventProductDeleted(BaseModel):
    product_id: str


class IncomingB2BEvent(BaseModel):
    event_type: str
    idempotency_key: str
    occurred_at: str
    payload: dict


@router.post(
    "/b2b/events",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Принято"},
        401: {"description": "Не авторизован"},
        409: {"description": "Дубликат события"},
    },
)
async def receive_b2b_event(
    body: IncomingB2BEvent,
    x_service_key: str | None = Header(default=None),
):
    if x_service_key != settings.MOD_TO_B2B_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid X-Service-Key"},
        )

    if body.event_type not in ("PRODUCT_CREATED", "PRODUCT_EDITED", "PRODUCT_DELETED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_EVENT", "message": "Unknown event type"},
        )

    product_id = body.payload.get("product_id")

    if body.event_type == "PRODUCT_EDITED":
        for ticket in _tickets_db.values():
            if ticket["product_id"] == product_id and ticket["status"] == "HARD_BLOCKED":
                return {"ok": True, "action": "ignored"}

        for ticket in _tickets_db.values():
            if ticket["product_id"] == product_id and ticket["status"] in ("PENDING", "IN_REVIEW"):
                return {"ok": True, "action": "ignored_duplicate"}

        return {"ok": True, "action": "no_ticket"}

    if body.event_type == "PRODUCT_DELETED":
        to_remove = [
            tid for tid, t in _tickets_db.items()
            if t["product_id"] == product_id
        ]
        for tid in to_remove:
            del _tickets_db[tid]

        return {"ok": True, "action": "tickets_removed"}

    return {"ok": True, "action": "no_action"}
