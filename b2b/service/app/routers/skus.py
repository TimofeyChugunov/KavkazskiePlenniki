import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..config import settings
from ..database import get_db
from ..models import Characteristic, Product, SKU
from ..schemas import ErrorResponse, SKUCreate, SKUCreateResponse

router = APIRouter(prefix="/api/v1/skus", tags=["SKUs"])


async def send_moderation_event(
    product_id: str,
    seller_id: str,
    event: str,
) -> None:
    idempotency_key = str(uuid.uuid4())
    payload = {
        "idempotency_key": idempotency_key,
        "product_id": product_id,
        "seller_id": seller_id,
        "event": event,
        "date": datetime.now(timezone.utc).isoformat(),
    }
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{settings.MODERATION_URL}/api/v1/events/product",
            json=payload,
            headers={"X-Service-Key": settings.B2B_TO_MOD_KEY},
            timeout=5.0,
        )


@router.post(
    "",
    response_model=SKUCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def create_sku(
    body: SKUCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    seller_id = current_user.get("sub")
    if not seller_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid token"},
        )

    product = await db.get(Product, body.product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    if product.seller_id != seller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Not your product"},
        )

    if product.status == "HARD_BLOCKED":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Cannot add SKU to hard-blocked product"},
        )

    existing_skus_count = (
        await db.execute(select(SKU).where(SKU.product_id == body.product_id))
    ).scalars().all()

    sku = SKU(
        product_id=body.product_id,
        name=body.name,
        price=body.price,
        cost_price=body.cost_price,
        discount=body.discount,
        image=body.image,
    )
    db.add(sku)
    await db.flush()

    for char in body.characteristics:
        db.add(Characteristic(sku_id=sku.id, name=char.name, value=char.value))

    event_to_send = None
    if len(existing_skus_count) == 0:
        product.status = "ON_MODERATION"
        event_to_send = "CREATED"
    elif product.status in ("MODERATED", "BLOCKED"):
        product.status = "ON_MODERATION"
        event_to_send = "EDITED"

    await db.commit()
    await db.refresh(sku)

    if event_to_send:
        await send_moderation_event(
            product_id=product.id,
            seller_id=seller_id,
            event=event_to_send,
        )

    chars = (
        await db.execute(select(Characteristic).where(Characteristic.sku_id == sku.id))
    ).scalars().all()

    return SKUCreateResponse(
        id=sku.id,
        product_id=sku.product_id,
        name=sku.name,
        price=sku.price,
        cost_price=sku.cost_price,
        discount=sku.discount,
        image=sku.image,
        active_quantity=sku.active_quantity,
        reserved_quantity=sku.reserved_quantity,
        characteristics=[{"name": c.name, "value": c.value} for c in chars],
    )
