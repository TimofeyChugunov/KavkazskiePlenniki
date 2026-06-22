import uuid
from datetime import datetime, timezone

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
        title="iPhone 15 Pro Max",
        slug="iphone-15-pro-max",
        description="Flagship",
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
        name="256GB White",
        price=12999000,
        cost_price=9500000,
        image="/s3/iphone15-white-256.jpg",
    )
    db_session.add(sku1)
    db_session.add(sku2)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def non_moderated_product(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
        category_id=test_category.id,
        title="Created Product",
        slug="created-product",
        description="Not yet moderated",
        status="CREATED",
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
        name="Basic SKU",
        price=500000,
        cost_price=300000,
        image="/s3/basic.jpg",
    )
    db_session.add(sku)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def others_product(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="other-seller-id",
        category_id=test_category.id,
        title="Other Product",
        slug="other-product",
        description="Not mine",
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
        name="Other SKU",
        price=1000000,
        cost_price=600000,
        image="/s3/other.jpg",
    )
    db_session.add(sku)
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
async def test_create_invoice_with_moderated_sku_returns_201(
    client: AsyncClient,
    moderated_product: Product,
    seller_auth: dict,
    db_session: AsyncSession,
):
    from sqlalchemy import select
    from app.models import SKU

    skus = (
        await db_session.execute(select(SKU).where(SKU.product_id == moderated_product.id))
    ).scalars().all()
    sku_ids = [s.id for s in skus]

    response = await client.post(
        "/api/v1/invoices",
        json={
            "items": [
                {"sku_id": sku_ids[0], "quantity": 10},
                {"sku_id": sku_ids[1], "quantity": 5},
            ]
        },
        headers=seller_auth,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "PENDING"
    assert "id" in data
    assert "created_at" in data
    assert len(data["items"]) == 2

    items = sorted(data["items"], key=lambda x: x["quantity"])
    assert items[0]["sku_id"] == sku_ids[1]
    assert items[0]["sku_name"] == "256GB White"
    assert items[0]["quantity"] == 5
    assert items[0]["accepted_quantity"] is None

    assert items[1]["sku_id"] == sku_ids[0]
    assert items[1]["sku_name"] == "256GB Black"
    assert items[1]["quantity"] == 10
    assert items[1]["accepted_quantity"] is None


@pytest.mark.asyncio
async def test_empty_items_returns_400(
    client: AsyncClient,
    seller_auth: dict,
):
    response = await client.post(
        "/api/v1/invoices",
        json={"items": []},
        headers=seller_auth,
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_non_moderated_sku_returns_400(
    client: AsyncClient,
    non_moderated_product: Product,
    seller_auth: dict,
    db_session: AsyncSession,
):
    from sqlalchemy import select
    from app.models import SKU

    skus = (
        await db_session.execute(select(SKU).where(SKU.product_id == non_moderated_product.id))
    ).scalars().all()
    sku_id = skus[0].id

    response = await client.post(
        "/api/v1/invoices",
        json={"items": [{"sku_id": sku_id, "quantity": 10}]},
        headers=seller_auth,
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "MODERATED" in data["message"]


@pytest.mark.asyncio
async def test_others_sku_returns_403(
    client: AsyncClient,
    others_product: Product,
    seller_auth: dict,
    db_session: AsyncSession,
):
    from sqlalchemy import select
    from app.models import SKU

    skus = (
        await db_session.execute(select(SKU).where(SKU.product_id == others_product.id))
    ).scalars().all()
    sku_id = skus[0].id

    response = await client.post(
        "/api/v1/invoices",
        json={"items": [{"sku_id": sku_id, "quantity": 10}]},
        headers=seller_auth,
    )

    assert response.status_code == 403
    data = response.json()
    assert data["code"] == "NOT_OWNER"
