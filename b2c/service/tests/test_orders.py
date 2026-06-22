from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from jose import jwt
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.config import settings

USER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SKU_ID_1 = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SKU_ID_2 = "8a4e3f9c-1a2b-4c8d-9e5f-6b7a8c9d0e1f"
PRODUCT_ID_1 = "550e8400-e29b-41d4-a716-446655440000"
PRODUCT_ID_2 = "660e8400-e29b-41d4-a716-446655440001"
IDEMPOTENCY_KEY = "f47ac10b-58cc-4372-a567-0e02b2c3d479"


def make_token(user_id: str = USER_ID) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc).timestamp() + 3600},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def auth_header(user_id: str = USER_ID) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id)}"}


SAMPLE_B2B_PRODUCT_1 = {
    "id": PRODUCT_ID_1,
    "title": "iPhone 15 Pro Max",
    "description": "Flagship smartphone",
    "status": "MODERATED",
    "deleted": False,
    "images": [],
    "characteristics": [],
    "skus": [
        {
            "id": SKU_ID_1,
            "name": "256GB Black",
            "price": 12999000,
            "active_quantity": 10,
            "discount": 0,
            "characteristics": [],
        }
    ],
}

SAMPLE_B2B_PRODUCT_2 = {
    "id": PRODUCT_ID_2,
    "title": "iPhone 15 Pro Max",
    "description": "Flagship smartphone",
    "status": "MODERATED",
    "deleted": False,
    "images": [],
    "characteristics": [],
    "skus": [
        {
            "id": SKU_ID_2,
            "name": "256GB White",
            "price": 12999000,
            "active_quantity": 5,
            "discount": 0,
            "characteristics": [],
        }
    ],
}

SAMPLE_B2B_RESERVE_SUCCESS = {
    "reserved": True,
    "items": [
        {
            "sku_id": SKU_ID_1,
            "reserved_quantity": 2,
            "remaining_stock": 8,
        },
        {
            "sku_id": SKU_ID_2,
            "reserved_quantity": 1,
            "remaining_stock": 4,
        },
    ],
}

SAMPLE_B2B_RESERVE_SINGLE_SUCCESS = {
    "reserved": True,
    "items": [
        {
            "sku_id": SKU_ID_1,
            "reserved_quantity": 2,
            "remaining_stock": 8,
        }
    ],
}

SAMPLE_B2B_RESERVE_FAIL = {
    "reserved": False,
    "failed_items": [
        {
            "sku_id": SKU_ID_2,
            "requested": 1,
            "available": 0,
            "reason": "INSUFFICIENT_STOCK",
        }
    ],
}

BLOCKED_PRODUCT = {
    "id": PRODUCT_ID_1,
    "title": "Blocked Product",
    "description": "Blocked",
    "status": "BLOCKED",
    "deleted": False,
    "images": [],
    "characteristics": [],
    "skus": [
        {
            "id": SKU_ID_1,
            "name": "256GB Black",
            "price": 12999000,
            "active_quantity": 10,
            "discount": 0,
            "characteristics": [],
        }
    ],
}

DELETED_PRODUCT = {
    "id": PRODUCT_ID_1,
    "title": "Deleted Product",
    "description": "Deleted",
    "status": "MODERATED",
    "deleted": True,
    "images": [],
    "characteristics": [],
    "skus": [
        {
            "id": SKU_ID_1,
            "name": "256GB Black",
            "price": 12999000,
            "active_quantity": 10,
            "discount": 0,
            "characteristics": [],
        }
    ],
}

LOW_STOCK_PRODUCT = {
    "id": PRODUCT_ID_1,
    "title": "Low Stock Product",
    "description": "Low stock",
    "status": "MODERATED",
    "deleted": False,
    "images": [],
    "characteristics": [],
    "skus": [
        {
            "id": SKU_ID_1,
            "name": "256GB Black",
            "price": 12999000,
            "active_quantity": 1,
            "discount": 0,
            "characteristics": [],
        }
    ],
}

OUT_OF_STOCK_PRODUCT = {
    "id": PRODUCT_ID_1,
    "title": "Out of Stock Product",
    "description": "Out of stock",
    "status": "MODERATED",
    "deleted": False,
    "images": [],
    "characteristics": [],
    "skus": [
        {
            "id": SKU_ID_1,
            "name": "256GB Black",
            "price": 12999000,
            "active_quantity": 0,
            "discount": 0,
            "characteristics": [],
        }
    ],
}


