from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


FLAT_CATEGORIES = [
    {
        "id": "123e4567-e89b-12d3-a456-426614174002",
        "name": "Электроника",
        "slug": "electronics",
        "parent_id": None,
        "description": "Электронные устройства",
        "is_active": True,
        "image_url": "https://cdn.neomarket.ru/categories/electronics.jpg",
        "created_at": "2024-01-15T10:30:00Z",
        "updated_at": "2024-03-01T14:20:00Z",
    },
    {
        "id": "123e4567-e89b-12d3-a456-426614174003",
        "name": "Смартфоны",
        "slug": "smartphones",
        "parent_id": "123e4567-e89b-12d3-a456-426614174002",
        "description": "Мобильные телефоны и смартфоны",
        "is_active": True,
        "image_url": "https://cdn.neomarket.ru/categories/smartphones.jpg",
        "created_at": "2024-01-15T10:30:00Z",
        "updated_at": "2024-03-01T14:20:00Z",
    },
    {
        "id": "123e4567-e89b-12d3-a456-426614174004",
        "name": "Android",
        "slug": "android",
        "parent_id": "123e4567-e89b-12d3-a456-426614174003",
        "description": "Смартфоны на Android",
        "is_active": True,
        "image_url": "https://cdn.neomarket.ru/categories/android.jpg",
        "created_at": "2024-01-15T10:30:00Z",
        "updated_at": "2024-03-01T14:20:00Z",
    },
    {
        "id": "123e4567-e89b-12d3-a456-426614174010",
        "name": "Одежда",
        "slug": "clothing",
        "parent_id": None,
        "description": "Одежда и обувь",
        "is_active": True,
        "image_url": "https://cdn.neomarket.ru/categories/clothing.jpg",
        "created_at": "2024-01-15T10:30:00Z",
        "updated_at": "2024-03-01T14:20:00Z",
    },
]


