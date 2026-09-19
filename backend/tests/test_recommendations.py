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
from app.models import CalendarEvent, Card, CardBenefit, Sale, UserSale
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


def opened(raw):
    """Mimic the runner: every URL the model cites was opened successfully by the backend."""
    urls = [
        src["url"]
        for key in ("best_now", "best_future")
        if isinstance(raw.get(key), dict)
        for src in raw[key].get("official_sources", [])
    ]
    return {**raw, "opened_urls": urls}


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
    assert verified.sale_ids == [] and verified.benefit_ids == []
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


async def test_en_us_localizes_backend_owned_copy(session, world):
    p = await pre(session, world, locale="en-US")
    capped = next(c for c in p.now_candidates if c["candidate_id"] == "capped-gogo#0")
    assert capped["cap_description"] == "Monthly cap: 100 points"
    assert p.model_payload()["request"]["locale"] == "en-US"

    result = service.assemble(p, _raw())
    assert result.wait_suggestion is not None
    assert result.wait_suggestion.calendar_draft.title == "Buy AirPods Pro at momo"
    assert "Estimated reward:" in result.wait_suggestion.calendar_draft.notes

    p.now_candidates = []
    p.future_candidates = []
    assert "do not require registration" in service.empty_explanation(p)
    p.held_cards = []
    assert service.empty_explanation(p).startswith("No cards have been added")


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
    best = service.assemble(p, opened(raw)).best_now
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
    return opened(
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
    bad_locale = {**REQ, "locale": "en"}
    assert (await api.post(f"{P}/recommendations/stream", json=bad_locale)).status_code == 422


async def test_stream_passes_en_us_locale_to_the_agent(api):
    api.agent.answer = _raw()
    await api.post(f"{P}/recommendations/stream", json={**REQ, "locale": "en-US"})
    assert api.agent.calls[0]["request"]["locale"] == "en-US"


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
    assert "en-US" in text and "request's `locale`" in text
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


async def test_url_the_backend_never_opened_cannot_verify(session, world):
    p = await pre(session, world)
    fabricated = official("https://www.esunbank.com/made-up/page-the-model-never-opened")
    raw = _raw()
    raw["best_now"]["official_sources"] = fabricated  # on-domain, https, but never opened
    result = service.assemble(p, raw)
    assert result.best_now.verification_status == "unverified"
    assert result.best_now.official_sources == []
    assert service.verified_sale_ids(p, raw) == ["future-gogo"]  # only the opened pick

    # No evidence at all (a runner that reports nothing) verifies nothing.
    bare = {k: v for k, v in _raw().items() if k != "opened_urls"}
    assert service.verified_sale_ids(p, bare) == []
    assert service.assemble(p, bare).wait_suggestion is None


async def test_opened_urls_match_despite_trailing_slash_and_fragment(session, world):
    p = await pre(session, world)
    raw = _raw()
    raw["opened_urls"] = [
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


async def test_search_results_alone_are_not_evidence(monkeypatch):
    import ddgs

    from app.services.recommendation_agent import _make_web_search

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results=5):
            return [{"title": "官方", "href": "https://www.esunbank.com/promo", "body": "b"}]

    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    tool = _make_web_search(
        {"held_card_names": [], "price": 100, "allowed_terms": (), "context_text": ""}
    )
    assert "esunbank" in tool("Unicard 玉山銀行 回饋")
    assert tool("Unicard 100 回饋").startswith("Search refused")

    # The search hit is not recorded anywhere the service trusts: without opened_urls the
    # pick stays unverified.
    raw = {
        "best_now": {
            "candidate_id": "free-uni#0",
            "reason": "r",
            "official_sources": official(),
        }
    }
    assert service._verified_sources(_cand("玉山銀行"), raw["best_now"], service._opened(raw)) == []


def _cand(bank):
    return {"card": {"bank_name": bank}}


async def test_open_tool_records_only_pages_that_really_opened(monkeypatch):
    from app.services import official_pages
    from app.services.official_pages import PageResult
    from app.services.recommendation_agent import _make_open_official_page

    results = {
        "https://www.esunbank.com/promo": PageResult(
            True, "https://www.esunbank.com/promo/", 200, "活動", "很長的活動內容 " * 20
        ),
        "https://www.esunbank.com/gone": PageResult(
            False, "https://www.esunbank.com/gone", 404, error="HTTP 404"
        ),
    }
    monkeypatch.setattr("app.services.recommendation_agent.fetch_page", lambda url: results[url])
    opened: set[str] = set()
    tool = _make_open_official_page(opened)

    assert tool("https://www.esunbank.com/gone") == "NOT OPENED: HTTP 404"
    assert opened == set()
    out = tool("https://www.esunbank.com/promo")
    assert out.startswith("OPENED https://www.esunbank.com/promo/")
    assert opened == {"https://www.esunbank.com/promo"}  # normalized; trailing slash folded
    assert official_pages.MIN_TEXT_CHARS > 0


async def test_only_opened_official_pages_verify_and_stamp(session, world):
    p = await pre(session, world)
    raw = _raw()
    raw["opened_urls"] = ["https://www.esunbank.com/promo"]  # best_now opened, future not
    result = service.assemble(p, raw)
    assert result.best_now.verification_status == "verified"
    assert result.wait_suggestion is None  # future pick was never opened
    assert service.verified_sale_ids(p, raw) == ["free-uni"]

    raw["opened_urls"] = []
    assert service.assemble(p, raw).best_now.verification_status == "unverified"
    assert service.verified_sale_ids(p, raw) == []


# --- final-URL-only evidence ----------------------------------------------

INITIAL = "https://www.esunbank.com/old-promo"
FINAL = "https://www.esunbank.com/zh-tw/promo/new"
PAGE = (
    "<html><body>" + "<p>指定網購享 3% 回饋，活動期間內每月上限 500 點。</p>" * 5 + "</body></html>"
)
HTML_HEADERS = {"content-type": "text/html; charset=utf-8"}


def _open_with(monkeypatch, responses):
    """Run the real open_official_page tool against canned responses (no network)."""
    from app.services import official_pages
    from app.services.recommendation_agent import _make_open_official_page

    def fake_fetch(url):
        return official_pages.fetch_page(
            url, get=lambda u: responses[u], is_public=lambda host: True
        )

    monkeypatch.setattr("app.services.recommendation_agent.fetch_page", fake_fetch)
    opened: set[str] = set()
    return _make_open_official_page(opened), opened


def _pick_citing(url):
    return {
        "best_now": {
            "candidate_id": "free-uni#0",
            "reason": "r",
            "official_sources": [{"title": "官方", "url": url}],
        }
    }


async def test_redirect_to_a_readable_page_records_only_the_final_url(session, world, monkeypatch):
    tool, opened = _open_with(
        monkeypatch,
        {
            INITIAL: (302, {"location": FINAL}, b""),
            FINAL: (200, HTML_HEADERS, PAGE.encode()),
        },
    )
    out = tool(INITIAL)
    assert out.startswith(f"OPENED {FINAL}")
    assert opened == {FINAL}  # not INITIAL, and no intermediate hop

    p = await pre(session, world)
    cited_initial = {**_pick_citing(INITIAL), "opened_urls": sorted(opened)}
    assert service.assemble(p, cited_initial).best_now.verification_status == "unverified"
    assert service.verified_sale_ids(p, cited_initial) == []

    cited_final = {**_pick_citing(FINAL), "opened_urls": sorted(opened)}
    assert service.assemble(p, cited_final).best_now.verification_status == "verified"
    assert service.verified_sale_ids(p, cited_final) == ["free-uni"]


async def test_multi_hop_redirect_records_neither_start_nor_middle(monkeypatch):
    middle = "https://www.esunbank.com/hop"
    tool, opened = _open_with(
        monkeypatch,
        {
            INITIAL: (301, {"location": middle}, b""),
            middle: (302, {"location": FINAL}, b""),
            FINAL: (200, HTML_HEADERS, PAGE.encode()),
        },
    )
    tool(INITIAL)
    assert opened == {FINAL}


async def test_redirect_ending_in_404_records_nothing_and_stamps_nothing(
    session, world, monkeypatch
):
    tool, opened = _open_with(
        monkeypatch,
        {
            INITIAL: (302, {"location": FINAL}, b""),
            FINAL: (404, HTML_HEADERS, b""),
        },
    )
    assert tool(INITIAL) == "NOT OPENED: HTTP 404"
    assert opened == set()

    p = await pre(session, world)
    for cited in (INITIAL, FINAL):
        raw = {**_pick_citing(cited), "opened_urls": sorted(opened)}
        assert service.assemble(p, raw).best_now.verification_status == "unverified"
        assert service.verified_sale_ids(p, raw) == []
    await service.mark_verified(session, service.verified_sale_ids(p, raw))
    await session.commit()
    stamped = (
        await session.scalars(select(Sale).where(Sale.official_verified_at.is_not(None)))
    ).all()
    assert stamped == []


@pytest.mark.parametrize(
    "final_response",
    [
        (200, {"content-type": "application/pdf"}, b"%PDF-1.4"),
        (200, HTML_HEADERS, b"<html><body>too short</body></html>"),
        (500, HTML_HEADERS, b""),
    ],
)
async def test_redirect_to_unreadable_final_page_records_nothing(monkeypatch, final_response):
    tool, opened = _open_with(
        monkeypatch,
        {INITIAL: (302, {"location": FINAL}, b""), FINAL: final_response},
    )
    assert tool(INITIAL).startswith("NOT OPENED")
    assert opened == set()


async def test_redirect_leaving_the_bank_records_nothing(monkeypatch):
    tool, opened = _open_with(
        monkeypatch, {INITIAL: (302, {"location": "https://evil.example/x"}, b"")}
    )
    assert tool(INITIAL).startswith("NOT OPENED")
    assert opened == set()


# --- base benefit fallback ------------------------------------------------

BASE_URL = "https://www.esunbank.com/base"


def benefit(card, title, rate, *, start=None, end=None, reg=False, platforms=()):
    return CardBenefit(
        card_id=card.id,
        title=title,
        reward=f"一般消費 {rate * 100:g}% 回饋",
        reward_rules=[rule(rate, reg=reg, platforms=platforms)],
        effective_start=start,
        effective_end=end,
        source_url=BASE_URL,
        source_payload={},
    )


@pytest_asyncio.fixture
async def bare(session):
    """A holder of Unicard + @GoGo whose only campaigns are expired or off-target.

    This is the situation that used to produce no candidates at all: a bank publishes a
    standing reward, but every dated campaign is over or for another platform.
    """
    user = await upsert_user(session, google_uid="bare", email="bare@x.com")
    uni = await cards_repo.get_or_create_card(session, bank_name="玉山銀行", name="Unicard")
    gogo = await cards_repo.get_or_create_card(session, bank_name="台新銀行", name="@GoGo 卡")
    await cards_repo.add_user_card(session, user.id, uni.id)
    await cards_repo.add_user_card(session, user.id, gogo.id)
    yesterday, tomorrow = TODAY - timedelta(days=1), TODAY + timedelta(days=1)
    session.add_all(
        [
            sale("expired-uni", uni, [rule(0.03)], end=yesterday),  # crawler's stale 2025 data
            sale("shopee-only", uni, [rule(0.08, platforms=["shopee"])]),  # not for momo
            sale("reg-uni", uni, [rule(0.05, reg=True)], register_url="https://www.esunbank.com/r"),
            benefit(uni, "一般消費", 0.01),  # no end date: legal for a standing reward
            benefit(gogo, "一般消費", 0.012, start=yesterday),
            benefit(uni, "已結束的基本權益", 0.09, end=yesterday),
            benefit(gogo, "尚未生效的基本權益", 0.09, start=tomorrow),
            benefit(uni, "需登錄的基本權益", 0.09, reg=True),
        ]
    )
    await session.flush()
    return user


def pick_base(card_name):
    """Fake model: choose the base-benefit candidate of the named card."""

    def answer(payload):
        cand = next(
            c
            for c in payload["now_candidates"]
            if c["candidate_type"] == "base_benefit" and c["card"]["name"] == card_name
        )
        return opened(
            {
                "best_now": {
                    "candidate_id": cand["candidate_id"],
                    "reason": "基本回饋",
                    "official_sources": official(),
                },
                "explanation": "",
            }
        )

    return answer


async def test_only_expired_campaigns_but_effective_base_benefit_still_recommends(session, bare):
    p = await pre(session, bare)
    assert {c["candidate_type"] for c in p.now_candidates} == {"base_benefit"}
    assert p.future_candidates == []

    agent = FakeAgent()
    agent.answer = pick_base("Unicard")
    response, verified = await service.recommend(p, agent)

    assert len(agent.calls) == 1, "the model must be consulted even with no live campaign"
    best = response.best_now
    assert best is not None, "a base benefit means the answer is not null"
    assert best.candidate_type == "base_benefit"
    assert best.sale_id is None and best.benefit_id is not None
    assert best.card.name == "Unicard" and best.rate_display == "1%"
    assert best.estimated_reward_twd == 74.9
    assert best.requires_registration is False
    assert verified.benefit_ids == [best.benefit_id] and verified.sale_ids == []


async def test_offtarget_campaign_falls_back_to_the_base_benefit(session, bare):
    p = await pre(session, bare, store_name="momo")
    now_ids = ids(p.now_candidates)
    assert "shopee-only#0" not in now_ids and "expired-uni#0" not in now_ids
    assert {c["candidate_type"] for c in p.now_candidates} == {"base_benefit"}
    assert {c["card"]["name"] for c in p.now_candidates} == {"Unicard", "@GoGo 卡"}

    # On shopee the platform campaign appears next to the base benefits as one more candidate.
    p2 = await pre(session, bare, store_name="蝦皮")
    assert {c["candidate_type"] for c in p2.now_candidates} == {"base_benefit", "campaign"}
    assert "shopee-only#0" in ids(p2.now_candidates)


async def test_base_benefit_effective_window_filters(session, bare):
    p = await pre(session, bare)
    titles = {(c["card"]["name"], c["title"]) for c in p.now_candidates}
    assert ("Unicard", "一般消費") in titles  # no end date: legal and effective
    assert ("@GoGo 卡", "一般消費") in titles  # started yesterday
    assert not any("已結束" in t or "尚未生效" in t for _, t in titles)
    by_title = {c["title"]: c for c in p.now_candidates}
    assert by_title["一般消費"]["effective_end"] is None
    assert p.excluded["base_benefit_not_effective"] == 0  # filtered in SQL before this stage


async def test_registration_off_uses_base_plus_free_campaigns_only(session, bare):
    bare.registration_campaigns_enabled = False
    session.add(sale("free-uni", await _card(session, "Unicard"), [rule(0.02)]))
    await session.flush()
    p = await pre(session, bare)
    assert p.mode == "no_registration"
    assert {c["candidate_type"] for c in p.now_candidates} == {"base_benefit", "campaign"}
    assert all(c["requires_registration"] is False for c in p.now_candidates)
    assert "free-uni#0" in ids(p.now_candidates) and "reg-uni#0" not in ids(p.now_candidates)
    assert not any("需登錄" in c["title"] for c in p.now_candidates)


async def test_registration_on_uses_registration_campaigns_only_and_never_falls_back(session, bare):
    bare.registration_campaigns_enabled = True
    session.add(sale("free-uni", await _card(session, "Unicard"), [rule(0.02)]))
    await session.flush()
    p = await pre(session, bare)
    assert p.mode == "registration"
    assert ids(p.now_candidates) == {"reg-uni#0"}
    assert all(c["candidate_type"] == "campaign" for c in p.now_candidates)
    # The three effective base benefits are withheld in this mode, not used as a fallback.
    assert p.excluded["base_benefit_registration_mode"] == 3

    # Remove the only registration campaign: nothing is offered, and nothing is substituted.
    await session.delete(await session.get(Sale, "reg-uni"))
    await session.flush()
    agent = FakeAgent()
    response, verified = await service.recommend(await pre(session, bare), agent)
    assert agent.calls == []
    assert response.best_now is None and response.wait_suggestion is None
    assert "需登錄" in response.explanation
    assert verified.sale_ids == [] and verified.benefit_ids == []


async def _card(session, name):
    return (await session.scalars(select(Card).where(Card.name == name))).one()


async def test_verified_base_benefit_pick_stamps_the_benefit_not_a_sale(session, bare):
    p = await pre(session, bare)
    raw = pick_base("Unicard")({"now_candidates": p.now_candidates})
    verified = service.verified_targets(p, raw)
    assert len(verified.benefit_ids) == 1 and verified.sale_ids == []

    await service.mark_verified(session, verified.sale_ids, verified.benefit_ids)
    stamped = (
        await session.scalars(
            select(CardBenefit).where(CardBenefit.official_verified_at.is_not(None))
        )
    ).all()
    assert [str(b.id) for b in stamped] == verified.benefit_ids
    assert (
        await session.scalars(select(Sale).where(Sale.official_verified_at.is_not(None)))
    ).all() == []

    # Unopened URL: no stamp for a base benefit either.
    raw["opened_urls"] = []
    assert service.verified_targets(p, raw).benefit_ids == []


@pytest_asyncio.fixture
async def bare_api(session, bare, monkeypatch):
    app = create_app()
    agent = FakeAgent()

    async def _session():
        yield session

    async def _user():
        return bare

    def _boom(*a, **k):
        raise AssertionError("recommendation must not touch Google Calendar")

    monkeypatch.setattr(calendar_route.google_calendar, "create_event", _boom)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_recommendation_agent] = lambda: agent
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.agent = agent
        yield c


async def test_stream_recommends_a_base_benefit_without_calendar_or_user_sales(bare_api, session):
    bare_api.agent.answer = pick_base("Unicard")
    events = parse_sse((await bare_api.post(f"{P}/recommendations/stream", json=REQ)).text)

    assert [n for n, _ in events] == ["searching", "searching", "recommendation", "done"]
    best = events[2][1]["best_now"]
    assert best["candidate_type"] == "base_benefit" and best["sale_id"] is None
    assert best["verification_status"] == "verified"
    assert await session.scalar(select(func.count()).select_from(CalendarEvent)) == 0
    assert await session.scalar(select(func.count()).select_from(UserSale)) == 0