@pytest_asyncio.fixture(autouse=True)
async def clean_orders():
    import app.routers.orders as orders_module
    orders_module._orders_db.clear()
    yield
    orders_module._orders_db.clear()


def mock_fetch_products(products_map: dict[str, dict]) -> AsyncMock:
    mock = AsyncMock()
    mock.return_value = products_map
    return mock


def mock_reserve_success(data: dict = None) -> AsyncMock:
    mock = AsyncMock()
    mock.return_value = (True, data or SAMPLE_B2B_RESERVE_SINGLE_SUCCESS)
    return mock


def mock_reserve_fail(data: dict = None) -> AsyncMock:
    mock = AsyncMock()
    mock.return_value = (False, data or SAMPLE_B2B_RESERVE_FAIL)
    return mock


def mock_b2b_503_response() -> AsyncMock:
    mock = AsyncMock()

    async def _raise(*args, **kwargs):
        from fastapi import HTTPException
        from starlette import status as starlette_status
        raise HTTPException(
            status_code=starlette_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "B2B_UNAVAILABLE",
                "message": "Сервис товаров временно недоступен, попробуйте позже",
            },
        )

    mock.side_effect = _raise
    return mock


# === REQUIRED 4 TESTS ===


@pytest.mark.asyncio
async def test_checkout_creates_paid_order_with_fixed_prices(client: AsyncClient):
    products = {
        SKU_ID_1: {"id": PRODUCT_ID_1, **SAMPLE_B2B_PRODUCT_1},
        SKU_ID_2: {"id": PRODUCT_ID_2, **SAMPLE_B2B_PRODUCT_2},
    }

    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_fetch_products(products),
    ):
        with patch(
            "app.routers.orders._call_b2b_reserve",
            mock_reserve_success(SAMPLE_B2B_RESERVE_SUCCESS),
        ):
            resp = await client.post(
                "/api/v1/orders",
                json={
                    "idempotency_key": IDEMPOTENCY_KEY,
                    "items": [
                        {"sku_id": SKU_ID_1, "quantity": 2},
                        {"sku_id": SKU_ID_2, "quantity": 1},
                    ],
                    "delivery_address": "г. Екатеринбург, ул. Мира 19, кв. 42",
                },
                headers=auth_header(),
            )

    assert resp.status_code == 201
    data = resp.json()

    assert data["status"] == "PAID"
    assert len(data["items"]) == 2
    assert data["total_amount"] == 12999000 * 2 + 12999000 * 1
    assert data["delivery_address"] == "г. Екатеринбург, ул. Мира 19, кв. 42"

    item1 = next(i for i in data["items"] if i["sku_id"] == SKU_ID_1)
    assert item1["product_title"] == "iPhone 15 Pro Max"
    assert item1["sku_name"] == "256GB Black"
    assert item1["quantity"] == 2
    assert item1["unit_price"] == 12999000
    assert item1["line_total"] == 12999000 * 2
    assert item1["product_id"] == PRODUCT_ID_1

    item2 = next(i for i in data["items"] if i["sku_id"] == SKU_ID_2)
    assert item2["product_title"] == "iPhone 15 Pro Max"
    assert item2["sku_name"] == "256GB White"
    assert item2["quantity"] == 1
    assert item2["unit_price"] == 12999000
    assert item2["line_total"] == 12999000 * 1

    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_partial_reserve_failure_returns_409(client: AsyncClient):
    products = {
        SKU_ID_1: {"id": PRODUCT_ID_1, **SAMPLE_B2B_PRODUCT_1},
        SKU_ID_2: {"id": PRODUCT_ID_2, **SAMPLE_B2B_PRODUCT_2},
    }

    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_fetch_products(products),
    ):
        with patch(
            "app.routers.orders._call_b2b_reserve",
            mock_reserve_fail(SAMPLE_B2B_RESERVE_FAIL),
        ):
            resp = await client.post(
                "/api/v1/orders",
                json={
                    "idempotency_key": IDEMPOTENCY_KEY,
                    "items": [
                        {"sku_id": SKU_ID_1, "quantity": 2},
                        {"sku_id": SKU_ID_2, "quantity": 1},
                    ],
                },
                headers=auth_header(),
            )

    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "RESERVE_FAILED"
    assert "failed_items" in data
    assert len(data["failed_items"]) == 1
    assert data["failed_items"][0]["sku_id"] == SKU_ID_2
    assert data["failed_items"][0]["reason"] == "INSUFFICIENT_STOCK"


