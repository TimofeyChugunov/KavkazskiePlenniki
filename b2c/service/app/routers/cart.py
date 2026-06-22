from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..config import settings

router = APIRouter(prefix="/api/v1", tags=["Cart"])

_cart_db: dict[str, list[dict]] = {}
_B2B_SERVICE_HEADERS = {"X-Service-Key": settings.B2C_TO_B2B_KEY}


def _check_no_idor_params(request: Request):
    for key in ("user_id", "session_id"):
        if key in request.query_params:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_REQUEST", "message": f"Parameter '{key}' is not allowed"},
            )


def _identity_key(request: Request) -> tuple[str, str]:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        from jose import jwt, JWTError

        try:
            payload = jwt.decode(
                auth[7:],
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
            )
            sub = payload.get("sub")
            if sub:
                return ("user", sub)
        except JWTError:
            pass

    session_id = request.headers.get("x-session-id")
    if session_id:
        return ("session", session_id)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "UNAUTHORIZED", "message": "Authentication required: provide Authorization header or X-Session-Id"},
    )


def _get_cart(identity_key: str) -> list[dict]:
    return _cart_db.setdefault(identity_key, [])


class CartItemAddRequest(BaseModel):
    sku_id: str
    quantity: int = Field(ge=1)


class CartItemUpdateRequest(BaseModel):
    quantity: int = Field(ge=1)


async def _fetch_b2b_batch(product_ids: list[str]) -> dict[str, dict]:
    import httpx

    if not product_ids:
        return {}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.B2B_URL}/api/v1/public/products/batch",
            headers=_B2B_SERVICE_HEADERS,
            json={"product_ids": product_ids},
            timeout=10.0,
        )

    if resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service temporarily unavailable"},
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "B2B_ERROR", "message": f"B2B returned {resp.status_code}"},
        )

    data = resp.json()
    result: dict[str, dict] = {}
    for p in data:
        result[p["id"]] = p
    return result


async def _validate_sku_from_b2b(sku_id: str) -> tuple[dict, str | None]:
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.B2B_URL}/api/v1/public/skus/{sku_id}",
            headers=_B2B_SERVICE_HEADERS,
            timeout=10.0,
        )

    if resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "B2B_UNAVAILABLE", "message": "B2B service temporarily unavailable"},
        )
    if resp.status_code == 404:
        return {}, "PRODUCT_DELETED"
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "B2B_ERROR", "message": f"B2B returned {resp.status_code}"},
        )

    return resp.json(), None


def _enrich_cart_item(cart_item: dict, b2b_product: dict | None) -> dict:
    sku_id = cart_item["sku_id"]
    quantity = cart_item["quantity"]

    if b2b_product is None:
        return {
            "sku_id": sku_id,
            "product_id": "",
            "name": f"SKU {sku_id[:8]}",
            "quantity": quantity,
            "unit_price": 0,
            "line_total": 0,
            "available_quantity": 0,
            "is_available": False,
            "unavailable_reason": "PRODUCT_DELETED",
            "unit_price_at_add": cart_item.get("unit_price_at_add"),
            "image": None,
        }

    skus = b2b_product.get("skus", [])
    found_sku = None
    for s in skus:
        if s["id"] == sku_id:
            found_sku = s
            break

    if found_sku is None:
        return {
            "sku_id": sku_id,
            "product_id": b2b_product.get("id", ""),
            "name": f"{b2b_product.get('title', 'Product')} / SKU {sku_id[:8]}",
            "quantity": quantity,
            "unit_price": 0,
            "line_total": 0,
            "available_quantity": 0,
            "is_available": False,
            "unavailable_reason": "PRODUCT_DELETED",
            "unit_price_at_add": cart_item.get("unit_price_at_add"),
            "image": None,
        }

    active_quantity = found_sku.get("active_quantity", 0)
    price = found_sku.get("price", 0)

    if active_quantity == 0:
        is_available = False
        reason = "OUT_OF_STOCK"
        line_total = 0
    else:
        is_available = True
        reason = None
        line_total = price * quantity

    images = b2b_product.get("images", [])
    image = images[0] if images else None

    return {
        "sku_id": sku_id,
        "product_id": b2b_product.get("id", ""),
        "name": f"{b2b_product.get('title', 'Product')} / {found_sku.get('name', sku_id[:8])}",
        "sku_code": found_sku.get("article"),
        "quantity": quantity,
        "unit_price": price,
        "unit_price_at_add": cart_item.get("unit_price_at_add"),
        "line_total": line_total,
        "available_quantity": active_quantity,
        "is_available": is_available,
        "unavailable_reason": reason,
        "image": {
            "id": image.get("id", ""),
            "url": image.get("url", ""),
            "alt": image.get("alt", ""),
            "ordering": image.get("ordering", 0),
            "is_main": image.get("is_main", False),
        } if image else None,
    }


