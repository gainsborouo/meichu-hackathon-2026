"""Purchase recommendation: backend preprocess, model guardrails, SSE endpoint.

The model is a fake and Google Calendar is stubbed to explode if touched: nothing here
reaches the network.
"""

import json
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.deps import get_current_user
from app.api.v1.routes import calendar as calendar_route
from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.models import CalendarEvent, Card, Sale, UserSale
from app.repositories import cards as cards_repo
from app.schemas.recommendations import RecommendationRequest
from app.services import purchase_recommendation as service
from app.services.official_sources import is_official_url
from app.services.recommendation_agent import (
    get_recommendation_agent,
    parse_model_json,
    query_problem,
    read_skill_text,
)
from app.services.users import upsert_user

P = get_settings().api_v1_prefix
TODAY = service.datetime.now(service.TAIPEI).date()
REQ = {"product_name": "AirPods Pro", "store_name": "momo", "price": 7490, "currency": "TWD"}


def rule(rate, *, reg=False, cap=None, unit="點", min_spend=None, platforms=(), scope="any"):
    return {
        "rate": rate,
        "rate_max": rate,
        "scope": scope,
        "channels": [],
        "categories": [],
        "platforms": list(platforms),
        "cap_amount": cap,
        "cap_unit": unit if cap else None,
        "unlimited": cap is None,
        "min_spend": min_spend,
        "requires_registration": reg,
        "source_text": f"每月回饋上限 {cap} 點" if cap else "無上限",
    }


def sale(sid, card, rules, *, start=None, end=None, register_url=None):
    return Sale(
        id=sid,
        card_id=card.id,
        bank_name=card.bank_name,
        card_name=card.name,
        title=f"title {sid}",
        reward_rules=rules,
        campaign_start=start,
        campaign_end=end,
        register_url=register_url,
        source_payload={},
    )


class FakeAgent:
    """Stands in for the model; records what it was asked and returns a canned answer."""

    def __init__(self):
        self.calls: list[dict] = []
        self.answer = None  # dict, or callable(payload) -> dict

    async def __call__(self, payload):
        self.calls.append(payload)
        return self.answer(payload) if callable(self.answer) else self.answer


def official(url="https://www.esunbank.com/promo"):
    return [{"title": "官方活動頁", "url": url}]


def observed(raw):
    """Mimic the runner: every URL the model cites was returned by the search tool."""
    urls = [
        src["url"]
        for key in ("best_now", "best_future")
        if isinstance(raw.get(key), dict)
        for src in raw[key].get("official_sources", [])
    ]
    return {**raw, "observed_urls": urls}


@pytest_asyncio.fixture
async def world(session):
    """A user holding Unicard (玉山) and @GoGo (台新); CUBE (國泰) exists but is not held."""
    user = await upsert_user(session, google_uid="g", email="secret@x.com")
    uni = await cards_repo.get_or_create_card(session, bank_name="玉山銀行", name="Unicard")
    gogo = await cards_repo.get_or_create_card(session, bank_name="台新銀行", name="@GoGo 卡")
    cube = await cards_repo.get_or_create_card(session, bank_name="國泰世華銀行", name="CUBE 卡")
    await cards_repo.add_user_card(session, user.id, uni.id)
    await cards_repo.add_user_card(session, user.id, gogo.id)
    yesterday, soon = TODAY - timedelta(days=1), TODAY + timedelta(days=4)
    session.add_all(
        [
            sale("free-uni", uni, [rule(0.03, cap=500)]),
            sale("reg-uni", uni, [rule(0.05, reg=True)], register_url="https://www.esunbank.com/r"),
            sale("big-min", uni, [rule(0.10, min_spend=20000)]),
            sale("capped-gogo", gogo, [rule(0.05, cap=100)]),
            sale("expired-gogo", gogo, [rule(0.09)], end=yesterday),
            sale("shopee-only", uni, [rule(0.08, platforms=["shopee"])]),
            sale("momo-only", gogo, [rule(0.02, platforms=["momo"])]),
            sale("overseas", uni, [rule(0.028, scope="overseas")]),
            sale("future-gogo", gogo, [rule(0.035)], start=soon),
            sale("unheld-cube", cube, [rule(0.06)]),
        ]
    )
    await session.flush()
    return user