ORPHAN_CATEGORIES = [
    {
        "id": "123e4567-e89b-12d3-a456-426614174002",
        "name": "Электроника",
        "slug": "electronics",
        "parent_id": None,
        "is_active": True,
    },
    {
        "id": "123e4567-e89b-12d3-a456-426614174003",
        "name": "Смартфоны",
        "slug": "smartphones",
        "parent_id": "123e4567-e89b-12d3-a456-426614170000",
        "is_active": True,
    },
]


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_category_tree_returns_nested_structure(mock_fetch):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.return_value = FLAT_CATEGORIES

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/categories")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    items = data["items"]

    electronics = next(i for i in items if i["name"] == "Электроника")
    assert electronics["parent_id"] is None
    assert "children" in electronics
    assert len(electronics["children"]) > 0

    smartphones = electronics["children"][0]
    assert smartphones["name"] == "Смартфоны"
    assert smartphones["parent_id"] == electronics["id"]
    assert len(smartphones["children"]) > 0

    android = smartphones["children"][0]
    assert android["name"] == "Android"
    assert android["parent_id"] == smartphones["id"]

    clothing = next(i for i in items if i["name"] == "Одежда")
    assert clothing["parent_id"] is None
    assert clothing["children"] == []

    cat_module.CATEGORIES_CACHE = None


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_breadcrumbs_return_path_from_root(mock_fetch):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.return_value = FLAT_CATEGORIES

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/breadcrumbs",
            params={"category_id": "123e4567-e89b-12d3-a456-426614174003"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "data" in data
    assert "meta" in data

    crumbs = data["data"]
    assert len(crumbs) == 2

    assert crumbs[0]["name"] == "Электроника"
    assert crumbs[0]["level"] == 0
    assert crumbs[0]["is_current"] is False
    assert crumbs[0]["url"] == "/catalog/electronics"

    assert crumbs[1]["name"] == "Смартфоны"
    assert crumbs[1]["level"] == 1
    assert crumbs[1]["is_current"] is True
    assert crumbs[1]["url"] == "/catalog/electronics/smartphones"

    assert data["meta"]["resolved_via"] == "category_id"
    assert data["meta"]["category_id"] == "123e4567-e89b-12d3-a456-426614174003"

    cat_module.CATEGORIES_CACHE = None


@pytest.mark.asyncio
async def test_ambiguous_params_returns_400(client: AsyncClient):
    response = await client.get(
        "/api/v1/breadcrumbs",
        params={
            "category_id": "123e4567-e89b-12d3-a456-426614174003",
            "product_id": "770e8400-e29b-41d4-a716-446655440002",
        },
    )

    assert response.status_code == 400
    data = response.json()
    assert data["error"] == "ambiguous_param"
    assert "only one of category_id or product_id" in data["message"]


@pytest.mark.asyncio
async def test_missing_param_returns_400(client: AsyncClient):
    response = await client.get("/api/v1/breadcrumbs")

    assert response.status_code == 400
    data = response.json()
    assert data["error"] == "missing_param"
    assert "category_id or product_id must be provided" in data["message"]


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_unknown_category_returns_404(mock_fetch, client: AsyncClient):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.return_value = FLAT_CATEGORIES

    response = await client.get(
        "/api/v1/breadcrumbs",
        params={"category_id": "00000000-0000-0000-0000-000000000000"},
    )

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "NOT_FOUND"

    cat_module.CATEGORIES_CACHE = None


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_orphan_node_returns_422(mock_fetch, client: AsyncClient):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.return_value = ORPHAN_CATEGORIES

    response = await client.get(
        "/api/v1/breadcrumbs",
        params={"category_id": "123e4567-e89b-12d3-a456-426614174003"},
    )

    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "orphan_node"
    assert "hierarchy is broken" in data["message"]

    cat_module.CATEGORIES_CACHE = None


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_category_tree_from_flat_list(mock_fetch):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.return_value = FLAT_CATEGORIES

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/categories")

    assert response.status_code == 200
    data = response.json()

    flat_items = [item["id"] for item in data["items"]]
    assert len(flat_items) == len(FLAT_CATEGORIES)
    for cat in FLAT_CATEGORIES:
        assert cat["id"] in flat_items

    cat_module.CATEGORIES_CACHE = None


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_category_details_returns_full_data(mock_fetch, client: AsyncClient):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.return_value = FLAT_CATEGORIES

    response = await client.get("/api/v1/categories/123e4567-e89b-12d3-a456-426614174003")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "123e4567-e89b-12d3-a456-426614174003"
    assert data["name"] == "Смартфоны"
    assert data["slug"] == "smartphones"
    assert data["parent"] is not None
    assert data["parent"]["id"] == "123e4567-e89b-12d3-a456-426614174002"
    assert data["parent"]["name"] == "Электроника"
    assert "seo" in data
    assert "meta_tags" in data
    assert data["is_active"] is True

    cat_module.CATEGORIES_CACHE = None


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_category_details_invalid_uuid_returns_404(mock_fetch, client: AsyncClient):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.return_value = FLAT_CATEGORIES

    response = await client.get("/api/v1/categories/not-a-valid-uuid")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"

    cat_module.CATEGORIES_CACHE = None


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_category_details_nonexistent_id_returns_404(mock_fetch, client: AsyncClient):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.return_value = FLAT_CATEGORIES

    response = await client.get("/api/v1/categories/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"

    cat_module.CATEGORIES_CACHE = None


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_breadcrumbs_root_category(mock_fetch):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.return_value = FLAT_CATEGORIES

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/breadcrumbs",
            params={"category_id": "123e4567-e89b-12d3-a456-426614174002"},
        )

    assert response.status_code == 200
    data = response.json()
    crumbs = data["data"]
    assert len(crumbs) == 1
    assert crumbs[0]["name"] == "Электроника"
    assert crumbs[0]["level"] == 0
    assert crumbs[0]["is_current"] is True
    assert crumbs[0]["url"] == "/catalog/electronics"

    cat_module.CATEGORIES_CACHE = None


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_breadcrumbs_deep_nested_category(mock_fetch):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.return_value = FLAT_CATEGORIES

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/breadcrumbs",
            params={"category_id": "123e4567-e89b-12d3-a456-426614174004"},
        )

    assert response.status_code == 200
    data = response.json()
    crumbs = data["data"]
    assert len(crumbs) == 3
    assert crumbs[0]["name"] == "Электроника"
    assert crumbs[0]["level"] == 0
    assert crumbs[1]["name"] == "Смартфоны"
    assert crumbs[1]["level"] == 1
    assert crumbs[2]["name"] == "Android"
    assert crumbs[2]["level"] == 2
    assert crumbs[2]["is_current"] is True
    assert crumbs[2]["url"] == "/catalog/electronics/smartphones/android"

    cat_module.CATEGORIES_CACHE = None


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_category_tree_b2b_unavailable_returns_502(mock_fetch):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.side_effect = Exception("Connection refused")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/categories")

    assert response.status_code == 502
    assert response.json()["code"] == "B2B_UNAVAILABLE"

    cat_module.CATEGORIES_CACHE = None


@pytest.mark.asyncio
@patch("app.routers.categories.fetch_flat_categories_from_b2b", new_callable=AsyncMock)
async def test_category_details_b2b_unavailable_returns_502(mock_fetch, client: AsyncClient):
    import app.routers.categories as cat_module

    cat_module.CATEGORIES_CACHE = None
    mock_fetch.side_effect = Exception("Connection refused")

    response = await client.get("/api/v1/categories/123e4567-e89b-12d3-a456-426614174003")
    assert response.status_code == 502
    assert response.json()["code"] == "B2B_UNAVAILABLE"

    cat_module.CATEGORIES_CACHE = None
