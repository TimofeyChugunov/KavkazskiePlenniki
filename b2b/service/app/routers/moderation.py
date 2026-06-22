import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import BlockingReason, FieldReport, ProcessedEvent, Product, SKU
from ..schemas import ErrorResponse, ModerationEventRequest

router = APIRouter(prefix="/api/v1/events", tags=["Moderation Events"])


async def verify_mod_service_key(x_service_key: str | None = Header(default=None)):
    if not x_service_key or x_service_key != settings.MOD_TO_B2B_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing X-Service-Key"},
        )
    return x_service_key


async def send_b2c_product_blocked(product_id: str, sku_ids: list[str], reason: str | None = None) -> None:
    idempotency_key = str(uuid.uuid4())
    payload = {
        "idempotency_key": idempotency_key,
        "event": "PRODUCT_BLOCKED",
        "product_id": product_id,
        "sku_ids": sku_ids,
        "date": datetime.now(timezone.utc).isoformat(),
    }
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{settings.B2C_URL}/api/v1/events/product",
            json=payload,
            headers={"X-Service-Key": settings.B2B_TO_B2C_KEY},
            timeout=5.0,
        )


@router.post(
    "/moderation",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Событие принято и обработано"},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def receive_moderation_event(
    body: ModerationEventRequest,
    _key: str = Depends(verify_mod_service_key),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(ProcessedEvent).where(ProcessedEvent.idempotency_key == body.idempotency_key)
    )
    if existing.scalar_one_or_none():
        return {"ok": True}

    product = await db.get(Product, body.product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    if body.status == "MODERATED":
        product.status = "MODERATED"
        product.blocked = False

        old_reason = (await db.execute(
            select(BlockingReason).where(BlockingReason.product_id == product.id)
        )).scalars().first()
        if old_reason:
            await db.delete(old_reason)

        old_reports = (await db.execute(
            select(FieldReport).where(FieldReport.product_id == product.id)
        )).scalars().all()
        for report in old_reports:
            await db.delete(report)

    elif body.status == "BLOCKED":
        if body.hard_block:
            product.status = "HARD_BLOCKED"
        else:
            product.status = "BLOCKED"
        product.blocked = True

        old_reason = (await db.execute(
            select(BlockingReason).where(BlockingReason.product_id == product.id)
        )).scalars().first()
        if old_reason:
            await db.delete(old_reason)

        old_reports = (await db.execute(
            select(FieldReport).where(FieldReport.product_id == product.id)
        )).scalars().all()
        for report in old_reports:
            await db.delete(report)

        if body.blocking_reason:
            db.add(BlockingReason(
                id=body.blocking_reason.id,
                product_id=product.id,
                title=body.blocking_reason.title,
                comment=body.blocking_reason.comment,
            ))

        if body.field_reports:
            for fr in body.field_reports:
                db.add(FieldReport(
                    product_id=product.id,
                    field_name=fr.field_name,
                    sku_id=fr.sku_id,
                    comment=fr.comment,
                ))

        sku_ids = [
            sku.id
            for sku in (await db.execute(select(SKU).where(SKU.product_id == product.id))).scalars().all()
        ]
        await send_b2c_product_blocked(
            product_id=product.id,
            sku_ids=sku_ids,
        )

    processed = ProcessedEvent(
        idempotency_key=body.idempotency_key,
        event_type=body.status,
    )
    db.add(processed)

    await db.commit()

    return {"ok": True}