def ids(cands):
    return {c["candidate_id"] for c in cands}


async def pre(session, user, **kw):
    return await service.preprocess(session, user, RecommendationRequest(**{**REQ, **kw}))


# --- preprocess -----------------------------------------------------------


async def test_free_mode_never_contains_registration_campaigns(session, world):
    world.registration_campaigns_enabled = False
    p = await pre(session, world)
    assert p.mode == "no_registration"
    assert all(not c["requires_registration"] for c in p.now_candidates + p.future_candidates)
    assert "reg-uni#0" not in ids(p.now_candidates)


async def test_registration_mode_never_contains_free_campaigns(session, world):
    world.registration_campaigns_enabled = True
    p = await pre(session, world)
    assert p.mode == "registration"
    assert ids(p.now_candidates) == {"reg-uni#0"}
    assert p.future_candidates == []
    assert p.now_candidates[0]["registration_url"] == "https://www.esunbank.com/r"


async def test_no_matching_candidates_does_not_fall_back_and_skips_the_model(session, world):
    world.registration_campaigns_enabled = True
    for s in (await session.scalars(select(Sale).where(Sale.id == "reg-uni"))).all():
        await session.delete(s)
    await session.flush()
    agent = FakeAgent()
    result, verified = await service.recommend(await pre(session, world), agent)
    assert verified == []
    assert result.mode == "registration"
    assert result.best_now is None and result.wait_suggestion is None
    assert "需登錄" in result.explanation
    assert agent.calls == [], "no candidates means nothing for the model to choose"


async def test_only_held_cards_are_candidates(session, world):
    p = await pre(session, world)
    assert "unheld-cube#0" not in ids(p.now_candidates + p.future_candidates)
    assert {c["name"] for c in p.held_cards} == {"Unicard", "@GoGo 卡"}
    assert all(c["card"]["name"] != "CUBE 卡" for c in p.now_candidates)


async def test_threshold_cap_date_and_platform_preprocess(session, world):
    p = await pre(session, world)
    now = {c["candidate_id"]: c for c in p.now_candidates}
    assert "big-min#0" not in now  # price 7490 < min spend 20000
    assert "expired-gogo#0" not in now
    assert "shopee-only#0" not in now and "overseas#0" not in now
    assert now["momo-only#0"]["platform_match"] is True

    assert now["free-uni#0"]["estimated_reward_twd"] == 224.7
    assert now["free-uni#0"]["rate_display"] == "3%"
    assert now["free-uni#0"]["cap_applied"] is False

    capped = now["capped-gogo#0"]
    assert capped["estimated_reward_twd"] == 100.0 and capped["cap_applied"] is True
    assert capped["cap_description"] == "每月上限 100 點"

    assert ids(p.future_candidates) == {"future-gogo#0"}
    assert p.future_candidates[0]["campaign_start"] == (TODAY + timedelta(days=4)).isoformat()
    assert p.excluded["below_min_spend"] == 1 and p.excluded["other_platform"] == 1

    # A price above the threshold lets the big-min campaign in.
    p2 = await pre(session, world, price=30000)
    assert "big-min#0" in ids(p2.now_candidates)


async def test_candidates_are_listed_neutrally_not_by_reward(session, world):
    p = await pre(session, world)
    keys = [(c["card"]["bank_name"], c["card"]["name"], c["sale_id"]) for c in p.now_candidates]
    assert keys == sorted(keys)  # stable identity order; reward plays no part


async def test_model_payload_contains_no_email(session, world):
    payload = (await pre(session, world)).model_payload()
    assert "secret@x.com" not in json.dumps(payload, ensure_ascii=False)


# --- model guardrails -----------------------------------------------------


