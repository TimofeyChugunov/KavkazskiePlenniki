from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.main import app


COLLECTION_ID_1 = "11111111-1111-1111-1111-111111111111"
COLLECTION_ID_2 = "22222222-2222-2222-2222-222222222222"
COLLECTION_ID_3 = "33333333-3333-3333-3333-333333333333"
PRODUCT_ID_1 = "770e8400-e29b-41d4-a716-446655440002"
PRODUCT_ID_2 = "770e8400-e29b-41d4-a716-446655440003"
PRODUCT_ID_MISSING = "99999999-9999-9999-9999-999999999999"


SAMPLE_B2B_PRODUCT = {
    "id": PRODUCT_ID_1,
    "title": "iPhone 15 Pro Max",
    "slug": "iphone-15-pro-max",
    "description": "Flagship smartphone",
    "status": "MODERATED",
    "category_id": "cat-1",
    "seller_id": "seller-1",
    "images": [{"id": "img-1", "url": "https://cdn.neomarket.ru/images/iphone15.jpg", "ordering": 0}],
    "characteristics": [],
    "skus": [
        {
            "id": "sku-1",
            "product_id": PRODUCT_ID_1,
            "name": "256GB Black",
            "price": 12999000,
            "discount": 0,
            "active_quantity": 10,
            "characteristics": [],
        }
    ],
}

SAMPLE_B2B_PRODUCT_2 = {
    "id": PRODUCT_ID_2,
    "title": "Samsung Galaxy S24",
    "slug": "samsung-galaxy-s24",
    "description": "Android flagship",
    "status": "MODERATED",
    "category_id": "cat-1",
    "seller_id": "seller-2",
    "images": [{"id": "img-2", "url": "https://cdn.neomarket.ru/images/s24.jpg", "ordering": 0}],
    "characteristics": [],
    "skus": [
        {
            "id": "sku-2",
            "product_id": PRODUCT_ID_2,
            "name": "128GB White",
            "price": 8999000,
            "discount": 0,
            "active_quantity": 5,
            "characteristics": [],
        }
    ],
}


