from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from jose import jwt
from httpx import AsyncClient

from app.config import settings

MODERATOR_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_MODERATOR_ID = "11111111-2222-3333-4444-555555555555"
PRODUCT_ID = "550e8400-e29b-41d4-a716-446655440000"
SELLER_ID = "660e8400-e29b-41d4-a716-446655440001"
TICKET_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def make_token(user_id: str = MODERATOR_ID) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc).timestamp() + 3600},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def auth_header(user_id: str = MODERATOR_ID) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id)}"}


def _make_ticket(
    ticket_id: str = TICKET_ID,
    status: str = "IN_REVIEW",
    moderator_id: str | None = MODERATOR_ID,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": ticket_id,
        "product_id": PRODUCT_ID,
        "seller_id": SELLER_ID,
        "category_id": None,
        "kind": "CREATE",
        "status": status,
        "queue_priority": 1,
        "assigned_moderator_id": moderator_id,
        "claimed_at": now,
        "claim_expires_at": now,
        "decision_at": None,
        "created_at": now,
        "updated_at": None,
        "json_before": None,
        "json_after": {"title": "Test Product"},
        "field_reports": [],
        "blocking_reasons": [],
        "decision_comment": None,
        "history": [],
    }


@pytest.fixture(autouse=True)
async def clean_tickets():
    import app.routers.tickets as tickets_module
    tickets_module._tickets_db.clear()
    yield
    tickets_module._tickets_db.clear()


def mock_has_skus(has: bool = True) -> AsyncMock:
    mock = AsyncMock()
    mock.return_value = has
    return mock


def mock_send_event_ok() -> AsyncMock:
    mock = AsyncMock()
    mock.return_value = None
    return mock


def mock_send_event_fail() -> AsyncMock:
    mock = AsyncMock()
    mock.side_effect = Exception("B2B unavailable")
    return mock


# === US-MOD-03 REQUIRED TESTS ===


@pytest.mark.asyncio
async def test_approve_transitions_to_moderated_and_emits_event(client: AsyncClient):
    import app.routers.tickets as tickets_module
    ticket = _make_ticket()
    tickets_module._tickets_db[TICKET_ID] = ticket

    with patch("app.routers.tickets._check_product_has_skus", mock_has_skus(True)):
        with patch("app.routers.tickets._send_moderated_event", mock_send_event_ok()):
            resp = await client.post(
                f"/api/v1/tickets/{TICKET_ID}/approve",
                json={"comment": "Товар соответствует требованиям"},
                headers=auth_header(),
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == TICKET_ID
    assert data["status"] == "APPROVED"
    assert data["decision_comment"] == "Товар соответствует требованиям"
    assert data["decision_at"] is not None
    assert len(data["field_reports"]) == 0
    assert len(data["blocking_reasons"]) == 0
    assert len(data["history"]) == 1
    assert data["history"][0]["action"] == "APPROVED"
    assert data["history"][0]["moderator_id"] == MODERATOR_ID


@pytest.mark.asyncio
async def test_approve_others_card_returns_403(client: AsyncClient):
    import app.routers.tickets as tickets_module
    ticket = _make_ticket(moderator_id=OTHER_MODERATOR_ID)
    tickets_module._tickets_db[TICKET_ID] = ticket

    resp = await client.post(
        f"/api/v1/tickets/{TICKET_ID}/approve",
        headers=auth_header(MODERATOR_ID),
    )

    assert resp.status_code == 403
    data = resp.json()
    assert data["code"] == "NOT_ASSIGNED"
    assert "not assigned to you" in data["message"].lower()


@pytest.mark.asyncio
async def test_approve_after_edited_returns_409(client: AsyncClient):
    import app.routers.tickets as tickets_module
    ticket = _make_ticket(status="APPROVED")
    tickets_module._tickets_db[TICKET_ID] = ticket

    resp = await client.post(
        f"/api/v1/tickets/{TICKET_ID}/approve",
        headers=auth_header(),
    )

    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "TICKET_WRONG_STATUS"
    assert "not in review" in data["message"].lower()


@pytest.mark.asyncio
async def test_approve_without_sku_returns_409(client: AsyncClient):
    import app.routers.tickets as tickets_module
    ticket = _make_ticket()
    tickets_module._tickets_db[TICKET_ID] = ticket

    with patch("app.routers.tickets._check_product_has_skus", mock_has_skus(False)):
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/approve",
            headers=auth_header(),
        )

    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "NO_SKUS"
    assert "no skus" in data["message"].lower()


@pytest.mark.asyncio
async def test_approve_hard_blocked_returns_409(client: AsyncClient):
    import app.routers.tickets as tickets_module
    ticket = _make_ticket(status="HARD_BLOCKED")
    tickets_module._tickets_db[TICKET_ID] = ticket

    resp = await client.post(
        f"/api/v1/tickets/{TICKET_ID}/approve",
        headers=auth_header(),
    )

    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "PRODUCT_PERMANENTLY_BLOCKED"


@pytest.mark.asyncio
async def test_approve_pending_ticket_returns_409(client: AsyncClient):
    import app.routers.tickets as tickets_module
    ticket = _make_ticket(status="PENDING")
    tickets_module._tickets_db[TICKET_ID] = ticket

    resp = await client.post(
        f"/api/v1/tickets/{TICKET_ID}/approve",
        headers=auth_header(),
    )

    assert resp.status_code == 409
    data = resp.json()
    assert data["code"] == "TICKET_WRONG_STATUS"


@pytest.mark.asyncio
async def test_approve_nonexistent_ticket_returns_404(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tickets/nonexistent-id/approve",
        headers=auth_header(),
    )

    assert resp.status_code == 404
    data = resp.json()
    assert data["code"] == "TICKET_NOT_FOUND"


@pytest.mark.asyncio
async def test_approve_b2b_failure_returns_500(client: AsyncClient):
    import app.routers.tickets as tickets_module
    ticket = _make_ticket()
    tickets_module._tickets_db[TICKET_ID] = ticket

    with patch("app.routers.tickets._check_product_has_skus", mock_has_skus(True)):
        with patch("app.routers.tickets._send_moderated_event", mock_send_event_fail()):
            resp = await client.post(
                f"/api/v1/tickets/{TICKET_ID}/approve",
                headers=auth_header(),
            )

    assert resp.status_code == 500
    data = resp.json()
    assert data["code"] == "B2B_UNAVAILABLE"

    assert tickets_module._tickets_db[TICKET_ID]["status"] == "IN_REVIEW"


@pytest.mark.asyncio
async def test_unauthorized_returns_401(client: AsyncClient):
    resp = await client.post(
        f"/api/v1/tickets/{TICKET_ID}/approve",
    )

    assert resp.status_code == 401
    data = resp.json()
    assert data["code"] == "UNAUTHORIZED"
