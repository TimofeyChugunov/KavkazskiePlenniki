import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..config import settings

router = APIRouter(prefix="/api/v1", tags=["Orders"])

_orders_db: dict[str, dict] = {}
_B2B_SERVICE_HEADERS = {"X-Service-Key": settings.B2C_TO_B2B_KEY}


class OrderItemRequest(BaseModel):
    sku_id: str = Field(..., min_length=1)
    quantity: int


class OrderCreateRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=1)
    items: list[OrderItemRequest] = Field(..., min_length=1)
    delivery_address: str | None = None


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


async def _fetch_b2b_products(sku_ids: list[str]) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.B2B_URL}/api/v1/public/products/batch",
            headers=_B2B_SERVICE_HEADERS,
            json={"product_ids": sku_ids},
            timeout=10.0,
        )

    if resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "B2B_UNAVAILABLE",
                "message": "Сервис товаров временно недоступен, попробуйте позже",
            },
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "B2B_UNAVAILABLE",
                "message": "Сервис товаров временно недоступен, попробуйте позже",
            },
        )

    products = resp.json()
    return {p["id"]: p for p in products}


def _find_sku_in_products(products: dict, sku_id: str) -> dict | None:
    for product in products.values():
        for sku in product.get("skus", []):
            if sku["id"] == sku_id:
                return {"product": product, "sku": sku}
    return None


async def _call_b2b_reserve(
    idempotency_key: str, items: list[dict]
) -> tuple[bool, dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.B2B_URL}/api/v1/inventory/reserve",
            headers=_B2B_SERVICE_HEADERS,
            json={
                "idempotency_key": idempotency_key,
                "items": items,
            },
            timeout=10.0,
        )

    if resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "B2B_UNAVAILABLE",
                "message": "Сервис товаров временно недоступен, попробуйте позже",
            },
        )

    data = resp.json()

    if resp.status_code == 409:
        return False, data

    if resp.status_code == 200:
        return True, data

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "B2B_UNAVAILABLE",
            "message": "Сервис товаров временно недоступен, попробуйте позже",
        },
    )


@router.post(
    "/orders",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Заказ создан"},
        400: {"description": "Невалидный запрос"},
        401: {"description": "Не авторизован"},
        409: {"description": "Резервирование не удалось"},
        422: {"description": "Невалидное количество"},
        503: {"description": "B2B недоступен"},
    },
)
async def create_order(
    body: OrderCreateRequest,
    request: Request,
):
    user_id = _get_user_id(request)
    idempotency_key = body.idempotency_key

    # 0. Idempotency check
    existing = _orders_db.get(idempotency_key)
    if existing:
        return existing

    # 1. Validation
    if not body.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_REQUEST",
                "message": "Список items не может быть пустым",
            },
        )

    for item in body.items:
        if item.quantity < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "INVALID_QUANTITY",
                    "message": "Количество должно быть не менее 1 для каждой позиции",
                },
            )

    # 2. Fetch products from B2B (get product info by SKU IDs)
    sku_ids = list({item.sku_id for item in body.items})
    try:
        products = await _fetch_b2b_products(sku_ids)
    except HTTPException:
        raise

    # 3. Pre-reserve validation: check product status, availability
    failed_items: list[dict] = []
    sku_info_map: dict[str, dict] = {}

    for item in body.items:
        found = _find_sku_in_products(products, item.sku_id)
        if not found:
            failed_items.append({
                "sku_id": item.sku_id,
                "requested": item.quantity,
                "available": 0,
                "reason": "SKU_NOT_FOUND",
            })
            continue

        product = found["product"]
        sku = found["sku"]

        sku_info_map[item.sku_id] = found

        if product.get("status") == "BLOCKED":
            failed_items.append({
                "sku_id": item.sku_id,
                "requested": item.quantity,
                "available": 0,
                "reason": "PRODUCT_BLOCKED",
            })
            continue

        if product.get("deleted"):
            failed_items.append({
                "sku_id": item.sku_id,
                "requested": item.quantity,
                "available": 0,
                "reason": "PRODUCT_DELETED",
            })
            continue

        active_quantity = sku.get("active_quantity", 0)
        if active_quantity < item.quantity:
            reason = (
                "INSUFFICIENT_STOCK" if active_quantity > 0 else "OUT_OF_STOCK"
            )
            failed_items.append({
                "sku_id": item.sku_id,
                "requested": item.quantity,
                "available": active_quantity,
                "reason": reason,
            })

    if failed_items:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "RESERVE_FAILED",
                "message": "Не удалось зарезервировать товары",
                "failed_items": failed_items,
            },
        )

    # 4. Reserve in B2B (all-or-nothing)
    reserve_items = [
        {"sku_id": item.sku_id, "quantity": item.quantity}
        for item in body.items
    ]
    reserved, reserve_data = await _call_b2b_reserve(idempotency_key, reserve_items)

    if not reserved:
        failed_from_b2b = reserve_data.get("failed_items", [])
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "RESERVE_FAILED",
                "message": "Не удалось зарезервировать товары",
                "failed_items": failed_from_b2b,
            },
        )

    # 5. Create order with fixed prices
    now = datetime.now(timezone.utc)
    order_id = str(uuid.uuid4())
    order_items = []
    total_amount = 0

    for item in body.items:
        found = sku_info_map[item.sku_id]
        sku = found["sku"]
        product = found["product"]
        unit_price = sku["price"]
        line_total = unit_price * item.quantity
        total_amount += line_total

        order_items.append({
            "id": str(uuid.uuid4()),
            "sku_id": item.sku_id,
            "product_id": product["id"],
            "product_title": product.get("title", ""),
            "sku_name": sku.get("name", ""),
            "quantity": item.quantity,
            "unit_price": unit_price,
            "line_total": line_total,
        })

    order = {
        "id": order_id,
        "status": "PAID",
        "items": order_items,
        "total_amount": total_amount,
        "delivery_address": body.delivery_address,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    _orders_db[idempotency_key] = order

    return order


@router.get(
    "/orders",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Список заказов"},
        401: {"description": "Не авторизован"},
    },
)
async def list_orders(
    request: Request,
    limit: int = 20,
    offset: int = 0,
):
    _get_user_id(request)

    all_orders = list(_orders_db.values())
    total = len(all_orders)
    page = all_orders[offset : offset + limit]

    return {
        "items": page,
        "total_count": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/orders/{order_id}",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Заказ"},
        401: {"description": "Не авторизован"},
        404: {"description": "Заказ не найден"},
    },
)
async def get_order(
    order_id: str,
    request: Request,
):
    _get_user_id(request)

    for order in _orders_db.values():
        if order["id"] == order_id:
            return order

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "NOT_FOUND", "message": "Заказ не найден"},
    )
