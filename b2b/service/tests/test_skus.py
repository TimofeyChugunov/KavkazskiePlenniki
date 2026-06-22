import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Product


@pytest_asyncio.fixture
async def test_product(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
        category_id=test_category.id,
        title="iPhone 15 Pro Max",
        slug="iphone-15-pro-max",
        description="Flagman",
        status="CREATED",
        deleted=False,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def hard_blocked_product(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
        category_id=test_category.id,
        title="Blocked Product",
        slug="blocked-product",
        description="Blocked",
        status="HARD_BLOCKED",
        deleted=False,
        blocked=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
def seller_auth() -> dict:
    token = __import__("app.auth", fromlist=["create_access_token"]).create_access_token(
        {"sub": "test-seller-id"}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
async def test_first_sku_transitions_product_to_on_moderation(
    mock_event: AsyncMock,
    client: AsyncClient,
    test_product: Product,
    seller_auth: dict,
):
    response = await client.post(
        "/api/v1/skus",
        json={
            "product_id": test_product.id,
            "name": "256GB Black",
            "price": 12999000,
            "cost_price": 9500000,
            "discount": 0,
            "image": "/s3/iphone15-black-256.jpg",
            "characteristics": [{"name": "Цвет", "value": "Чёрный"}],
        },
        headers=seller_auth,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "256GB Black"
    assert data["price"] == 12999000
    assert data["cost_price"] == 9500000
    assert data["discount"] == 0
    assert data["image"] == "/s3/iphone15-black-256.jpg"
    assert data["active_quantity"] == 0
    assert data["reserved_quantity"] == 0
    assert len(data["characteristics"]) == 1
    assert data["characteristics"][0]["name"] == "Цвет"


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
async def test_first_sku_emits_created_event_to_moderation(
    mock_event: AsyncMock,
    client: AsyncClient,
    test_product: Product,
    seller_auth: dict,
):
    await client.post(
        "/api/v1/skus",
        json={
            "product_id": test_product.id,
            "name": "256GB Black",
            "price": 12999000,
            "cost_price": 9500000,
            "discount": 0,
            "image": "/s3/iphone15-black-256.jpg",
        },
        headers=seller_auth,
    )

    mock_event.assert_called_once()
    call_args = mock_event.call_args
    assert call_args.kwargs["product_id"] == test_product.id
    assert call_args.kwargs["seller_id"] == "test-seller-id"
    assert call_args.kwargs["event"] == "CREATED"


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
async def test_second_sku_no_state_change(
    mock_event: AsyncMock,
    client: AsyncClient,
    test_product: Product,
    seller_auth: dict,
    db_session: AsyncSession,
):
    from app.models import SKU

    sku1 = SKU(
        product_id=test_product.id,
        name="First SKU",
        price=1000,
        cost_price=500,
        image="/s3/first.jpg",
    )
    db_session.add(sku1)
    test_product.status = "ON_MODERATION"
    await db_session.commit()

    response = await client.post(
        "/api/v1/skus",
        json={
            "product_id": test_product.id,
            "name": "Second SKU",
            "price": 2000,
            "cost_price": 1000,
            "image": "/s3/second.jpg",
        },
        headers=seller_auth,
    )

    assert response.status_code == 201
    mock_event.assert_not_called()

    await db_session.refresh(test_product)
    assert test_product.status == "ON_MODERATION"


@pytest.mark.asyncio
async def test_add_sku_to_hard_blocked_returns_403(
    client: AsyncClient,
    hard_blocked_product: Product,
    seller_auth: dict,
):
    response = await client.post(
        "/api/v1/skus",
        json={
            "product_id": hard_blocked_product.id,
            "name": "256GB Black",
            "price": 12999000,
            "cost_price": 9500000,
            "image": "/s3/iphone15-black-256.jpg",
        },
        headers=seller_auth,
    )

    assert response.status_code == 403
    data = response.json()
    assert data["code"] == "FORBIDDEN"
    assert "hard-blocked" in data["message"].lower()


@pytest.mark.asyncio
async def test_add_sku_to_nonexistent_product_returns_404(
    client: AsyncClient,
    seller_auth: dict,
):
    response = await client.post(
        "/api/v1/skus",
        json={
            "product_id": str(uuid.uuid4()),
            "name": "256GB Black",
            "price": 12999000,
            "cost_price": 9500000,
            "image": "/s3/iphone15-black-256.jpg",
        },
        headers=seller_auth,
    )

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "NOT_FOUND"
    assert "Product not found" in data["message"]


@pytest.mark.asyncio
async def test_missing_image_returns_400(
    client: AsyncClient,
    test_product: Product,
    seller_auth: dict,
):
    response = await client.post(
        "/api/v1/skus",
        json={
            "product_id": test_product.id,
            "name": "256GB Black",
            "price": 12999000,
            "cost_price": 9500000,
        },
        headers=seller_auth,
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "image" in data["message"].lower()
