import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Category, Product, SKU

SERVICE_HEADERS = {"X-Service-Key": settings.B2C_TO_B2B_KEY}


@pytest_asyncio.fixture
async def product_with_reserved_skus(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="seller-1",
        category_id=test_category.id,
        title="Reserved Product",
        slug="reserved-product",
        description="Has reserved quantities",
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
        active_quantity=7,
        reserved_quantity=3,
    )
    sku2 = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="512GB White",
        price=15999000,
        cost_price=12000000,
        image="/s3/white.jpg",
        active_quantity=3,
        reserved_quantity=2,
    )
    db_session.add(sku1)
    db_session.add(sku2)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest.mark.asyncio
async def test_fulfill_decreases_reserved_quantity(
    client: AsyncClient,
    product_with_reserved_skus: Product,
    db_session: AsyncSession,
):
    skus = (await db_session.execute(
        select(SKU).where(SKU.product_id == product_with_reserved_skus.id)
    )).scalars().all()
    sku_ids = [s.id for s in skus]

    response = await client.post(
        "/api/v1/fulfill",
        json={
            "order_id": str(uuid.uuid4()),
            "items": [
                {"sku_id": sku_ids[0], "quantity": 2},
                {"sku_id": sku_ids[1], "quantity": 1},
            ],
        },
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True

    await db_session.refresh(skus[0])
    await db_session.refresh(skus[1])
    assert skus[0].reserved_quantity == 1
    assert skus[1].reserved_quantity == 1


@pytest.mark.asyncio
async def test_active_quantity_unchanged(
    client: AsyncClient,
    product_with_reserved_skus: Product,
    db_session: AsyncSession,
):
    skus = (await db_session.execute(
        select(SKU).where(SKU.product_id == product_with_reserved_skus.id)
    )).scalars().all()
    sku_ids = [s.id for s in skus]

    original_active_0 = skus[0].active_quantity
    original_active_1 = skus[1].active_quantity

    response = await client.post(
        "/api/v1/fulfill",
        json={
            "order_id": str(uuid.uuid4()),
            "items": [
                {"sku_id": sku_ids[0], "quantity": 2},
                {"sku_id": sku_ids[1], "quantity": 1},
            ],
        },
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200

    await db_session.refresh(skus[0])
    await db_session.refresh(skus[1])
    assert skus[0].active_quantity == original_active_0
    assert skus[1].active_quantity == original_active_1


@pytest.mark.asyncio
async def test_idempotent_fulfill_no_double_deduction(
    client: AsyncClient,
    product_with_reserved_skus: Product,
    db_session: AsyncSession,
):
    skus = (await db_session.execute(
        select(SKU).where(SKU.product_id == product_with_reserved_skus.id)
    )).scalars().all()
    sku_ids = [s.id for s in skus]
    order_id = str(uuid.uuid4())

    response1 = await client.post(
        "/api/v1/fulfill",
        json={
            "order_id": order_id,
            "items": [
                {"sku_id": sku_ids[0], "quantity": 2},
                {"sku_id": sku_ids[1], "quantity": 1},
            ],
        },
        headers=SERVICE_HEADERS,
    )
    assert response1.status_code == 200
    assert response1.json()["ok"] is True

    await db_session.refresh(skus[0])
    await db_session.refresh(skus[1])
    assert skus[0].reserved_quantity == 1
    assert skus[1].reserved_quantity == 1

    response2 = await client.post(
        "/api/v1/fulfill",
        json={
            "order_id": order_id,
            "items": [
                {"sku_id": sku_ids[0], "quantity": 2},
                {"sku_id": sku_ids[1], "quantity": 1},
            ],
        },
        headers=SERVICE_HEADERS,
    )
    assert response2.status_code == 200
    assert response2.json()["ok"] is True

    await db_session.refresh(skus[0])
    await db_session.refresh(skus[1])
    assert skus[0].reserved_quantity == 1
    assert skus[1].reserved_quantity == 1


@pytest.mark.asyncio
async def test_fulfill_missing_service_key_returns_401(
    client: AsyncClient,
    product_with_reserved_skus: Product,
):
    response = await client.post(
        "/api/v1/fulfill",
        json={
            "order_id": str(uuid.uuid4()),
            "items": [{"sku_id": str(uuid.uuid4()), "quantity": 1}],
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_fulfill_nonexistent_sku_returns_404(
    client: AsyncClient,
):
    response = await client.post(
        "/api/v1/fulfill",
        json={
            "order_id": str(uuid.uuid4()),
            "items": [{"sku_id": str(uuid.uuid4()), "quantity": 1}],
        },
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_fulfill_insufficient_reserved_returns_400(
    client: AsyncClient,
    product_with_reserved_skus: Product,
    db_session: AsyncSession,
):
    skus = (await db_session.execute(
        select(SKU).where(SKU.product_id == product_with_reserved_skus.id)
    )).scalars().all()
    sku_id = skus[0].id

    response = await client.post(
        "/api/v1/fulfill",
        json={
            "order_id": str(uuid.uuid4()),
            "items": [{"sku_id": sku_id, "quantity": 999}],
        },
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INSUFFICIENT_RESERVED"
