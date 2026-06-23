from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from jose import jwt
from httpx import AsyncClient

from app.config import settings

MODERATOR_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PRODUCT_ID = "550e8400-e29b-41d4-a716-446655440000"
SELLER_ID = "660e8400-e29b-41d4-a716-446655440001"
TICKET_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
HARD_REASON_ID = "b8c9d0e1-2345-6789-f012-901234567890"
SOFT_REASON_ID = "c9d0e1f2-3456-7890-abcd-012345678901"


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


def mock_reasons_hard() -> AsyncMock:
    mock = AsyncMock()

    async def fake_get(url, **kwargs):
        resp = AsyncMock()
        resp.status_code = 200
        resp.json.return_value = {
            "id": HARD_REASON_ID,
            "code": "COUNTERFEIT",
            "title": "Контрафактный товар",
            "hard_block": True,
            "is_active": True,
        }
        return resp

    mock.side_effect = fake_get
    return mock


def mock_reasons_soft() -> AsyncMock:
    mock = AsyncMock()

    async def fake_get(url, **kwargs):
        resp = AsyncMock()
        resp.status_code = 200
        resp.json.return_value = {
            "id": SOFT_REASON_ID,
            "code": "BAD_DESCRIPTION",
            "title": "Некорректное описание",
            "hard_block": False,
            "is_active": True,
        }
        return resp

    mock.side_effect = fake_get
    return mock


def mock_send_event_ok() -> AsyncMock:
    mock = AsyncMock()
    mock.return_value = None
    return mock


def mock_send_event_fail() -> AsyncMock:
    mock = AsyncMock()
    mock.side_effect = Exception("B2B unavailable")
    return mock


# === US-MOD-05 REQUIRED TESTS ===


