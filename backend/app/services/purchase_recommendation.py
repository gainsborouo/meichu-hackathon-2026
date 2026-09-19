"""Purchase recommendation: backend facts in, model judgement, backend gates out.

The split is deliberate. This module never ranks cards. It:

  1. builds the set of candidates the model may choose from (hard filters + reward math),
  2. hands that set to a model, which does the actual ranking and reasoning,
  3. checks the model stayed inside the set, re-attaches the backend's own numbers,
     decides verification from the URL's domain, and applies the "is waiting really
     worth it" gates.

The only write is `mark_verified`: stamping `sales.official_verified_at` on campaigns
the model backed with an allow-listed official URL. Nothing here writes to a calendar or
to user_sales; `calendar_draft` is plain data.

Live search and page opening verify and supplement campaigns that already exist in `sales`. It never
adds new campaigns: the candidate set comes only from the crawler + import_sales data.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CardBenefit, Sale, User
from app.repositories import analyses as analyses_repo
from app.repositories import benefits as benefits_repo
from app.repositories import cards as cards_repo
from app.repositories import sales as sales_repo
from app.repositories.benefits import is_effective
from app.schemas.recommendations import (
    BestNow,
    CalendarDraft,
    CardRef,
    OfficialSource,
    RecommendationRequest,
    RecommendationResponse,
    WaitSuggestion,
)
from app.services.official_sources import filter_official, normalize_url
from app.services.reward_rules import _PLATFORM_ALIASES

TAIPEI = timezone(timedelta(hours=8))
_MAX_ANALYSIS_JSON_CHARS = 8000

# (payload for the model) -> raw model JSON. Real one lives in recommendation_agent.
RecommendationAgent = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class RecommendationError(RuntimeError):
    """The model's answer was unusable, e.g. it picked a card outside the allowed set."""


@dataclass
class Preprocessed:
    mode: str
    today: date
    request: RecommendationRequest
    held_cards: list[dict[str, Any]]
    now_candidates: list[dict[str, Any]]
    future_candidates: list[dict[str, Any]]
    spend_context: dict[str, Any]
    excluded: dict[str, int] = field(default_factory=dict)

    @property
    def has_candidates(self) -> bool:
        return bool(self.now_candidates or self.future_candidates)

    def model_payload(self) -> dict[str, Any]:
        """Everything the model sees. Deliberately excludes email and other identity."""
        return {
            "today": self.today.isoformat(),
            "mode": self.mode,
            "request": self.request.model_dump(),
            "held_cards": self.held_cards,
            "now_candidates": self.now_candidates,
            "future_candidates": self.future_candidates,
            "spend_context": self.spend_context,
        }


# --- preprocess -----------------------------------------------------------


def _card_ref(card) -> dict[str, Any]:
    return {
        "id": str(card.id),
        "bank_name": card.bank_name,
        "name": card.name,
        "artwork_id": card.artwork_id,
    }


def _fmt_rate(rate: float) -> str:
    return f"{round(rate * 100, 4):g}%"


def _store_matches(store: str, platforms: list[str]) -> bool:
    text = store.lower()
    return any(
        alias in text for slug in platforms for alias in _PLATFORM_ALIASES.get(slug, (slug,))
    )


def _cap_description(rule: dict[str, Any], locale: str) -> str | None:
    if rule.get("unlimited"):
        return "No reward cap" if locale == "en-US" else "無上限"
    if rule.get("cap_amount") is None:
        return None
    monthly = "每月" in (rule.get("source_text") or "")
    unit = rule.get("cap_unit") or "元"
    if locale == "en-US":
        translated_unit = {
            "元": "TWD",
            "點": "points",
            "點數": "points",
            "胖達幣": "Panda Coins",
        }.get(unit, unit)
        period = "Monthly " if monthly else ""
        return f"{period}cap: {rule['cap_amount']:g} {translated_unit}"
    period = "每月" if monthly else ""
    return f"{period}上限 {rule['cap_amount']:g} {unit}"


def _estimate(price: float, rule: dict[str, Any]) -> tuple[float, bool]:
    reward = price * rule["rate"]
    cap = rule.get("cap_amount")
    if not rule.get("unlimited") and cap is not None and reward > cap:
        return round(float(cap), 1), True
    return round(reward, 1), False