async def test_model_cannot_pick_outside_the_allowed_set(session, world):
    p = await pre(session, world)
    for bad in (
        {"best_now": {"candidate_id": "unheld-cube#0", "reason": "x"}},  # not held
        {"best_now": {"candidate_id": "big-min#0", "reason": "x"}},  # filtered by threshold
        {"best_now": {"candidate_id": "reg-uni#0", "reason": "x"}},  # wrong registration mode
        {"best_now": {"candidate_id": "future-gogo#0", "reason": "x"}},  # future in now slot
        {"best_future": {"candidate_id": "free-uni#0", "reason": "x"}},  # now in future slot
        {"best_now": {"candidate_id": "made-up", "reason": "x"}},
    ):
        with pytest.raises(service.RecommendationError):
            service.assemble(p, {**bad, "explanation": ""})


async def test_no_official_source_is_never_verified(session, world):
    p = await pre(session, world)
    for sources in (
        [],
        [{"title": "ptt", "url": "https://www.ptt.cc/bbs/creditcard/M.1.html"}],
        [{"title": "lookalike", "url": "https://www.esunbank.com.evil.example/x"}],
        [{"title": "plain http", "url": "http://www.esunbank.com/x"}],
        [{"title": "wrong bank", "url": "https://www.ctbcbank.com/x"}],
    ):
        raw = {
            "best_now": {"candidate_id": "free-uni#0", "reason": "r", "official_sources": sources}
        }
        result = service.assemble(p, raw)
        assert result.best_now.verification_status == "unverified"
        assert result.best_now.official_sources == []


async def test_official_source_verifies_and_backend_numbers_win(session, world):
    p = await pre(session, world)
    raw = {
        "best_now": {
            "candidate_id": "free-uni#0",
            "reason": "r",
            "official_sources": official(),
            "estimated_reward_twd": 99999,  # invented by the "model"; must be ignored
        }
    }
    best = service.assemble(p, observed(raw)).best_now
    assert best.verification_status == "verified"
    assert best.estimated_reward_twd == 224.7 and best.rate_display == "3%"
    assert best.card.name == "Unicard" and best.requires_registration is False


def test_official_domain_rules():
    assert is_official_url("玉山銀行", "https://www.esunbank.com/a")
    assert is_official_url("中國信託商業銀行", "https://www.ctbcbank.com/a")
    assert is_official_url("中國信託銀行", "https://ctbcbank.com/a")
    assert not is_official_url("玉山銀行", "https://www.ctbcbank.com/a")
    assert not is_official_url("某某銀行", "https://www.esunbank.com/a")
    assert not is_official_url("玉山銀行", None)


# --- wait suggestion ------------------------------------------------------


def _raw(now_id="free-uni#0", fut_id="future-gogo#0", fut_sources=None):
    return observed(
        {
            "best_now": {"candidate_id": now_id, "reason": "now", "official_sources": official()},
            "best_future": {
                "candidate_id": fut_id,
                "reason": "wait",
                "official_sources": official("https://www.taishinbank.com.tw/gogo")
                if fut_sources is None
                else fut_sources,
            },
            "explanation": "",
        }
    )


async def test_best_future_can_be_a_different_held_card_and_gets_a_draft(session, world):
    # Make the future campaign clearly better than 224.7 so the wait gates pass.
    fut = next(s for s in (await session.scalars(select(Sale))).all() if s.id == "future-gogo")
    fut.reward_rules = [rule(0.05)]
    await session.flush()
    result = service.assemble(await pre(session, world), _raw())

    wait = result.wait_suggestion
    assert wait is not None and wait.recommended is True
    assert wait.card.name == "@GoGo 卡" and result.best_now.card.name == "Unicard"
    assert wait.starts_at == TODAY + timedelta(days=4)
    assert wait.estimated_reward_twd == 374.5
    assert wait.estimated_extra_reward_twd == round(374.5 - 224.7, 1)
    draft = wait.calendar_draft
    assert draft.title == "momo 購買 AirPods Pro"
    assert draft.starts_at.hour == 9 and draft.starts_at.utcoffset() == timedelta(hours=8)
    assert draft.starts_at.date() == wait.starts_at and "title future-gogo" in draft.notes