async def _build_cart_response(identity_key: str, display_id: str) -> dict:
    cart_items = _cart_db.get(identity_key, [])

    if not cart_items:
        return {
            "id": display_id,
            "items": [],
            "items_count": 0,
            "subtotal": 0,
            "is_valid": True,
            "summary": {
                "total_amount": 0,
                "total_items": 0,
                "unavailable_count": 0,
                "checkout_ready": False,
            },
            "checkout_payload": {"items": []},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    product_ids = list({ci["product_id"] for ci in cart_items if ci.get("product_id")})
    b2b_products: dict[str, dict] = {}
    if product_ids:
        try:
            b2b_products = await _fetch_b2b_batch(product_ids)
        except HTTPException:
            raise

    enriched_items = []
    total_amount = 0
    total_items = 0
    unavailable_count = 0

    for ci in cart_items:
        pid = ci.get("product_id", "")
        b2b_product = b2b_products.get(pid)
        enriched = _enrich_cart_item(ci, b2b_product)
        enriched_items.append(enriched)

        if enriched["is_available"]:
            total_amount += enriched["line_total"]
            total_items += enriched["quantity"]
        else:
            unavailable_count += 1

    is_valid = all(item["is_available"] for item in enriched_items)
    checkout_ready = is_valid and len(enriched_items) > 0

    checkout_items = [
        {"sku_id": item["sku_id"], "quantity": item["quantity"], "unit_price": item["unit_price"]}
        for item in enriched_items if item["is_available"]
    ]

    return {
        "id": display_id,
        "items": enriched_items,
        "items_count": sum(item["quantity"] for item in enriched_items),
        "subtotal": total_amount,
        "is_valid": is_valid,
        "summary": {
            "total_amount": total_amount,
            "total_items": total_items,
            "unavailable_count": unavailable_count,
            "checkout_ready": checkout_ready,
        },
        "checkout_payload": {"items": checkout_items},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/cart")
async def get_cart(request: Request):
    _check_no_idor_params(request)
    id_type, id_value = _identity_key(request)
    identity_key = f"{id_type}:{id_value}"
    return await _build_cart_response(identity_key, id_value)


@router.post("/cart/items", status_code=status.HTTP_201_CREATED)
async def add_to_cart(body: CartItemAddRequest, request: Request):
    _check_no_idor_params(request)
    id_type, id_value = _identity_key(request)
    identity_key = f"{id_type}:{id_value}"
    cart_items = _get_cart(identity_key)

    sku_data, err = await _validate_sku_from_b2b(body.sku_id)
    if err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SKU_NOT_FOUND", "message": "SKU not found or product is deleted"},
        )

    if not sku_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SKU_NOT_FOUND", "message": "SKU not found"},
        )

    product_id = sku_data.get("product_id", "")
    active_quantity = sku_data.get("active_quantity", 0)

    existing = next((ci for ci in cart_items if ci["sku_id"] == body.sku_id), None)
    desired_total = (existing["quantity"] if existing else 0) + body.quantity

    if active_quantity < desired_total:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INSUFFICIENT_STOCK", "message": f"Available: {active_quantity}, requested: {desired_total}"},
        )

    if existing:
        existing["quantity"] = desired_total
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        resp_status = status.HTTP_200_OK
    else:
        cart_items.append({
            "id": str(uuid4()),
            "sku_id": body.sku_id,
            "product_id": product_id,
            "quantity": body.quantity,
            "unit_price_at_add": sku_data.get("price", 0),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        resp_status = status.HTTP_201_CREATED

    return JSONResponse(
        status_code=resp_status,
        content=await _build_cart_response(identity_key, id_value),
    )


@router.patch("/cart/items/{sku_id}")
async def update_cart_item(sku_id: str, body: CartItemUpdateRequest, request: Request):
    _check_no_idor_params(request)
    id_type, id_value = _identity_key(request)
    identity_key = f"{id_type}:{id_value}"
    cart_items = _get_cart(identity_key)

    existing = next((ci for ci in cart_items if ci["sku_id"] == sku_id), None)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Cart item not found"},
        )

    sku_data, _ = await _validate_sku_from_b2b(sku_id)
    if sku_data:
        active_quantity = sku_data.get("active_quantity", 0)
        if active_quantity < body.quantity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "INSUFFICIENT_STOCK", "message": f"Available: {active_quantity}, requested: {body.quantity}"},
            )

    existing["quantity"] = body.quantity
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()

    return await _build_cart_response(identity_key, id_value)


