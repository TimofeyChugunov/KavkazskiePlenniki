from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from jose import jwt
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.config import settings

USER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SESSION_ID = "11111111-2222-3333-4444-555555555555"
SKU_ID_1 = "sku-00000001-0000-0000-0000-000000000001"
SKU_ID_2 = "sku-00000002-0000-0000-0000-000000000002"
PRODUCT_ID_1 = "770e8400-e29b-41d4-a716-446655440002"
PRODUCT_ID_2 = "770e8400-e29b-41d4-a716-446655440003"


def make_token(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc).timestamp() + 3600},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def auth_header(user_id: str = USER_ID) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id)}"}


def session_header(session_id: str = SESSION_ID) -> dict:
    return {"X-Session-Id": session_id}


SAMPLE_B2B_PRODUCT = {
    "id": PRODUCT_ID_1,
    "title": "iPhone 15 Pro Max",
    "description": "Flagship smartphone",
    "status": "MODERATED",
    "images": [{"id": "img-1", "url": "https://cdn.neomarket.ru/images/iphone15.jpg", "ordering": 0}],
    "skus": [
        {
            "id": SKU_ID_1,
            "product_id": PRODUCT_ID_1,
            "name": "256GB Black",
            "price": 12999000,
            "discount": 0,
            "active_quantity": 10,
            "characteristics": [],
        }
    ],
}

SAMPLE_B2B_PRODUCT_2 = {
    "id": PRODUCT_ID_2,
    "title": "Samsung Galaxy S24",
    "description": "Android flagship",
    "status": "MODERATED",
    "images": [{"id": "img-2", "url": "https://cdn.neomarket.ru/images/s24.jpg", "ordering": 0}],
    "skus": [
        {
            "id": SKU_ID_2,
            "product_id": PRODUCT_ID_2,
            "name": "128GB White",
            "price": 8999000,
            "discount": 0,
            "active_quantity": 5,
            "characteristics": [],
        }
    ],
}

SAMPLE_B2B_SKU = {
    "id": SKU_ID_1,
    "product_id": PRODUCT_ID_1,
    "name": "256GB Black",
    "price": 12999000,
    "discount": 0,
    "active_quantity": 10,
    "stock_quantity": 15,
    "article": "IPH-15PM-256-BLK",
    "images": [],
    "characteristics": [],
}

SAMPLE_B2B_SKU_LOW_STOCK = {
    "id": SKU_ID_1,
    "product_id": PRODUCT_ID_1,
    "name": "256GB Black",
    "price": 12999000,
    "discount": 0,
    "active_quantity": 2,
    "stock_quantity": 5,
    "article": "IPH-15PM-256-BLK",
    "images": [],
    "characteristics": [],
}


@pytest_asyncio.fixture(autouse=True)
async def clean_cart():
    import app.routers.cart as cart_module
    cart_module._cart_db.clear()
    yield
    cart_module._cart_db.clear()


def mock_b2b_batch(products: list[dict]) -> AsyncMock:
    mock = AsyncMock()
    mock.return_value = {p["id"]: p for p in products}
    return mock


def mock_b2b_sku(sku_data: dict) -> AsyncMock:
    mock = AsyncMock()
    mock.return_value = (sku_data, None)
    return mock


# === 4 REQUIRED TESTS ===


@pytest.mark.asyncio
async def test_add_sku_increments_quantity_if_already_in_cart(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            resp1 = await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 2},
                headers={**auth_header(), **session_header()},
            )
            assert resp1.status_code == 201

    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            resp2 = await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 3},
                headers={**auth_header(), **session_header()},
            )
            assert resp2.status_code == 200

    data = resp2.json()
    items = data["items"]
    assert len(items) == 1
    assert items[0]["sku_id"] == SKU_ID_1
    assert items[0]["quantity"] == 5
    assert items[0]["unit_price"] == 12999000
    assert items[0]["line_total"] == 12999000 * 5
    assert data["subtotal"] == 12999000 * 5
    assert data["summary"]["total_amount"] == 12999000 * 5
    assert data["summary"]["total_items"] == 5


