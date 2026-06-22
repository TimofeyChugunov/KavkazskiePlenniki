import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Product, SKU


@pytest_asyncio.fixture
async def moderated_product(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
        category_id=test_category.id,
        title="Moderated Product",
        slug="moderated-product",
        description="Was moderated",
        status="MODERATED",
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
async def blocked_product(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
        category_id=test_category.id,
        title="Blocked Product",
        slug="blocked-product",
        description="Was blocked",
        status="BLOCKED",
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
async def hard_blocked_product(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
        category_id=test_category.id,
        title="Hard Blocked Product",
        slug="hard-blocked-product",
        description="Hard blocked",
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
async def others_product(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="other-seller-id",
        category_id=test_category.id,
        title="Others Product",
        slug="others-product",
        description="Not mine",
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
async def sku_with_reserves(db_session: AsyncSession, moderated_product: Product) -> SKU:
    sku = SKU(
        id=str(uuid.uuid4()),
        product_id=moderated_product.id,
        name="256GB Black",
        price=12999000,
        cost_price=9500000,
        discount=0,
        image="/s3/iphone.jpg",
        reserved_quantity=5,
        active_quantity=10,
    )
    db_session.add(sku)
    await db_session.commit()
    await db_session.refresh(sku)
    return sku


@pytest_asyncio.fixture
def seller_auth() -> dict:
    token = __import__("app.auth", fromlist=["create_access_token"]).create_access_token(
        {"sub": "test-seller-id"}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@patch("app.routers.products.send_moderation_event", new_callable=AsyncMock)
async def test_edit_moderated_product_returns_to_on_moderation(
    mock_event: AsyncMock,
    client: AsyncClient,
    moderated_product: Product,
    seller_auth: dict,
):
    response = await client.put(
        f"/api/v1/products/{moderated_product.id}",
        json={
            "title": "Updated Title",
            "description": "Updated description",
        },
        headers=seller_auth,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["description"] == "Updated description"
    assert data["status"] == "ON_MODERATION"

    mock_event.assert_called_once()
    assert mock_event.call_args.kwargs["event"] == "EDITED"
    assert mock_event.call_args.kwargs["product_id"] == moderated_product.id


@pytest.mark.asyncio
@patch("app.routers.products.send_moderation_event", new_callable=AsyncMock)
async def test_edit_blocked_product_returns_to_on_moderation(
    mock_event: AsyncMock,
    client: AsyncClient,
    blocked_product: Product,
    seller_auth: dict,
):
    response = await client.put(
        f"/api/v1/products/{blocked_product.id}",
        json={
            "title": "Fixed after block",
        },
        headers=seller_auth,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ON_MODERATION"

    mock_event.assert_called_once()
    assert mock_event.call_args.kwargs["event"] == "EDITED"


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
async def test_reserves_preserved_after_sku_edit(
    mock_event: AsyncMock,
    client: AsyncClient,
    sku_with_reserves: SKU,
    seller_auth: dict,
    db_session: AsyncSession,
):
    response = await client.put(
        f"/api/v1/skus/{sku_with_reserves.id}",
        json={
            "name": "Updated SKU Name",
            "price": 13499000,
        },
        headers=seller_auth,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated SKU Name"
    assert data["price"] == 13499000
    assert data["reserved_quantity"] == 5
    assert data["active_quantity"] == 10

    mock_event.assert_called_once()
    assert mock_event.call_args.kwargs["event"] == "EDITED"


@pytest.mark.asyncio
async def test_edit_hard_blocked_returns_403(
    client: AsyncClient,
    hard_blocked_product: Product,
    seller_auth: dict,
):
    response = await client.put(
        f"/api/v1/products/{hard_blocked_product.id}",
        json={"title": "Try to edit"},
        headers=seller_auth,
    )

    assert response.status_code == 403
    data = response.json()
    assert data["code"] == "FORBIDDEN"
    assert "hard-blocked" in data["message"].lower()


@pytest.mark.asyncio
async def test_edit_others_product_returns_403(
    client: AsyncClient,
    others_product: Product,
    seller_auth: dict,
):
    response = await client.put(
        f"/api/v1/products/{others_product.id}",
        json={"title": "Hijack"},
        headers=seller_auth,
    )

    assert response.status_code == 403
    data = response.json()
    assert data["code"] == "NOT_OWNER"


@pytest.mark.asyncio
async def test_edit_nonexistent_product_returns_404(
    client: AsyncClient,
    seller_auth: dict,
):
    response = await client.put(
        f"/api/v1/products/{uuid.uuid4()}",
        json={"title": "X"},
        headers=seller_auth,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
async def test_edit_sku_on_moderated_product_triggers_edit_event(
    mock_event: AsyncMock,
    client: AsyncClient,
    moderated_product: Product,
    db_session: AsyncSession,
    seller_auth: dict,
):
    sku = SKU(
        id=str(uuid.uuid4()),
        product_id=moderated_product.id,
        name="Original SKU",
        price=1000,
        cost_price=500,
        image="/s3/orig.jpg",
    )
    db_session.add(sku)
    await db_session.commit()
    await db_session.refresh(sku)

    response = await client.put(
        f"/api/v1/skus/{sku.id}",
        json={"name": "Updated SKU"},
        headers=seller_auth,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Updated SKU"

    mock_event.assert_called_once()
    assert mock_event.call_args.kwargs["event"] == "EDITED"
    assert mock_event.call_args.kwargs["product_id"] == moderated_product.id
