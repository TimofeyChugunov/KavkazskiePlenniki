import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token
from app.models import Category, Product, SKU


@pytest_asyncio.fixture
async def seller_products(db_session: AsyncSession, test_category: Category) -> dict:
    seller_id = "list-seller-id"
    other_seller_id = "other-seller-id"

    p1 = Product(
        id=str(uuid.uuid4()),
        seller_id=seller_id,
        category_id=test_category.id,
        title="iPhone 15 Pro",
        slug="iphone-15-pro",
        description="Smartphone",
        status="MODERATED",
        deleted=False,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    p2 = Product(
        id=str(uuid.uuid4()),
        seller_id=seller_id,
        category_id=test_category.id,
        title="iPhone 14",
        slug="iphone-14",
        description="Old smartphone",
        status="BLOCKED",
        deleted=False,
        blocked=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    p3 = Product(
        id=str(uuid.uuid4()),
        seller_id=seller_id,
        category_id=test_category.id,
        title="Samsung Galaxy S24",
        slug="samsung-galaxy-s24",
        description="Android flagship",
        status="MODERATED",
        deleted=True,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    p_other = Product(
        id=str(uuid.uuid4()),
        seller_id=other_seller_id,
        category_id=test_category.id,
        title="Other Seller Product",
        slug="other-seller-product",
        description="Not mine",
        status="MODERATED",
        deleted=False,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    db_session.add_all([p1, p2, p3, p_other])
    await db_session.flush()

    sku1 = SKU(
        id=str(uuid.uuid4()),
        product_id=p1.id,
        name="256GB",
        price=12999000,
        cost_price=9500000,
        image="/s3/15pro.jpg",
        active_quantity=10,
        reserved_quantity=0,
    )
    sku2 = SKU(
        id=str(uuid.uuid4()),
        product_id=p1.id,
        name="512GB",
        price=15999000,
        cost_price=12000000,
        image="/s3/15pro512.jpg",
        active_quantity=15,
        reserved_quantity=0,
    )
    sku3 = SKU(
        id=str(uuid.uuid4()),
        product_id=p2.id,
        name="128GB",
        price=8999000,
        cost_price=6000000,
        image="/s3/14.jpg",
        active_quantity=5,
        reserved_quantity=0,
    )

    db_session.add_all([sku1, sku2, sku3])
    await db_session.commit()

    return {
        "seller_id": seller_id,
        "other_seller_id": other_seller_id,
        "p1": p1,
        "p2": p2,
        "p3": p3,
        "p_other": p_other,
        "sku1": sku1,
        "sku2": sku2,
        "sku3": sku3,
    }


@pytest_asyncio.fixture
def seller_auth() -> dict:
    token = create_access_token({"sub": "list-seller-id"})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
def other_seller_auth() -> dict:
    token = create_access_token({"sub": "other-seller-id"})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_returns_only_own_products(
    client: AsyncClient,
    seller_products: dict,
    seller_auth: dict,
):
    response = await client.get(
        "/api/v1/products",
        headers=seller_auth,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 2

    ids = {item["id"] for item in data["items"]}
    assert seller_products["p1"].id in ids
    assert seller_products["p2"].id in ids
    assert seller_products["p_other"].id not in ids


@pytest.mark.asyncio
async def test_idor_query_param_seller_id_ignored(
    client: AsyncClient,
    seller_products: dict,
    seller_auth: dict,
):
    response = await client.get(
        "/api/v1/products",
        params={"seller_id": seller_products["other_seller_id"]},
        headers=seller_auth,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 2

    ids = {item["id"] for item in data["items"]}
    assert seller_products["p_other"].id not in ids
    assert seller_products["p1"].id in ids


@pytest.mark.asyncio
async def test_deleted_products_visible_with_deleted_flag(
    client: AsyncClient,
    seller_products: dict,
    seller_auth: dict,
):
    response_default = await client.get(
        "/api/v1/products",
        headers=seller_auth,
    )
    assert response_default.status_code == 200
    assert response_default.json()["total_count"] == 2

    ids_default = {item["id"] for item in response_default.json()["items"]}
    assert seller_products["p3"].id not in ids_default

    response_deleted = await client.get(
        "/api/v1/products",
        params={"include_deleted": "true"},
        headers=seller_auth,
    )
    assert response_deleted.status_code == 200
    assert response_deleted.json()["total_count"] == 3

    ids_deleted = {item["id"] for item in response_deleted.json()["items"]}
    assert seller_products["p3"].id in ids_deleted


@pytest.mark.asyncio
async def test_status_filter_works_correctly(
    client: AsyncClient,
    seller_products: dict,
    seller_auth: dict,
):
    response = await client.get(
        "/api/v1/products",
        params={"status": "BLOCKED"},
        headers=seller_auth,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 1
    assert data["items"][0]["status"] == "BLOCKED"
    assert data["items"][0]["id"] == seller_products["p2"].id


@pytest.mark.asyncio
async def test_search_by_title_case_insensitive(
    client: AsyncClient,
    seller_products: dict,
    seller_auth: dict,
):
    response = await client.get(
        "/api/v1/products",
        params={"search": "iphone"},
        headers=seller_auth,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 2

    titles = {item["title"] for item in data["items"]}
    assert "iPhone 15 Pro" in titles
    assert "iPhone 14" in titles
    assert "Samsung Galaxy S24" not in titles


@pytest.mark.asyncio
async def test_list_response_includes_skus_count_and_total_active(
    client: AsyncClient,
    seller_products: dict,
    seller_auth: dict,
):
    response = await client.get(
        "/api/v1/products",
        headers=seller_auth,
    )

    assert response.status_code == 200
    items = response.json()["items"]

    p1_item = next(i for i in items if i["id"] == seller_products["p1"].id)
    assert p1_item["skus_count"] == 2
    assert p1_item["total_active_quantity"] == 25

    p2_item = next(i for i in items if i["id"] == seller_products["p2"].id)
    assert p2_item["skus_count"] == 1
    assert p2_item["total_active_quantity"] == 5


@pytest.mark.asyncio
async def test_list_response_includes_category_object(
    client: AsyncClient,
    seller_products: dict,
    seller_auth: dict,
):
    response = await client.get(
        "/api/v1/products",
        headers=seller_auth,
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert "category" in item
    assert "id" in item["category"]
    assert "name" in item["category"]


@pytest.mark.asyncio
async def test_list_response_includes_images(
    client: AsyncClient,
    seller_products: dict,
    seller_auth: dict,
    db_session: AsyncSession,
):
    from app.models import ProductImage
    db_session.add(ProductImage(
        product_id=seller_products["p1"].id,
        url="/s3/front.jpg",
        ordering=0,
    ))
    await db_session.commit()

    response = await client.get(
        "/api/v1/products",
        headers=seller_auth,
    )

    assert response.status_code == 200
    p1_item = next(i for i in response.json()["items"] if i["id"] == seller_products["p1"].id)
    assert len(p1_item["images"]) == 1
    assert p1_item["images"][0]["url"] == "/s3/front.jpg"


@pytest.mark.asyncio
async def test_list_requires_auth(
    client: AsyncClient,
):
    response = await client.get("/api/v1/products")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_other_seller_sees_only_own_products(
    client: AsyncClient,
    seller_products: dict,
    other_seller_auth: dict,
):
    response = await client.get(
        "/api/v1/products",
        headers=other_seller_auth,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 1
    assert data["items"][0]["id"] == seller_products["p_other"].id