@router.delete("/cart/items/{sku_id}")
async def remove_cart_item(sku_id: str, request: Request):
    _check_no_idor_params(request)
    id_type, id_value = _identity_key(request)
    identity_key = f"{id_type}:{id_value}"
    cart_items = _get_cart(identity_key)

    new_items = [ci for ci in cart_items if ci["sku_id"] != sku_id]
    if len(new_items) == len(cart_items):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Cart item not found"},
        )

    _cart_db[identity_key] = new_items

    return await _build_cart_response(identity_key, id_value)


@router.delete("/cart", status_code=status.HTTP_204_NO_CONTENT)
async def clear_cart(request: Request):
    _check_no_idor_params(request)
    id_type, id_value = _identity_key(request)
    identity_key = f"{id_type}:{id_value}"
    _cart_db[identity_key] = []


@router.post("/cart/validate")
async def validate_cart(request: Request):
    _check_no_idor_params(request)
    id_type, id_value = _identity_key(request)
    identity_key = f"{id_type}:{id_value}"
    cart_items = _get_cart(identity_key)

    if not cart_items:
        return {
            "is_valid": True,
            "can_checkout": False,
            "cart": {
                "id": id_value,
                "items": [],
                "items_count": 0,
                "subtotal": 0,
                "is_valid": True,
                "summary": {
                    "total_amount": 0,
                    "total_items": 0,
                    "unavailable_count": 0,
                    "checkout_ready": False,
                },
                "checkout_payload": {"items": []},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "issues": [],
        }

    product_ids = list({ci["product_id"] for ci in cart_items if ci.get("product_id")})
    b2b_products: dict[str, dict] = {}
    if product_ids:
        try:
            b2b_products = await _fetch_b2b_batch(product_ids)
        except HTTPException:
            raise

    issues = []
    for ci in cart_items:
        pid = ci.get("product_id", "")
        b2b_product = b2b_products.get(pid)
        enriched = _enrich_cart_item(ci, b2b_product)

        if not enriched["is_available"]:
            reason = enriched.get("unavailable_reason", "PRODUCT_DELETED")
            issues.append({
                "sku_id": ci["sku_id"],
                "type": reason,
                "severity": "critical",
                "message": f"SKU {ci['sku_id'][:8]} is unavailable: {reason}",
                "old_value": ci["quantity"],
                "new_value": 0,
            })
        elif enriched["available_quantity"] < ci["quantity"]:
            issues.append({
                "sku_id": ci["sku_id"],
                "type": "QUANTITY_REDUCED",
                "severity": "warning",
                "message": f"Requested {ci['quantity']} but only {enriched['available_quantity']} available",
                "old_value": ci["quantity"],
                "new_value": enriched["available_quantity"],
            })

    cart_response = await _build_cart_response(identity_key, id_value)
    is_valid = len(issues) == 0
    can_checkout = is_valid and len(cart_response["items"]) > 0

    return {
        "is_valid": is_valid,
        "can_checkout": can_checkout,
        "cart": cart_response,
        "issues": issues,
    }


@router.post("/cart/merge")
async def merge_cart(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authorization required for merge"},
        )

    from jose import jwt, JWTError

    try:
        payload = jwt.decode(
            auth[7:],
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHORIZED", "message": "Invalid token: missing sub claim"},
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid token"},
        )

    session_id = request.headers.get("x-session-id")
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": "X-Session-Id header is required for merge"},
        )

    guest_key = f"session:{session_id}"
    auth_key = f"user:{user_id}"

    guest_items = _cart_db.get(guest_key, [])
    auth_items = _cart_db.get(auth_key, [])

    auth_sku_map = {ci["sku_id"]: ci for ci in auth_items}

    for gi in guest_items:
        sku_id = gi["sku_id"]
        if sku_id in auth_sku_map:
            auth_sku_map[sku_id]["quantity"] = max(auth_sku_map[sku_id]["quantity"], gi["quantity"])
            auth_sku_map[sku_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        else:
            gi["user_id"] = user_id
            gi.pop("session_id", None)
            auth_items.append(gi)

    _cart_db[auth_key] = auth_items
    _cart_db[guest_key] = []

    return await _build_cart_response(auth_key, user_id)
