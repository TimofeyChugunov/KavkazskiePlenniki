import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Product, SKU


@pytest_asyncio.fixture
async def product_with_skus(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
        category_id=test_category.id,
        title="iPhone 15 Pro Max",
        slug="iphone-15-pro-max",
        description="Flagman",
        status="MODERATED",
        deleted=False,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    await db_session.flush()

    sku1 = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="256GB Black",
        price=12999000,
        cost_price=9500000,
        image="/s3/iphone15-black-256.jpg",
    )
    sku2 = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="512GB White",
        price=15999000,
        cost_price=12000000,
        image="/s3/iphone15-white-512.jpg",
    )
    db_session.add(sku1)
    db_session.add(sku2)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def deleted_product(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
        category_id=test_category.id,
        title="Already Deleted Product",
        slug="already-deleted-product",
        description="Was deleted",
        status="MODERATED",
        deleted=True,
        blocked=False,
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
def seller_auth() -> dict:
    token = __import__("app.auth", fromlist=["create_access_token"]).create_access_token(
        {"sub": "test-seller-id"}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@patch("app.routers.products.send_moderation_event", new_callable=AsyncMock)
@patch("app.routers.products.send_b2c_product_deleted", new_callable=AsyncMock)
async def test_delete_sets_deleted_true(
    mock_b2c: AsyncMock,
    mock_mod: AsyncMock,
    client: AsyncClient,
    product_with_skus: Product,
    seller_auth: dict,
    db_session: AsyncSession,
):
    response = await client.delete(
        f"/api/v1/products/{product_with_skus.id}",
        headers=seller_auth,
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    await db_session.refresh(product_with_skus)
    assert product_with_skus.deleted is True


@pytest.mark.asyncio
@patch("app.routers.products.send_moderation_event", new_callable=AsyncMock)
@patch("app.routers.products.send_b2c_product_deleted", new_callable=AsyncMock)
async def test_delete_emits_event_to_moderation(
    mock_b2c: AsyncMock,
    mock_mod: AsyncMock,
    client: AsyncClient,
    product_with_skus: Product,
    seller_auth: dict,
):
    response = await client.delete(
        f"/api/v1/products/{product_with_skus.id}",
        headers=seller_auth,
    )

    assert response.status_code == 200

    mock_mod.assert_called_once()
    call_args = mock_mod.call_args
    assert call_args.kwargs["product_id"] == product_with_skus.id
    assert call_args.kwargs["seller_id"] == "test-seller-id"
    assert call_args.kwargs["event"] == "DELETED"


@pytest.mark.asyncio
@patch("app.routers.products.send_moderation_event", new_callable=AsyncMock)
@patch("app.routers.products.send_b2c_product_deleted", new_callable=AsyncMock)
async def test_delete_emits_product_deleted_to_b2c(
    mock_b2c: AsyncMock,
    mock_mod: AsyncMock,
    client: AsyncClient,
    product_with_skus: Product,
    seller_auth: dict,
    db_session: AsyncSession,
):
    skus = (await db_session.execute(select(SKU).where(SKU.product_id == product_with_skus.id))).scalars().all()
    sku_ids = sorted([s.id for s in skus])

    response = await client.delete(
        f"/api/v1/products/{product_with_skus.id}",
        headers=seller_auth,
    )

    assert response.status_code == 200

    mock_b2c.assert_called_once()
    call_args = mock_b2c.call_args
    assert call_args.kwargs["product_id"] == product_with_skus.id
    assert sorted(call_args.kwargs["sku_ids"]) == sku_ids


@pytest.mark.asyncio
async def test_delete_already_deleted_returns_400(
    client: AsyncClient,
    deleted_product: Product,
    seller_auth: dict,
):
    response = await client.delete(
        f"/api/v1/products/{deleted_product.id}",
        headers=seller_auth,
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "already deleted" in data["message"].lower()


@pytest.mark.asyncio
async def test_delete_others_product_returns_403(
    client: AsyncClient,
    others_product: Product,
    seller_auth: dict,
):
    response = await client.delete(
        f"/api/v1/products/{others_product.id}",
        headers=seller_auth,
    )

    assert response.status_code == 403
    data = response.json()
    assert data["code"] == "NOT_OWNER"


@pytest.mark.asyncio
async def test_delete_nonexistent_product_returns_404(
    client: AsyncClient,
    seller_auth: dict,
):
    response = await client.delete(
        f"/api/v1/products/{uuid.uuid4()}",
        headers=seller_auth,
    )

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "NOT_FOUND"


@pytest.mark.asyncio
@patch("app.routers.products.send_moderation_event", new_callable=AsyncMock)
@patch("app.routers.products.send_b2c_product_deleted", new_callable=AsyncMock)
async def test_deleted_product_not_in_seller_list(
    mock_b2c: AsyncMock,
    mock_mod: AsyncMock,
    client: AsyncClient,
    product_with_skus: Product,
    seller_auth: dict,
    db_session: AsyncSession,
):
    from app.auth import create_access_token

    seller_id = "test-seller-id"
    token = create_access_token({"sub": seller_id})
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get(
        "/api/v1/products",
        headers=headers,
    )
    assert response.status_code == 200
    items = response.json()["items"]
    product_ids = [p["id"] for p in items]
    assert product_with_skus.id in product_ids

    await client.delete(
        f"/api/v1/products/{product_with_skus.id}",
        headers=headers,
    )

    response = await client.get(
        "/api/v1/products",
        headers=headers,
    )
    assert response.status_code == 200
    items = response.json()["items"]
    product_ids = [p["id"] for p in items]
    assert product_with_skus.id not in product_ids
