from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import EMPTY_PRODUCTS_RESPONSE, SAMPLE_PRODUCTS_RESPONSE


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_catalog_returns_filtered_sorted_products(mock_fetch):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/products",
            params={
                "category_id": "123e4567-e89b-12d3-a456-426614174001",
                "sort": "price_asc",
                "limit": 20,
                "offset": 0,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total_count" in data
    assert data["limit"] == 20
    assert data["offset"] == 0
    assert data["total_count"] == 2
    assert len(data["items"]) == 2

    item = data["items"][0]
    assert "id" in item
    assert "title" in item
    assert "image" in item
    assert "price" in item
    assert "in_stock" in item
    assert "is_in_cart" in item

    call_args = mock_fetch.call_args
    assert "/api/v1/public/products" in call_args[0][0]
    params = call_args[0][1]
    assert params["sort"] == "price_asc"
    assert params["category_id"] == "123e4567-e89b-12d3-a456-426614174001"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_catalog_with_multiple_filters(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={
            "category_id": "123e4567-e89b-12d3-a456-426614174001",
            "sort": "popularity",
            "filters[brand]": "Apple",
            "filters[memory]": "256",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert data["total_count"] == 2

    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert "filters[brand]" in params
    assert params["filters[brand]"] == "Apple"
    assert "filters[memory]" in params
    assert params["filters[memory]"] == "256"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_facets_return_counts_per_filter_value(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/catalog/facets",
        params={"category_id": "123e4567-e89b-12d3-a456-426614174001"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "category_id" in data
    assert data["category_id"] == "123e4567-e89b-12d3-a456-426614174001"
    assert "facets" in data
    assert len(data["facets"]) > 0

    brand_facet = next((f for f in data["facets"] if f["name"] == "Бренд"), None)
    assert brand_facet is not None
    assert len(brand_facet["values"]) == 2

    brand_counts = {v["value"]: v["count"] for v in brand_facet["values"]}
    assert brand_counts["Apple"] == 1
    assert brand_counts["Samsung"] == 1

    for facet in data["facets"]:
        assert "name" in facet
        assert "values" in facet
        for val in facet["values"]:
            assert "value" in val
            assert "count" in val
            assert val["count"] > 0


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_facets_with_filters(mock_fetch, client: AsyncClient):
    only_apple = {
        "items": [SAMPLE_PRODUCTS_RESPONSE["items"][0]],
        "total_count": 1,
        "limit": 20,
        "offset": 0,
    }
    mock_fetch.return_value = (200, only_apple)

    response = await client.get(
        "/api/v1/catalog/facets",
        params={
            "category_id": "123e4567-e89b-12d3-a456-426614174001",
            "filters[brand]": "Apple",
        },
    )

    assert response.status_code == 200
    data = response.json()
    brand_facet = next((f for f in data["facets"] if f["name"] == "Бренд"), None)
    assert brand_facet is not None
    assert len(brand_facet["values"]) == 1
    assert brand_facet["values"][0]["value"] == "Apple"
    assert brand_facet["values"][0]["count"] == 1


@pytest.mark.asyncio
async def test_invalid_sort_returns_400(client: AsyncClient):
    response = await client.get(
        "/api/v1/products",
        params={"sort": "invalid_sort"},
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "Invalid sort parameter" in data["message"]
    assert "rating" in data["message"]
    assert "popularity" in data["message"]
    assert "price_asc" in data["message"]
    assert "price_desc" in data["message"]
    assert "date_desc" in data["message"]
    assert "discount_desc" in data["message"]


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_b2b_unavailable_returns_502(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (500, {"code": "INTERNAL_ERROR", "message": "Server error"})

    response = await client.get("/api/v1/products")

    assert response.status_code == 502
    data = response.json()
    assert data["code"] == "B2B_UNAVAILABLE"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_b2b_503_returns_502(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (503, {"code": "SERVICE_UNAVAILABLE", "message": "Maintenance"})

    response = await client.get("/api/v1/products")

    assert response.status_code == 502
    data = response.json()
    assert data["code"] == "B2B_UNAVAILABLE"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_nonexistent_category_returns_404(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (404, {"code": "NOT_FOUND", "message": "Not found"})

    response = await client.get(
        "/api/v1/products",
        params={"category_id": "nonexistent-uuid"},
    )

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "NOT_FOUND"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_empty_category_returns_empty_list(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, EMPTY_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={"category_id": "empty-category-uuid"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total_count"] == 0


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_pagination_params(mock_fetch, client: AsyncClient):
    page_response = {
        "items": [SAMPLE_PRODUCTS_RESPONSE["items"][0]],
        "total_count": 50,
        "limit": 10,
        "offset": 20,
    }
    mock_fetch.return_value = (200, page_response)

    response = await client.get(
        "/api/v1/products",
        params={"limit": 10, "offset": 20},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 10
    assert data["offset"] == 20
    assert data["total_count"] == 50


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_sort_by_date_desc(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={"sort": "date_desc"},
    )

    assert response.status_code == 200
    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert params["sort"] == "created_desc"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_sort_by_discount_desc(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={"sort": "discount_desc"},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_category_filters_endpoint(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/categories/123e4567-e89b-12d3-a456-426614174001/filters",
    )

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) >= 1

    brand_filter = next((f for f in data["items"] if f["slug"] == "brand"), None)
    assert brand_filter is not None
    assert brand_filter["type"] == "list"
    assert "Apple" in brand_filter["value"]
    assert "Samsung" in brand_filter["value"]

    price_filter = next((f for f in data["items"] if f["slug"] == "price"), None)
    assert price_filter is not None
    assert price_filter["type"] == "range"
    assert price_filter["min"] == 8999000
    assert price_filter["max"] == 12999000


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_category_filters_not_found(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (404, {"code": "NOT_FOUND", "message": "Not found"})

    response = await client.get(
        "/api/v1/categories/nonexistent/filters",
    )

    assert response.status_code == 404


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_in_stock_detection(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get("/api/v1/products")

    assert response.status_code == 200
    data = response.json()

    for item in data["items"]:
        assert isinstance(item["in_stock"], bool)
        assert isinstance(item["is_in_cart"], bool)

    iphone = next(i for i in data["items"] if i["title"] == "iPhone 15 Pro Max")
    assert iphone["in_stock"] is True

    samsung = next(i for i in data["items"] if i["title"] == "Samsung Galaxy S24")
    assert samsung["in_stock"] is True


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_out_of_stock_product(mock_fetch, client: AsyncClient):
    out_of_stock_response = {
        "items": [
            {
                "id": "out-of-stock-id",
                "title": "Out of Stock Phone",
                "description": "No stock",
                "status": "MODERATED",
                "category": {"id": "cat-1", "name": "Phones"},
                "images": [{"url": "https://example.com/img.jpg", "ordering": 0}],
                "characteristics": [],
                "skus": [
                    {
                        "id": "sku-oos",
                        "name": "No Stock SKU",
                        "price": 500000,
                        "discount": 0,
                        "image": "/s3/oos.jpg",
                        "active_quantity": 0,
                        "characteristics": [],
                    }
                ],
            }
        ],
        "total_count": 1,
        "limit": 20,
        "offset": 0,
    }
    mock_fetch.return_value = (200, out_of_stock_response)

    response = await client.get("/api/v1/products")

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["in_stock"] is False


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_facets_empty_category(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, EMPTY_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/catalog/facets",
        params={"category_id": "empty-cat"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["facets"] == []
    assert data["category_id"] == "empty-cat"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_facets_b2b_error_returns_502(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (500, {"code": "ERROR", "message": "fail"})

    response = await client.get("/api/v1/catalog/facets")

    assert response.status_code == 502


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_category_filters_b2b_error_returns_502(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (500, {"code": "ERROR", "message": "fail"})

    response = await client.get("/api/v1/categories/some-id/filters")

    assert response.status_code == 502


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_sort_by_rating_uses_popular(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={"sort": "rating"},
    )

    assert response.status_code == 200
    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert params["sort"] == "popular"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_sort_by_popularity_uses_popular(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={"sort": "popularity"},
    )

    assert response.status_code == 200
    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert params["sort"] == "popular"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_sort_by_price_asc(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={"sort": "price_asc"},
    )

    assert response.status_code == 200
    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert params["sort"] == "price_asc"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_sort_by_price_desc(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={"sort": "price_desc"},
    )

    assert response.status_code == 200
    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert params["sort"] == "price_desc"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_image_from_first_product(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get("/api/v1/products")

    assert response.status_code == 200
    data = response.json()
    assert data["items"][0]["image"] == "https://cdn.neomarket.ru/images/iphone15.jpg"
    assert data["items"][1]["image"] == "https://cdn.neomarket.ru/images/s24.jpg"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_b2b_auth_failure_returns_502(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (401, {"code": "UNAUTHORIZED", "message": "Invalid key"})

    response = await client.get("/api/v1/products")

    assert response.status_code == 502
    data = response.json()
    assert data["code"] == "B2B_ERROR"


@pytest.mark.asyncio
async def test_invalid_sort_returns_400_with_exact_message(client: AsyncClient):
    response = await client.get(
        "/api/v1/products",
        params={"sort": "wrong"},
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    expected = "Invalid sort parameter. Allowed: rating, popularity, price_asc, price_desc, date_desc, discount_desc"
    assert data["message"] == expected


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_response_has_no_old_price_field(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get("/api/v1/products")

    assert response.status_code == 200
    data = response.json()
    for item in data["items"]:
        assert "old_price" not in item


# ==================== US-CAT-02: Текстовый поиск ====================


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_search_returns_matching_products(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={"search": "iPhone"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert data["total_count"] == 2

    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert params["search"] == "iPhone"


@pytest.mark.asyncio
async def test_short_query_returns_400(client: AsyncClient):
    response = await client.get(
        "/api/v1/products",
        params={"search": "ab"},
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "Search query must be at least 3 characters" in data["message"]


@pytest.mark.asyncio
async def test_one_char_query_returns_400(client: AsyncClient):
    response = await client.get(
        "/api/v1/products",
        params={"search": "a"},
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_two_char_query_returns_400(client: AsyncClient):
    response = await client.get(
        "/api/v1/products",
        params={"search": "ab"},
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_exactly_3_chars_is_valid(client: AsyncClient):
    with patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (200, EMPTY_PRODUCTS_RESPONSE)

        response = await client.get(
            "/api/v1/products",
            params={"search": "abc"},
        )

        assert response.status_code == 200


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_long_query_returns_400(mock_fetch, client: AsyncClient):
    long_query = "a" * 256

    response = await client.get(
        "/api/v1/products",
        params={"search": long_query},
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "Search query must be at most 255 characters" in data["message"]


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_exactly_255_chars_is_valid(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, EMPTY_PRODUCTS_RESPONSE)
    query_255 = "a" * 255

    response = await client.get(
        "/api/v1/products",
        params={"search": query_255},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_special_chars_do_not_break_query(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, EMPTY_PRODUCTS_RESPONSE)

    special_queries = [
        "iPhone%15",
        "кофе'",
        "test%",
        "test_",
        "test'",
        "%test%",
        "_test_",
        "a'b'c",
        'test"',
        "test\\",
    ]

    for query in special_queries:
        mock_fetch.reset_mock()
        mock_fetch.return_value = (200, EMPTY_PRODUCTS_RESPONSE)

        response = await client.get(
            "/api/v1/products",
            params={"search": query},
        )

        assert response.status_code == 200, f"Failed for query: {query}"

        call_args = mock_fetch.call_args
        params = call_args[0][1]
        assert params["search"] == query, f"Search param not passed for: {query}"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_empty_results_returns_200(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, EMPTY_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={"search": "несуществующийтовар123"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total_count"] == 0


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_search_with_category_filter(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={
            "search": "наушники",
            "category_id": "123e4567-e89b-12d3-a456-426614174001",
        },
    )

    assert response.status_code == 200
    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert params["search"] == "наушники"
    assert params["category_id"] == "123e4567-e89b-12d3-a456-426614174001"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_search_with_filters_and_sort(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={
            "search": "наушники",
            "category_id": "123e4567-e89b-12d3-a456-426614174001",
            "filters[brand]": "Sony",
            "sort": "price_asc",
        },
    )

    assert response.status_code == 200
    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert params["search"] == "наушники"
    assert params["filters[brand]"] == "Sony"
    assert params["sort"] == "price_asc"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_search_without_search_param_works(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get("/api/v1/products")

    assert response.status_code == 200
    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert "search" not in params


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_search_with_pagination(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={
            "search": "iPhone",
            "limit": 5,
            "offset": 10,
        },
    )

    assert response.status_code == 200
    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert params["search"] == "iPhone"
    assert params["limit"] == 5
    assert params["offset"] == 10


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_search_cyrillic_query(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCTS_RESPONSE)

    response = await client.get(
        "/api/v1/products",
        params={"search": "беспроводные наушники"},
    )

    assert response.status_code == 200
    call_args = mock_fetch.call_args
    params = call_args[0][1]
    assert params["search"] == "беспроводные наушники"


# ==================== US-CAT-03: Карточка товара ====================


SAMPLE_PRODUCT_CARD = {
    "id": "770e8400-e29b-41d4-a716-446655440002",
    "title": "iPhone 15 Pro Max",
    "description": "Флагманский смартфон Apple 2024 года с чипом A17 Pro",
    "status": "MODERATED",
    "category": {"id": "123e4567-e89b-12d3-a456-426614174001", "name": "Смартфоны"},
    "images": [
        {"url": "https://cdn.neomarket.ru/images/iphone15-front.jpg", "ordering": 0},
        {"url": "https://cdn.neomarket.ru/images/iphone15-back.jpg", "ordering": 1},
    ],
    "characteristics": [
        {"name": "Бренд", "value": "Apple"},
        {"name": "Страна-производитель", "value": "Китай"},
    ],
    "skus": [
        {
            "id": "660e8400-e29b-41d4-a716-446655440001",
            "name": "256GB Black",
            "price": 12999000,
            "discount": 0,
            "image": "/s3/iphone15-black-256.jpg",
            "active_quantity": 10,
            "characteristics": [
                {"name": "Цвет", "value": "Чёрный"},
                {"name": "Объём памяти", "value": "256 ГБ"},
            ],
        },
        {
            "id": "660e8400-e29b-41d4-a716-446655440002",
            "name": "256GB White",
            "price": 12999000,
            "discount": 500000,
            "image": "/s3/iphone15-white-256.jpg",
            "active_quantity": 3,
            "characteristics": [
                {"name": "Цвет", "value": "Белый"},
                {"name": "Объём памяти", "value": "256 ГБ"},
            ],
        },
    ],
}


PRODUCT_CARD_NO_STOCK = {
    "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "title": "No Stock Product",
    "description": "All SKUs out of stock",
    "status": "MODERATED",
    "category": {"id": "cat-11111111-2222-3333-4444-555555555555", "name": "Phones"},
    "images": [{"url": "https://example.com/img.jpg", "ordering": 0}],
    "characteristics": [],
    "skus": [
        {
            "id": "sku-11111111-2222-3333-4444-555555555555",
            "name": "Empty SKU",
            "price": 500000,
            "discount": 0,
            "image": "/s3/empty.jpg",
            "active_quantity": 0,
            "characteristics": [],
        }
    ],
}


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_product_card_returns_full_data_with_skus(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCT_CARD)

    response = await client.get(
        "/api/v1/products/770e8400-e29b-41d4-a716-446655440002",
    )

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == "770e8400-e29b-41d4-a716-446655440002"
    assert data["title"] == "iPhone 15 Pro Max"
    assert data["description"] == "Флагманский смартфон Apple 2024 года с чипом A17 Pro"
    assert data["status"] == "MODERATED"
    assert len(data["images"]) == 2
    assert data["images"][0]["url"] == "https://cdn.neomarket.ru/images/iphone15-front.jpg"
    assert data["images"][1]["url"] == "https://cdn.neomarket.ru/images/iphone15-back.jpg"
    assert len(data["characteristics"]) == 2
    assert len(data["skus"]) == 2

    sku1 = data["skus"][0]
    assert sku1["id"] == "660e8400-e29b-41d4-a716-446655440001"
    assert sku1["name"] == "256GB Black"
    assert sku1["price"] == 12999000
    assert sku1["discount"] == 0
    assert sku1["active_quantity"] == 10
    assert len(sku1["characteristics"]) == 2

    sku2 = data["skus"][1]
    assert sku2["id"] == "660e8400-e29b-41d4-a716-446655440002"
    assert sku2["price"] == 12999000
    assert sku2["discount"] == 500000
    assert sku2["active_quantity"] == 3

    call_args = mock_fetch.call_args
    assert "/api/v1/public/products/770e8400-e29b-41d4-a716-446655440002" in call_args[0][0]


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_cost_price_absent_in_response(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCT_CARD)

    response = await client.get(
        "/api/v1/products/770e8400-e29b-41d4-a716-446655440002",
    )

    assert response.status_code == 200
    data = response.json()

    for sku in data["skus"]:
        assert "cost_price" not in sku, "cost_price must not be in SKU response"
        assert "reserved_quantity" not in sku, "reserved_quantity must not be in SKU response"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_blocked_product_returns_404(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (404, {"code": "NOT_FOUND", "message": "Product not found"})

    response = await client.get(
        "/api/v1/products/770e8400-e29b-41d4-a716-446655440002",
    )

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "NOT_FOUND"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_sku_without_stock_is_shown_as_unavailable(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, PRODUCT_CARD_NO_STOCK)

    response = await client.get(
        "/api/v1/products/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["skus"]) == 1
    assert data["skus"][0]["active_quantity"] == 0


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_product_card_b2b_unavailable_returns_502(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (500, {"code": "INTERNAL_ERROR", "message": "Server error"})

    response = await client.get(
        "/api/v1/products/770e8400-e29b-41d4-a716-446655440002",
    )

    assert response.status_code == 502
    data = response.json()
    assert data["code"] == "B2B_UNAVAILABLE"


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_product_card_invalid_uuid_returns_404(mock_fetch, client: AsyncClient):
    response = await client.get(
        "/api/v1/products/not-a-valid-uuid",
    )

    assert response.status_code == 404
    assert mock_fetch.call_count == 0


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_product_card_with_discount(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCT_CARD)

    response = await client.get(
        "/api/v1/products/770e8400-e29b-41d4-a716-446655440002",
    )

    assert response.status_code == 200
    data = response.json()

    sku_with_discount = data["skus"][1]
    assert sku_with_discount["discount"] == 500000
    assert sku_with_discount["price"] == 12999000


@pytest.mark.asyncio
@patch("app.routers.catalog.fetch_from_b2b", new_callable=AsyncMock)
async def test_product_card_without_discount(mock_fetch, client: AsyncClient):
    mock_fetch.return_value = (200, SAMPLE_PRODUCT_CARD)

    response = await client.get(
        "/api/v1/products/770e8400-e29b-41d4-a716-446655440002",
    )

    assert response.status_code == 200
    data = response.json()

    sku_no_discount = data["skus"][0]
    assert sku_no_discount["discount"] == 0
    assert sku_no_discount["price"] == 12999000