def _spend_context(user: User, analyses: list, card_names: dict) -> dict[str, Any]:
    recent = []
    for row in analyses:
        data = row.analysis_data
        if (
            data is not None
            and len(json.dumps(data, ensure_ascii=False)) > _MAX_ANALYSIS_JSON_CHARS
        ):
            data = {"omitted": "analysis_data too large to include"}
        recent.append(
            {
                "month": row.analysis_month.strftime("%Y-%m"),
                "card": card_names.get(row.user_card_id),
                "report": row.report,
                "analysis_data": data,
            }
        )
    return {"latest_spend_report": user.latest_spend_report, "recent_analyses": recent}


def _usable_rules(
    rules: list[dict[str, Any]],
    request: RecommendationRequest,
    registration_mode: bool,
    excluded: dict[str, int],
):
    """Yield (index, rule, platforms, platform_match, min_spend, reward, cap_applied) for
    each rule that passes the hard gates. Shared by campaigns and base benefits so both
    are held to identical registration / platform / threshold rules."""
    for index, rule in enumerate(rules or []):
        if rule.get("rate") is None:
            continue
        if bool(rule.get("requires_registration")) != registration_mode:
            excluded["registration_mode"] += 1
            continue
        if rule.get("scope") == "overseas":
            excluded["overseas_only"] += 1
            continue
        platforms = rule.get("platforms") or []
        platform_match = bool(platforms) and _store_matches(request.store_name, platforms)
        if platforms and not platform_match:
            excluded["other_platform"] += 1
            continue
        min_spend = rule.get("min_spend")
        if min_spend is not None and request.price < min_spend:
            excluded["below_min_spend"] += 1
            continue
        reward, cap_applied = _estimate(request.price, rule)
        yield index, rule, platforms, platform_match, min_spend, reward, cap_applied


def _candidate(
    *,
    candidate_id: str,
    candidate_type: str,
    card: dict[str, Any],
    title: str,
    rule: dict[str, Any],
    platforms: list[str],
    platform_match: bool,
    min_spend: Any,
    reward: float,
    cap_applied: bool,
    locale: str,
    source_url: str | None,
    verified_at: datetime | None,
    sale_id: str | None = None,
    benefit_id: str | None = None,
    campaign_start: date | None = None,
    campaign_end: date | None = None,
    effective_start: date | None = None,
    effective_end: date | None = None,
    is_future: bool = False,
    registration_url: str | None = None,
) -> dict[str, Any]:
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "card": card,
        "sale_id": sale_id,
        "benefit_id": benefit_id,
        "title": title,
        "campaign_start": iso(campaign_start),
        "campaign_end": iso(campaign_end),
        "effective_start": iso(effective_start),
        "effective_end": iso(effective_end),
        "is_future": is_future,
        "rate": rule["rate"],
        "rate_display": _fmt_rate(rule["rate"]),
        "rate_max_display": _fmt_rate(rule["rate_max"])
        if rule.get("rate_max") and rule["rate_max"] != rule["rate"]
        else None,
        "cap_description": _cap_description(rule, locale),
        "cap_applied": cap_applied,
        "min_spend": min_spend,
        "estimated_reward_twd": reward,
        "requires_registration": bool(rule.get("requires_registration")),
        "registration_url": registration_url if rule.get("requires_registration") else None,
        "conditions": rule.get("source_text"),
        "categories": rule.get("categories") or [],
        "platforms": platforms,
        "platform_match": platform_match,
        "source_url": source_url,
        "official_verified_at": iso(verified_at),
    }


