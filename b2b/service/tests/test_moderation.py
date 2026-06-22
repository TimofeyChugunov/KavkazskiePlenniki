import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import BlockingReason, Category, FieldReport, Product, SKU


@pytest_asyncio.fixture
async def product_on_moderation(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
        category_id=test_category.id,
        title="Product On Moderation",
        slug="product-on-moderation",
        description="Awaiting moderation",
        status="ON_MODERATION",
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
async def product_with_blocking_data(db_session: AsyncSession, test_category: Category) -> Product:
    product = Product(
        id=str(uuid.uuid4()),
        seller_id="test-seller-id",
        category_id=test_category.id,
        title="Blocked Product With Data",
        slug="blocked-product-with-data",
        description="Previously blocked with data",
        status="BLOCKED",
        deleted=False,
        blocked=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    await db_session.flush()

    db_session.add(BlockingReason(
        id=str(uuid.uuid4()),
        product_id=product.id,
        title="Old reason",
        comment="Old comment",
    ))
    db_session.add(FieldReport(
        id=str(uuid.uuid4()),
        product_id=product.id,
        field_name="description",
        sku_id=None,
        comment="Old report",
    ))
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
def seller_auth() -> dict:
    token = __import__("app.auth", fromlist=["create_access_token"]).create_access_token(
        {"sub": "test-seller-id"}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
def mod_service_key() -> dict:
    return {"X-Service-Key": settings.MOD_TO_B2B_KEY}


@pytest.mark.asyncio
@patch("app.routers.moderation.send_b2c_product_blocked", new_callable=AsyncMock)
async def test_moderated_event_clears_blocking_data(
    mock_b2c: AsyncMock,
    client: AsyncClient,
    product_with_blocking_data: Product,
    mod_service_key: dict,
    db_session: AsyncSession,
):
    response = await client.post(
        "/api/v1/events/moderation",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": product_with_blocking_data.id,
            "status": "MODERATED",
        },
        headers=mod_service_key,
    )

    assert response.status_code == 200

    await db_session.refresh(product_with_blocking_data)
    assert product_with_blocking_data.status == "MODERATED"
    assert product_with_blocking_data.blocked is False

    reasons = (await db_session.execute(
        select(BlockingReason).where(BlockingReason.product_id == product_with_blocking_data.id)
    )).scalars().all()
    assert len(reasons) == 0

    reports = (await db_session.execute(
        select(FieldReport).where(FieldReport.product_id == product_with_blocking_data.id)
    )).scalars().all()
    assert len(reports) == 0

    mock_b2c.assert_not_called()


@pytest.mark.asyncio
@patch("app.routers.moderation.send_b2c_product_blocked", new_callable=AsyncMock)
async def test_blocked_soft_saves_field_reports(
    mock_b2c: AsyncMock,
    client: AsyncClient,
    product_on_moderation: Product,
    mod_service_key: dict,
    db_session: AsyncSession,
):
    response = await client.post(
        "/api/v1/events/moderation",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": product_on_moderation.id,
            "status": "BLOCKED",
            "hard_block": False,
            "blocking_reason": {
                "id": str(uuid.uuid4()),
                "title": "Описание не соответствует товару",
                "comment": "Несоответствие описания и фотографий",
            },
            "field_reports": [
                {
                    "field_name": "description",
                    "sku_id": None,
                    "comment": "Текст описания скопирован с другого товара",
                }
            ],
        },
        headers=mod_service_key,
    )

    assert response.status_code == 200

    await db_session.refresh(product_on_moderation)
    assert product_on_moderation.status == "BLOCKED"
    assert product_on_moderation.blocked is True

    reasons = (await db_session.execute(
        select(BlockingReason).where(BlockingReason.product_id == product_on_moderation.id)
    )).scalars().all()
    assert len(reasons) == 1
    assert reasons[0].title == "Описание не соответствует товару"

    reports = (await db_session.execute(
        select(FieldReport).where(FieldReport.product_id == product_on_moderation.id)
    )).scalars().all()
    assert len(reports) == 1
    assert reports[0].field_name == "description"
    assert reports[0].comment == "Текст описания скопирован с другого товара"

    mock_b2c.assert_called_once()
    call_kwargs = mock_b2c.call_args.kwargs
    assert call_kwargs["product_id"] == product_on_moderation.id


@pytest.mark.asyncio
@patch("app.routers.moderation.send_b2c_product_blocked", new_callable=AsyncMock)
async def test_blocked_hard_sets_terminal_status(
    mock_b2c: AsyncMock,
    client: AsyncClient,
    product_on_moderation: Product,
    mod_service_key: dict,
    db_session: AsyncSession,
):
    response = await client.post(
        "/api/v1/events/moderation",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": product_on_moderation.id,
            "status": "BLOCKED",
            "hard_block": True,
            "blocking_reason": {
                "id": str(uuid.uuid4()),
                "title": "Серьёзное нарушение",
                "comment": "Нарушение правил площадки",
            },
        },
        headers=mod_service_key,
    )

    assert response.status_code == 200

    await db_session.refresh(product_on_moderation)
    assert product_on_moderation.status == "HARD_BLOCKED"
    assert product_on_moderation.blocked is True

    mock_b2c.assert_called_once()


