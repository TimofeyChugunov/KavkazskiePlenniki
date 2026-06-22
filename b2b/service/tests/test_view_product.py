import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token
from app.models import BlockingReason, Characteristic, Category, FieldReport, Product, ProductImage, SKU


@pytest_asyncio.fixture
async def moderated_product_with_skus(
    db_session: AsyncSession, test_category: Category
) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
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

    img = ProductImage(
        product_id=product.id,
        url="/s3/iphone15-front.jpg",
        ordering=0,
    )
    db_session.add(img)

    char = Characteristic(
        product_id=product.id,
        sku_id=None,
        name="Brand",
        value="Apple",
    )
    db_session.add(char)

    sku = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="256GB Black",
        price=12999000,
        cost_price=9500000,
        discount=0,
        image="/s3/iphone15-black-256.jpg",
        active_quantity=10,
        reserved_quantity=2,
    )
    db_session.add(sku)
    await db_session.flush()

    sku_char = Characteristic(
        product_id=None,
        sku_id=sku.id,
        name="Color",
        value="Black",
    )
    db_session.add(sku_char)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def blocked_product_with_reason(
    db_session: AsyncSession, test_category: Category
) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
        category_id=test_category.id,
        title="Levi's 501 Original",
        slug="levis-501-original",
        description="Classic jeans",
        status="BLOCKED",
        deleted=False,
        blocked=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    await db_session.flush()

    blocking = BlockingReason(
        id=str(uuid.uuid4()),
        product_id=product.id,
        title="Description does not match product",
        comment="Mismatch between description and photos",
    )
    db_session.add(blocking)

    fr1 = FieldReport(
        product_id=product.id,
        field_name="description",
        sku_id=None,
        comment="Description says 'genuine leather', photos show synthetic",
    )
    fr2 = FieldReport(
        product_id=product.id,
        field_name="sku_image",
        sku_id="some-sku-id",
        comment="SKU photo does not match specified color",
    )
    db_session.add(fr1)
    db_session.add(fr2)

    sku = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="Size 32",
        price=899000,
        cost_price=450000,
        discount=0,
        image="/s3/levis-501-32.jpg",
        active_quantity=0,
        reserved_quantity=0,
    )
    db_session.add(sku)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def others_product(
    db_session: AsyncSession, test_category: Category
) -> Product:
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
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
def seller_auth() -> dict:
    token = create_access_token({"sub": "test-seller-id"})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_get_moderated_product_returns_full_payload(
    client: AsyncClient,
    moderated_product_with_skus: Product,
    seller_auth: dict,
):
    response = await client.get(
        f"/api/v1/products/{moderated_product_with_skus.id}",
        headers=seller_auth,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == moderated_product_with_skus.id
    assert data["title"] == "iPhone 15 Pro Max"
    assert data["description"] == "Flagship smartphone"
    assert data["status"] == "MODERATED"
    assert data["deleted"] is False
    assert data["blocked"] is False
    assert data["category"]["id"] is not None
    assert data["category"]["name"] == "\u0421\u043c\u0430\u0440\u0442\u0444\u043e\u043d\u044b"
    assert len(data["images"]) == 1
    assert data["images"][0]["url"] == "/s3/iphone15-front.jpg"
    assert len(data["characteristics"]) == 1
    assert data["characteristics"][0]["name"] == "Brand"
    assert len(data["skus"]) == 1
    assert data["skus"][0]["name"] == "256GB Black"
    assert data["skus"][0]["price"] == 12999000
    assert data["skus"][0]["cost_price"] == 9500000
    assert data["skus"][0]["active_quantity"] == 10
    assert data["skus"][0]["reserved_quantity"] == 2
    assert len(data["skus"][0]["characteristics"]) == 1
    assert data["blocking_reason"] is None
    assert data["field_reports"] == []


@pytest.mark.asyncio
async def test_get_blocked_product_returns_blocking_reason_and_field_reports(
    client: AsyncClient,
    blocked_product_with_reason: Product,
    seller_auth: dict,
):
    response = await client.get(
        f"/api/v1/products/{blocked_product_with_reason.id}",
        headers=seller_auth,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "BLOCKED"
    assert data["blocked"] is True
    assert data["blocking_reason"] is not None
    assert data["blocking_reason"]["title"] == "Description does not match product"
    assert data["blocking_reason"]["comment"] == "Mismatch between description and photos"
    assert data["blocking_reason"]["id"] is not None
    assert len(data["field_reports"]) == 2
    assert data["field_reports"][0]["field_name"] == "description"
    assert data["field_reports"][0]["sku_id"] is None
    assert data["field_reports"][1]["field_name"] == "sku_image"
    assert data["field_reports"][1]["sku_id"] is not None


@pytest.mark.asyncio
async def test_get_others_product_returns_404(
    client: AsyncClient,
    others_product: Product,
    seller_auth: dict,
):
    response = await client.get(
        f"/api/v1/products/{others_product.id}",
        headers=seller_auth,
    )

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_get_nonexistent_returns_404(
    client: AsyncClient,
    seller_auth: dict,
):
    response = await client.get(
        f"/api/v1/products/{uuid.uuid4()}",
        headers=seller_auth,
    )

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_get_product_via_service_key_hides_cost_price(
    client: AsyncClient,
    moderated_product_with_skus: Product,
):
    from app.config import settings

    response = await client.get(
        f"/api/v1/products/{moderated_product_with_skus.id}",
        headers={"X-Service-Key": settings.B2B_TO_MOD_KEY},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["skus"][0]["cost_price"] is None
    assert data["skus"][0]["reserved_quantity"] is None
