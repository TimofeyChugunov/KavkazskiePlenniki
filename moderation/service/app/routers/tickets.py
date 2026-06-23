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


class FieldReportRequest(BaseModel):
    field_path: str
    message: str = ""
    severity: str = "ERROR"


class BlockRequest(BaseModel):
    blocking_reason_ids: list[str] = Field(min_length=1)
    comment: str | None = None
    field_reports: list[FieldReportRequest] = Field(default_factory=list)


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
            status_code=status.HTTP_403_FORBIDDEN,
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


async def _send_blocked_event(
    product_id: str,
    blocking_reason: dict,
    hard_block: bool,
    moderator_comment: str | None,
    field_reports: list[dict],
) -> None:
    idempotency_key = str(uuid.uuid4())
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.B2B_URL}/api/v1/events/moderation",
            headers=_MOD_B2B_HEADERS,
            json={
                "idempotency_key": idempotency_key,
                "product_id": product_id,
                "status": "BLOCKED",
                "hard_block": hard_block,
                "blocking_reason": blocking_reason,
                "field_reports": field_reports,
            },
            timeout=10.0,
        )
    if resp.status_code != 200:
        raise Exception("B2B event delivery failed")


async def _resolve_blocking_reasons(
    blocking_reason_ids: list[str],
) -> tuple[list[dict], bool]:
    reasons: list[dict] = []
    is_hard = False
    try:
        async with httpx.AsyncClient() as client:
            for reason_id in blocking_reason_ids:
                resp = await client.get(
                    f"{settings.B2B_URL}/api/v1/blocking-reasons/{reason_id}",
                    headers=_MOD_B2B_HEADERS,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    reason = resp.json()
                    reasons.append(reason)
                    if reason.get("hard_block"):
                        is_hard = True
                else:
                    reasons.append({
                        "id": reason_id,
                        "title": "Unknown",
                        "comment": "",
                        "hard_block": False,
                    })
    except Exception:
        pass
    return reasons, is_hard


@router.post(
    "/tickets/{ticket_id}/block",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Решение принято"},
        400: {"description": "Невалидный запрос"},
        401: {"description": "Не авторизован"},
        403: {"description": "Тикет закреплён за другим модератором"},
        404: {"description": "Тикет не найден"},
        409: {"description": "Неверный статус"},
        500: {"description": "B2B недоступен"},
    },
)
async def block_ticket(
    ticket_id: str,
    request: Request,
    body: BlockRequest,
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
            status_code=status.HTTP_403_FORBIDDEN,
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

    reasons, is_hard = await _resolve_blocking_reasons(body.blocking_reason_ids)

    blocking_reason = None
    if reasons:
        primary = reasons[0]
        blocking_reason = {
            "id": primary.get("id", primary.get("code", "")),
            "title": primary.get("title", ""),
            "comment": body.comment or "",
        }

    field_reports_data = [
        {"field_name": fr.field_path, "sku_id": None, "comment": fr.message}
        for fr in body.field_reports
    ]

    try:
        await _send_blocked_event(
            product_id=ticket["product_id"],
            blocking_reason=blocking_reason,
            hard_block=is_hard,
            moderator_comment=body.comment,
            field_reports=field_reports_data,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "B2B_UNAVAILABLE",
                "message": "Не удалось доставить событие в B2B, повторите позже",
            },
        )

    now = datetime.now(timezone.utc)
    ticket["status"] = "HARD_BLOCKED" if is_hard else "BLOCKED"
    ticket["decision_at"] = now.isoformat()
    ticket["decision_comment"] = body.comment
    ticket["field_reports"] = [
        {"field_path": fr.field_path, "message": fr.message, "severity": fr.severity}
        for fr in body.field_reports
    ]
    ticket["blocking_reasons"] = [
        {"id": r.get("id", r.get("code")), "title": r.get("title", ""), "hard_block": r.get("hard_block", False)}
        for r in reasons
    ]
    ticket["updated_at"] = now.isoformat()
    ticket["history"].append({
        "at": now.isoformat(),
        "action": "HARD_BLOCKED" if is_hard else "BLOCKED",
        "moderator_id": user_id,
        "comment": body.comment,
    })

    return ticket