async def test_wait_only_needs_future_to_beat_now(session, world):
    fut = next(s for s in (await session.scalars(select(Sale))).all() if s.id == "future-gogo")
    # now = 3% -> 224.7. Equal or worse: no wait. Any real improvement: wait, no extra margin.
    for rate in (0.01, 0.03):
        fut.reward_rules = [rule(rate)]
        await session.flush()
        result = service.assemble(await pre(session, world), _raw())
        assert result.wait_suggestion is None, rate
        assert result.best_now.card.name == "Unicard"
    fut.reward_rules = [rule(0.031)]  # 232.2 vs 224.7: only +7.5, still worth proposing
    await session.flush()
    wait = service.assemble(await pre(session, world), _raw()).wait_suggestion
    assert wait is not None and wait.estimated_extra_reward_twd == 7.5


async def test_wait_is_null_without_official_source(session, world):
    p = await pre(session, world)
    assert service.assemble(p, _raw(fut_sources=[])).wait_suggestion is None
    ptt = [{"title": "ptt", "url": "https://www.ptt.cc/x"}]
    assert service.assemble(p, _raw(fut_sources=ptt)).wait_suggestion is None


async def test_undated_campaign_cannot_be_a_wait_suggestion(session, world):
    p = await pre(session, world)
    # 'free-uni' has no start date, so it is a now candidate and cannot fill the future slot.
    with pytest.raises(service.RecommendationError):
        service.assemble(p, _raw(fut_id="free-uni#0"))


async def test_wait_needs_a_start_date_even_if_a_candidate_slipped_in(session, world):
    p = await pre(session, world)
    cand = dict(p.future_candidates[0], campaign_start=None)
    p.future_candidates = [cand]
    assert service.assemble(p, _raw()).wait_suggestion is None


# --- endpoint -------------------------------------------------------------


@pytest_asyncio.fixture
async def api(session, world, monkeypatch):
    app = create_app()
    agent = FakeAgent()

    async def _session():
        yield session

    async def _user():
        return world

    def _boom(*a, **k):
        raise AssertionError("recommendation must not touch Google Calendar")

    monkeypatch.setattr(calendar_route.google_calendar, "create_event", _boom)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_recommendation_agent] = lambda: agent
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.agent, c.app = agent, app
        yield c


def parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


async def test_stream_event_order_and_shape(api):
    api.agent.answer = _raw()
    r = await api.post(f"{P}/recommendations/stream", json=REQ)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    assert [n for n, _ in events] == ["searching", "searching", "recommendation", "done"]
    body = events[2][1]
    assert body["mode"] == "no_registration"
    assert body["best_now"]["card"]["name"] == "Unicard"
    assert body["best_now"]["verification_status"] == "verified"
    assert body["best_now"]["estimated_reward_twd"] == 224.7
    assert "email" not in json.dumps(api.agent.calls[0])


async def test_stream_reports_model_violation_as_error_event(api):
    api.agent.answer = {"best_now": {"candidate_id": "unheld-cube#0", "reason": "x"}}
    events = parse_sse((await api.post(f"{P}/recommendations/stream", json=REQ)).text)
    assert events[-1][0] == "error" and "allowed" in events[-1][1]["message"]
    assert "recommendation" not in [n for n, _ in events]


async def test_stream_with_no_candidates_returns_null_best_now(api, session, world):
    world.registration_campaigns_enabled = True
    for s in (await session.scalars(select(Sale).where(Sale.id == "reg-uni"))).all():
        await session.delete(s)
    await session.flush()
    events = parse_sse((await api.post(f"{P}/recommendations/stream", json=REQ)).text)
    assert [n for n, _ in events][-2:] == ["recommendation", "done"]
    body = events[-2][1]
    assert body["best_now"] is None and body["wait_suggestion"] is None and body["explanation"]
    assert api.agent.calls == []


async def test_stream_has_no_calendar_or_user_sales_side_effects(api, session):
    api.agent.answer = _raw()
    before = (
        await session.scalar(select(func.count()).select_from(CalendarEvent)),
        await session.scalar(select(func.count()).select_from(UserSale)),
    )
    await api.post(f"{P}/recommendations/stream", json=REQ)
    after = (
        await session.scalar(select(func.count()).select_from(CalendarEvent)),
        await session.scalar(select(func.count()).select_from(UserSale)),
    )
    assert before == after == (0, 0)  # and create_event was patched to fail if called