@pytest.mark.asyncio
async def test_idempotency_returns_existing_order(client: AsyncClient):
    products = {
        SKU_ID_1: {"id": PRODUCT_ID_1, **SAMPLE_B2B_PRODUCT_1},
    }

    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_fetch_products(products),
    ):
        with patch(
            "app.routers.orders._call_b2b_reserve",
            mock_reserve_success(SAMPLE_B2B_RESERVE_SINGLE_SUCCESS),
        ):
            resp1 = await client.post(
                "/api/v1/orders",
                json={
                    "idempotency_key": IDEMPOTENCY_KEY,
                    "items": [
                        {"sku_id": SKU_ID_1, "quantity": 2},
                    ],
                },
                headers=auth_header(),
            )

    assert resp1.status_code == 201
    first_order = resp1.json()

    resp2 = await client.post(
        "/api/v1/orders",
        json={
            "idempotency_key": IDEMPOTENCY_KEY,
            "items": [
                {"sku_id": SKU_ID_1, "quantity": 2},
            ],
        },
        headers=auth_header(),
    )

    assert resp2.status_code == 201
    second_order = resp2.json()
    assert first_order["id"] == second_order["id"]
    assert first_order["total_amount"] == second_order["total_amount"]
    assert first_order["created_at"] == second_order["created_at"]


@pytest.mark.asyncio
async def test_b2b_unavailable_returns_503(client: AsyncClient):
    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_b2b_503_response(),
    ):
        resp = await client.post(
            "/api/v1/orders",
            json={
                "idempotency_key": IDEMPOTENCY_KEY,
                "items": [
                    {"sku_id": SKU_ID_1, "quantity": 2},
                ],
            },
            headers=auth_header(),
        )

    assert resp.status_code == 503
    data = resp.json()
    assert data["code"] == "B2B_UNAVAILABLE"


# === ADDITIONAL TESTS ===


@pytest.mark.asyncio
async def test_unauthorized_returns_401(client: AsyncClient):
    resp = await client.post(
        "/api/v1/orders",
        json={
            "idempotency_key": IDEMPOTENCY_KEY,
            "items": [{"sku_id": SKU_ID_1, "quantity": 1}],
        },
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_empty_items_returns_400(client: AsyncClient):
    resp = await client.post(
        "/api/v1/orders",
        json={
            "idempotency_key": IDEMPOTENCY_KEY,
            "items": [],
        },
        headers=auth_header(),
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_invalid_quantity_returns_422(client: AsyncClient):
    resp = await client.post(
        "/api/v1/orders",
        json={
            "idempotency_key": IDEMPOTENCY_KEY,
            "items": [{"sku_id": SKU_ID_1, "quantity": 0}],
        },
        headers=auth_header(),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_QUANTITY"


@pytest.mark.asyncio
async def test_blocked_product_returns_409(client: AsyncClient):
    products = {SKU_ID_1: BLOCKED_PRODUCT}

    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_fetch_products(products),
    ):
        resp = await client.post(
            "/api/v1/orders",
            json={
                "idempotency_key": IDEMPOTENCY_KEY,
                "items": [{"sku_id": SKU_ID_1, "quantity": 1}],
            },
            headers=auth_header(),
        )

    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "RESERVE_FAILED"
    assert data["failed_items"][0]["reason"] == "PRODUCT_BLOCKED"


@pytest.mark.asyncio
async def test_deleted_product_returns_409(client: AsyncClient):
    products = {SKU_ID_1: DELETED_PRODUCT}

    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_fetch_products(products),
    ):
        resp = await client.post(
            "/api/v1/orders",
            json={
                "idempotency_key": IDEMPOTENCY_KEY,
                "items": [{"sku_id": SKU_ID_1, "quantity": 1}],
            },
            headers=auth_header(),
        )

    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "RESERVE_FAILED"
    assert data["failed_items"][0]["reason"] == "PRODUCT_DELETED"


@pytest.mark.asyncio
async def test_insufficient_stock_returns_409(client: AsyncClient):
    products = {SKU_ID_1: LOW_STOCK_PRODUCT}

    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_fetch_products(products),
    ):
        resp = await client.post(
            "/api/v1/orders",
            json={
                "idempotency_key": IDEMPOTENCY_KEY,
                "items": [{"sku_id": SKU_ID_1, "quantity": 5}],
            },
            headers=auth_header(),
        )

    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "RESERVE_FAILED"
    assert data["failed_items"][0]["reason"] == "INSUFFICIENT_STOCK"
    assert data["failed_items"][0]["available"] == 1
    assert data["failed_items"][0]["requested"] == 5


