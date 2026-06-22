import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_product_returns_201_with_created_status(
    client: AsyncClient, test_category, auth_headers
):
    response = await client.post(
        "/api/v1/products",
        json={
            "title": "iPhone 15 Pro Max",
            "description": "Флагманский смартфон Apple 2024 года",
            "category_id": test_category.id,
            "images": [{"url": "/s3/iphone15-front.jpg", "ordering": 0}],
            "characteristics": [{"name": "Бренд", "value": "Apple"}],
        },
        headers=auth_headers,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "CREATED"
    assert data["deleted"] is False
    assert data["blocked"] is False
    assert data["skus"] == []
    assert data["title"] == "iPhone 15 Pro Max"
    assert data["description"] == "Флагманский смартфон Apple 2024 года"
    assert data["category"]["id"] == test_category.id
    assert data["category"]["name"] == "Смартфоны"
    assert len(data["images"]) == 1
    assert data["images"][0]["url"] == "/s3/iphone15-front.jpg"
    assert data["images"][0]["ordering"] == 0
    assert len(data["characteristics"]) == 1
    assert data["characteristics"][0]["name"] == "Бренд"
    assert data["characteristics"][0]["value"] == "Apple"
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_seller_id_taken_from_jwt(
    client: AsyncClient, test_category, db_session
):
    from sqlalchemy import select

    from app.auth import create_access_token
    from app.models import Product

    seller_id = "fixed-seller-id-from-jwt"
    token = create_access_token({"sub": seller_id})
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.post(
        "/api/v1/products",
        json={
            "title": "Samsung Galaxy S24",
            "description": "Флагман Samsung",
            "category_id": test_category.id,
            "images": [{"url": "/s3/samsung.jpg", "ordering": 0}],
        },
        headers=headers,
    )

    assert response.status_code == 201
    data = response.json()

    result = await db_session.execute(
        select(Product).where(Product.id == data["id"])
    )
    product = result.scalar_one()
    assert product.seller_id == seller_id


@pytest.mark.asyncio
async def test_missing_images_returns_400(
    client: AsyncClient, test_category, auth_headers
):
    response = await client.post(
        "/api/v1/products",
        json={
            "title": "Товар без фото",
            "description": "Описание",
            "category_id": test_category.id,
            "images": [],
        },
        headers=auth_headers,
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "At least one image is required" in data["message"]


@pytest.mark.asyncio
async def test_missing_category_returns_400(
    client: AsyncClient, auth_headers
):
    response = await client.post(
        "/api/v1/products",
        json={
            "title": "Товар",
            "description": "Описание",
            "category_id": "00000000-0000-0000-0000-000000000000",
            "images": [{"url": "/s3/img.jpg", "ordering": 0}],
        },
        headers=auth_headers,
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "Category not found" in data["message"]


@pytest.mark.asyncio
async def test_invalid_category_id_returns_400(
    client: AsyncClient, auth_headers
):
    response = await client.post(
        "/api/v1/products",
        json={
            "title": "Товар",
            "description": "Описание",
            "category_id": "not-a-uuid",
            "images": [{"url": "/s3/img.jpg", "ordering": 0}],
        },
        headers=auth_headers,
    )

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert "category_id must be a valid UUID" in data["message"]
