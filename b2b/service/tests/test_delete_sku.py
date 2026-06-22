import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token
from app.models import Category, Product, SKU


@pytest_asyncio.fixture
async def delete_test_data(db_session: AsyncSession, test_category: Category) -> dict:
    seller_id = "delete-seller-id"
    other_seller_id = "other-delete-seller"

    product = Product(
        id=str(uuid.uuid4()),
        seller_id=seller_id,
        category_id=test_category.id,
        title="Product with SKUs",
        slug="product-with-skus",
        description="Test",
        status="MODERATED",
        deleted=False,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    product_on_mod = Product(
        id=str(uuid.uuid4()),
        seller_id=seller_id,
        category_id=test_category.id,
        title="Product On Moderation",
        slug="product-on-mod",
        description="One SKU",
        status="ON_MODERATION",
        deleted=False,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    product_hard_blocked = Product(
        id=str(uuid.uuid4()),
        seller_id=seller_id,
        category_id=test_category.id,
        title="Hard Blocked",
        slug="hard-blocked",
        description="No delete",
        status="HARD_BLOCKED",
        deleted=False,
        blocked=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    product_other = Product(
        id=str(uuid.uuid4()),
        seller_id=other_seller_id,
        category_id=test_category.id,
        title="Other Seller",
        slug="other-seller",
        description="Not mine",
        status="MODERATED",
        deleted=False,
        blocked=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    db_session.add_all([product, product_on_mod, product_hard_blocked, product_other])
    await db_session.flush()

    sku1 = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="SKU1",
        price=1000,
        cost_price=500,
        image="/s3/sku1.jpg",
        active_quantity=10,
        reserved_quantity=0,
    )
    sku2 = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="SKU2",
        price=2000,
        cost_price=1000,
        image="/s3/sku2.jpg",
        active_quantity=5,
        reserved_quantity=0,
    )
    sku_on_mod = SKU(
        id=str(uuid.uuid4()),
        product_id=product_on_mod.id,
        name="Single SKU",
        price=3000,
        cost_price=1500,
        image="/s3/single.jpg",
        active_quantity=8,
        reserved_quantity=0,
    )
    sku_with_reserves = SKU(
        id=str(uuid.uuid4()),
        product_id=product.id,
        name="Reserved SKU",
        price=4000,
        cost_price=2000,
        image="/s3/reserved.jpg",
        active_quantity=7,
        reserved_quantity=3,
    )
    sku_hard = SKU(
        id=str(uuid.uuid4()),
        product_id=product_hard_blocked.id,
        name="Hard SKU",
        price=500,
        cost_price=250,
        image="/s3/hard.jpg",
        active_quantity=1,
        reserved_quantity=0,
    )
    sku_other = SKU(
        id=str(uuid.uuid4()),
        product_id=product_other.id,
        name="Other SKU",
        price=999,
        cost_price=500,
        image="/s3/other.jpg",
        active_quantity=4,
        reserved_quantity=0,
    )

    db_session.add_all([sku1, sku2, sku_on_mod, sku_with_reserves, sku_hard, sku_other])
    await db_session.commit()

    return {
        "seller_id": seller_id,
        "other_seller_id": other_seller_id,
        "product": product,
        "product_on_mod": product_on_mod,
        "product_hard_blocked": product_hard_blocked,
        "product_other": product_other,
        "sku1": sku1,
        "sku2": sku2,
        "sku_on_mod": sku_on_mod,
        "sku_with_reserves": sku_with_reserves,
        "sku_hard": sku_hard,
        "sku_other": sku_other,
    }


@pytest_asyncio.fixture
def seller_auth() -> dict:
    token = create_access_token({"sub": "delete-seller-id"})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
@patch("app.routers.skus.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_delete_sku_succeeds(
    mock_oos: AsyncMock,
    mock_mod: AsyncMock,
    client: AsyncClient,
    delete_test_data: dict,
    seller_auth: dict,
    db_session: AsyncSession,
):
    sku_id = delete_test_data["sku1"].id
    product_id = delete_test_data["product"].id

    response = await client.delete(
        f"/api/v1/skus/{sku_id}",
        headers=seller_auth,
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True

    db_session.expire_all()
    sku = await db_session.get(SKU, sku_id)
    assert sku is None

    remaining = (await db_session.execute(
        select(SKU).where(SKU.product_id == product_id)
    )).scalars().all()
    assert len(remaining) == 2
    remaining_ids = {s.id for s in remaining}
    assert sku_id not in remaining_ids


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
async def test_last_sku_on_moderation_transitions_product_to_created(
    mock_mod: AsyncMock,
    client: AsyncClient,
    delete_test_data: dict,
    seller_auth: dict,
    db_session: AsyncSession,
):
    sku_id = delete_test_data["sku_on_mod"].id
    product_id = delete_test_data["product_on_mod"].id

    response = await client.delete(
        f"/api/v1/skus/{sku_id}",
        headers=seller_auth,
    )

    assert response.status_code == 200

    db_session.expire_all()
    product = await db_session.get(Product, product_id)
    assert product.status == "CREATED"

    mock_mod.assert_called_once()
    assert mock_mod.call_args.kwargs["product_id"] == product_id
    assert mock_mod.call_args.kwargs["event"] == "DELETED"


@pytest.mark.asyncio
async def test_delete_sku_with_active_reserves_returns_409(
    client: AsyncClient,
    delete_test_data: dict,
    seller_auth: dict,
):
    sku_id = delete_test_data["sku_with_reserves"].id

    response = await client.delete(
        f"/api/v1/skus/{sku_id}",
        headers=seller_auth,
    )

    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_delete_sku_hard_blocked_product_returns_403(
    client: AsyncClient,
    delete_test_data: dict,
    seller_auth: dict,
):
    sku_id = delete_test_data["sku_hard"].id

    response = await client.delete(
        f"/api/v1/skus/{sku_id}",
        headers=seller_auth,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
@patch("app.routers.skus.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_sku_out_of_stock_event_on_moderated_product(
    mock_oos: AsyncMock,
    mock_mod: AsyncMock,
    client: AsyncClient,
    delete_test_data: dict,
    seller_auth: dict,
    db_session: AsyncSession,
):
    product = delete_test_data["product"]
    product.status = "MODERATED"
    await db_session.commit()

    sku_id = delete_test_data["sku1"].id

    response = await client.delete(
        f"/api/v1/skus/{sku_id}",
        headers=seller_auth,
    )

    assert response.status_code == 200

    mock_oos.assert_called_once()
    assert mock_oos.call_args[0][0] == sku_id


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
@patch("app.routers.skus.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_delete_sku_not_owner_returns_403(
    mock_oos: AsyncMock,
    mock_mod: AsyncMock,
    client: AsyncClient,
    delete_test_data: dict,
    db_session: AsyncSession,
):
    other_token = create_access_token({"sub": delete_test_data["other_seller_id"]})
    headers = {"Authorization": f"Bearer {other_token}"}

    sku_id = delete_test_data["sku1"].id

    response = await client.delete(
        f"/api/v1/skus/{sku_id}",
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "NOT_OWNER"


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
@patch("app.routers.skus.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_delete_nonexistent_sku_returns_404(
    mock_oos: AsyncMock,
    mock_mod: AsyncMock,
    client: AsyncClient,
    seller_auth: dict,
):
    response = await client.delete(
        f"/api/v1/skus/{uuid.uuid4()}",
        headers=seller_auth,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
@patch("app.routers.skus.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_delete_requires_auth(
    mock_oos: AsyncMock,
    mock_mod: AsyncMock,
    client: AsyncClient,
    delete_test_data: dict,
):
    response = await client.delete(
        f"/api/v1/skus/{delete_test_data['sku1'].id}",
    )

    assert response.status_code == 401


@pytest.mark.asyncio
@patch("app.routers.skus.send_moderation_event", new_callable=AsyncMock)
@patch("app.routers.skus.send_sku_out_of_stock", new_callable=AsyncMock)
async def test_no_out_of_stock_event_when_zero_active(
    mock_oos: AsyncMock,
    mock_mod: AsyncMock,
    client: AsyncClient,
    delete_test_data: dict,
    seller_auth: dict,
    db_session: AsyncSession,
):
    product = delete_test_data["product"]
    product.status = "MODERATED"
    await db_session.commit()

    sku = delete_test_data["sku2"]
    sku.active_quantity = 0
    await db_session.commit()

    response = await client.delete(
        f"/api/v1/skus/{sku.id}",
        headers=seller_auth,
    )

    assert response.status_code == 200
    mock_oos.assert_not_called()
