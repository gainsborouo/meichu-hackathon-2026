"""Live refresh must never block the event loop or wait for a stuck bank site.

The search and page fetch are blocking library calls. These tests make them genuinely block
(a thread waiting on an Event that only the test releases) and check, on the running loop,
that timeouts fire on time, other work keeps running, and an abandoned thread stops early.
"""

import asyncio
import threading
import time
from datetime import UTC, date, datetime, timedelta

import pytest

from app.models import Card
from app.services import live_refresh
from app.services.campaign_crawler import CrawlTarget
from app.services.live_refresh import LiveLookup, run_lookup
from app.services.official_pages import PageResult
from tests import test_live_refresh as base
from tests.test_live_refresh import (
    CTBC_URL,
    QUOTE,
    REQ,
    FakeLookup,
    count,
    make_client,
    pick_first_base,
    stream,
)
from tests.test_recommendations import FakeAgent, official, opened, rule, sale

world = base.world  # the shared fixture: a user holding the canonical CTBC card
TODAY = date(2026, 9, 20)
NOW = datetime(2026, 9, 20, tzinfo=UTC)
CTBC = CrawlTarget("ctbc-linepay", "中國信託銀行", "LINE Pay 聯名卡")
ESUN = CrawlTarget("esun-unicard", "玉山銀行", "Unicard")
ESUN_URL = "https://www.esunbank.com/zh-tw/unicard"
ESUN_TEXT = f"Unicard 玉山銀行信用卡。目前{QUOTE}。" * 4
PER_CARD, OVERALL = 0.3, 1.0
HANG = 10.0  # a stuck call would sit this long if nothing gave up on it


@pytest.fixture(autouse=True)
def _timeouts_and_memo(monkeypatch):
    live_refresh.reset_lookup_memo()
    monkeypatch.setattr(live_refresh, "PER_CARD_TIMEOUT_SECONDS", PER_CARD)
    monkeypatch.setattr(live_refresh, "LOOKUP_TIMEOUT_SECONDS", OVERALL)
    yield
    live_refresh.reset_lookup_memo()


@pytest.fixture
def release():
    """The Event stuck calls wait on. Always released at teardown so no thread outlives the
    test (pool threads are joined at interpreter exit)."""
    event = threading.Event()
    yield event
    event.set()


class Heartbeat:
    """Ticks on the event loop; the largest gap between ticks is how long it was blocked."""

    def __init__(self):
        self.max_gap = 0.0
        self._stop = False
        self._task = None

    async def __aenter__(self):
        async def beat():
            last = time.monotonic()
            while not self._stop:
                await asyncio.sleep(0.02)
                now = time.monotonic()
                self.max_gap = max(self.max_gap, now - last)
                last = now

        self._task = asyncio.create_task(beat())
        await asyncio.sleep(0.05)
        return self

    async def __aexit__(self, *exc):
        self._stop = True
        await self._task


def esun_extraction():
    return {
        "base_benefits": [
            {
                "title": "國內一般消費",
                "reward": QUOTE,
                "source_url": ESUN_URL,
                "evidence": QUOTE,
                "validity_evidence": "目前" + QUOTE,
                "confidence": 0.9,
            }
        ],
        "campaigns": [],
    }


class RoutedLookup(LiveLookup):
    """CTBC's bank site hangs (in `block_in`); Esun's answers immediately."""

    def __init__(self, release, *, block_in="search", hang_ctbc=True):
        self.release, self.block_in, self.hang_ctbc = release, block_in, hang_ctbc
        self.queries: list[str] = []
        self.fetched: list[str] = []
        self.threads: list[str] = []
        super().__init__(search=self._search, fetch=self._fetch, extract=self._extract)

    def _search(self, query):
        self.queries.append(query)
        self.threads.append(threading.current_thread().name)
        if "ctbcbank" in query:
            if self.hang_ctbc and self.block_in == "search":
                self.release.wait(HANG)
            return [CTBC_URL]
        return [ESUN_URL]

    def _fetch(self, url):
        self.fetched.append(url)
        if url == CTBC_URL:
            if self.hang_ctbc and self.block_in == "fetch":
                self.release.wait(HANG)
            return PageResult(True, url, 200, "LINE Pay", "LINE Pay 聯名卡。" * 20)
        return PageResult(True, url, 200, "Unicard", ESUN_TEXT)

    async def _extract(self, payload):
        return esun_extraction() if payload["bank"] == "玉山銀行" else {}


async def timed(coro):
    start = time.monotonic()
    result = await coro
    return result, time.monotonic() - start


# --- per-card timeout ------------------------------------------------------


