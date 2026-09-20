"""Zero-candidate live refresh: the recommendation looks up the user's own held cards on
their bank's official site, caches what verifies, and only then decides. No network, no LLM."""

import json
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.models import CalendarEvent, Card, CardBenefit, UserSale
from app.repositories import cards as cards_repo
from app.services import live_refresh
from app.services.live_refresh import LiveLookup
from app.services.official_pages import PageResult
from app.services.recommendation_agent import get_recommendation_agent
from app.services.users import upsert_user
from tests.test_recommendations import FakeAgent, official, opened, parse_sse

P = get_settings().api_v1_prefix
REQ = {"product_name": "AirPods Pro", "store_name": "momo", "price": 7490, "currency": "TWD"}
CTBC_URL = "https://www.ctbcbank.com/tw/credit-cards/linepay"
CTBC_TEXT = "LINE Pay 聯名卡。目前國內一般消費享 1% 回饋，無上限。" * 4
QUOTE = "國內一般消費享 1% 回饋，無上限"


@pytest.fixture(autouse=True)
def _fresh_memo():
    live_refresh.reset_lookup_memo()
    yield
    live_refresh.reset_lookup_memo()


class FakeLookup(LiveLookup):
    """search / fetch / extract fakes that also record what was asked."""

    def __init__(self, *, pages=None, extraction=None, search_error=None, fetch_ok=True):
        self.queries: list[str] = []
        self.fetched: list[str] = []
        self.extracted: list[dict] = []
        self._pages = {CTBC_URL: CTBC_TEXT} if pages is None else pages
        self._extraction = extraction
        self._search_error = search_error
        self._fetch_ok = fetch_ok
        super().__init__(search=self._search, fetch=self._fetch, extract=self._extract)

    def _search(self, query):
        self.queries.append(query)
        if self._search_error:
            raise self._search_error
        return list(self._pages)

    def _fetch(self, url):
        self.fetched.append(url)
        if not self._fetch_ok or url not in self._pages:
            # Like the real fetch_page: an unknown or dead URL is "not opened", never an exception.
            return PageResult(False, url, 404, error="HTTP 404")
        return PageResult(True, url, 200, "LINE Pay", self._pages[url])

    async def _extract(self, payload):
        self.extracted.append(payload)
        if isinstance(self._extraction, Exception):
            raise self._extraction
        if self._extraction is not None:
            return self._extraction
        return {
            "base_benefits": [
                {
                    "title": "國內一般消費",
                    "reward": QUOTE,
                    "source_url": CTBC_URL,
                    "evidence": QUOTE,
                    "validity_evidence": "目前" + QUOTE,
                    "confidence": 0.9,
                }
            ],
            "campaigns": [],
        }

    @property
    def calls(self):
        return len(self.queries)


@pytest_asyncio.fixture
async def world(session, catalog):
    """A user holding the CANONICAL CTBC card (the catalog's 'LINE Pay 信用卡'), plus a
    catalog card they do not hold."""
    user = await upsert_user(session, google_uid="g", email="secret@x.com")
    ctbc = (await session.scalars(select(Card).where(Card.catalog_key == "ctbc-linepay"))).one()
    await cards_repo.add_user_card(session, user.id, ctbc.id)
    await session.flush()
    return user, ctbc


def make_client(session, user, lookup, agent, monkeypatch=None):
    app = create_app()

    async def _session():
        yield session

    async def _user():
        return user

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_recommendation_agent] = lambda: agent
    app.dependency_overrides[live_refresh.get_live_lookup] = lambda: lookup
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def pick_first_base(payload):
    cand = next(c for c in payload["now_candidates"] if c["candidate_type"] == "base_benefit")
    return opened(
        {
            "best_now": {
                "candidate_id": cand["candidate_id"],
                "reason": "基本回饋",
                "official_sources": official(CTBC_URL.replace("ctbcbank", "ctbcbank")),
            },
            "explanation": "",
        }
    )


async def count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


async def stream(client):
    r = await client.post(f"{P}/recommendations/stream", json=REQ)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)


def stages(events):
    return [(n, d.get("stage")) for n, d in events if n == "searching"]


# --- 5. nothing on file -> live lookup -> reprocess -> model ------------------------------


async def test_fast_demo_mode_skips_live_lookup_when_imported_data_has_no_candidate(session, world):
    user, _ = world
    lookup, agent = FakeLookup(), FakeAgent()

    async with make_client(session, user, lookup, agent) as client:
        response = await client.post(
            f"{P}/recommendations/stream",
            json={**REQ, "web_search_enabled": False},
        )
    assert response.status_code == 200
    events = parse_sse(response.text)

    assert stages(events) == [("searching", "preprocessing")]
    assert lookup.calls == 0
    assert agent.calls == []
    assert events[-2][1]["best_now"] is None