async def test_stream_requires_auth(api):
    api.app.dependency_overrides.pop(get_current_user)
    assert (await api.post(f"{P}/recommendations/stream", json=REQ)).status_code == 401


async def test_stream_validates_the_request(api):
    bad = {**REQ, "price": 0}
    assert (await api.post(f"{P}/recommendations/stream", json=bad)).status_code == 422


async def test_old_search_endpoint_is_gone(api):
    r = await api.post(f"{P}/search", json={"price": 100})
    assert r.status_code == 404
    assert f"{P}/search" not in api.app.openapi()["paths"]
    assert f"{P}/recommendations/stream" in api.app.openapi()["paths"]


# --- agent helpers --------------------------------------------------------


def test_search_queries_are_screened():
    held = ["Unicard", "@GoGo 卡", "CUBE 卡"]
    assert query_problem("AirPods Pro momo Unicard 玉山銀行 登錄 回饋", held) is None
    assert query_problem("card for me@example.com", held)
    assert query_problem("Unicard CUBE 卡 @GoGo 卡 比較", held)
    assert query_problem("x" * 200, held)


def test_model_json_parsing_and_skill_loading():
    assert parse_model_json('```json\n{"best_now": null}\n```') == {"best_now": None}
    with pytest.raises(service.RecommendationError):
        parse_model_json("not json")
    text = read_skill_text()
    assert "final ranker" in text and "official-source" in text.lower()
    assert not text.startswith("---")


async def test_cards_table_untouched_by_preprocess(session, world):
    n = await session.scalar(select(func.count()).select_from(Card))
    await pre(session, world)
    assert await session.scalar(select(func.count()).select_from(Card)) == n


# --- official_verified_at -------------------------------------------------


async def _stamps(session):
    await session.commit()
    rows = (await session.scalars(select(Sale))).all()
    for r in rows:
        await session.refresh(r)
    return {r.id: r.official_verified_at for r in rows if r.official_verified_at}


async def test_verified_picks_stamp_their_sales_and_only_them(api, session):
    api.agent.answer = _raw()  # both picks carry allow-listed bank URLs
    await api.post(f"{P}/recommendations/stream", json=REQ)
    assert set(await _stamps(session)) == {"free-uni", "future-gogo"}


async def test_unverifiable_picks_are_not_stamped(api, session):
    raw = _raw(fut_sources=[{"title": "ptt", "url": "https://www.ptt.cc/x"}])
    raw["best_now"]["official_sources"] = [{"title": "x", "url": "http://www.esunbank.com/x"}]
    api.agent.answer = raw
    events = parse_sse((await api.post(f"{P}/recommendations/stream", json=REQ)).text)
    assert events[-2][1]["best_now"]["verification_status"] == "unverified"
    assert await _stamps(session) == {}


async def test_partial_verification_stamps_only_the_verified_sale(api, session):
    api.agent.answer = _raw(fut_sources=[])
    await api.post(f"{P}/recommendations/stream", json=REQ)
    assert set(await _stamps(session)) == {"free-uni"}


async def test_error_and_empty_results_never_stamp(api, session):
    api.agent.answer = {"best_now": {"candidate_id": "unheld-cube#0", "reason": "x"}}
    await api.post(f"{P}/recommendations/stream", json=REQ)
    assert await _stamps(session) == {}


async def test_restamp_updates_the_timestamp(session, world):
    from datetime import UTC, datetime

    await service.mark_verified(session, ["free-uni"], at=datetime(2026, 1, 1, tzinfo=UTC))
    first = (await _stamps(session))["free-uni"]
    await service.mark_verified(session, ["free-uni"], at=datetime(2026, 2, 1, tzinfo=UTC))
    assert (await _stamps(session))["free-uni"] > first
    await service.mark_verified(session, [])  # no-op


# --- review findings ------------------------------------------------------


