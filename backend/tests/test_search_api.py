"""Ranking and reward-rule extraction."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.models import Sale
from app.repositories import cards as cards_repo
from app.services.reward_rules import extract_rules
from app.services.sales_import import import_campaigns, load_campaigns
from app.services.users import upsert_user

P = get_settings().api_v1_prefix


@pytest_asyncio.fixture
async def client(session):
    app = create_app()

    async def _session():
        yield session

    async def _user():
        return await upsert_user(session, google_uid="g", email="e@x.com")

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _user
    await import_campaigns(session, load_campaigns())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


def test_extracts_domestic_and_overseas_rates_separately() -> None:
    rules = extract_rules(
        "國內一般消費享 1% LINE POINTS 回饋無上限；"
        "海外實體/線上消費享 2.8% LINE POINTS 回饋無上限。"
    )
    by_scope = {r["scope"]: r for r in rules}
    assert by_scope["domestic"]["rate"] == 0.01
    assert round(by_scope["overseas"]["rate"], 3) == 0.028
    assert by_scope["domestic"]["unlimited"] is True


def test_extracts_cap_registration_and_ranges() -> None:
    [rule] = extract_rules("完成登錄享最高 5% 回饋（加碼 2.2%），每戶每月回饋上限 450 點")
    assert rule["rate"] == 0.022 and rule["rate_max"] == 0.05
    assert rule["cap_amount"] == 450 and rule["cap_unit"] == "點"
    assert rule["requires_registration"] is True


def test_discount_notation_becomes_a_rate() -> None:
    [rule] = extract_rules("館內指定專櫃刷卡購物享9折至95折優惠")
    assert rule["rate"] == 0.05 and rule["rate_max"] == 0.1  # 95折=5%, 9折=10%


def test_unknown_item_resolves_to_null_rather_than_a_guess() -> None:
    from app.services.recommend import normalize_category

    assert normalize_category("除濕機") == "home"
    assert normalize_category("dining") == "dining"
    assert normalize_category("某個沒人聽過的東西") is None


def test_unquantifiable_reward_yields_no_rule() -> None:
    assert extract_rules("享館內每日最高2至4小時免費停車優惠") == []
    assert extract_rules(None) == []


def test_min_spend_threshold_is_recorded() -> None:
    [rule] = extract_rules("當月累積消費達指定門檻（滿 NT$100）享 1% 點數回饋")
    assert rule["min_spend"] == 100


async def test_search_ranks_by_money_earned(client, session) -> None:
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    for sale in (await session.scalars(__import__("sqlalchemy").select(Sale))).all():
        if sale.card_id:
            await cards_repo.add_user_card(session, user.id, sale.card_id)

    r = await client.post(f"{P}/search", json={"price": 1000, "platform": "momo",
                                               "category": "除濕機"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["best"] is not None
    amounts = [
        row["estimated_reward"]["amount"]
        for row in [body["best"], *body["alternatives"]]
        if row["estimated_reward"]
    ]
    assert amounts == sorted(amounts, reverse=True), "must be ranked best-first"
    # 除濕機 is an appliance; the platform match is what drives the ranking,
    # but echoing the understood category tells the caller how it was read.
    assert body["resolved_category"] == "home"


async def test_overseas_only_rate_does_not_inflate_a_local_purchase(client, session) -> None:
    """The 2.8% overseas LINE Pay rate must not be offered for a domestic buy."""
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    sale = await session.get(Sale, "ctbc-linepay-2025-general")
    await cards_repo.add_user_card(session, user.id, sale.card_id)

    body = (await client.post(f"{P}/search", json={"price": 1000})).json()
    assert body["best"]["estimated_reward"]["amount"] == 10.0  # 1% domestic, not 28.0


async def test_cards_without_a_parsable_rate_are_returned_last(client, session) -> None:
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    for sale in (await session.scalars(__import__("sqlalchemy").select(Sale))).all():
        if sale.card_id:
            await cards_repo.add_user_card(session, user.id, sale.card_id)

    rows = (await client.post(f"{P}/search", json={"price": 500})).json()
    ordered = [rows["best"], *rows["alternatives"]]
    seen_null = False
    for row in ordered:
        if row["estimated_reward"] is None:
            seen_null = True
            assert row["reason"]
        else:
            assert not seen_null, "quantified cards must all precede unquantified ones"


async def test_search_with_no_cards_returns_empty_not_error(client) -> None:
    body = (await client.post(f"{P}/search", json={"price": 100})).json()
    assert body["best"] is None and body["alternatives"] == []
    assert body["considered_card_count"] == 0


async def test_include_unowned_surfaces_cards_the_user_lacks(client) -> None:
    body = (await client.post(f"{P}/search",
                              json={"price": 100, "include_unowned": True})).json()
    assert body["considered_card_count"] > 0
    assert all(row["owned"] is False for row in [body["best"], *body["alternatives"]])
    assert body["best"]["user_card_id"] is None
