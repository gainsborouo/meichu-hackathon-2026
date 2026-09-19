"""Calendar reminders. Google itself is stubbed; no network here."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.deps import get_current_user
from app.api.v1.routes import calendar as route
from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.models import CalendarEvent, Sale, UserSale
from app.services.google_calendar import CalendarError
from app.services.sales_import import import_campaigns, load_campaigns
from app.services.users import upsert_user

P = get_settings().api_v1_prefix
EVENT = {"title": "買除濕機", "starts_at": "2026-10-01T10:00:00+08:00"}


@pytest_asyncio.fixture
async def client(session, monkeypatch):
    app = create_app()
    created: list[dict] = []
    deleted: list[str] = []

    async def _fake_create(token, **kwargs):
        created.append({"token": token, **kwargs})
        return {"id": f"gcal-{len(created)}", "htmlLink": "https://calendar.google.com/x"}

    async def _fake_delete(token, event_id, **kwargs):
        deleted.append(event_id)

    monkeypatch.setattr(route.google_calendar, "create_event", _fake_create)
    monkeypatch.setattr(route.google_calendar, "delete_event", _fake_delete)

    async def _session():
        yield session

    async def _user():
        return await upsert_user(session, google_uid="g", email="e@x.com")

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _user
    await import_campaigns(session, load_campaigns()[:3])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.created, c.deleted = created, deleted
        yield c


async def _connect(session):
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    user.google_refresh_token = "refresh-token"
    user.calendar_push_enabled = True
    await session.flush()
    return user


async def test_status_reports_not_connected(client) -> None:
    body = (await client.get(f"{P}/me/calendar")).json()
    assert body == {"connected": False, "calendar_push_enabled": False}


async def test_event_requires_push_enabled(client) -> None:
    r = await client.post(f"{P}/me/calendar/events", json=EVENT)
    assert r.status_code == 403 and "PATCH /me" in r.json()["detail"]


async def test_event_requires_a_connected_calendar(client, session) -> None:
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    user.calendar_push_enabled = True
    await session.flush()
    r = await client.post(f"{P}/me/calendar/events", json=EVENT)
    assert r.status_code == 409 and "connect" in r.json()["detail"].lower()


async def test_plain_item_reminder_needs_no_sale(client, session) -> None:
    await _connect(session)
    r = await client.post(f"{P}/me/calendar/events", json=EVENT)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["event"]["provider_event_id"] == "gcal-1"
    assert body["event"]["sale_id"] is None and body["user_sale"] is None
    assert body["already_notified"] is False
    assert client.created[0]["token"] == "refresh-token"


async def test_sale_linked_event_records_the_notification(client, session) -> None:
    await _connect(session)
    sale = (await session.scalars(select(Sale))).first()
    r = await client.post(f"{P}/me/calendar/events", json={**EVENT, "sale_id": sale.id})
    assert r.status_code == 201
    body = r.json()
    assert body["user_sale"]["notification_channel"] == "calendar"
    assert (await client.get(f"{P}/me/sales")).json()[0]["sale"]["id"] == sale.id


async def test_same_sale_twice_is_deduped_not_an_error(client, session) -> None:
    await _connect(session)
    sale = (await session.scalars(select(Sale))).first()
    first = await client.post(f"{P}/me/calendar/events", json={**EVENT, "sale_id": sale.id})
    second = await client.post(f"{P}/me/calendar/events", json={**EVENT, "sale_id": sale.id})
    assert first.status_code == 201 and first.json()["already_notified"] is False
    assert second.status_code == 200 and second.json()["already_notified"] is True
    rows = (await session.scalars(select(UserSale))).all()
    assert len(rows) == 1, "the unique key must prevent a duplicate notification"


async def test_unknown_sale_is_rejected_before_touching_the_calendar(client, session) -> None:
    await _connect(session)
    r = await client.post(f"{P}/me/calendar/events", json={**EVENT, "sale_id": "nope"})
    assert r.status_code == 404
    assert client.created == [], "must not create a calendar event for a bad sale"


async def test_provider_failure_surfaces_as_502_and_stores_nothing(client, session, monkeypatch):
    await _connect(session)

    async def _boom(token, **kwargs):
        raise CalendarError("Calendar rejected the event: quota")

    monkeypatch.setattr(route.google_calendar, "create_event", _boom)
    r = await client.post(f"{P}/me/calendar/events", json=EVENT)
    assert r.status_code == 502
    assert (await session.scalars(select(CalendarEvent))).all() == []


async def test_delete_removes_locally_and_remotely_but_keeps_the_notification(client, session):
    await _connect(session)
    sale = (await session.scalars(select(Sale))).first()
    event = (await client.post(f"{P}/me/calendar/events",
                               json={**EVENT, "sale_id": sale.id})).json()["event"]

    assert (await client.delete(f"{P}/me/calendar/events/{event['id']}")).status_code == 204
    assert client.deleted == ["gcal-1"]
    assert (await session.scalars(select(CalendarEvent))).all() == []
    # "Already notified" is history; clearing it would re-push the same offer.
    assert len((await session.scalars(select(UserSale))).all()) == 1


async def test_delete_of_someone_elses_event_is_404(client, session) -> None:
    await _connect(session)
    event = (await client.post(f"{P}/me/calendar/events", json=EVENT)).json()["event"]
    other = await upsert_user(session, google_uid="other", email="other@x.com")
    row = await session.get(CalendarEvent, __import__("uuid").UUID(event["id"]))
    row.user_id = other.id
    await session.flush()
    assert (await client.delete(f"{P}/me/calendar/events/{event['id']}")).status_code == 404


async def test_listing_returns_only_my_events(client, session) -> None:
    await _connect(session)
    await client.post(f"{P}/me/calendar/events", json=EVENT)
    await client.post(f"{P}/me/calendar/events", json={**EVENT, "title": "買冷氣"})
    titles = {e["title"] for e in (await client.get(f"{P}/me/calendar/events")).json()}
    assert titles == {"買除濕機", "買冷氣"}


async def test_disconnect_clears_the_token(client, session) -> None:
    user = await _connect(session)
    assert (await client.delete(f"{P}/me/calendar/connect")).status_code == 204
    await session.refresh(user)
    assert user.google_refresh_token is None and user.calendar_push_enabled is False