@pytest.mark.asyncio
async def test_out_of_stock_returns_409(client: AsyncClient):
    products = {SKU_ID_1: OUT_OF_STOCK_PRODUCT}

    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_fetch_products(products),
    ):
        resp = await client.post(
            "/api/v1/orders",
            json={
                "idempotency_key": IDEMPOTENCY_KEY,
                "items": [{"sku_id": SKU_ID_1, "quantity": 1}],
            },
            headers=auth_header(),
        )

    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "RESERVE_FAILED"
    assert data["failed_items"][0]["reason"] == "OUT_OF_STOCK"
    assert data["failed_items"][0]["available"] == 0


@pytest.mark.asyncio
async def test_sku_not_found_returns_409(client: AsyncClient):
    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_fetch_products({}),
    ):
        resp = await client.post(
            "/api/v1/orders",
            json={
                "idempotency_key": IDEMPOTENCY_KEY,
                "items": [{"sku_id": SKU_ID_1, "quantity": 1}],
            },
            headers=auth_header(),
        )

    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "RESERVE_FAILED"
    assert data["failed_items"][0]["reason"] == "SKU_NOT_FOUND"


@pytest.mark.asyncio
async def test_missing_idempotency_key_returns_400(client: AsyncClient):
    resp = await client.post(
        "/api/v1/orders",
        json={
            "items": [{"sku_id": SKU_ID_1, "quantity": 1}],
        },
        headers=auth_header(),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delivery_address_is_optional(client: AsyncClient):
    products = {SKU_ID_1: SAMPLE_B2B_PRODUCT_1}

    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_fetch_products(products),
    ):
        with patch(
            "app.routers.orders._call_b2b_reserve",
            mock_reserve_success(SAMPLE_B2B_RESERVE_SINGLE_SUCCESS),
        ):
            resp = await client.post(
                "/api/v1/orders",
                json={
                    "idempotency_key": IDEMPOTENCY_KEY,
                    "items": [{"sku_id": SKU_ID_1, "quantity": 1}],
                },
                headers=auth_header(),
            )

    assert resp.status_code == 201
    data = resp.json()
    assert data["delivery_address"] is None


@pytest.mark.asyncio
async def test_get_order_by_id(client: AsyncClient):
    products = {SKU_ID_1: SAMPLE_B2B_PRODUCT_1}

    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_fetch_products(products),
    ):
        with patch(
            "app.routers.orders._call_b2b_reserve",
            mock_reserve_success(SAMPLE_B2B_RESERVE_SINGLE_SUCCESS),
        ):
            create_resp = await client.post(
                "/api/v1/orders",
                json={
                    "idempotency_key": IDEMPOTENCY_KEY,
                    "items": [{"sku_id": SKU_ID_1, "quantity": 2}],
                },
                headers=auth_header(),
            )

    assert create_resp.status_code == 201
    order_id = create_resp.json()["id"]

    get_resp = await client.get(
        f"/api/v1/orders/{order_id}",
        headers=auth_header(),
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == order_id
    assert get_resp.json()["status"] == "PAID"


@pytest.mark.asyncio
async def test_get_nonexistent_order_returns_404(client: AsyncClient):
    resp = await client.get(
        "/api/v1/orders/nonexistent-id",
        headers=auth_header(),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_orders_returns_created_orders(client: AsyncClient):
    products = {SKU_ID_1: SAMPLE_B2B_PRODUCT_1}

    with patch(
        "app.routers.orders._fetch_b2b_products",
        mock_fetch_products(products),
    ):
        with patch(
            "app.routers.orders._call_b2b_reserve",
            mock_reserve_success(SAMPLE_B2B_RESERVE_SINGLE_SUCCESS),
        ):
            await client.post(
                "/api/v1/orders",
                json={
                    "idempotency_key": "key-1",
                    "items": [{"sku_id": SKU_ID_1, "quantity": 1}],
                },
                headers=auth_header(),
            )
            await client.post(
                "/api/v1/orders",
                json={
                    "idempotency_key": "key-2",
                    "items": [{"sku_id": SKU_ID_1, "quantity": 2}],
                },
                headers=auth_header(),
            )

    resp = await client.get(
        "/api/v1/orders",
        headers=auth_header(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 2
    assert len(data["items"]) == 2