@pytest.mark.asyncio
async def test_get_cart_enriched_with_b2b_data(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 2},
                headers={**auth_header(), **session_header()},
            )

    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
        resp = await client.get(
            "/api/v1/cart",
            headers={**auth_header(), **session_header()},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["sku_id"] == SKU_ID_1
    assert item["product_id"] == PRODUCT_ID_1
    assert item["name"] == "iPhone 15 Pro Max / 256GB Black"
    assert item["unit_price"] == 12999000
    assert item["line_total"] == 12999000 * 2
    assert item["is_available"] is True
    assert item["available_quantity"] == 10
    assert item["unavailable_reason"] is None
    assert data["subtotal"] == 12999000 * 2
    assert data["items_count"] == 2
    assert data["is_valid"] is True
    assert data["summary"]["total_amount"] == 12999000 * 2
    assert data["summary"]["total_items"] == 2
    assert data["summary"]["unavailable_count"] == 0
    assert data["summary"]["checkout_ready"] is True
    assert len(data["checkout_payload"]["items"]) == 1
    assert data["checkout_payload"]["items"][0]["sku_id"] == SKU_ID_1


@pytest.mark.asyncio
async def test_unavailable_sku_shown_with_reason(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 2},
                headers={**auth_header(), **session_header()},
            )

    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([])):
        resp = await client.get(
            "/api/v1/cart",
            headers={**auth_header(), **session_header()},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["is_available"] is False
    assert item["unavailable_reason"] == "PRODUCT_DELETED"
    assert item["line_total"] == 0
    assert data["subtotal"] == 0
    assert data["is_valid"] is False
    assert data["summary"]["total_amount"] == 0
    assert data["summary"]["unavailable_count"] == 1
    assert data["summary"]["checkout_ready"] is False


@pytest.mark.asyncio
async def test_guest_cart_merged_on_login(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 2},
                headers=session_header(),
            )

    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 1},
                headers={**auth_header(), **session_header()},
            )

    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
        resp = await client.post(
            "/api/v1/cart/merge",
            headers={**auth_header(), **session_header()},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["quantity"] == 2
    assert data["items"][0]["sku_id"] == SKU_ID_1

    import app.routers.cart as cart_module
    guest_key = f"session:{SESSION_ID}"
    assert cart_module._cart_db.get(guest_key, []) == []


# === ADDITIONAL TESTS ===


@pytest.mark.asyncio
async def test_add_new_sku_returns_201(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            resp = await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 1},
                headers={**auth_header(), **session_header()},
            )

    assert resp.status_code == 201
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["sku_id"] == SKU_ID_1
    assert data["items"][0]["quantity"] == 1


@pytest.mark.asyncio
async def test_guest_cart_uses_session_id(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 1},
                headers=session_header(),
            )

    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
        resp = await client.get(
            "/api/v1/cart",
            headers=session_header(),
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["sku_id"] == SKU_ID_1


@pytest.mark.asyncio
async def test_update_cart_item_quantity(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 1},
                headers={**auth_header(), **session_header()},
            )

    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            resp = await client.patch(
                f"/api/v1/cart/items/{SKU_ID_1}",
                json={"quantity": 5},
                headers={**auth_header(), **session_header()},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["items"][0]["quantity"] == 5


@pytest.mark.asyncio
async def test_remove_cart_item(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 1},
                headers={**auth_header(), **session_header()},
            )

    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
        resp = await client.delete(
            f"/api/v1/cart/items/{SKU_ID_1}",
            headers={**auth_header(), **session_header()},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 0
    assert data["subtotal"] == 0


@pytest.mark.asyncio
async def test_clear_cart(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 1},
                headers={**auth_header(), **session_header()},
            )

    resp = await client.delete(
        "/api/v1/cart",
        headers={**auth_header(), **session_header()},
    )
    assert resp.status_code == 204

    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([])):
        resp = await client.get(
            "/api/v1/cart",
            headers={**auth_header(), **session_header()},
        )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_unauthorized_returns_401(client: AsyncClient):
    resp = await client.get("/api/v1/cart")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_empty_cart_returns_empty(client: AsyncClient):
    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([])):
        resp = await client.get(
            "/api/v1/cart",
            headers={**auth_header(), **session_header()},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["subtotal"] == 0
    assert data["is_valid"] is True
    assert data["summary"]["total_amount"] == 0
    assert data["summary"]["checkout_ready"] is False
    assert data["checkout_payload"]["items"] == []


@pytest.mark.asyncio
async def test_cart_user_isolation(client: AsyncClient):
    user_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    user_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 2},
                headers=auth_header(user_a),
            )
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_2, "quantity": 3},
                headers=auth_header(user_b),
            )

    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
        resp_a = await client.get("/api/v1/cart", headers=auth_header(user_a))
    assert resp_a.status_code == 200
    assert len(resp_a.json()["items"]) == 1
    assert resp_a.json()["items"][0]["sku_id"] == SKU_ID_1

    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT_2])):
        resp_b = await client.get("/api/v1/cart", headers=auth_header(user_b))
    assert resp_b.status_code == 200
    assert len(resp_b.json()["items"]) == 1
    assert resp_b.json()["items"][0]["sku_id"] == SKU_ID_2