@pytest.mark.asyncio
async def test_hard_block_transitions_to_terminal_and_emits_event(client: AsyncClient):
    import app.routers.tickets as tickets_module
    ticket = _make_ticket()
    tickets_module._tickets_db[TICKET_ID] = ticket

    with patch("app.routers.tickets._resolve_blocking_reasons", return_value=([
        {"id": HARD_REASON_ID, "title": "Контрафактный товар", "hard_block": True}
    ], True)):
        with patch("app.routers.tickets._send_blocked_event", mock_send_event_ok()):
            resp = await client.post(
                f"/api/v1/tickets/{TICKET_ID}/block",
                json={
                    "blocking_reason_ids": [HARD_REASON_ID],
                    "comment": "Товар является контрафактом",
                    "field_reports": [],
                },
                headers=auth_header(),
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == TICKET_ID
    assert data["status"] == "HARD_BLOCKED"
    assert data["decision_at"] is not None
    assert data["decision_comment"] == "Товар является контрафактом"
    assert len(data["blocking_reasons"]) == 1
    assert data["blocking_reasons"][0]["hard_block"] is True
    assert len(data["history"]) == 1
    assert data["history"][0]["action"] == "HARD_BLOCKED"
    assert data["history"][0]["moderator_id"] == MODERATOR_ID


@pytest.mark.asyncio
async def test_hard_block_event_carries_hard_block_true(client: AsyncClient):
    import app.routers.tickets as tickets_module
    ticket = _make_ticket()
    tickets_module._tickets_db[TICKET_ID] = ticket

    captured_args = {}

    async def capture_send_event(**kwargs):
        captured_args.update(kwargs)

    with patch("app.routers.tickets._resolve_blocking_reasons", return_value=([
        {"id": HARD_REASON_ID, "title": "Контрафактный товар", "hard_block": True}
    ], True)):
        with patch("app.routers.tickets._send_blocked_event", side_effect=capture_send_event):
            resp = await client.post(
                f"/api/v1/tickets/{TICKET_ID}/block",
                json={
                    "blocking_reason_ids": [HARD_REASON_ID],
                    "comment": "Контрафакт",
                },
                headers=auth_header(),
            )

    assert resp.status_code == 200
    assert captured_args["hard_block"] is True
    assert captured_args["product_id"] == PRODUCT_ID
    blocking = captured_args["blocking_reason"]
    assert blocking["id"] == HARD_REASON_ID
    assert blocking["title"] == "Контрафактный товар"


@pytest.mark.asyncio
async def test_any_modify_on_hard_blocked_returns_403(client: AsyncClient):
    import app.routers.tickets as tickets_module

    ticket = _make_ticket(status="IN_REVIEW")
    tickets_module._tickets_db[TICKET_ID] = ticket

    with patch("app.routers.tickets._resolve_blocking_reasons", return_value=([
        {"id": HARD_REASON_ID, "title": "Контрафактный товар", "hard_block": True}
    ], True)):
        with patch("app.routers.tickets._send_blocked_event", mock_send_event_ok()):
            resp_block = await client.post(
                f"/api/v1/tickets/{TICKET_ID}/block",
                json={
                    "blocking_reason_ids": [HARD_REASON_ID],
                    "comment": "Контрафакт",
                },
                headers=auth_header(),
            )
    assert resp_block.status_code == 200

    resp_approve = await client.post(
        f"/api/v1/tickets/{TICKET_ID}/approve",
        json={"comment": "Попытка одобрить"},
        headers=auth_header(),
    )
    assert resp_approve.status_code == 403
    assert resp_approve.json()["code"] == "PRODUCT_PERMANENTLY_BLOCKED"

    resp_block2 = await client.post(
        f"/api/v1/tickets/{TICKET_ID}/block",
        json={
            "blocking_reason_ids": [HARD_REASON_ID],
            "comment": "Повторная блокировка",
        },
        headers=auth_header(),
    )
    assert resp_block2.status_code == 403
    assert resp_block2.json()["code"] == "PRODUCT_PERMANENTLY_BLOCKED"


@pytest.mark.asyncio
async def test_edited_event_on_hard_blocked_is_ignored(client: AsyncClient):
    import app.routers.tickets as tickets_module

    ticket = _make_ticket(status="IN_REVIEW")
    tickets_module._tickets_db[TICKET_ID] = ticket

    with patch("app.routers.tickets._resolve_blocking_reasons", return_value=([
        {"id": HARD_REASON_ID, "title": "Контрафактный товар", "hard_block": True}
    ], True)):
        with patch("app.routers.tickets._send_blocked_event", mock_send_event_ok()):
            resp_block = await client.post(
                f"/api/v1/tickets/{TICKET_ID}/block",
                json={
                    "blocking_reason_ids": [HARD_REASON_ID],
                    "comment": "Контрафакт",
                },
                headers=auth_header(),
            )
    assert resp_block.status_code == 200
    assert tickets_module._tickets_db[TICKET_ID]["status"] == "HARD_BLOCKED"

    resp_edit = await client.post(
        "/api/v1/b2b/events",
        json={
            "event_type": "PRODUCT_EDITED",
            "idempotency_key": "11111111-2222-3333-4444-555555555555",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "product_id": PRODUCT_ID,
                "seller_id": SELLER_ID,
                "json_before": {"title": "Old"},
                "json_after": {"title": "New"},
            },
        },
        headers={"X-Service-Key": settings.MOD_TO_B2B_KEY},
    )

    assert resp_edit.status_code == 202
    data = resp_edit.json()
    assert data["ok"] is True
    assert data["action"] == "ignored"

    assert tickets_module._tickets_db[TICKET_ID]["status"] == "HARD_BLOCKED"


@pytest.mark.asyncio
async def test_deleted_event_removes_hard_blocked(client: AsyncClient):
    import app.routers.tickets as tickets_module

    ticket = _make_ticket(status="IN_REVIEW")
    tickets_module._tickets_db[TICKET_ID] = ticket

    with patch("app.routers.tickets._resolve_blocking_reasons", return_value=([
        {"id": HARD_REASON_ID, "title": "Контрафактный товар", "hard_block": True}
    ], True)):
        with patch("app.routers.tickets._send_blocked_event", mock_send_event_ok()):
            resp_block = await client.post(
                f"/api/v1/tickets/{TICKET_ID}/block",
                json={
                    "blocking_reason_ids": [HARD_REASON_ID],
                    "comment": "Контрафакт",
                },
                headers=auth_header(),
            )
    assert resp_block.status_code == 200
    assert TICKET_ID in tickets_module._tickets_db
    assert tickets_module._tickets_db[TICKET_ID]["status"] == "HARD_BLOCKED"

    resp_delete = await client.post(
        "/api/v1/b2b/events",
        json={
            "event_type": "PRODUCT_DELETED",
            "idempotency_key": "22222222-3333-4444-5555-666666666666",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "product_id": PRODUCT_ID,
            },
        },
        headers={"X-Service-Key": settings.MOD_TO_B2B_KEY},
    )

    assert resp_delete.status_code == 202
    data = resp_delete.json()
    assert data["ok"] is True
    assert data["action"] == "tickets_removed"

    assert TICKET_ID not in tickets_module._tickets_db
