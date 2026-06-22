from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from jose import jwt
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.config import settings

USER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PRODUCT_ID = "770e8400-e29b-41d4-a716-446655440002"


def make_token(user_id: str = USER_ID) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc).timestamp() + 3600},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def auth_header(user_id: str = USER_ID) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id)}"}


@pytest_asyncio.fixture(autouse=True)
async def clean_subscriptions():
    import app.routers.favorites as fav_module
    fav_module._subscriptions_db.clear()
    yield
    fav_module._subscriptions_db.clear()


@pytest.mark.asyncio
async def test_subscribe_returns_201_with_notify_on(client: AsyncClient):
    with patch("app.routers.favorites._check_product_exists", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = True

        response = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_header(),
            json={"notify_on": ["IN_STOCK", "PRICE_DOWN"]},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["product_id"] == PRODUCT_ID
    assert data["notify_on"] == ["IN_STOCK", "PRICE_DOWN"]
    assert "created_at" in data


@pytest.mark.asyncio
async def test_duplicate_subscription_returns_409(client: AsyncClient):
    with patch("app.routers.favorites._check_product_exists", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = True

        response1 = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_header(),
            json={"notify_on": ["IN_STOCK"]},
        )
        assert response1.status_code == 201

        response2 = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_header(),
            json={"notify_on": ["IN_STOCK"]},
        )
        assert response2.status_code == 409
        assert response2.json()["code"] == "SUBSCRIPTION_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_invalid_notify_on_returns_400_empty(client: AsyncClient):
    response = await client.post(
        f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
        headers=auth_header(),
        json={"notify_on": []},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_NOTIFY_ON"


@pytest.mark.asyncio
async def test_invalid_notify_on_returns_400_bad_value(client: AsyncClient):
    response = await client.post(
        f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
        headers=auth_header(),
        json={"notify_on": ["IN_STOCK", "INVALID_EVENT"]},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_NOTIFY_ON"
    assert "INVALID_EVENT" in response.json()["message"]


@pytest.mark.asyncio
async def test_subscribe_to_unknown_product_returns_404(client: AsyncClient):
    with patch("app.routers.favorites._check_product_exists", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = False

        response = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_header(),
            json={"notify_on": ["IN_STOCK"]},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "PRODUCT_NOT_FOUND"


@pytest.mark.asyncio
async def test_unsubscribe_returns_204(client: AsyncClient):
    with patch("app.routers.favorites._check_product_exists", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = True

        await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_header(),
            json={"notify_on": ["IN_STOCK"]},
        )

    response = await client.delete(
        f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
        headers=auth_header(),
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_unsubscribe_nonexistent_returns_204(client: AsyncClient):
    response = await client.delete(
        f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
        headers=auth_header(),
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_subscribe_single_notify_on(client: AsyncClient):
    with patch("app.routers.favorites._check_product_exists", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = True

        response = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_header(),
            json={"notify_on": ["PRICE_DOWN"]},
        )

    assert response.status_code == 201
    assert response.json()["notify_on"] == ["PRICE_DOWN"]


@pytest.mark.asyncio
async def test_subscribe_user_isolation(client: AsyncClient):
    user_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    user_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    with patch("app.routers.favorites._check_product_exists", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = True

        resp_a = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_header(user_a),
            json={"notify_on": ["IN_STOCK"]},
        )
        assert resp_a.status_code == 201

        resp_b = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_header(user_b),
            json={"notify_on": ["IN_STOCK"]},
        )
        assert resp_b.status_code == 201

    import app.routers.favorites as fav_module
    assert user_a in fav_module._subscriptions_db
    assert user_b in fav_module._subscriptions_db
    assert len(fav_module._subscriptions_db[user_a]) == 1
    assert len(fav_module._subscriptions_db[user_b]) == 1


@pytest.mark.asyncio
async def test_unsubscribe_preserves_other_subscriptions(client: AsyncClient):
    product_2 = "770e8400-e29b-41d4-a716-446655440003"

    with patch("app.routers.favorites._check_product_exists", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = True

        await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_header(),
            json={"notify_on": ["IN_STOCK"]},
        )
        await client.post(
            f"/api/v1/favorites/{product_2}/subscribe",
            headers=auth_header(),
            json={"notify_on": ["PRICE_DOWN"]},
        )

    response = await client.delete(
        f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
        headers=auth_header(),
    )
    assert response.status_code == 204

    import app.routers.favorites as fav_module
    subs = fav_module._subscriptions_db.get(USER_ID, {})
    assert PRODUCT_ID not in subs
    assert product_2 in subs


@pytest.mark.asyncio
async def test_unauthorized_returns_401(client: AsyncClient):
    response = await client.post(
        f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
        json={"notify_on": ["IN_STOCK"]},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_subscribe_b2b_unavailable_returns_502(client: AsyncClient):
    with patch("app.routers.favorites._check_product_exists", new_callable=AsyncMock) as mock_check:
        mock_check.side_effect = Exception("Connection refused")

        response = await client.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_header(),
            json={"notify_on": ["IN_STOCK"]},
        )

    assert response.status_code == 502
    assert response.json()["code"] == "B2B_UNAVAILABLE"
