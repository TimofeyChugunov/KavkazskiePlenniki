import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import ReserveOperation, SKU
from ..schemas import (
    ErrorResponse,
    InventoryItem,
    ReserveRequest,
    ReserveSuccessResponse,
    UnreserveRequest,
    UnreserveResponse,
)

router = APIRouter(prefix="/api/v1/inventory", tags=["Inventory"])


async def verify_service_key(x_service_key: str | None = Header(default=None)):
    if not x_service_key or x_service_key != settings.B2C_TO_B2B_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing X-Service-Key"},
        )
    return x_service_key


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
    "/reserve",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Зарезервировано"},
        409: {"description": "Недостаточно остатков"},
        401: {"model": ErrorResponse},
    },
)
async def reserve_inventory(
    body: ReserveRequest,
    _key: str = Depends(verify_service_key),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(ReserveOperation).where(ReserveOperation.idempotency_key == body.idempotency_key)
    )
    existing_op = existing.scalar_one_or_none()
    if existing_op:
        return existing_op.result

    sku_ids = [item.sku_id for item in body.items]
    result = await db.execute(
        select(SKU).where(SKU.id.in_(sku_ids)).with_for_update()
    )
    skus = {sku.id: sku for sku in result.scalars().all()}

    for item in body.items:
        if item.sku_id not in skus:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "SKU not found"},
            )

    failed_items = []
    for item in body.items:
        sku = skus[item.sku_id]
        if sku.active_quantity == 0:
            failed_items.append({
                "sku_id": item.sku_id,
                "requested": item.quantity,
                "available": 0,
                "reason": "OUT_OF_STOCK",
            })
        elif sku.active_quantity < item.quantity:
            failed_items.append({
                "sku_id": item.sku_id,
                "requested": item.quantity,
                "available": sku.active_quantity,
                "reason": "INSUFFICIENT_STOCK",
            })

    if failed_items:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"reserved": False, "failed_items": failed_items},
        )

    for item in body.items:
        sku = skus[item.sku_id]
        sku.active_quantity -= item.quantity
        sku.reserved_quantity += item.quantity

    await db.flush()

    items_response = []
    out_of_stock_skus = []
    for item in body.items:
        sku = skus[item.sku_id]
        remaining = sku.active_quantity
        items_response.append({
            "sku_id": item.sku_id,
            "reserved_quantity": item.quantity,
            "remaining_stock": remaining,
        })
        if remaining == 0:
            out_of_stock_skus.append(item.sku_id)

    response_data = {"reserved": True, "items": items_response}

    op = ReserveOperation(
        idempotency_key=body.idempotency_key,
        result=response_data,
    )
    db.add(op)
    await db.commit()

    for sku_id in out_of_stock_skus:
        await send_sku_out_of_stock(sku_id)

    return response_data


@router.post(
    "/unreserve",
    response_model=UnreserveResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Снято"},
        401: {"model": ErrorResponse},
    },
)
async def unreserve_inventory(
    body: UnreserveRequest,
    _key: str = Depends(verify_service_key),
    db: AsyncSession = Depends(get_db),
):
    sku_ids = [item.sku_id for item in body.items]
    result = await db.execute(
        select(SKU).where(SKU.id.in_(sku_ids)).with_for_update()
    )
    skus = {sku.id: sku for sku in result.scalars().all()}

    for item in body.items:
        if item.sku_id not in skus:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "SKU not found"},
            )

    for item in body.items:
        sku = skus[item.sku_id]
        sku.active_quantity += item.quantity
        sku.reserved_quantity -= item.quantity

    await db.commit()

    return {"ok": True}
