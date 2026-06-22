import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Category, Product, SKU

SERVICE_HEADERS = {"X-Service-Key": settings.B2C_TO_B2B_KEY}


@pytest_asyncio.fixture
async def product_with_skus(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="seller-1",
        category_id=test_category.id,
        title="iPhone 15",
        slug="iphone-15",
        description="Smartphone",
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
        image="/s3/black.jpg",
        active_quantity=10,
        reserved_quantity=0,
    )
    sku2 = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="512GB White",
        price=15999000,
        cost_price=12000000,
        image="/s3/white.jpg",
        active_quantity=5,
        reserved_quantity=0,
    )
    db_session.add(sku1)
    db_session.add(sku2)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def product_low_stock(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="seller-1",
        category_id=test_category.id,
        title="Low Stock Item",
        slug="low-stock",
        description="Low",
        status="MODERATED",
        deleted=False,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    await db_session.flush()

    sku = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="Only 2 Left",
        price=500000,
        cost_price=300000,
        image="/s3/low.jpg",
        active_quantity=2,
        reserved_quantity=0,
    )
    db_session.add(sku)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest.mark.asyncio
@patch("app.routers.inventory.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_reserve_all_skus_succeeds(
    mock_event: AsyncMock,
    client: AsyncClient,
    product_with_skus: Product,
    db_session: AsyncSession,
):
    from sqlalchemy import select
    skus = (await db_session.execute(
        select(SKU).where(SKU.product_id == product_with_skus.id)
    )).scalars().all()
    sku_ids = [s.id for s in skus]

    response = await client.post(
        "/api/v1/inventory/reserve",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "items": [
                {"sku_id": sku_ids[0], "quantity": 3},
                {"sku_id": sku_ids[1], "quantity": 2},
            ],
        },
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["reserved"] is True
    assert len(data["items"]) == 2

    items_by_id = {item["sku_id"]: item for item in data["items"]}

    assert items_by_id[sku_ids[0]]["reserved_quantity"] == 3
    assert items_by_id[sku_ids[0]]["remaining_stock"] == 7

    assert items_by_id[sku_ids[1]]["reserved_quantity"] == 2
    assert items_by_id[sku_ids[1]]["remaining_stock"] == 3

    await db_session.refresh(skus[0])
    await db_session.refresh(skus[1])
    assert skus[0].active_quantity == 7
    assert skus[0].reserved_quantity == 3
    assert skus[1].active_quantity == 3
    assert skus[1].reserved_quantity == 2


@pytest.mark.asyncio
@patch("app.routers.inventory.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_idempotent_reserve_returns_200_without_double_deduction(
    mock_event: AsyncMock,
    client: AsyncClient,
    product_with_skus: Product,
    db_session: AsyncSession,
):
    from sqlalchemy import select
    skus = (await db_session.execute(
        select(SKU).where(SKU.product_id == product_with_skus.id)
    )).scalars().all()
    sku_id = skus[0].id
    idempotency_key = str(uuid.uuid4())

    response1 = await client.post(
        "/api/v1/inventory/reserve",
        json={
            "idempotency_key": idempotency_key,
            "items": [{"sku_id": sku_id, "quantity": 3}],
        },
        headers=SERVICE_HEADERS,
    )
    assert response1.status_code == 200
    assert response1.json()["reserved"] is True

    await db_session.refresh(skus[0])
    assert skus[0].active_quantity == 7
    assert skus[0].reserved_quantity == 3

    response2 = await client.post(
        "/api/v1/inventory/reserve",
        json={
            "idempotency_key": idempotency_key,
            "items": [{"sku_id": sku_id, "quantity": 3}],
        },
        headers=SERVICE_HEADERS,
    )
    assert response2.status_code == 200
    assert response2.json()["reserved"] is True
    assert response2.json() == response1.json()

    await db_session.refresh(skus[0])
    assert skus[0].active_quantity == 7
    assert skus[0].reserved_quantity == 3


