from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from jose import jwt
from httpx import AsyncClient

from app.main import app
from app.config import settings


def make_token(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc).timestamp() + 3600},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def auth_header(user_id: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") -> dict:
    return {"Authorization": f"Bearer {make_token(user_id)}"}


@pytest_asyncio.fixture(autouse=True)
async def clean_banners():
    import app.routers.banners as banners_module
    banners_module._banners_db.clear()
    banners_module._banner_events_db.clear()
    yield
    banners_module._banners_db.clear()
    banners_module._banner_events_db.clear()


def _make_banner(
    title: str = "Test Banner",
    priority: int = 10,
    is_active: bool = True,
    start_at: str | None = None,
    end_at: str | None = None,
) -> dict:
    return {
        "id": f"test-{title.lower().replace(' ', '-')}",
        "title": title,
        "image_url": f"/cdn/banners/{title.lower().replace(' ', '-')}.jpg",
        "link": "/catalog",
        "priority": priority,
        "is_active": is_active,
        "start_at": start_at,
        "end_at": end_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# === REQUIRED TESTS ===


@pytest.mark.asyncio
async def test_active_banners_returned_sorted_by_priority(client: AsyncClient):
    import app.routers.banners as banners_module

    banners_module._banners_db.extend([
        _make_banner("Low priority", priority=20),
        _make_banner("High priority", priority=1),
        _make_banner("Medium priority", priority=10),
    ])

    resp = await client.get("/api/v1/home/banners")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_count"] == 3
    assert len(data["items"]) == 3

    priorities = [item["priority"] for item in data["items"]]
    assert priorities == [1, 10, 20]

    assert data["items"][0]["title"] == "High priority"
    assert data["items"][1]["title"] == "Medium priority"
    assert data["items"][2]["title"] == "Low priority"


@pytest.mark.asyncio
async def test_no_active_banners_returns_200_empty(client: AsyncClient):
    resp = await client.get("/api/v1/home/banners")
    assert resp.status_code == 200

    data = resp.json()
    assert data["items"] == []
    assert data["total_count"] == 0


@pytest.mark.asyncio
async def test_click_on_unknown_banner_returns_400(client: AsyncClient):
    resp = await client.post(
        "/api/v1/banner-events",
        json={"events": [{"banner_id": "nonexistent-id", "event": "click"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BANNER_NOT_FOUND"


# === ADDITIONAL TESTS ===


@pytest.mark.asyncio
async def test_inactive_banners_excluded(client: AsyncClient):
    import app.routers.banners as banners_module

    banners_module._banners_db.extend([
        _make_banner("Active", is_active=True, priority=1),
        _make_banner("Inactive", is_active=False, priority=2),
    ])

    resp = await client.get("/api/v1/home/banners")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_count"] == 1
    assert data["items"][0]["title"] == "Active"


@pytest.mark.asyncio
async def test_expired_banner_excluded(client: AsyncClient):
    import app.routers.banners as banners_module

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    banners_module._banners_db.extend([
        _make_banner("Expired", priority=1, end_at=past),
        _make_banner("Active", priority=2),
    ])

    resp = await client.get("/api/v1/home/banners")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_count"] == 1
    assert data["items"][0]["title"] == "Active"


@pytest.mark.asyncio
async def test_future_banner_excluded(client: AsyncClient):
    import app.routers.banners as banners_module

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    banners_module._banners_db.extend([
        _make_banner("Future", priority=1, start_at=future),
        _make_banner("Active", priority=2),
    ])

    resp = await client.get("/api/v1/home/banners")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_count"] == 1
    assert data["items"][0]["title"] == "Active"


@pytest.mark.asyncio
async def test_banner_events_accepted(client: AsyncClient):
    import app.routers.banners as banners_module

    banner = _make_banner("Test", priority=1)
    banners_module._banners_db.append(banner)

    resp = await client.post(
        "/api/v1/banner-events",
        json={
            "events": [
                {"banner_id": banner["id"], "event": "impression"},
                {"banner_id": banner["id"], "event": "click"},
            ]
        },
    )
    assert resp.status_code == 202
    assert resp.json()["accepted"] == 2
    assert len(banners_module._banner_events_db) == 2


@pytest.mark.asyncio
async def test_empty_events_returns_400(client: AsyncClient):
    resp = await client.post(
        "/api/v1/banner-events",
        json={"events": []},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "EMPTY_EVENTS"


@pytest.mark.asyncio
async def test_banner_events_with_user_id(client: AsyncClient):
    import app.routers.banners as banners_module

    banner = _make_banner("Test", priority=1)
    banners_module._banners_db.append(banner)

    resp = await client.post(
        "/api/v1/banner-events",
        json={"events": [{"banner_id": banner["id"], "event": "click"}]},
        headers=auth_header("user-123"),
    )
    assert resp.status_code == 202

    event = banners_module._banner_events_db[0]
    assert event["user_id"] == "user-123"
    assert event["event"] == "click"


@pytest.mark.asyncio
async def test_banner_events_without_auth(client: AsyncClient):
    import app.routers.banners as banners_module

    banner = _make_banner("Test", priority=1)
    banners_module._banners_db.append(banner)

    resp = await client.post(
        "/api/v1/banner-events",
        json={"events": [{"banner_id": banner["id"], "event": "impression"}]},
    )
    assert resp.status_code == 202

    event = banners_module._banner_events_db[0]
    assert event["user_id"] is None


@pytest.mark.asyncio
async def test_banner_no_auth_required(client: AsyncClient):
    resp = await client.get("/api/v1/home/banners")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_banner_response_fields(client: AsyncClient):
    import app.routers.banners as banners_module

    banner = _make_banner("Field Test", priority=5)
    banners_module._banners_db.append(banner)

    resp = await client.get("/api/v1/home/banners")
    assert resp.status_code == 200

    item = resp.json()["items"][0]
    assert "id" in item
    assert "title" in item
    assert "image_url" in item
    assert "link" in item
    assert "priority" in item
    assert item["id"] == banner["id"]
    assert item["title"] == "Field Test"
    assert item["priority"] == 5


@pytest.mark.asyncio
async def test_invalid_event_type_returns_400(client: AsyncClient):
    import app.routers.banners as banners_module

    banner = _make_banner("Test", priority=1)
    banners_module._banners_db.append(banner)

    resp = await client.post(
        "/api/v1/banner-events",
        json={"events": [{"banner_id": banner["id"], "event": "invalid"}]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_banner_within_schedule_is_active(client: AsyncClient):
    import app.routers.banners as banners_module

    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=1)).isoformat()
    end = (now + timedelta(hours=1)).isoformat()

    banners_module._banners_db.append(
        _make_banner("Scheduled", priority=1, start_at=start, end_at=end)
    )

    resp = await client.get("/api/v1/home/banners")
    assert resp.status_code == 200
    assert resp.json()["total_count"] == 1
