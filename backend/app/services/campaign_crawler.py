"""Rules for building credit_card_campaigns.json from official bank pages.

Split from skills/credit_card_campaigns/news.py so every gate is testable without an LLM
or the network. The runner (news.py) supplies three seams:

  search(query)   -> list of URLs        (DuckDuckGo via ddgs)
  fetch(url)      -> PageResult          (app.services.official_pages.fetch_page)
  extract(payload)-> {"base_benefits": [...], "campaigns": [...]}   (the LLM)

The model never chooses what is searched and never supplies a source it did not read:
queries are generated here from bank / card / year, only official https pages that the
backend actually opened are shown to the model, and every extracted item is re-checked
against those opened pages before it can reach the output file.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.services.official_pages import PageResult, fetch_page
from app.services.official_sources import is_official_url, normalize_url, official_domains
from app.services.reward_rules import extract_rules
from app.services.sales_import import parse_date

logger = logging.getLogger(__name__)

MAX_PAGES_PER_CARD = 5
MAX_URLS_TRIED_PER_CARD = 12
MAX_CHARS_PER_PAGE_FOR_MODEL = 6000
_RECURRENCES = {"once", "monthly", "quarterly", "yearly"}

Search = Callable[[str], list[str]]
Fetch = Callable[[str], PageResult]
Extract = Callable[[dict[str, Any]], Any]


# --- card identity ---------------------------------------------------------


def card_aliases(card: str) -> list[str]:
    """Names a bank page may use for the card: each "/" part, parenthetical parts, and the
    name without a generic 聯名卡/信用卡/卡 suffix. Longest first; nothing shorter than 2
    characters, so "卡" alone can never match everything."""
    names: list[str] = []
    for part in re.split(r"\s*/\s*", card):
        names.append(part)
        for inner in re.findall(r"[（(]([^）)]+)[）)]", part):
            names.append(inner)
        names.append(re.sub(r"[（(][^）)]*[）)]", "", part))
    expanded = list(names)
    for name in names:
        stripped = re.sub(r"(聯名卡|信用卡|卡)$", "", name.strip()).strip()
        expanded.append(stripped)
    seen: set[str] = set()
    out = []
    for name in sorted((n.strip() for n in expanded), key=len, reverse=True):
        key = _norm(name)
        if len(key) >= 2 and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text.lower())


def page_mentions_card(text: str, aliases: list[str]) -> bool:
    haystack = _norm(text)
    return any(_norm(a) in haystack for a in aliases if len(_norm(a)) >= 2)


def title_recognizable(title: str, text: str) -> bool:
    """True if a distinctive part of the campaign title (a 4+ character segment) is on the page."""
    haystack = _norm(text)
    segments = [_norm(s) for s in re.split(r"[\s\-–—:：、，,/｜|（）()「」【】]+", title)]
    return any(len(seg) >= 4 and seg in haystack for seg in segments)


# --- evidence binding ---------------------------------------------------------

_CONTEXT_RADIUS = 200
_YMD = re.compile(r"(?<!\d)(\d{4})\s*[/\-.年]\s*(\d{1,2})\s*[/\-.月]\s*(\d{1,2})\s*日?")
_ROC = re.compile(r"民國\s*(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_YEAR = re.compile(r"(?<!\d)(20\d{2})\s*年")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_DISCOUNT = re.compile(r"(?<!\d)(\d{1,2})\s*折")
# Wording that says a standing reward is in force now, when the quote carries no dates.
_CURRENT_MARKERS = (
    "現行",
    "目前",
    "即日起",
    "現在起",
    "長期",
    "常態",
    "不限期",
    "無期限",
    "持續",
    "永久",
)


def _compact(text: str) -> str:
    """Whitespace removed, everything else untouched: verbatim matching that survives the
    line breaks and spacing of HTML-to-text, but not paraphrase or altered numbers."""
    return re.sub(r"\s+", "", text)


def verbatim_on_page(quote: str, page_text: str) -> bool:
    q = _compact(quote)
    return bool(q) and q in _compact(page_text)


def page_context(page_text: str, *quotes: str, radius: int = _CONTEXT_RADIUS) -> str:
    """The quotes plus the page text around each occurrence (compact form)."""
    page = _compact(page_text)
    parts = []
    for quote in quotes:
        q = _compact(quote)
        at = page.find(q) if q else -1
        if at != -1:
            parts.append(page[max(0, at - radius) : at + len(q) + radius])
    return " ".join(parts)


def dated_mentions(text: str) -> list[tuple[date, str]]:
    """Dates in the text with their role: "start", "end" or "plain"."""
    found: list[tuple[date, str]] = []
    compact = _compact(text)
    for pattern, roc in ((_YMD, False), (_ROC, True)):
        for m in pattern.finditer(compact):
            year = int(m.group(1)) + (1911 if roc else 0)
            try:
                when = date(year, int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            before, after = (
                compact[max(0, m.start() - 6) : m.start()],
                compact[m.end() : m.end() + 6],
            )
            if re.search(r"(至|到|迄|~|～|–|—|-)$", before) or re.match(
                r"(止|截止|結束|為止|前)", after
            ):
                role = "end"
            elif re.search(r"(自|從)$", before) or re.match(r"(起|開始)", after):
                role = "start"
            elif re.match(r"(至|到|~|～|–|—|-)(民國)?\d", after):
                role = "start"
            else:
                role = "plain"
            found.append((when, role))
    return found


def years_mentioned(text: str) -> list[int]:
    compact = _compact(text)
    years = [int(y) for y in _YEAR.findall(compact)]
    years += [d.year for d, _ in dated_mentions(text)]
    return years


def validity_problem(validity: str, context: str, today: date) -> str | None:
    """Why the quoted text does NOT show a standing benefit is valid today, or None.

    effective_end=null only means "no end date was extracted". It cannot rescue a page that
    itself states a past end date or only talks about earlier years, and the quote must
    positively show the benefit is in force now: a start date on or before today, an end
    date on or after today, or explicit "current" wording.
    """
    in_quote, around = dated_mentions(validity), dated_mentions(context)
    ended = [d for d, role in in_quote + around if role == "end" and d < today]
    if ended:
        return f"page states an end date that has passed ({max(ended)})"
    years = years_mentioned(validity) + years_mentioned(context)
    if years and max(years) < today.year:
        return f"page only refers to past years (latest {max(years)})"
    started = any(role == "start" and d <= today for d, role in in_quote)
    running = any(role == "end" and d >= today for d, role in in_quote)
    current = any(marker in validity for marker in _CURRENT_MARKERS)
    if not (started or running or current):
        return "validity_evidence does not show the benefit is currently valid"
    return None


def reward_unsupported(reward: str, scope: str) -> str | None:
    """Every rate the reward claims must appear in the quoted text or right around it."""
    compact = _compact(scope)
    percents = {float(n) for n in _PERCENT.findall(compact)}
    discounts = {int(n) for n in _DISCOUNT.findall(compact)}
    for n in _PERCENT.findall(reward):
        if float(n) not in percents:
            return f"reward rate {n}% is not on the opened page"
    for n in _DISCOUNT.findall(reward):
        if int(n) not in discounts:
            return f"reward discount {n}折 is not on the opened page"
    return None


# --- queries and opening pages ---------------------------------------------


def build_queries(bank: str, card: str, year: int) -> list[str]:
    """Structured queries: official bank domain + card + current year + fixed wording only."""
    domains = official_domains(bank)[:2]
    name = (card_aliases(card) or [card])[0]
    queries = []
    for domain in domains:
        queries.append(f"site:{domain} {name} 信用卡 {year} 權益 回饋")
        queries.append(f"site:{domain} {name} {year} 活動 登錄")
        # Product/standing-benefit pages often omit a year. Validation still requires
        # page evidence that any extracted reward is current, so this broadens discovery
        # without letting stale data through.
        queries.append(f"site:{domain} {name} 信用卡 權益 回饋")
    return queries


@dataclass(frozen=True)
class OpenedPage:
    url: str  # final URL the backend actually read
    title: str
    text: str


def gather_official_pages(
    bank: str,
    card: str,
    *,
    year: int,
    search: Search,
    fetch: Fetch = fetch_page,
    cancelled: Callable[[], bool] | None = None,
) -> list[OpenedPage]:
    """Open official pages for the card. Only successfully opened, readable pages count,
    and each is identified by its FINAL url.

    `cancelled` is polled between the blocking calls. A thread cannot be killed, so when a
    caller gives up (a timeout) it sets this and the work stops at the next call boundary
    instead of running on for minutes."""
    aliases = card_aliases(card)
    candidates: list[str] = []
    for query in build_queries(bank, card, year):
        if cancelled is not None and cancelled():
            return []
        try:
            results = search(query)
        except Exception as exc:  # one rate-limited/no-result provider must not lose the card
            logger.info("search failed for %r: %s", query, exc)
            continue
        for href in results:
            if is_official_url(bank, href) and normalize_url(href) not in {
                normalize_url(c) for c in candidates
            }:
                candidates.append(href)

    pages: dict[str, OpenedPage] = {}
    for href in candidates[:MAX_URLS_TRIED_PER_CARD]:
        if cancelled is not None and cancelled():
            return []
        result = fetch(href)
        if not result.ok or not is_official_url(bank, result.url):
            logger.info("not opened (%s): %s", result.error or "unofficial final URL", href)
            continue
        pages.setdefault(
            normalize_url(result.url), OpenedPage(result.url, result.title, result.text)
        )
    ordered = sorted(pages.values(), key=lambda p: not page_mentions_card(p.text, aliases))
    return ordered[:MAX_PAGES_PER_CARD]


SEARCH_RESULTS_PER_QUERY = 8


async def crawler_targets(session: Any) -> list[CrawlTarget]:
    """Every crawler-enabled catalog card, from the database (ordered by catalog_key)."""
    from sqlalchemy import select

    from app.models import Card

    rows = await session.execute(
        select(Card.catalog_key, Card.search_bank_name, Card.search_card_name)
        .where(
            Card.crawler_enabled.is_(True),
            Card.search_bank_name.is_not(None),
            Card.search_card_name.is_not(None),
        )
        .order_by(Card.catalog_key)
    )
    return [CrawlTarget(key, bank, card) for key, bank, card in rows.all()]


def ddgs_search(query: str) -> list[str]:
    """Default search seam. Only backend-built queries ever reach it."""
    from ddgs import DDGS

    with DDGS() as client:
        results = client.text(query, max_results=SEARCH_RESULTS_PER_QUERY)
    return [r["href"] for r in results if isinstance(r.get("href"), str)]


# --- validation of extracted items -----------------------------------------


@dataclass(frozen=True)
class CrawlTarget:
    """One catalog card to look up. `bank` / `card` are the wording to SEARCH and to match
    official pages with; identity is only ever `card_key`."""

    card_key: str
    bank: str
    card: str


@dataclass
class CardResult:
    bank: str
    card: str
    card_key: str = ""
    base_benefits: list[dict[str, Any]] = field(default_factory=list)
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    error: str | None = None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _stable_id(kind: str, card_key: str, title: str, start: str | None) -> str:
    digest = hashlib.sha1(f"{kind}|{card_key}|{title}|{start or ''}".encode()).hexdigest()[:10]
    return f"{kind}-{digest}"


def _confidence(item: dict[str, Any]) -> float | None:
    value = item.get("confidence")
    return float(value) if isinstance(value, (int, float)) and 0 <= value <= 1 else None


def _rate_parseable(reward: str) -> bool:
    return any(rule.get("rate") is not None for rule in extract_rules(reward))


def _source_page(
    item: dict[str, Any], bank: str, pages: dict[str, OpenedPage]
) -> OpenedPage | None:
    """The opened page the item cites; None unless it is an official URL we really opened."""
    url = _text(item.get("source_url"))
    page = pages.get(normalize_url(url)) if url else None
    return page if page is not None and is_official_url(bank, page.url) else None


def validate_extraction(
    raw: Any,
    *,
    card_key: str,
    bank: str,
    card: str,
    pages: list[OpenedPage],
    today: date,
    now: datetime | None = None,
) -> CardResult:
    """Keep only items that survive every hard check. Everything else is dropped with a reason."""
    if not card_key:
        raise ValueError("card_key is required: extracted data must name its catalog card")
    result = CardResult(bank, card, card_key)
    if not isinstance(raw, dict):
        result.error = "extraction was not a JSON object"
        return result

    opened_at = (now or datetime.now(UTC)).isoformat()
    aliases = card_aliases(card)
    by_url = {normalize_url(p.url): p for p in pages}

    def drop(kind: str, item: Any, reason: str) -> None:
        title = _text(item.get("title")) if isinstance(item, dict) else "?"
        result.dropped.append(f"{kind} {title!r}: {reason}")

    for item in raw.get("base_benefits") or []:
        if not isinstance(item, dict):
            drop("base_benefit", item, "not an object")
            continue
        title, reward = _text(item.get("title")), _text(item.get("reward"))
        page = _source_page(item, bank, by_url)
        start, end = parse_date(item.get("effective_start")), parse_date(item.get("effective_end"))
        evidence = _text(item.get("evidence"))
        validity = _text(item.get("validity_evidence"))
        if not title or not reward:
            drop("base_benefit", item, "missing title or reward")
        elif page is None:
            drop("base_benefit", item, "source_url is not an official page the backend opened")
        elif not _rate_parseable(reward):
            drop("base_benefit", item, "reward has no parseable rate")
        elif item.get("effective_start") and start is None:
            drop("base_benefit", item, "unparseable effective_start")
        elif item.get("effective_end") and end is None:
            drop("base_benefit", item, "unparseable effective_end")
        elif end is not None and end < today:
            drop("base_benefit", item, f"already ended {end}")
        elif start is not None and start > today:
            drop("base_benefit", item, f"not effective until {start}")
        elif not page_mentions_card(page.text, aliases):
            drop("base_benefit", item, "opened page does not mention the card")
        elif not verbatim_on_page(evidence, page.text):
            drop("base_benefit", item, "evidence is not on the opened page")
        elif not verbatim_on_page(validity, page.text):
            drop("base_benefit", item, "validity_evidence is missing or not verbatim on the page")
        elif problem := validity_problem(
            validity, page_context(page.text, evidence, validity), today
        ):
            drop("base_benefit", item, problem)
        elif problem := reward_unsupported(reward, page_context(page.text, evidence)):
            drop("base_benefit", item, problem)
        else:
            result.base_benefits.append(
                {
                    "id": _stable_id("benefit", card_key, title, None),
                    "card_key": card_key,
                    "source_bank_name": bank,
                    "source_card_name": card,
                    "title": title,
                    "reward": reward,
                    "conditions": _text(item.get("conditions")) or None,
                    "effective_start": start.isoformat() if start else None,
                    "effective_end": end.isoformat() if end else None,
                    "source_url": page.url,
                    "evidence": evidence,
                    "validity_evidence": validity,
                    "confidence": _confidence(item),
                    "source_opened_at": opened_at,
                }
            )

    for item in raw.get("campaigns") or []:
        if not isinstance(item, dict):
            drop("campaign", item, "not an object")
            continue
        title, reward = _text(item.get("title")), _text(item.get("reward"))
        page = _source_page(item, bank, by_url)
        start, end = parse_date(item.get("campaign_start")), parse_date(item.get("campaign_end"))
        register_url = _text(item.get("register_url")) or None
        campaign_evidence = _text(item.get("evidence"))
        if not title or not reward:
            drop("campaign", item, "missing title or reward")
        elif start is None or end is None:
            drop("campaign", item, "campaign_start and campaign_end are required")
        elif end < start:
            drop("campaign", item, "campaign_end before campaign_start")
        elif end < today:
            drop("campaign", item, f"already ended {end}")
        elif page is None:
            drop("campaign", item, "source_url is not an official page the backend opened")
        elif not _rate_parseable(reward):
            drop("campaign", item, "reward has no parseable rate")
        elif register_url and not is_official_url(bank, register_url):
            drop("campaign", item, "register_url is not an official https URL")
        elif not (page_mentions_card(page.text, aliases) or title_recognizable(title, page.text)):
            drop("campaign", item, "opened page mentions neither the card nor the campaign")
        elif not verbatim_on_page(campaign_evidence, page.text):
            drop("campaign", item, "evidence is missing or not verbatim on the opened page")
        elif problem := _campaign_unsupported(item, start, end, reward, campaign_evidence, page):
            drop("campaign", item, problem)
        else:
            recurrence = _text(item.get("recurrence")).lower()
            result.campaigns.append(
                {
                    "id": _stable_id("campaign", card_key, title, start.isoformat()),
                    "card_key": card_key,
                    "source_bank_name": bank,
                    "source_card_name": card,
                    "title": title,
                    "register_from": _iso_or_none(item.get("register_from")),
                    "register_until": _iso_or_none(item.get("register_until")),
                    "register_url": register_url,
                    "recurrence": recurrence if recurrence in _RECURRENCES else "once",
                    "campaign_start": start.isoformat(),
                    "campaign_end": end.isoformat(),
                    "reward": reward,
                    "quota_limited": bool(item.get("quota_limited")),
                    "source_url": page.url,
                    "evidence": campaign_evidence,
                    "confidence": _confidence(item),
                    "source_opened_at": opened_at,
                }
            )
    return result


def _campaign_unsupported(
    item: dict[str, Any],
    start: date,
    end: date,
    reward: str,
    evidence: str,
    page: OpenedPage,
) -> str | None:
    """Dates and reward figures must be readable in the quote or in the text around it:
    a model that only saw a campaign title cannot supply them."""
    scope = page_context(page.text, evidence)
    stated = {when for when, _ in dated_mentions(scope)}
    for label, when in (("campaign_start", start), ("campaign_end", end)):
        if when not in stated:
            return f"{label} {when} is not stated in the evidence or the page text around it"
    return reward_unsupported(reward, scope)


def _iso_or_none(value: Any) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


def extraction_payload(
    bank: str, card: str, pages: list[OpenedPage], today: date
) -> dict[str, Any]:
    return {
        "today": today.isoformat(),
        "bank": bank,
        "card": card,
        "pages": [
            {"url": p.url, "title": p.title, "text": p.text[:MAX_CHARS_PER_PAGE_FOR_MODEL]}
            for p in pages
        ],
    }


async def crawl_card(
    target: CrawlTarget,
    *,
    today: date,
    search: Search,
    extract: Callable[[dict[str, Any]], Any],
    fetch: Fetch = fetch_page,
    now: datetime | None = None,
    run_blocking: Callable[..., Awaitable[Any]] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> CardResult:
    """One catalog card: search (structured), open official pages, extract, validate.

    The search and page fetches are synchronous, which is fine for the batch CLI. Inside an
    async server pass `run_blocking` (an awaitable runner such as a thread-pool executor) so
    they run off the event loop, and `cancelled` so an abandoned run stops early."""
    bank, card = target.bank, target.card
    try:
        gather = functools.partial(
            gather_official_pages,
            bank,
            card,
            year=today.year,
            search=search,
            fetch=fetch,
            cancelled=cancelled,
        )
        pages = await run_blocking(gather) if run_blocking is not None else gather()
    except Exception as exc:  # network / search library failure for this card only
        return CardResult(bank, card, target.card_key, error=f"search or fetch failed: {exc}")
    if not pages:
        return CardResult(bank, card, target.card_key, error="no official page could be opened")
    try:
        raw = extract(extraction_payload(bank, card, pages, today))
        raw = await raw if hasattr(raw, "__await__") else raw
    except Exception as exc:
        return CardResult(bank, card, target.card_key, error=f"extraction failed: {exc}")
    return validate_extraction(
        raw, card_key=target.card_key, bank=bank, card=card, pages=pages, today=today, now=now
    )


# --- output ----------------------------------------------------------------


def build_dataset(results: list[CardResult], *, now: datetime) -> dict[str, Any]:
    def unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for item in items:
            seen.setdefault(item["id"], item)
        return list(seen.values())

    return {
        "generated_at": now.isoformat(),
        "base_benefits": unique([b for r in results for b in r.base_benefits]),
        "campaigns": unique([c for r in results for c in r.campaigns]),
    }


def write_dataset_atomically(dataset: dict[str, Any], path: Path) -> None:
    """Write beside the target, re-read to check it, then replace in one step."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        reread = json.loads(tmp.read_text(encoding="utf-8"))
        if not (reread["base_benefits"] or reread["campaigns"]):
            raise ValueError("refusing to write an empty dataset")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class ExistingDatasetUnreadable(Exception):
    """The canonical file exists but cannot be merged safely."""


