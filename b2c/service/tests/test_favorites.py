from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from jose import jwt
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.config import settings

USER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
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


SAMPLE_PRODUCT = {
    "id": PRODUCT_ID_1,
    "title": "iPhone 15 Pro Max",
    "description": "Flagship smartphone",
    "status": "MODERATED",
    "category": {"id": "cat-1", "name": "Смартфоны"},
    "images": [{"url": "https://cdn.neomarket.ru/images/iphone15.jpg", "ordering": 0}],
    "skus": [
        {
            "id": "sku-1",
            "name": "256GB Black",
            "price": 12999000,
            "discount": 0,
            "active_quantity": 10,
            "characteristics": [],
        }
    ],
}

SAMPLE_PRODUCT_2 = {
    "id": PRODUCT_ID_2,
    "title": "Samsung Galaxy S24",
    "description": "Android flagship",
    "status": "MODERATED",
    "category": {"id": "cat-1", "name": "Смартфоны"},
    "images": [{"url": "https://cdn.neomarket.ru/images/s24.jpg", "ordering": 0}],
    "skus": [
        {
            "id": "sku-2",
            "name": "128GB White",
            "price": 8999000,
            "discount": 500000,
            "active_quantity": 5,
            "characteristics": [],
        }
    ],
}


@pytest_asyncio.fixture(autouse=True)
async def clean_favorites():
    import app.routers.favorites as fav_module

    fav_module._favorites_db.clear()
    yield
    fav_module._favorites_db.clear()


@pytest.mark.asyncio
async def test_add_to_favorites_returns_201(client: AsyncClient):
    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {}

        response = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID_1}",
            headers=auth_header(),
        )

    assert response.status_code == 201
    data = response.json()
    assert data["product_id"] == PRODUCT_ID_1
    assert "added_at" in data


@pytest.mark.asyncio
async def test_repeat_add_returns_200_not_duplicate(client: AsyncClient):
    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {}

        response1 = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID_1}",
            headers=auth_header(),
        )
        assert response1.status_code == 201

        response2 = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID_1}",
            headers=auth_header(),
        )
        assert response2.status_code == 200

    import app.routers.favorites as fav_module

    favorites = fav_module._favorites_db.get(USER_ID, [])
    assert len(favorites) == 1


@pytest.mark.asyncio
async def test_blocked_product_excluded_from_list(client: AsyncClient):
    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {}

        await client.post(
            f"/api/v1/favorites/{PRODUCT_ID_1}",
            headers=auth_header(),
        )

    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {}

        response = await client.get(
            "/api/v1/favorites",
            headers=auth_header(),
        )

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total_count"] == 1


@pytest.mark.asyncio
async def test_user_id_from_query_is_ignored(client: AsyncClient):
    other_user = "11111111-2222-3333-4444-555555555555"

    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {}

        response = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID_1}",
            headers=auth_header(),
            params={"user_id": other_user},
        )

    assert response.status_code == 201

    import app.routers.favorites as fav_module

    assert USER_ID in fav_module._favorites_db
    assert other_user not in fav_module._favorites_db


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_204(client: AsyncClient):
    response = await client.delete(
        f"/api/v1/favorites/{PRODUCT_ID_1}",
        headers=auth_header(),
    )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_existing_returns_204(client: AsyncClient):
    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {}

        await client.post(
            f"/api/v1/favorites/{PRODUCT_ID_1}",
            headers=auth_header(),
        )

    response = await client.delete(
        f"/api/v1/favorites/{PRODUCT_ID_1}",
        headers=auth_header(),
    )
    assert response.status_code == 204

    import app.routers.favorites as fav_module

    assert len(fav_module._favorites_db.get(USER_ID, [])) == 0


@pytest.mark.asyncio
async def test_get_favorites_enriched_from_b2b(client: AsyncClient):
    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {}

        await client.post(
            f"/api/v1/favorites/{PRODUCT_ID_1}",
            headers=auth_header(),
        )

    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {PRODUCT_ID_1: SAMPLE_PRODUCT}

        response = await client.get(
            "/api/v1/favorites",
            headers=auth_header(),
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 1
    assert len(data["items"]) == 1

    item = data["items"][0]
    assert item["id"] == PRODUCT_ID_1
    assert item["name"] == "iPhone 15 Pro Max"
    assert item["min_price"] == 12999000
    assert item["has_stock"] is True
    assert "added_at" in item


@pytest.mark.asyncio
async def test_empty_favorites_returns_200(client: AsyncClient):
    response = await client.get(
        "/api/v1/favorites",
        headers=auth_header(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total_count"] == 0


@pytest.mark.asyncio
async def test_unauthorized_returns_401(client: AsyncClient):
    response = await client.get("/api/v1/favorites")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_returns_401(client: AsyncClient):
    response = await client.get(
        "/api/v1/favorites",
        headers={"Authorization": "Bearer invalid.token.here"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_user_isolation(client: AsyncClient):
    user_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    user_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {}

        await client.post(
            f"/api/v1/favorites/{PRODUCT_ID_1}",
            headers=auth_header(user_a),
        )
        await client.post(
            f"/api/v1/favorites/{PRODUCT_ID_2}",
            headers=auth_header(user_b),
        )

    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {PRODUCT_ID_1: SAMPLE_PRODUCT}

        resp_a = await client.get(
            "/api/v1/favorites",
            headers=auth_header(user_a),
        )

    assert resp_a.status_code == 200
    assert resp_a.json()["total_count"] == 1
    assert resp_a.json()["items"][0]["id"] == PRODUCT_ID_1

    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {PRODUCT_ID_2: SAMPLE_PRODUCT_2}

        resp_b = await client.get(
            "/api/v1/favorites",
            headers=auth_header(user_b),
        )

    assert resp_b.status_code == 200
    assert resp_b.json()["total_count"] == 1
    assert resp_b.json()["items"][0]["id"] == PRODUCT_ID_2


@pytest.mark.asyncio
async def test_pagination(client: AsyncClient):
    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {}

        await client.post(f"/api/v1/favorites/{PRODUCT_ID_1}", headers=auth_header())
        await client.post(f"/api/v1/favorites/{PRODUCT_ID_2}", headers=auth_header())

    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {
            PRODUCT_ID_1: SAMPLE_PRODUCT,
            PRODUCT_ID_2: SAMPLE_PRODUCT_2,
        }

        response = await client.get(
            "/api/v1/favorites",
            headers=auth_header(),
            params={"limit": 1, "offset": 0},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["total_count"] == 2
    assert data["limit"] == 1
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_b2b_unavailable_returns_502(client: AsyncClient):
    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.return_value = {}

        await client.post(f"/api/v1/favorites/{PRODUCT_ID_1}", headers=auth_header())

    with patch("app.routers.favorites._enrich_from_b2b", new_callable=AsyncMock) as mock_enrich:
        mock_enrich.side_effect = Exception("Connection refused")

        response = await client.get(
            "/api/v1/favorites",
            headers=auth_header(),
        )

    assert response.status_code == 502
    assert response.json()["code"] == "B2B_UNAVAILABLE"
