import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..config import settings

router = APIRouter(prefix="/api/v1", tags=["Tickets"])

_tickets_db: dict[str, dict] = {}
_MOD_B2B_HEADERS = {"X-Service-Key": settings.MOD_TO_B2B_KEY}


class ApproveRequest(BaseModel):
    comment: str | None = None


def _get_user_id(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Требуется авторизация"},
        )

    from jose import jwt, JWTError

    try:
        payload = jwt.decode(
            auth[7:],
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        sub = payload.get("sub")
        if sub:
            return sub
    except JWTError:
        pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "UNAUTHORIZED", "message": "Требуется авторизация"},
    )


async def _check_product_has_skus(product_id: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.B2B_URL}/api/v1/public/products/{product_id}",
                headers=_MOD_B2B_HEADERS,
                timeout=10.0,
            )
        if resp.status_code == 200:
            product = resp.json()
            skus = product.get("skus", [])
            return len(skus) > 0
    except Exception:
        pass
    return True


async def _send_moderated_event(product_id: str) -> None:
    idempotency_key = str(uuid.uuid4())
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.B2B_URL}/api/v1/events/moderation",
            headers=_MOD_B2B_HEADERS,
            json={
                "idempotency_key": idempotency_key,
                "product_id": product_id,
                "status": "MODERATED",
            },
            timeout=10.0,
        )
    if resp.status_code != 200:
        raise Exception("B2B event delivery failed")


@router.post(
    "/tickets/{ticket_id}/approve",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Тикет одобрен"},
        401: {"description": "Не авторизован"},
        403: {"description": "Тикет закреплён за другим модератором"},
        404: {"description": "Тикет не найден"},
        409: {"description": "Неверный статус или товар без SKU"},
        500: {"description": "B2B недоступен"},
    },
)
async def approve_ticket(
    ticket_id: str,
    request: Request,
    body: ApproveRequest | None = None,
):
    user_id = _get_user_id(request)

    ticket = _tickets_db.get(ticket_id)
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TICKET_NOT_FOUND", "message": "Тикет не найден"},
        )

    if ticket["status"] == "HARD_BLOCKED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PRODUCT_PERMANENTLY_BLOCKED",
                "message": "Product is permanently blocked",
            },
        )

    if ticket["status"] != "IN_REVIEW":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "TICKET_WRONG_STATUS",
                "message": "Product is not in review status",
            },
        )

    if ticket.get("assigned_moderator_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "NOT_ASSIGNED",
                "message": "This moderation card is not assigned to you",
            },
        )

    has_skus = await _check_product_has_skus(ticket["product_id"])
    if not has_skus:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NO_SKUS",
                "message": "Product has no SKUs, cannot approve",
            },
        )

    try:
        await _send_moderated_event(ticket["product_id"])
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "B2B_UNAVAILABLE",
                "message": "Не удалось доставить событие в B2B, повторите позже",
            },
        )

    now = datetime.now(timezone.utc)
    ticket["status"] = "APPROVED"
    ticket["decision_at"] = now.isoformat()
    ticket["decision_comment"] = body.comment if body else None
    ticket["field_reports"] = []
    ticket["blocking_reasons"] = []
    ticket["updated_at"] = now.isoformat()
    ticket["history"].append({
        "at": now.isoformat(),
        "action": "APPROVED",
        "moderator_id": user_id,
        "comment": body.comment if body else None,
    })

    return ticket