def build_candidates(
    *,
    sales: list[Sale],
    request: RecommendationRequest,
    registration_mode: bool,
    today: date,
    card_refs: dict[Any, dict[str, Any]],
    benefits: list[CardBenefit] | None = None,
) -> tuple[list[dict], list[dict], dict[str, int]]:
    """Hard filters + reward math. No ordering by reward, by design.

    Candidate sets by mode:
      registration_mode False: effective base benefits + effective campaigns that need no
        registration.
      registration_mode True:  only effective campaigns that need registration. Base
        benefits (and free campaigns) are never used as a fallback here.
    """
    now: list[dict] = []
    future: list[dict] = []
    excluded = {
        "expired": 0,
        "registration_mode": 0,
        "below_min_spend": 0,
        "other_platform": 0,
        "overseas_only": 0,
        "base_benefit_registration_mode": 0,
        "base_benefit_not_effective": 0,
    }

    for benefit in sorted(benefits or [], key=lambda b: (str(b.card_id), b.title)):
        if registration_mode:
            excluded["base_benefit_registration_mode"] += 1
            continue
        if not is_effective(benefit, today) or benefit.card_id not in card_refs:
            excluded["base_benefit_not_effective"] += 1
            continue
        for index, rule, platforms, platform_match, min_spend, reward, cap in _usable_rules(
            benefit.reward_rules, request, registration_mode, excluded
        ):
            now.append(
                _candidate(
                    candidate_id=f"benefit:{benefit.id}#{index}",
                    candidate_type="base_benefit",
                    card=card_refs[benefit.card_id],
                    title=benefit.title,
                    rule=rule,
                    platforms=platforms,
                    platform_match=platform_match,
                    min_spend=min_spend,
                    reward=reward,
                    cap_applied=cap,
                    locale=request.locale,
                    source_url=benefit.source_url,
                    verified_at=benefit.official_verified_at,
                    benefit_id=str(benefit.id),
                    effective_start=benefit.effective_start,
                    effective_end=benefit.effective_end,
                )
            )

    for sale in sorted(sales, key=lambda s: (s.bank_name, s.card_name, s.id)):
        start, end = sale.campaign_start, sale.campaign_end
        if end is not None and end < today:
            excluded["expired"] += 1
            continue
        is_future = start is not None and start > today
        if is_future and end is not None and end < start:
            excluded["expired"] += 1
            continue

        for index, rule, platforms, platform_match, min_spend, reward, cap in _usable_rules(
            sale.reward_rules, request, registration_mode, excluded
        ):
            candidate = _candidate(
                candidate_id=f"{sale.id}#{index}",
                candidate_type="campaign",
                card=card_refs[sale.card_id],
                title=sale.title,
                rule=rule,
                platforms=platforms,
                platform_match=platform_match,
                min_spend=min_spend,
                reward=reward,
                cap_applied=cap,
                locale=request.locale,
                source_url=sale.source_url,
                verified_at=sale.official_verified_at,
                sale_id=sale.id,
                campaign_start=start,
                campaign_end=end,
                is_future=is_future,
                registration_url=sale.register_url,
            )
            (future if is_future else now).append(candidate)

    return now, future, excluded


async def preprocess(
    session: AsyncSession,
    user: User,
    request: RecommendationRequest,
    *,
    today: date | None = None,
) -> Preprocessed:
    today = today or datetime.now(TAIPEI).date()
    user_cards = await cards_repo.list_user_cards(session, user.id)
    card_refs = {uc.card_id: _card_ref(uc.card) for uc in user_cards}
    card_names = {uc.id: uc.card.name for uc in user_cards}

    sales = await sales_repo.list_sales_for_user_cards(session, user.id) if user_cards else []
    benefits = (
        await benefits_repo.list_effective_for_user_cards(session, user.id, today)
        if user_cards
        else []
    )
    analyses = await analyses_repo.latest_months_for_user(session, user.id, months=3)

    registration_mode = bool(user.registration_campaigns_enabled)
    now, future, excluded = build_candidates(
        sales=sales,
        request=request,
        registration_mode=registration_mode,
        today=today,
        card_refs=card_refs,
        benefits=benefits,
    )
    return Preprocessed(
        mode="registration" if registration_mode else "no_registration",
        today=today,
        request=request,
        held_cards=list(card_refs.values()),
        now_candidates=now,
        future_candidates=future,
        spend_context=_spend_context(user, analyses, card_names),
        excluded=excluded,
    )


# --- assemble -------------------------------------------------------------


def _pick(raw: Any, allowed: dict[str, dict], label: str) -> tuple[dict, dict] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("candidate_id"), str):
        raise RecommendationError(f"{label} is malformed")
    candidate = allowed.get(raw["candidate_id"])
    if candidate is None:
        raise RecommendationError(
            f"model selected {raw['candidate_id']!r} for {label}, "
            "which is not among the allowed candidates"
        )
    return candidate, raw


def _opened(raw: dict[str, Any]) -> set[str]:
    urls = raw.get("opened_urls")
    return (
        {normalize_url(u) for u in urls if isinstance(u, str)} if isinstance(urls, list) else set()
    )