@pytest.mark.parametrize("block_in", ["search", "fetch"])
async def test_a_stuck_search_or_fetch_times_out_without_blocking_the_event_loop(release, block_in):
    lookup = RoutedLookup(release, block_in=block_in)
    async with Heartbeat() as beat:
        results, elapsed = await timed(run_lookup([CTBC], lookup, today=TODAY, now=NOW))

    assert elapsed < PER_CARD + 0.6, f"waited {elapsed:.2f}s; the call would hang {HANG}s"
    assert beat.max_gap < 0.25, f"event loop was blocked for {beat.max_gap:.2f}s"
    [result] = results
    assert result.card_key == "ctbc-linepay"
    assert "timed out" in result.error
    assert not (result.base_benefits or result.campaigns)


async def test_a_slow_card_does_not_delay_the_next_card(release):
    lookup = RoutedLookup(release)  # CTBC hangs, Esun is instant
    results, elapsed = await timed(run_lookup([CTBC, ESUN], lookup, today=TODAY, now=NOW))

    assert elapsed < PER_CARD + 0.8
    by_key = {r.card_key: r for r in results}
    assert "timed out" in by_key["ctbc-linepay"].error
    esun = by_key["esun-unicard"]
    assert esun.error is None and len(esun.base_benefits) == 1  # unaffected by the hung card
    assert esun.base_benefits[0]["card_key"] == "esun-unicard"


async def test_each_card_gets_its_own_budget_not_a_shared_one(release, monkeypatch):
    """Two stuck cards each time out at PER_CARD (so ~2x), not one after the other's leftovers."""
    monkeypatch.setattr(live_refresh, "LOOKUP_TIMEOUT_SECONDS", 5.0)
    second_hang = CrawlTarget("fubon-momo", "台北富邦銀行", "momo 卡")

    class TwoHang(RoutedLookup):
        def _search(self, query):
            self.queries.append(query)
            if "ctbcbank" in query or "fubon" in query:
                self.release.wait(HANG)
            return [ESUN_URL]

    lookup = TwoHang(release)
    results, elapsed = await timed(run_lookup([CTBC, second_hang], lookup, today=TODAY, now=NOW))
    assert all("timed out after 0.3s" in r.error for r in results) and len(results) == 2
    assert 2 * PER_CARD - 0.05 <= elapsed < 2 * PER_CARD + 0.8


# --- overall timeout -------------------------------------------------------


async def test_the_overall_timeout_stops_the_whole_lookup_and_reports_the_stuck_card(
    release, monkeypatch
):
    monkeypatch.setattr(live_refresh, "PER_CARD_TIMEOUT_SECONDS", 5.0)  # per-card is not the limit
    monkeypatch.setattr(live_refresh, "LOOKUP_TIMEOUT_SECONDS", 0.4)
    lookup = RoutedLookup(release)

    async with Heartbeat() as beat:
        results, elapsed = await timed(run_lookup([CTBC, ESUN], lookup, today=TODAY, now=NOW))

    assert 0.35 <= elapsed < 1.2 and beat.max_gap < 0.25
    assert [r.card_key for r in results] == ["ctbc-linepay"]  # Esun was never started
    assert "overall" in results[0].error and "timed out" in results[0].error
    assert not any("esunbank" in q for q in lookup.queries)
    assert live_refresh._recent_misses.get("ctbc-linepay", 0) > time.monotonic()


# --- other work keeps running ----------------------------------------------


async def test_a_stuck_lookup_does_not_block_a_concurrent_lookup(release):
    stuck = RoutedLookup(release)
    fast = RoutedLookup(release, hang_ctbc=False)

    async def finish_time(lookup, target):
        start = time.monotonic()
        results = await run_lookup([target], lookup, today=TODAY, now=NOW)
        return results, time.monotonic() - start

    (stuck_res, stuck_t), (fast_res, fast_t) = await asyncio.gather(
        finish_time(stuck, CTBC), finish_time(fast, ESUN)
    )
    assert fast_res[0].error is None and len(fast_res[0].base_benefits) == 1
    assert fast_t < 0.2, f"the healthy lookup waited {fast_t:.2f}s behind the stuck one"
    assert "timed out" in stuck_res[0].error and stuck_t >= PER_CARD - 0.05


async def test_lookup_work_runs_off_the_loop_in_its_own_pool_not_the_default_executor(release):
    lookup = RoutedLookup(release)
    task = asyncio.create_task(run_lookup([CTBC], lookup, today=TODAY, now=NOW))
    await asyncio.sleep(0.1)  # the stuck call is now occupying a worker thread

    assert lookup.threads and all(t.startswith("live-lookup") for t in lookup.threads)
    assert threading.current_thread().name not in lookup.threads
    # The default executor (Firebase token verification etc.) is unaffected by the hung pool.
    assert await asyncio.wait_for(asyncio.to_thread(lambda: "ok"), timeout=0.5) == "ok"
    await task


