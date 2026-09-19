import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.services.notifications import record_sale_notification
from app.services.sales_import import import_campaigns, load_campaigns
from app.services.users import upsert_user


@pytest_asyncio.fixture
async def client(session):
    app = create_app()

    async def _session():
        yield session

    async def _user():
        return await upsert_user(session, google_uid="g", email="e@x.com")

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _user
    await import_campaigns(session, load_campaigns()[:3])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_card_analysis_and_sales_flow(client, session) -> None:
    p = get_settings().api_v1_prefix
    card = (await client.get(f"{p}/cards")).json()[0]
    assert {
        "artwork_id",
        "display_name",
        "issuer_en",
        "variant",
        "network",
        "tier",
        "official_image_url",
        "image_is_composite",
    } <= card.keys()
    r = await client.post(f"{p}/me/cards", json={"card_id": card["id"]})
    assert r.status_code == 201
    uc = r.json()["id"]
    assert (await client.post(f"{p}/me/cards", json={"card_id": card["id"]})).status_code == 200

    r = await client.put(
        f"{p}/me/cards/{uc}/analyses",
        json={"analysis_month": "2026-09-20", "report": "hello", "analysis_data": {"a": 1}},
    )
    assert r.status_code == 200 and r.json()["analysis_month"] == "2026-09-01"
    assert "2026-09" in (await client.get(f"{p}/me")).json()["latest_spend_report"]

    sale = (await client.get(f"{p}/sales", params={"card_id": card["id"]})).json()[0]
    assert (await client.get(f"{p}/me/sales")).json() == []
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    await record_sale_notification(session, user.id, sale["id"])
    assert len((await client.get(f"{p}/me/sales")).json()) == 1
    assert (await client.get(f"{p}/me")).json()["registration_campaigns_enabled"] is False
    r = await client.patch(f"{p}/me", json={"registration_campaigns_enabled": True})
    assert r.json()["registration_campaigns_enabled"] is True


async def test_me_requires_auth(client) -> None:
    app = client._transport.app
    app.dependency_overrides.pop(get_current_user)
    assert (await client.get(f"{get_settings().api_v1_prefix}/me")).status_code == 401
