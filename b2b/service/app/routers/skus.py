import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..config import settings
from ..database import get_db
from ..models import Characteristic, Product, SKU
from ..schemas import ErrorResponse, SKUCreate, SKUCreateResponse, SKUUpdate

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


async def send_sku_out_of_stock(sku_id: str) -> None:
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{settings.B2C_URL}/api/v1/events/sku",
            json={
                "event": "SKU_OUT_OF_STOCK",
                "sku_id": sku_id,
            },
            headers={"X-Service-Key": settings.B2B_TO_B2C_KEY},
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


@router.put(
    "/{sku_id}",
    response_model=SKUCreateResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def update_sku(
    sku_id: str,
    body: SKUUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    seller_id = current_user.get("sub")
    if not seller_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid token"},
        )

    sku = await db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "SKU not found"},
        )

    product = await db.get(Product, sku.product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    if product.seller_id != seller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NOT_OWNER", "message": "Product does not belong to the authenticated seller"},
        )

    if product.status == "HARD_BLOCKED":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Cannot edit hard-blocked product"},
        )

    if body.name is not None:
        sku.name = body.name
    if body.price is not None:
        sku.price = body.price
    if body.cost_price is not None:
        sku.cost_price = body.cost_price
    if body.discount is not None:
        sku.discount = body.discount
    if body.image is not None:
        sku.image = body.image

    event_to_send = None
    if product.status in ("MODERATED", "BLOCKED"):
        product.status = "ON_MODERATION"
        event_to_send = "EDITED"

    if body.characteristics is not None:
        old_chars = (await db.execute(select(Characteristic).where(Characteristic.sku_id == sku.id))).scalars().all()
        for char in old_chars:
            await db.delete(char)
        for char in body.characteristics:
            db.add(Characteristic(sku_id=sku.id, name=char.name, value=char.value))

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


@router.delete(
    "/{sku_id}",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Удалён"},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def delete_sku(
    sku_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    seller_id = current_user.get("sub")
    if not seller_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid token"},
        )

    sku = await db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "SKU not found"},
        )

    product = await db.get(Product, sku.product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Product not found"},
        )

    if product.seller_id != seller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NOT_OWNER", "message": "SKU does not belong to the authenticated seller"},
        )

    if product.status == "HARD_BLOCKED":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Cannot delete SKU of hard-blocked product"},
        )

    if sku.reserved_quantity > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": "Cannot delete SKU with active reserves"},
        )

    remaining_skus = (
        await db.execute(
            select(func.count(SKU.id)).where(SKU.product_id == product.id)
        )
    ).scalar()

    was_moderated = product.status == "MODERATED"
    sku_active = sku.active_quantity
    sku_id_value = sku.id

    await db.delete(sku)
    await db.flush()

    remaining_after = remaining_skus - 1

    event_to_moderation = None
    if remaining_after == 0 and product.status == "ON_MODERATION":
        product.status = "CREATED"
        event_to_moderation = "DELETED"

    await db.commit()

    if event_to_moderation:
        await send_moderation_event(
            product_id=product.id,
            seller_id=seller_id,
            event=event_to_moderation,
        )

    if was_moderated and sku_active > 0:
        await send_sku_out_of_stock(sku_id_value)

    return {"ok": True}