@pytest.mark.asyncio
async def test_validate_cart_returns_issues(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 2},
                headers={**auth_header(), **session_header()},
            )

    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([])):
        resp = await client.post(
            "/api/v1/cart/validate",
            headers={**auth_header(), **session_header()},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is False
    assert data["can_checkout"] is False
    assert len(data["issues"]) == 1
    assert data["issues"][0]["type"] == "PRODUCT_DELETED"
    assert data["issues"][0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_sku_not_found_returns_404(client: AsyncClient):
    mock = AsyncMock()
    mock.return_value = ({}, "PRODUCT_DELETED")

    with patch("app.routers.cart._validate_sku_from_b2b", mock):
        resp = await client.post(
            "/api/v1/cart/items",
            json={"sku_id": "nonexistent-sku", "quantity": 1},
            headers={**auth_header(), **session_header()},
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "SKU_NOT_FOUND"


@pytest.mark.asyncio
async def test_idor_user_id_in_query_rejected(client: AsyncClient):
    other_user = "11111111-2222-3333-4444-555555555555"

    resp = await client.get(
        "/api/v1/cart",
        headers=auth_header(),
        params={"user_id": other_user},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_idor_session_id_in_query_rejected(client: AsyncClient):
    resp = await client.get(
        "/api/v1/cart",
        headers=session_header(),
        params={"session_id": "some-other-session"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_edge_case3_quantity_exceeds_stock_but_available(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 5},
                headers={**auth_header(), **session_header()},
            )

    low_stock_sku = {**SAMPLE_B2B_SKU, "active_quantity": 3}
    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([{
        **SAMPLE_B2B_PRODUCT,
        "skus": [{**SAMPLE_B2B_PRODUCT["skus"][0], "active_quantity": 3}],
    }])):
        resp = await client.get(
            "/api/v1/cart",
            headers={**auth_header(), **session_header()},
        )

    assert resp.status_code == 200
    data = resp.json()
    item = data["items"][0]
    assert item["is_available"] is True
    assert item["available_quantity"] == 3
    assert item["quantity"] == 5
    assert item["line_total"] == 12999000 * 5
    assert data["subtotal"] == 12999000 * 5


@pytest.mark.asyncio
async def test_validate_cart_can_checkout_when_valid(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 1},
                headers={**auth_header(), **session_header()},
            )

    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
        resp = await client.post(
            "/api/v1/cart/validate",
            headers={**auth_header(), **session_header()},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is True
    assert data["can_checkout"] is True
    assert data["issues"] == []


@pytest.mark.asyncio
async def test_validate_cart_empty_not_checkout(client: AsyncClient):
    resp = await client.post(
        "/api/v1/cart/validate",
        headers={**auth_header(), **session_header()},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is True
    assert data["can_checkout"] is False
    assert data["issues"] == []


@pytest.mark.asyncio
async def test_guest_cart_merge_different_skus(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_1, "quantity": 2},
                headers=session_header(),
            )

    sku2_data = {**SAMPLE_B2B_SKU, "id": SKU_ID_2, "product_id": PRODUCT_ID_2}
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(sku2_data)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT_2])):
            await client.post(
                "/api/v1/cart/items",
                json={"sku_id": SKU_ID_2, "quantity": 3},
                headers={**auth_header(), **session_header()},
            )

    with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT, SAMPLE_B2B_PRODUCT_2])):
        resp = await client.post(
            "/api/v1/cart/merge",
            headers={**auth_header(), **session_header()},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    sku_quantities = {item["sku_id"]: item["quantity"] for item in data["items"]}
    assert sku_quantities[SKU_ID_1] == 2
    assert sku_quantities[SKU_ID_2] == 3


@pytest.mark.asyncio
async def test_patch_nonexistent_sku_returns_404(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            resp = await client.patch(
                f"/api/v1/cart/items/{SKU_ID_1}",
                json={"quantity": 5},
                headers={**auth_header(), **session_header()},
            )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_sku_returns_404(client: AsyncClient):
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(SAMPLE_B2B_SKU)):
        with patch("app.routers.cart._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
            resp = await client.delete(
                f"/api/v1/cart/items/{SKU_ID_1}",
                headers={**auth_header(), **session_header()},
            )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_insufficient_stock_returns_409(client: AsyncClient):
    low_stock = {**SAMPLE_B2B_SKU, "active_quantity": 2}
    with patch("app.routers.cart._validate_sku_from_b2b", mock_b2b_sku(low_stock)):
        resp = await client.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU_ID_1, "quantity": 5},
            headers={**auth_header(), **session_header()},
        )

    assert resp.status_code == 409
    assert resp.json()["code"] == "INSUFFICIENT_STOCK"