def _verified_sources(candidate: dict, pick: dict, opened: set[str]) -> list[dict]:
    """Model-claimed official sources that are on the bank's domain AND that the backend
    itself opened successfully during this run.

    Neither the allow-list nor a search hit is enough: a model can cite a plausible bank
    URL it never read, and a search snippet only proves the URL was listed. `opened_urls`
    is filled in by the agent runner from `open_official_page` (a real backend GET that
    returned a readable 2xx page), never from model output. A runner that reports
    nothing opened verifies nothing.
    """
    sources = pick.get("official_sources")
    official = filter_official(
        candidate["card"]["bank_name"], sources if isinstance(sources, list) else []
    )
    return [s for s in official if normalize_url(s["url"]) in opened]


def _reason(raw: dict) -> str:
    return str(raw.get("reason") or "").strip()


def _calendar_draft(pre: Preprocessed, cand: dict) -> CalendarDraft:
    start = date.fromisoformat(cand["campaign_start"])
    if pre.request.locale == "en-US":
        notes = [
            f"Offer: {cand['title']} ({cand['card']['bank_name']} {cand['card']['name']})",
            f"Starts: {cand['campaign_start']}",
            f"Estimated reward: about NT${cand['estimated_reward_twd']:g} ({cand['rate_display']})",
        ]
        if cand["cap_description"]:
            notes.append(f"Reward cap: {cand['cap_description']}")
        if cand["conditions"]:
            notes.append(f"Terms: {cand['conditions']}")
        if cand["requires_registration"]:
            notes.append(
                "Registration required"
                + (f": {cand['registration_url']}" if cand["registration_url"] else "")
            )
        title = f"Buy {pre.request.product_name} at {pre.request.store_name}"
    else:
        notes = [
            f"活動：{cand['title']}（{cand['card']['bank_name']} {cand['card']['name']}）",
            f"開始日：{cand['campaign_start']}",
            f"預估回饋：約 NT${cand['estimated_reward_twd']:g}（{cand['rate_display']}）",
        ]
        if cand["cap_description"]:
            notes.append(f"回饋上限：{cand['cap_description']}")
        if cand["conditions"]:
            notes.append(f"條件：{cand['conditions']}")
        if cand["requires_registration"]:
            notes.append(
                "需先登錄活動"
                + (f"：{cand['registration_url']}" if cand["registration_url"] else "")
            )
        title = f"{pre.request.store_name} 購買 {pre.request.product_name}"
    return CalendarDraft(
        title=title,
        starts_at=datetime(start.year, start.month, start.day, 9, tzinfo=TAIPEI),
        notes="\n".join(notes),
    )


def _wait_suggestion(
    pre: Preprocessed,
    best_now: BestNow | None,
    future_pick: tuple[dict, dict] | None,
    opened: set[str],
) -> WaitSuggestion | None:
    if future_pick is None:
        return None
    cand, raw = future_pick
    start_raw = cand.get("campaign_start")
    if not start_raw or date.fromisoformat(start_raw) <= pre.today:
        return None
    sources = _verified_sources(cand, raw, opened)
    if not sources:
        return None
    now_reward = best_now.estimated_reward_twd if best_now else 0.0
    extra = round(cand["estimated_reward_twd"] - now_reward, 1)
    if extra <= 0:
        return None
    return WaitSuggestion(
        card=CardRef(**cand["card"]),
        sale_id=cand["sale_id"],
        starts_at=date.fromisoformat(start_raw),
        estimated_reward_twd=cand["estimated_reward_twd"],
        estimated_extra_reward_twd=extra,
        reason=_reason(raw),
        official_sources=[OfficialSource(**s) for s in sources],
        calendar_draft=_calendar_draft(pre, cand),
    )


def empty_explanation(pre: Preprocessed) -> str:
    if pre.request.locale == "en-US":
        if not pre.held_cards:
            return "No cards have been added, so a recommendation cannot be made."
        kind = (
            "require registration" if pre.mode == "registration" else "do not require registration"
        )
        return f"None of your cards has a valid offer for this purchase that {kind}."
    if not pre.held_cards:
        return "尚未加入任何持有的信用卡，無法推薦。"
    if pre.mode == "registration":
        return "目前持有的卡片中，沒有符合此購物條件且需登錄的有效優惠。"
    return "目前持有的卡片中，沒有符合此購物條件的有效基本回饋或免登錄優惠。"


