from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


SAMPLE_PRODUCTS_RESPONSE = {
    "items": [
        {
            "id": "770e8400-e29b-41d4-a716-446655440002",
            "title": "iPhone 15 Pro Max",
            "description": "Flagship smartphone",
            "status": "MODERATED",
            "category": {"id": "123e4567-e89b-12d3-a456-426614174001", "name": "Смартфоны"},
            "images": [{"url": "https://cdn.neomarket.ru/images/iphone15.jpg", "ordering": 0}],
            "characteristics": [
                {"name": "Бренд", "value": "Apple"},
                {"name": "Память", "value": "256"},
            ],
            "skus": [
                {
                    "id": "sku-1",
                    "name": "256GB Black",
                    "price": 12999000,
                    "discount": 0,
                    "image": "/s3/iphone.jpg",
                    "active_quantity": 10,
                    "characteristics": [],
                }
            ],
        },
        {
            "id": "770e8400-e29b-41d4-a716-446655440003",
            "title": "Samsung Galaxy S24",
            "description": "Android flagship",
            "status": "MODERATED",
            "category": {"id": "123e4567-e89b-12d3-a456-426614174001", "name": "Смартфоны"},
            "images": [{"url": "https://cdn.neomarket.ru/images/s24.jpg", "ordering": 0}],
            "characteristics": [
                {"name": "Бренд", "value": "Samsung"},
                {"name": "Память", "value": "128"},
            ],
            "skus": [
                {
                    "id": "sku-2",
                    "name": "128GB White",
                    "price": 8999000,
                    "discount": 500000,
                    "image": "/s3/s24.jpg",
                    "active_quantity": 5,
                    "characteristics": [],
                }
            ],
        },
    ],
    "total_count": 2,
    "limit": 20,
    "offset": 0,
}

EMPTY_PRODUCTS_RESPONSE = {
    "items": [],
    "total_count": 0,
    "limit": 20,
    "offset": 0,
}


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