@pytest.mark.asyncio
@patch("app.routers.inventory.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_partial_insufficient_stock_returns_409_all_rollback(
    mock_event: AsyncMock,
    client: AsyncClient,
    product_with_skus: Product,
    db_session: AsyncSession,
):
    from sqlalchemy import select
    skus = (await db_session.execute(
        select(SKU).where(SKU.product_id == product_with_skus.id)
    )).scalars().all()
    sku_ids = [s.id for s in skus]

    response = await client.post(
        "/api/v1/inventory/reserve",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "items": [
                {"sku_id": sku_ids[0], "quantity": 3},
                {"sku_id": sku_ids[1], "quantity": 99},
            ],
        },
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 409
    data = response.json()
    assert data["reserved"] is False
    assert len(data["failed_items"]) == 1
    assert data["failed_items"][0]["sku_id"] == sku_ids[1]
    assert data["failed_items"][0]["reason"] == "INSUFFICIENT_STOCK"

    await db_session.refresh(skus[0])
    await db_session.refresh(skus[1])
    assert skus[0].active_quantity == 10
    assert skus[0].reserved_quantity == 0
    assert skus[1].active_quantity == 5
    assert skus[1].reserved_quantity == 0


@pytest.mark.asyncio
@patch("app.routers.inventory.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_sku_out_of_stock_event_emitted(
    mock_event: AsyncMock,
    client: AsyncClient,
    product_low_stock: Product,
    db_session: AsyncSession,
):
    from sqlalchemy import select
    skus = (await db_session.execute(
        select(SKU).where(SKU.product_id == product_low_stock.id)
    )).scalars().all()
    sku_id = skus[0].id

    response = await client.post(
        "/api/v1/inventory/reserve",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "items": [{"sku_id": sku_id, "quantity": 2}],
        },
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["reserved"] is True
    assert data["items"][0]["remaining_stock"] == 0

    mock_event.assert_called_once()
    assert mock_event.call_args[0][0] == sku_id


@pytest.mark.asyncio
@patch("app.routers.inventory.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_unreserve_restores_quantities(
    mock_event: AsyncMock,
    client: AsyncClient,
    product_with_skus: Product,
    db_session: AsyncSession,
):
    from sqlalchemy import select
    skus = (await db_session.execute(
        select(SKU).where(SKU.product_id == product_with_skus.id)
    )).scalars().all()
    sku_id = skus[0].id
    order_id = str(uuid.uuid4())

    response1 = await client.post(
        "/api/v1/inventory/reserve",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "items": [{"sku_id": sku_id, "quantity": 4}],
        },
        headers=SERVICE_HEADERS,
    )
    assert response1.status_code == 200

    await db_session.refresh(skus[0])
    assert skus[0].active_quantity == 6
    assert skus[0].reserved_quantity == 4

    response2 = await client.post(
        "/api/v1/inventory/unreserve",
        json={
            "order_id": order_id,
            "items": [{"sku_id": sku_id, "quantity": 4}],
        },
        headers=SERVICE_HEADERS,
    )
    assert response2.status_code == 200
    assert response2.json()["ok"] is True

    await db_session.refresh(skus[0])
    assert skus[0].active_quantity == 10
    assert skus[0].reserved_quantity == 0


@pytest.mark.asyncio
async def test_reserve_missing_service_key_returns_401(
    client: AsyncClient,
    product_with_skus: Product,
):
    response = await client.post(
        "/api/v1/inventory/reserve",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "items": [{"sku_id": str(uuid.uuid4()), "quantity": 1}],
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
@patch("app.routers.inventory.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_reserve_out_of_stock_returns_409(
    mock_event: AsyncMock,
    client: AsyncClient,
    db_session: AsyncSession,
    test_category: Category,
):
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="seller-1",
        category_id=test_category.id,
        title="Empty",
        slug="empty",
        description="No stock",
        status="MODERATED",
        deleted=False,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    await db_session.flush()

    sku = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="Empty SKU",
        price=1000,
        cost_price=500,
        image="/s3/empty.jpg",
        active_quantity=0,
    )
    db_session.add(sku)
    await db_session.commit()

    response = await client.post(
        "/api/v1/inventory/reserve",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "items": [{"sku_id": sku.id, "quantity": 1}],
        },
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 409
    data = response.json()
    assert data["reserved"] is False
    assert data["failed_items"][0]["reason"] == "OUT_OF_STOCK"


@pytest.mark.asyncio
@patch("app.routers.inventory.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_reserve_nonexistent_sku_returns_404(
    mock_event: AsyncMock,
    client: AsyncClient,
):
    response = await client.post(
        "/api/v1/inventory/reserve",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "items": [{"sku_id": str(uuid.uuid4()), "quantity": 1}],
        },
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