def assemble(pre: Preprocessed, raw: dict[str, Any]) -> RecommendationResponse:
    if not isinstance(raw, dict):
        raise RecommendationError("model did not return a JSON object")

    now_by_id = {c["candidate_id"]: c for c in pre.now_candidates}
    future_by_id = {c["candidate_id"]: c for c in pre.future_candidates}
    now_pick = _pick(raw.get("best_now"), now_by_id, "best_now")
    future_pick = _pick(raw.get("best_future"), future_by_id, "best_future")

    opened = _opened(raw)
    best_now: BestNow | None = None
    if now_pick is not None:
        cand, model_raw = now_pick
        sources = _verified_sources(cand, model_raw, opened)
        best_now = BestNow(
            candidate_type=cand["candidate_type"],
            card=CardRef(**cand["card"]),
            sale_id=cand["sale_id"],
            benefit_id=cand["benefit_id"],
            campaign_title=cand["title"],
            estimated_reward_twd=cand["estimated_reward_twd"],
            rate_display=cand["rate_display"],
            cap_description=cand["cap_description"],
            requires_registration=cand["requires_registration"],
            registration_url=cand["registration_url"],
            reason=_reason(model_raw),
            verification_status="verified" if sources else "unverified",
            official_sources=[OfficialSource(**s) for s in sources],
        )

    explanation = str(raw.get("explanation") or "").strip() or None
    if best_now is None and explanation is None:
        explanation = empty_explanation(pre)

    return RecommendationResponse(
        mode=pre.mode,  # type: ignore[arg-type]
        best_now=best_now,
        wait_suggestion=_wait_suggestion(pre, best_now, future_pick, opened),
        explanation=explanation,
    )


@dataclass
class Verified:
    """Rows whose pick was backed by an official page the backend opened."""

    sale_ids: list[str] = field(default_factory=list)
    benefit_ids: list[str] = field(default_factory=list)


def verified_targets(pre: Preprocessed, raw: dict[str, Any]) -> Verified:
    picks = (
        _pick(raw.get("best_now"), {c["candidate_id"]: c for c in pre.now_candidates}, "best_now"),
        _pick(
            raw.get("best_future"),
            {c["candidate_id"]: c for c in pre.future_candidates},
            "best_future",
        ),
    )
    opened = _opened(raw)
    out = Verified()
    for pick in picks:
        if pick is None or not _verified_sources(*pick, opened):
            continue
        cand = pick[0]
        if cand["candidate_type"] == "base_benefit":
            if cand["benefit_id"] not in out.benefit_ids:
                out.benefit_ids.append(cand["benefit_id"])
        elif cand["sale_id"] not in out.sale_ids:
            out.sale_ids.append(cand["sale_id"])
    return out


def verified_sale_ids(pre: Preprocessed, raw: dict[str, Any]) -> list[str]:
    return verified_targets(pre, raw).sale_ids


async def mark_verified(
    session: AsyncSession,
    sale_ids: list[str],
    benefit_ids: list[str] | None = None,
    *,
    at: datetime | None = None,
) -> None:
    """Stamp official_verified_at. Only ever called with ids that passed the allow-list."""
    if not sale_ids and not benefit_ids:
        return
    stamp = at or datetime.now(UTC)
    if sale_ids:
        await session.execute(
            update(Sale).where(Sale.id.in_(sale_ids)).values(official_verified_at=stamp)
        )
    if benefit_ids:
        await session.execute(
            update(CardBenefit)
            .where(CardBenefit.id.in_([uuid.UUID(b) for b in benefit_ids]))
            .values(official_verified_at=stamp)
        )
    await session.commit()


async def recommend(
    pre: Preprocessed, agent: RecommendationAgent
) -> tuple[RecommendationResponse, Verified]:
    """Run the model only when there is something to choose from.

    Returns the response and which rows were officially verified.
    """
    if not pre.has_candidates:
        empty = RecommendationResponse(
            mode=pre.mode,  # type: ignore[arg-type]
            best_now=None,
            wait_suggestion=None,
            explanation=empty_explanation(pre),
        )
        return empty, Verified()
    raw = await agent(pre.model_payload())
    response = assemble(pre, raw)
    return response, verified_targets(pre, raw)


__all__ = [
    "Preprocessed",
    "Verified",
    "RecommendationAgent",
    "RecommendationError",
    "assemble",
    "mark_verified",
    "verified_sale_ids",
    "verified_targets",
    "build_candidates",
    "preprocess",
    "recommend",
]