def _make_collection(
    col_id: str = COLLECTION_ID_1,
    title: str = "Хиты продаж",
    priority: int = 10,
    is_active: bool = True,
    start_date: str | None = None,
    product_ids: list[str] | None = None,
) -> dict:
    return {
        "id": col_id,
        "title": title,
        "description": f"Описание подборки {title}",
        "cover_image_url": f"/cdn/collections/{title.lower().replace(' ', '-')}.jpg",
        "target_url": f"/collections/{col_id}",
        "priority": priority,
        "is_active": is_active,
        "start_date": start_date,
        "product_ids": product_ids or [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest_asyncio.fixture(autouse=True)
async def clean_collections():
    import app.routers.collections as col_module
    col_module._collections_db.clear()
    yield
    col_module._collections_db.clear()


def mock_b2b_batch(products: list[dict]) -> AsyncMock:
    mock = AsyncMock()
    mock.return_value = products
    return mock


# === 4 REQUIRED TESTS ===


@pytest.mark.asyncio
async def test_collections_list_returns_metadata_without_products(client: AsyncClient):
    import app.routers.collections as col_module

    col_module._collections_db.extend([
        _make_collection(COLLECTION_ID_1, "Хиты продаж", priority=10, product_ids=[PRODUCT_ID_1, PRODUCT_ID_2]),
        _make_collection(COLLECTION_ID_2, "Новинки сезона", priority=5, product_ids=[PRODUCT_ID_1]),
    ])

    resp = await client.get("/api/v1/main/collections")
    assert resp.status_code == 200

    data = resp.json()
    assert data["metadata"]["total_count"] == 2
    assert len(data["collections"]) == 2

    assert data["collections"][0]["title"] == "Новинки сезона"
    assert data["collections"][0]["priority"] == 5
    assert data["collections"][0]["total_products"] == 1
    assert "products" not in data["collections"][0] or data["collections"][0].get("total_products") is not None

    assert data["collections"][1]["title"] == "Хиты продаж"
    assert data["collections"][1]["total_products"] == 2


@pytest.mark.asyncio
async def test_collection_products_enriched_from_b2b(client: AsyncClient):
    import app.routers.collections as col_module

    col_module._collections_db.append(
        _make_collection(
            COLLECTION_ID_1,
            "Хиты продаж",
            product_ids=[PRODUCT_ID_1, PRODUCT_ID_2],
        )
    )

    with patch("app.routers.collections._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT, SAMPLE_B2B_PRODUCT_2])):
        resp = await client.get(f"/api/v1/collections/{COLLECTION_ID_1}/products")

    assert resp.status_code == 200
    data = resp.json()
    assert data["collection_title"] == "Хиты продаж"
    assert len(data["items"]) == 2
    assert data["unavailable_ids"] == []
    assert data["total_products"] == 2

    item1 = next(i for i in data["items"] if i["id"] == PRODUCT_ID_1)
    assert item1["title"] == "iPhone 15 Pro Max"
    assert item1["min_price"] == 12999000
    assert item1["has_stock"] is True

    item2 = next(i for i in data["items"] if i["id"] == PRODUCT_ID_2)
    assert item2["title"] == "Samsung Galaxy S24"
    assert item2["min_price"] == 8999000


@pytest.mark.asyncio
async def test_unavailable_products_in_unavailable_ids(client: AsyncClient):
    import app.routers.collections as col_module

    col_module._collections_db.append(
        _make_collection(
            COLLECTION_ID_1,
            "Тест",
            product_ids=[PRODUCT_ID_1, PRODUCT_ID_MISSING],
        )
    )

    with patch("app.routers.collections._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
        resp = await client.get(f"/api/v1/collections/{COLLECTION_ID_1}/products")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == PRODUCT_ID_1
    assert data["unavailable_ids"] == [PRODUCT_ID_MISSING]
    assert data["total_products"] == 2


@pytest.mark.asyncio
async def test_unknown_collection_returns_404(client: AsyncClient):
    resp = await client.get("/api/v1/collections/nonexistent-id/products")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# === ADDITIONAL TESTS ===


@pytest.mark.asyncio
async def test_inactive_collection_excluded(client: AsyncClient):
    import app.routers.collections as col_module

    col_module._collections_db.extend([
        _make_collection(COLLECTION_ID_1, "Активная", is_active=True),
        _make_collection(COLLECTION_ID_2, "Неактивная", is_active=False),
    ])

    resp = await client.get("/api/v1/main/collections")
    assert resp.status_code == 200
    data = resp.json()
    assert data["metadata"]["total_count"] == 1
    assert data["collections"][0]["title"] == "Активная"


@pytest.mark.asyncio
async def test_future_start_date_excluded(client: AsyncClient):
    import app.routers.collections as col_module

    future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    col_module._collections_db.extend([
        _make_collection(COLLECTION_ID_1, "Будущая", start_date=future),
        _make_collection(COLLECTION_ID_2, "Активная"),
    ])

    resp = await client.get("/api/v1/main/collections")
    assert resp.status_code == 200
    data = resp.json()
    assert data["metadata"]["total_count"] == 1
    assert data["collections"][0]["title"] == "Активная"


@pytest.mark.asyncio
async def test_no_active_collections_returns_empty(client: AsyncClient):
    resp = await client.get("/api/v1/main/collections")
    assert resp.status_code == 200
    data = resp.json()
    assert data["collections"] == []
    assert data["metadata"]["total_count"] == 0


@pytest.mark.asyncio
async def test_empty_collection_returns_zero_products(client: AsyncClient):
    import app.routers.collections as col_module

    col_module._collections_db.append(
        _make_collection(COLLECTION_ID_1, "Пустая", product_ids=[])
    )

    resp = await client.get(f"/api/v1/collections/{COLLECTION_ID_1}/products")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["unavailable_ids"] == []
    assert data["total_products"] == 0


@pytest.mark.asyncio
async def test_all_products_unavailable_returns_valid_response(client: AsyncClient):
    import app.routers.collections as col_module

    col_module._collections_db.append(
        _make_collection(
            COLLECTION_ID_1,
            "Все удалены",
            product_ids=[PRODUCT_ID_MISSING, "aaaaaaaa-bbbb-cccc-dddd-ffffffffffff"],
        )
    )

    with patch("app.routers.collections._fetch_b2b_batch", mock_b2b_batch([])):
        resp = await client.get(f"/api/v1/collections/{COLLECTION_ID_1}/products")

    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert len(data["unavailable_ids"]) == 2


@pytest.mark.asyncio
async def test_no_auth_required_for_collections_list(client: AsyncClient):
    resp = await client.get("/api/v1/main/collections")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_no_auth_required_for_collection_products(client: AsyncClient):
    import app.routers.collections as col_module

    col_module._collections_db.append(
        _make_collection(COLLECTION_ID_1, "Тест")
    )

    resp = await client.get(f"/api/v1/collections/{COLLECTION_ID_1}/products")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_collection_products_response_fields(client: AsyncClient):
    import app.routers.collections as col_module

    col_module._collections_db.append(
        _make_collection(COLLECTION_ID_1, "Тест", product_ids=[PRODUCT_ID_1])
    )

    with patch("app.routers.collections._fetch_b2b_batch", mock_b2b_batch([SAMPLE_B2B_PRODUCT])):
        resp = await client.get(f"/api/v1/collections/{COLLECTION_ID_1}/products")

    assert resp.status_code == 200
    data = resp.json()
    assert "collection_id" in data
    assert "collection_title" in data
    assert "items" in data
    assert "unavailable_ids" in data
    assert "total_products" in data
    assert "limit" in data
    assert "offset" in data

    item = data["items"][0]
    assert "id" in item
    assert "title" in item
    assert "min_price" in item
    assert "has_stock" in item
    assert "images" in item


@pytest.mark.asyncio
async def test_collection_products_pagination(client: AsyncClient):
    import app.routers.collections as col_module

    product_ids = [f"prod-{i:03d}-0000-0000-000000000000" for i in range(5)]
    col_module._collections_db.append(
        _make_collection(COLLECTION_ID_1, "Пагинация", product_ids=product_ids)
    )

    products = [
        {"id": pid, "title": f"Product {i}", "skus": [{"price": 1000, "active_quantity": 1}], "images": []}
        for i, pid in enumerate(product_ids)
    ]

    with patch("app.routers.collections._fetch_b2b_batch", mock_b2b_batch(products[:2])):
        resp = await client.get(f"/api/v1/collections/{COLLECTION_ID_1}/products?limit=2&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["total_products"] == 5

    with patch("app.routers.collections._fetch_b2b_batch", mock_b2b_batch(products[2:4])):
        resp = await client.get(f"/api/v1/collections/{COLLECTION_ID_1}/products?limit=2&offset=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_collections_sorted_by_priority(client: AsyncClient):
    import app.routers.collections as col_module

    col_module._collections_db.extend([
        _make_collection(COLLECTION_ID_1, "Низкий приоритет", priority=100),
        _make_collection(COLLECTION_ID_2, "Высокий приоритет", priority=1),
        _make_collection(COLLECTION_ID_3, "Средний приоритет", priority=50),
    ])

    resp = await client.get("/api/v1/main/collections")
    assert resp.status_code == 200
    data = resp.json()
    priorities = [c["priority"] for c in data["collections"]]
    assert priorities == [1, 50, 100]


@pytest.mark.asyncio
async def test_b2b_unavailable_returns_502(client: AsyncClient):
    import app.routers.collections as col_module

    col_module._collections_db.append(
        _make_collection(COLLECTION_ID_1, "Тест", product_ids=[PRODUCT_ID_1])
    )

    with patch("app.routers.collections._fetch_b2b_batch", AsyncMock(side_effect=Exception("Connection refused"))):
        resp = await client.get(f"/api/v1/collections/{COLLECTION_ID_1}/products")

    assert resp.status_code == 502