@pytest.mark.asyncio
async def test_hard_blocked_product_rejects_seller_edits(
    client: AsyncClient,
    product_on_moderation: Product,
    mod_service_key: dict,
    db_session: AsyncSession,
    seller_auth: dict,
):
    await client.post(
        "/api/v1/events/moderation",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": product_on_moderation.id,
            "status": "BLOCKED",
            "hard_block": True,
        },
        headers=mod_service_key,
    )

    response = await client.put(
        f"/api/v1/products/{product_on_moderation.id}",
        json={"title": "Try to edit hard blocked"},
        headers=seller_auth,
    )

    assert response.status_code == 403
    data = response.json()
    assert data["code"] == "FORBIDDEN"

    response = await client.delete(
        f"/api/v1/products/{product_on_moderation.id}",
        headers=seller_auth,
    )

    assert response.status_code == 403
    data = response.json()
    assert data["code"] == "FORBIDDEN"


@pytest.mark.asyncio
@patch("app.routers.moderation.send_b2c_product_blocked", new_callable=AsyncMock)
async def test_duplicate_event_same_idempotency_key_no_side_effects(
    mock_b2c: AsyncMock,
    client: AsyncClient,
    product_on_moderation: Product,
    mod_service_key: dict,
    db_session: AsyncSession,
):
    idempotency_key = str(uuid.uuid4())

    response1 = await client.post(
        "/api/v1/events/moderation",
        json={
            "idempotency_key": idempotency_key,
            "product_id": product_on_moderation.id,
            "status": "BLOCKED",
            "hard_block": True,
        },
        headers=mod_service_key,
    )
    assert response1.status_code == 200

    await db_session.refresh(product_on_moderation)
    assert product_on_moderation.status == "HARD_BLOCKED"

    product_on_moderation.status = "BLOCKED"
    await db_session.commit()

    mock_b2c.reset_mock()

    response2 = await client.post(
        "/api/v1/events/moderation",
        json={
            "idempotency_key": idempotency_key,
            "product_id": product_on_moderation.id,
            "status": "BLOCKED",
            "hard_block": False,
        },
        headers=mod_service_key,
    )
    assert response2.status_code == 200

    await db_session.refresh(product_on_moderation)
    assert product_on_moderation.status == "BLOCKED"
    assert product_on_moderation.blocked is True

    mock_b2c.assert_not_called()


@pytest.mark.asyncio
async def test_missing_service_key_returns_401(
    client: AsyncClient,
    product_on_moderation: Product,
):
    response = await client.post(
        "/api/v1/events/moderation",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": product_on_moderation.id,
            "status": "MODERATED",
        },
    )

    assert response.status_code == 401
    data = response.json()
    assert data["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_invalid_service_key_returns_401(
    client: AsyncClient,
    product_on_moderation: Product,
):
    response = await client.post(
        "/api/v1/events/moderation",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": product_on_moderation.id,
            "status": "MODERATED",
        },
        headers={"X-Service-Key": "wrong-key"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_nonexistent_product_returns_404(
    client: AsyncClient,
    mod_service_key: dict,
):
    response = await client.post(
        "/api/v1/events/moderation",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": str(uuid.uuid4()),
            "status": "MODERATED",
        },
        headers=mod_service_key,
    )

    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "NOT_FOUND"


@pytest.mark.asyncio
@patch("app.routers.moderation.send_b2c_product_blocked", new_callable=AsyncMock)
async def test_invalid_status_returns_400(
    mock_b2c: AsyncMock,
    client: AsyncClient,
    product_on_moderation: Product,
    mod_service_key: dict,
):
    response = await client.post(
        "/api/v1/events/moderation",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": product_on_moderation.id,
            "status": "INVALID",
        },
        headers=mod_service_key,
    )

    assert response.status_code == 400


@pytest.mark.asyncio
@patch("app.routers.moderation.send_b2c_product_blocked", new_callable=AsyncMock)
async def test_blocked_soft_clears_old_blocking_data(
    mock_b2c: AsyncMock,
    client: AsyncClient,
    product_with_blocking_data: Product,
    mod_service_key: dict,
    db_session: AsyncSession,
):
    response = await client.post(
        "/api/v1/events/moderation",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "product_id": product_with_blocking_data.id,
            "status": "BLOCKED",
            "hard_block": False,
            "blocking_reason": {
                "id": str(uuid.uuid4()),
                "title": "Новая причина",
                "comment": "Новый комментарий",
            },
            "field_reports": [
                {
                    "field_name": "title",
                    "sku_id": None,
                    "comment": "Новое замечание",
                }
            ],
        },
        headers=mod_service_key,
    )

    assert response.status_code == 200

    reasons = (await db_session.execute(
        select(BlockingReason).where(BlockingReason.product_id == product_with_blocking_data.id)
    )).scalars().all()
    assert len(reasons) == 1
    assert reasons[0].title == "Новая причина"

    reports = (await db_session.execute(
        select(FieldReport).where(FieldReport.product_id == product_with_blocking_data.id)
    )).scalars().all()
    assert len(reports) == 1
    assert reports[0].field_name == "title"