async def test_zero_candidates_triggers_lookup_caches_it_and_then_ranks(session, world):
    user, ctbc = world
    cards_before = await count(session, Card)
    lookup, agent = FakeLookup(), FakeAgent()
    agent.answer = pick_first_base

    async with make_client(session, user, lookup, agent) as client:
        events = await stream(client)

    assert stages(events) == [
        ("searching", "preprocessing"),
        ("searching", "live_card_lookup"),
        ("searching", "reprocessing"),
        ("searching", "official_verification"),
    ]
    by_stage = {d["stage"]: d for n, d in events if n == "searching"}
    assert by_stage["preprocessing"]["now_candidates"] == 0
    assert by_stage["live_card_lookup"]["cards"] == 1
    assert by_stage["reprocessing"] == {
        "stage": "reprocessing",
        "now_candidates": 1,
        "future_candidates": 0,
    }
    assert [n for n, _ in events][-2:] == ["recommendation", "done"]

    assert len(agent.calls) == 1, "the ranking model runs once data exists"
    best = events[-2][1]["best_now"]
    assert best["candidate_type"] == "base_benefit" and best["rate_display"] == "1%"
    assert best["card"]["id"] == str(ctbc.id), "the canonical catalog card, not a new one"

    # The result was cached on the canonical card; no card was created.
    assert await count(session, Card) == cards_before
    benefit = (await session.scalars(select(CardBenefit))).one()
    assert benefit.card_id == ctbc.id and benefit.official_verified_at is not None
    assert benefit.source_url == CTBC_URL
    assert benefit.source_payload["card_key"] == "ctbc-linepay"


async def test_lookup_queries_are_backend_built_and_contain_no_user_data(session, world):
    user, _ = world
    lookup, agent = FakeLookup(), FakeAgent()
    agent.answer = pick_first_base
    async with make_client(session, user, lookup, agent) as client:
        await stream(client)

    assert lookup.queries, "a lookup happened"
    # Some queries are year-scoped; one deliberately is not (product pages often omit a year).
    assert any(str(2026) in q for q in lookup.queries)
    for query in lookup.queries:
        assert query.startswith("site:ctbcbank.com")
        assert "LINE Pay 聯名卡" in query
        for forbidden in ("secret@x.com", "@", "7490", "AirPods", "momo"):
            assert forbidden not in query, forbidden
    assert "spend_context" not in json.dumps(lookup.extracted, ensure_ascii=False)
    assert "secret@x.com" not in json.dumps(lookup.extracted, ensure_ascii=False)


async def test_only_held_cards_are_looked_up(session, world):
    user, _ = world  # holds CTBC only; esun-unicard exists in the catalog but is not held
    lookup, agent = FakeLookup(), FakeAgent()
    agent.answer = pick_first_base
    async with make_client(session, user, lookup, agent) as client:
        await stream(client)
    assert all("esunbank" not in q and "Unicard" not in q for q in lookup.queries)
    assert {p["card"] for p in lookup.extracted} == {"LINE Pay 聯名卡"}


# --- 6. already have a candidate -> no lookup ---------------------------------------------


async def test_existing_candidate_means_no_live_lookup(session, world):
    user, ctbc = world
    session.add(
        CardBenefit(
            card_id=ctbc.id,
            title="已在資料庫的基本回饋",
            reward="一般消費 1%",
            reward_rules=[
                {
                    "rate": 0.01,
                    "rate_max": 0.01,
                    "scope": "any",
                    "categories": [],
                    "platforms": [],
                    "cap_amount": None,
                    "cap_unit": None,
                    "unlimited": True,
                    "min_spend": None,
                    "requires_registration": False,
                    "source_text": "一般消費 1%",
                }
            ],
            source_url=CTBC_URL,
        )
    )
    await session.flush()
    lookup, agent = FakeLookup(), FakeAgent()
    agent.answer = pick_first_base
    async with make_client(session, user, lookup, agent) as client:
        events = await stream(client)

    assert lookup.calls == 0 and lookup.fetched == [] and lookup.extracted == []
    assert [n for n, _ in events] == ["searching", "searching", "recommendation", "done"]
    assert {d["stage"] for n, d in events if n == "searching"} == {
        "preprocessing",
        "official_verification",
    }
    assert len(agent.calls) == 1