async def test_url_never_returned_by_search_cannot_verify(session, world):
    p = await pre(session, world)
    fabricated = official("https://www.esunbank.com/made-up/page-the-model-never-opened")
    raw = _raw()
    raw["best_now"]["official_sources"] = fabricated  # on-domain, https, but never observed
    result = service.assemble(p, raw)
    assert result.best_now.verification_status == "unverified"
    assert result.best_now.official_sources == []
    assert service.verified_sale_ids(p, raw) == ["future-gogo"]  # only the observed pick

    # No evidence at all (a runner that reports nothing) verifies nothing.
    bare = {k: v for k, v in _raw().items() if k != "observed_urls"}
    assert service.verified_sale_ids(p, bare) == []
    assert service.assemble(p, bare).wait_suggestion is None


async def test_observed_urls_match_despite_trailing_slash_and_fragment(session, world):
    p = await pre(session, world)
    raw = _raw()
    raw["observed_urls"] = [
        "HTTPS://WWW.esunbank.com/promo/#top",
        "https://www.taishinbank.com.tw/gogo",
    ]
    assert service.assemble(p, raw).best_now.verification_status == "verified"


async def test_stream_frees_the_db_transaction_before_the_model_runs(api, session):
    seen = {}

    async def agent(payload):
        seen["in_transaction"] = session.in_transaction()
        return _raw()

    api.app.dependency_overrides[get_recommendation_agent] = lambda: agent
    await api.post(f"{P}/recommendations/stream", json=REQ)
    assert seen == {"in_transaction": False}


async def test_card_artwork_id_is_passed_through(session, world):
    uni = (await session.scalars(select(Card).where(Card.name == "Unicard"))).one()
    uni.artwork_id = "esun-unicard"
    await session.flush()
    p = await pre(session, world)
    assert next(c for c in p.held_cards if c["name"] == "Unicard")["artwork_id"] == "esun-unicard"
    result = service.assemble(p, _raw())
    assert result.best_now.card.artwork_id == "esun-unicard"
    assert result.wait_suggestion.card.artwork_id is None  # card without artwork


def test_search_queries_cannot_leak_price_or_spending_text():
    from app.services.recommendation_agent import screening_inputs

    payload = {
        "request": {"product_name": "AirPods Pro", "store_name": "momo", "price": 7490},
        "held_cards": [{"name": "Unicard", "bank_name": "玉山銀行"}],
        "now_candidates": [
            {
                "card": {"name": "Unicard", "bank_name": "玉山銀行"},
                "title": "指定網購加碼",
                "conditions": "每月回饋上限 500 點",
            }
        ],
        "future_candidates": [],
        "spend_context": {
            "latest_spend_report": "本月最大宗消費是樂天市場的除濕機與定期訂閱，總花費 18,204 元",
            "recent_analyses": [],
        },
    }
    screen = screening_inputs(payload)

    def problem(q):
        return query_problem(q, **screen)

    assert problem("AirPods Pro momo Unicard 玉山銀行 指定網購加碼 登錄 2026") is None
    assert problem("Unicard 每月回饋上限 500 點") is None  # 500 is campaign wording
    assert "price" in problem("AirPods Pro momo 7490 回饋")
    assert "price" in problem("AirPods Pro 7,490 元 信用卡")
    assert "amounts" in problem("Unicard 18204 回饋")
    assert "spending-summary" in problem("Unicard 樂天市場的除濕機與定期訂閱")
    assert problem("Unicard 回饋 me@example.com")


async def test_search_tool_records_only_urls_it_really_returned(monkeypatch):
    import duckduckgo_search

    from app.services.recommendation_agent import _make_web_search

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results=5):
            return [
                {"title": "官方", "href": "https://www.esunbank.com/promo", "body": "b"},
                {"title": "ptt", "href": "https://www.ptt.cc/x", "body": "b"},
            ]

    monkeypatch.setattr(duckduckgo_search, "DDGS", FakeDDGS)
    seen: set[str] = set()
    tool = _make_web_search(
        {"held_card_names": [], "price": 100, "allowed_terms": (), "context_text": ""}, seen
    )
    assert "esunbank" in tool("Unicard 玉山銀行 回饋")
    assert seen == {"https://www.esunbank.com/promo", "https://www.ptt.cc/x"}

    seen.clear()
    assert tool("Unicard 100 回饋").startswith("Search refused")
    assert seen == set()  # refused queries reach nothing