def load_existing_dataset(path: Path) -> dict[str, Any]:
    """The current canonical dataset in object form (a legacy list counts as campaigns).
    A missing file is an empty dataset; a corrupt one raises rather than being replaced."""
    if not path.exists():
        return {"base_benefits": [], "campaigns": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExistingDatasetUnreadable(str(exc)) from exc
    if isinstance(raw, list):
        return {"base_benefits": [], "campaigns": raw}
    if isinstance(raw, dict):
        return {
            "base_benefits": list(raw.get("base_benefits") or []),
            "campaigns": list(raw.get("campaigns") or []),
        }
    raise ExistingDatasetUnreadable("top-level JSON is neither an object nor a list")


def merge_datasets(
    existing: dict[str, Any],
    new: dict[str, Any],
    refreshed: set[str],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Cards (by card_key) that crawled successfully are replaced by their new results;
    every other entry (cards that failed this run, cards not crawled, legacy rows that carry
    no card_key) is kept untouched."""

    def keep(item: Any) -> bool:
        return not (isinstance(item, dict) and item.get("card_key") in refreshed)

    def combine(kind: str) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for i, item in enumerate(x for x in existing[kind] if keep(x)):
            merged[item.get("id", f"_unkeyed-{i}") if isinstance(item, dict) else f"_raw-{i}"] = (
                item
            )
        for item in new[kind]:
            merged[item["id"]] = item  # a fresh result wins over a stale row with the same id
        return list(merged.values())

    return {
        "generated_at": now.isoformat(),
        "base_benefits": combine("base_benefits"),
        "campaigns": combine("campaigns"),
    }


def finalize(results: list[CardResult], path: Path, *, now: datetime) -> int:
    """Log per-card outcomes, merge into the canonical file, and return the exit code.

    A card that crawled successfully replaces its own entries; a card that failed keeps
    whatever the file already had for it, so one flaky bank page never truncates the
    dataset. Exit 1 and no write when this run produced no verified item at all, or when the
    existing file cannot be read and therefore cannot be merged safely.
    """
    failed = [r for r in results if r.error]
    for r in results:
        for reason in r.dropped:
            logger.info("dropped for %s: %s", r.card_key or r.card, reason)
    for r in failed:
        logger.warning("FAILED %s (%s %s): %s", r.card_key, r.bank, r.card, r.error)

    new = build_dataset(results, now=now)
    new_total = len(new["base_benefits"]) + len(new["campaigns"])
    if new_total == 0:
        logger.error(
            "no verified base benefits or campaigns from %d cards; %s left unchanged",
            len(results),
            path,
        )
        return 1

    try:
        existing = load_existing_dataset(path)
    except ExistingDatasetUnreadable as exc:
        logger.error("cannot merge into %s (%s); file left unchanged", path, exc)
        return 1

    refreshed = {r.card_key for r in results if not r.error and r.card_key}
    merged = merge_datasets(existing, new, refreshed, now=now)
    write_dataset_atomically(merged, path)

    for r in failed:
        kept = sum(
            1
            for kind in ("base_benefits", "campaigns")
            for item in existing[kind]
            if isinstance(item, dict) and r.card_key and item.get("card_key") == r.card_key
        )
        logger.warning("kept %d existing entries for failed card %s", kept, r.card_key or r.card)
    logger.info(
        "wrote %d base benefits and %d campaigns to %s (%d new this run)",
        len(merged["base_benefits"]),
        len(merged["campaigns"]),
        path,
        new_total,
    )
    if failed:
        logger.warning(
            "%d cards failed: %s",
            len(failed),
            ", ".join(r.card_key or f"{r.bank} {r.card}" for r in failed),
        )
    return 0
