from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import FulfilledOrder, SKU
from ..schemas import ErrorResponse, FulfillRequest, FulfillResponse

router = APIRouter(prefix="/api/v1", tags=["Fulfill"])


async def verify_service_key(x_service_key: str | None = Header(default=None)):
    if not x_service_key or x_service_key != settings.B2C_TO_B2B_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing X-Service-Key"},
        )
    return x_service_key


@router.post(
    "/fulfill",
    response_model=FulfillResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Списано"},
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def fulfill_delivery(
    body: FulfillRequest,
    _key: str = Depends(verify_service_key),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(FulfilledOrder).where(FulfilledOrder.order_id == body.order_id)
    )
    if existing.scalar_one_or_none():
        return FulfillResponse(ok=True)

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
        if sku.reserved_quantity < item.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INSUFFICIENT_RESERVED", "message": f"SKU {item.sku_id} has insufficient reserved quantity"},
            )
        sku.reserved_quantity -= item.quantity

    fulfilled = FulfilledOrder(order_id=body.order_id)
    db.add(fulfilled)

    await db.commit()

    return FulfillResponse(ok=True)