# --- 7. nothing found / failures: never a 500, never a new card ---------------------------


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda: FakeLookup(pages={}), id="search-finds-nothing"),
        pytest.param(lambda: FakeLookup(fetch_ok=False), id="official-page-cannot-be-opened"),
        pytest.param(
            lambda: FakeLookup(search_error=RuntimeError("rate limited")), id="search-raises"
        ),
        pytest.param(
            lambda: FakeLookup(extraction=RuntimeError("LLM down")), id="extractor-raises"
        ),
        pytest.param(
            lambda: FakeLookup(extraction={"base_benefits": [], "campaigns": []}),
            id="nothing-extracted",
        ),
        pytest.param(
            lambda: FakeLookup(
                extraction={
                    "base_benefits": [
                        {
                            "title": "捏造",
                            "reward": "一般消費 9% 回饋",
                            "source_url": CTBC_URL,
                            "evidence": "一般消費 9% 回饋",
                            "validity_evidence": "目前一般消費 9% 回饋",
                        }
                    ]
                }
            ),
            id="model-invents-an-unsupported-reward",
        ),
    ],
)
async def test_lookup_failure_returns_a_safe_null_and_creates_nothing(session, world, make):
    user, _ = world
    cards_before = await count(session, Card)
    lookup, agent = make(), FakeAgent()
    async with make_client(session, user, lookup, agent) as client:
        events = await stream(client)  # asserts HTTP 200

    assert [n for n, _ in events][-2:] == ["recommendation", "done"]
    body = events[-2][1]
    assert body["best_now"] is None and body["wait_suggestion"] is None
    assert "已嘗試即時查詢" in body["explanation"]
    assert agent.calls == [], "no candidates, so the ranking model is not called"
    assert await count(session, Card) == cards_before
    assert await count(session, CardBenefit) == 0
    assert await count(session, CalendarEvent) == 0 and await count(session, UserSale) == 0


async def test_a_card_that_yielded_nothing_is_not_re_crawled_on_every_request(session, world):
    user, _ = world
    lookup = FakeLookup(pages={})
    async with make_client(session, user, lookup, FakeAgent()) as client:
        await stream(client)
        first = lookup.calls
        events = await stream(client)  # immediately again
    assert first >= 1 and lookup.calls == first, "the miss is remembered for a while"
    assert "live_card_lookup" not in {d.get("stage") for n, d in events if n == "searching"}
    assert events[-2][1]["best_now"] is None


async def test_held_card_without_crawler_metadata_triggers_no_lookup(session, catalog):
    user = await upsert_user(session, google_uid="h", email="h@x.com")
    adhoc = await cards_repo.get_or_create_card(session, bank_name="某銀行", name="某卡")
    await cards_repo.add_user_card(session, user.id, adhoc.id)
    await session.flush()
    lookup = FakeLookup()
    async with make_client(session, user, lookup, FakeAgent()) as client:
        events = await stream(client)
    assert lookup.calls == 0
    assert "live_card_lookup" not in {d.get("stage") for n, d in events if n == "searching"}
    assert events[-2][1]["best_now"] is None


# --- 8. registration mode never falls back to a base benefit ------------------------------


async def test_registration_mode_never_uses_the_looked_up_base_benefit(session, world):
    user, ctbc = world
    user.registration_campaigns_enabled = True
    await session.flush()
    lookup, agent = FakeLookup(), FakeAgent()
    agent.answer = pick_first_base  # would crash if a base benefit were offered
    async with make_client(session, user, lookup, agent) as client:
        events = await stream(client)

    body = events[-2][1]
    assert body["mode"] == "registration" and body["best_now"] is None
    assert agent.calls == [], "no registration-required campaign exists, so nothing to rank"
    # The lookup still cached the base benefit for no-registration users...
    assert (await session.scalars(select(CardBenefit))).one().card_id == ctbc.id
    # ...but this user was not offered it.
    assert "需登錄" in body["explanation"]


async def test_registration_mode_off_after_the_same_lookup_offers_the_base_benefit(session, world):
    user, _ = world
    user.registration_campaigns_enabled = False
    lookup, agent = FakeLookup(), FakeAgent()
    agent.answer = pick_first_base
    async with make_client(session, user, lookup, agent) as client:
        events = await stream(client)
    assert events[-2][1]["best_now"]["candidate_type"] == "base_benefit"


# --- 9. side effects ------------------------------------------------------------------------


async def test_live_refresh_writes_no_calendar_events_or_user_sales(session, world):
    user, _ = world
    lookup, agent = FakeLookup(), FakeAgent()
    agent.answer = pick_first_base
    async with make_client(session, user, lookup, agent) as client:
        await stream(client)
    assert await count(session, CalendarEvent) == 0
    assert await count(session, UserSale) == 0


async def test_future_only_data_still_counts_as_zero_now_candidates(session, world):
    """now_candidates is what triggers the lookup, even if a future campaign is on file."""
    from datetime import date

    from tests.test_recommendations import rule, sale

    user, ctbc = world
    soon = date.today() + timedelta(days=30)
    session.add(sale("future-only", ctbc, [rule(0.05)], start=soon))
    await session.flush()
    lookup, agent = FakeLookup(), FakeAgent()
    agent.answer = pick_first_base
    async with make_client(session, user, lookup, agent) as client:
        events = await stream(client)
    assert lookup.calls >= 1
    assert "live_card_lookup" in {d.get("stage") for n, d in events if n == "searching"}