async def test_an_abandoned_worker_stops_at_its_next_call(release):
    """After the timeout the stop flag is set: once the stuck call returns, no further
    search query or fetch is issued."""
    lookup = RoutedLookup(release)
    [result] = await run_lookup([CTBC], lookup, today=TODAY, now=NOW)
    assert "timed out" in result.error
    issued = len(lookup.queries)
    assert issued == 1

    release.set()  # the hung call finally returns...
    await asyncio.sleep(0.3)
    assert len(lookup.queries) == issued, "the abandoned worker kept searching"
    assert lookup.fetched == []


async def test_cancellation_stops_the_worker_and_leaves_the_loop_responsive(release):
    lookup = RoutedLookup(release)
    task = asyncio.create_task(run_lookup([CTBC], lookup, today=TODAY, now=NOW))
    await asyncio.sleep(0.1)
    start = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)
    assert time.monotonic() - start < 0.5

    release.set()
    await asyncio.sleep(0.3)
    assert len(lookup.queries) == 1 and lookup.fetched == []
    assert await asyncio.wait_for(asyncio.sleep(0.01, "alive"), 1) == "alive"


# --- the SSE request -------------------------------------------------------


class StuckSearch(FakeLookup):
    def __init__(self, release, **kw):
        self.release = release
        super().__init__(**kw)

    def _search(self, query):
        self.queries.append(query)
        self.release.wait(HANG)
        return [CTBC_URL]


async def test_sse_request_with_a_stuck_bank_site_returns_null_promptly(session, world, release):
    user, ctbc = world
    cards_before = await count(session, Card)
    lookup, agent = StuckSearch(release), FakeAgent()

    async with Heartbeat() as beat:
        async with make_client(session, user, lookup, agent) as client:
            events, elapsed = await timed(stream(client))  # stream() asserts HTTP 200

    assert elapsed < PER_CARD + 1.0, f"the request waited {elapsed:.2f}s for a stuck call"
    assert beat.max_gap < 0.25
    # The lookup timed out, but the request still re-ran preprocessing and answered.
    assert [d.get("stage") for n, d in events if n == "searching"] == [
        "preprocessing",
        "live_card_lookup",
        "reprocessing",
    ]
    assert [n for n, _ in events][-2:] == ["recommendation", "done"]
    body = events[-2][1]
    assert body["best_now"] is None and body["wait_suggestion"] is None
    assert "已嘗試即時查詢" in body["explanation"]
    assert agent.calls == []
    assert await count(session, Card) == cards_before
    assert live_refresh._recent_misses["ctbc-linepay"] > time.monotonic()


async def test_sse_timeout_still_uses_candidates_already_on_file(session, world, release):
    """A held card with a future campaign on file has no *current* candidate, so a lookup
    starts; when it times out the request still answers from what is on file."""
    user, ctbc = world
    session.add(sale("future-ctbc", ctbc, [rule(0.05)], start=date.today() + timedelta(days=5)))
    await session.flush()

    def answer(payload):
        future = payload["future_candidates"][0]["candidate_id"]
        return opened(
            {
                "best_now": None,
                "best_future": {
                    "candidate_id": future,
                    "reason": "等幾天更划算",
                    "official_sources": official(CTBC_URL),
                },
                "explanation": "目前沒有可用優惠",
            }
        )

    lookup, agent = StuckSearch(release), FakeAgent()
    agent.answer = answer
    async with make_client(session, user, lookup, agent) as client:
        events, elapsed = await timed(stream(client))

    assert elapsed < PER_CARD + 1.0
    body = events[-2][1]
    assert body["best_now"] is None
    assert (
        body["wait_suggestion"] is not None and body["wait_suggestion"]["sale_id"] == "future-ctbc"
    )
    assert len(agent.calls) == 1


async def test_sse_cancelled_request_does_not_wedge_the_next_one(session, world, release):
    user, _ = world
    stuck = StuckSearch(release)
    client_ctx = make_client(session, user, stuck, FakeAgent())
    async with client_ctx as client:
        request = asyncio.create_task(client.post("/api/v1/recommendations/stream", json=REQ))
        await asyncio.sleep(0.1)  # the lookup is now inside its stuck search
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(request, timeout=1.0)

        release.set()
        await asyncio.sleep(0.3)
        assert len(stuck.queries) == 1, "the cancelled request's worker kept going"

        # A fresh request on the same loop completes normally (candidate on file: no lookup).
        fresh = FakeLookup()
        agent = FakeAgent()
        agent.answer = pick_first_base
        async with make_client(session, user, fresh, agent) as second:
            events, elapsed = await timed(stream(second))
    assert elapsed < PER_CARD + 1.0 and events[-1][0] == "done"
