import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Category, Product, SKU

SERVICE_HEADERS = {"X-Service-Key": settings.B2C_TO_B2B_KEY}


@pytest_asyncio.fixture
async def product_moderated_in_stock(
    db_session: AsyncSession, test_category: Category
) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="seller-1",
        category_id=test_category.id,
        title="iPhone 15 Pro Max",
        slug="iphone-15-pro-max",
        description="Flagship smartphone",
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
        name="256GB Black",
        price=12999000,
        cost_price=9500000,
        discount=0,
        image="/s3/iphone.jpg",
        active_quantity=10,
        reserved_quantity=2,
    )
    db_session.add(sku)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def product_hard_blocked(
    db_session: AsyncSession, test_category: Category
) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="seller-2",
        category_id=test_category.id,
        title="Blocked Phone",
        slug="blocked-phone",
        description="Hard blocked",
        status="HARD_BLOCKED",
        deleted=False,
        blocked=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    await db_session.flush()

    sku = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="Basic",
        price=500000,
        cost_price=300000,
        image="/s3/basic.jpg",
        active_quantity=5,
    )
    db_session.add(sku)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def product_moderated_no_stock(
    db_session: AsyncSession, test_category: Category
) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="seller-3",
        category_id=test_category.id,
        title="Out of Stock Phone",
        slug="out-of-stock-phone",
        description="No active quantity",
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
        price=800000,
        cost_price=400000,
        image="/s3/empty.jpg",
        active_quantity=0,
    )
    db_session.add(sku)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def product_deleted(
    db_session: AsyncSession, test_category: Category
) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="seller-4",
        category_id=test_category.id,
        title="Deleted Phone",
        slug="deleted-phone",
        description="Soft deleted",
        status="MODERATED",
        deleted=True,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    await db_session.flush()

    sku = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="Deleted SKU",
        price=700000,
        cost_price=350000,
        image="/s3/deleted.jpg",
        active_quantity=3,
    )
    db_session.add(sku)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest.mark.asyncio
async def test_catalog_returns_moderated_in_stock_products(
    client: AsyncClient,
    product_moderated_in_stock: Product,
):
    response = await client.get(
        "/api/v1/public/products",
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 1
    assert len(data["items"]) == 1

    item = data["items"][0]
    assert item["id"] == product_moderated_in_stock.id
    assert item["title"] == "iPhone 15 Pro Max"
    assert item["status"] == "MODERATED"
    assert len(item["skus"]) == 1
    assert item["skus"][0]["active_quantity"] == 10


@pytest.mark.asyncio
async def test_catalog_excludes_hard_blocked(
    client: AsyncClient,
    product_moderated_in_stock: Product,
    product_hard_blocked: Product,
):
    response = await client.get(
        "/api/v1/public/products",
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    ids = [item["id"] for item in data["items"]]
    assert product_moderated_in_stock.id in ids
    assert product_hard_blocked.id not in ids


@pytest.mark.asyncio
async def test_catalog_missing_service_key_returns_401(
    client: AsyncClient,
    product_moderated_in_stock: Product,
):
    response = await client.get("/api/v1/public/products")

    assert response.status_code == 401
    data = response.json()
    assert data["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_catalog_response_has_no_cost_price(
    client: AsyncClient,
    product_moderated_in_stock: Product,
):
    response = await client.get(
        "/api/v1/public/products",
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    item = data["items"][0]
    sku = item["skus"][0]
    assert "cost_price" not in sku
    assert "reserved_quantity" not in sku


@pytest.mark.asyncio
async def test_batch_ids_returns_visible_subset(
    client: AsyncClient,
    product_moderated_in_stock: Product,
    product_hard_blocked: Product,
    product_moderated_no_stock: Product,
    product_deleted: Product,
):
    all_ids = ",".join([
        product_moderated_in_stock.id,
        product_hard_blocked.id,
        product_moderated_no_stock.id,
        product_deleted.id,
    ])

    response = await client.get(
        f"/api/v1/public/products?ids={all_ids}",
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    returned_ids = [item["id"] for item in data["items"]]
    assert product_moderated_in_stock.id in returned_ids
    assert product_hard_blocked.id not in returned_ids
    assert product_moderated_no_stock.id not in returned_ids
    assert product_deleted.id not in returned_ids
    assert len(returned_ids) == 1


@pytest.mark.asyncio
async def test_catalog_excludes_zero_active_quantity(
    client: AsyncClient,
    product_moderated_in_stock: Product,
    product_moderated_no_stock: Product,
):
    response = await client.get(
        "/api/v1/public/products",
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    ids = [item["id"] for item in data["items"]]
    assert product_moderated_in_stock.id in ids
    assert product_moderated_no_stock.id not in ids


@pytest.mark.asyncio
async def test_catalog_excludes_deleted(
    client: AsyncClient,
    product_moderated_in_stock: Product,
    product_deleted: Product,
):
    response = await client.get(
        "/api/v1/public/products",
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    ids = [item["id"] for item in data["items"]]
    assert product_deleted.id not in ids


@pytest.mark.asyncio
async def test_catalog_category_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    test_category: Category,
):
    other_category = Category(
        id=str(uuid.uuid4()),
        name="Ноутбуки",
        parent_id=None,
        level=0,
        path="laptops",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(other_category)
    await db_session.flush()

    p1 = Product(
        id=str(uuid.uuid4()),
        seller_id="seller-1",
        category_id=test_category.id,
        title="Phone",
        slug="phone",
        description="A phone",
        status="MODERATED",
        deleted=False,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    p2 = Product(
        id=str(uuid.uuid4()),
        seller_id="seller-1",
        category_id=other_category.id,
        title="Laptop",
        slug="laptop",
        description="A laptop",
        status="MODERATED",
        deleted=False,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(p1)
    db_session.add(p2)
    await db_session.flush()

    db_session.add(SKU(product_id=p1.id, name="SKU1", price=1000, cost_price=500, image="/1.jpg", active_quantity=1))
    db_session.add(SKU(product_id=p2.id, name="SKU2", price=2000, cost_price=1000, image="/2.jpg", active_quantity=1))
    await db_session.commit()

    response = await client.get(
        f"/api/v1/public/products?category_id={test_category.id}",
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 1
    assert data["items"][0]["id"] == p1.id
