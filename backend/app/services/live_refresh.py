"""Live official-source lookup for a user's held cards, used when nothing is on file.

When a recommendation has zero current candidates the database simply may not have the
card's data yet. Instead of answering "nothing", look the held cards up on their banks'
official sites, cache what verifies, and let the caller re-run its candidate preprocessing.

Everything reuses the crawler: backend-built queries (bank + card + year, never user data or
prices), official-domain allow-list, the backend's own page fetch with redirect / SSRF rules,
and the evidence / date / reward / validity checks in validate_extraction. Data is attached
to the EXISTING canonical card through its card_key; nothing here can create a Card, and it
never touches Calendar or user_sales. No background worker: a lookup only ever runs inside
the request that needs it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Card, UserCard
from app.services.campaign_crawler import (
    CardResult,
    CrawlTarget,
    Extract,
    Fetch,
    Search,
    build_dataset,
    crawl_card,
    ddgs_search,
)
from app.services.card_extractor import build_extractor
from app.services.crawl_sources import base_urls_for
from app.services.official_pages import fetch_page
from app.services.sales_import import import_dataset

logger = logging.getLogger(__name__)

MAX_LIVE_LOOKUP_CARDS = 4
# Each card gets its own budget so one slow bank cannot use up the others' time; the whole
# lookup has a ceiling too. Both are enforced on the event loop, so a blocked search or fetch
# can never hold them past the deadline.
PER_CARD_TIMEOUT_SECONDS = 45.0
LOOKUP_TIMEOUT_SECONDS = 150.0
# The search and page fetches are synchronous library calls. They run in this dedicated pool,
# never on the event loop and never in the default executor that other requests use (e.g.
# Firebase token verification), so a hung bank site cannot starve the rest of the server.
MAX_LOOKUP_THREADS = 8
# A card that yielded nothing is not looked up again for this long, so a request that
# cannot be answered does not trigger a web crawl on every retry. Per process, in memory.
MISS_TTL_SECONDS = 3600.0

_recent_misses: dict[str, float] = {}
_executor = ThreadPoolExecutor(max_workers=MAX_LOOKUP_THREADS, thread_name_prefix="live-lookup")


async def _run_blocking(fn: Callable[[], Any]) -> Any:
    return await asyncio.get_running_loop().run_in_executor(_executor, fn)


def reset_lookup_memo() -> None:
    _recent_misses.clear()


@dataclass
class LiveLookup:
    """The three external seams of a lookup; tests replace them, nothing here uses the
    network directly."""

    search: Search
    fetch: Fetch
    extract: Extract


async def _extract_with_llm(payload: dict[str, Any]) -> Any:
    extractor = build_extractor(date.fromisoformat(payload["today"]))
    return await extractor(payload)


def get_live_lookup() -> LiveLookup:
    """FastAPI dependency: the real search, page fetch and LLM extraction."""
    return LiveLookup(search=ddgs_search, fetch=fetch_page, extract=_extract_with_llm)


async def lookup_targets(session: AsyncSession, user_id: Any) -> list[CrawlTarget]:
    """Held cards that can be looked up: catalog cards with crawler metadata. Only the
    user's own cards, never the whole catalog."""
    now = time.monotonic()
    rows = await session.execute(
        select(Card.catalog_key, Card.search_bank_name, Card.search_card_name)
        .join(UserCard, UserCard.card_id == Card.id)
        .where(
            UserCard.user_id == user_id,
            Card.crawler_enabled.is_(True),
            Card.search_bank_name.is_not(None),
            Card.search_card_name.is_not(None),
        )
        .order_by(UserCard.created_at, Card.catalog_key)
    )
    targets = [
        CrawlTarget(key, bank, card, base_urls_for(key))
        for key, bank, card in rows.all()
        if _recent_misses.get(key, 0.0) <= now
    ]
    return targets[:MAX_LIVE_LOOKUP_CARDS]


async def _crawl_with_deadline(
    target: CrawlTarget, lookup: LiveLookup, *, today: date, now: datetime
) -> CardResult:
    """One card, off the event loop, with its own timeout. On timeout or cancellation the
    stop flag is set so the worker thread quits at its next call instead of running on."""
    stop = threading.Event()
    try:
        return await asyncio.wait_for(
            crawl_card(
                target,
                today=today,
                search=lookup.search,
                fetch=lookup.fetch,
                extract=lookup.extract,
                now=now,
                run_blocking=_run_blocking,
                cancelled=stop.is_set,
            ),
            timeout=PER_CARD_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        stop.set()
        return CardResult(
            target.bank,
            target.card,
            target.card_key,
            error=f"lookup timed out after {PER_CARD_TIMEOUT_SECONDS:g}s",
        )
    except BaseException:  # includes CancelledError: stop the thread, then let it propagate
        stop.set()
        raise


async def run_lookup(
    targets: list[CrawlTarget], lookup: LiveLookup, *, today: date, now: datetime
) -> list[CardResult]:
    """Crawl each target in turn without ever blocking the event loop.

    Never raises for a lookup problem: a card that fails or times out is recorded on its
    result, and whatever finished before the overall timeout is still returned. A card that
    was still running when the overall deadline hit is reported as timed out too."""
    results: list[CardResult] = []
    in_flight: list[CrawlTarget] = []

    async def crawl_all() -> None:
        for target in targets:
            in_flight[:] = [target]
            results.append(await _crawl_with_deadline(target, lookup, today=today, now=now))
            in_flight.clear()

    try:
        await asyncio.wait_for(crawl_all(), timeout=LOOKUP_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning("live lookup timed out after %d of %d cards", len(results), len(targets))
        for target in in_flight:
            results.append(
                CardResult(
                    target.bank,
                    target.card,
                    target.card_key,
                    error=f"lookup timed out after {LOOKUP_TIMEOUT_SECONDS:g}s overall",
                )
            )
    except Exception:
        logger.exception("live lookup failed")

    expiry = time.monotonic() + MISS_TTL_SECONDS
    for result in results:
        if result.error or not (result.base_benefits or result.campaigns):
            _recent_misses[result.card_key] = expiry
        for reason in result.dropped:
            logger.info("live lookup dropped for %s: %s", result.card_key, reason)
        if result.error:
            logger.warning("live lookup for %s failed: %s", result.card_key, result.error)
    return results


async def persist_results(
    session: AsyncSession, results: list[CardResult], *, now: datetime
) -> dict[str, dict[str, int]] | None:
    """Upsert verified results onto the existing cards (via card_key) and commit. None when
    nothing verified. official_verified_at is not set here: it is stamped only when a
    recommendation itself opens the page it cites."""
    dataset = build_dataset(results, now=now)
    if not (dataset["base_benefits"] or dataset["campaigns"]):
        return None
    stats = await import_dataset(session, dataset)
    await session.commit()
    return stats
