"""Pick the card that earns the most on a specific purchase.

Ranking runs off `sales.reward_rules` (derived at import time by
app.services.reward_rules) rather than the prose reward, so a request is a
handful of dict comparisons instead of 43 text parses.

A card whose campaigns yield no quantified rule is not dropped -- it is returned
with `estimated_reward: null` and ranked last. "We could not read a rate off this
campaign" is useful; silently omitting the card is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models import Sale, UserCard
from app.services.reward_rules import _CATEGORY_KEYWORDS  # shared vocabulary

# How specifically a rule matched the query. Higher wins ties on equal money,
# because a platform-specific offer is more likely to actually apply.
_MATCH_PLATFORM = 3
_MATCH_CATEGORY = 2
_MATCH_GENERAL = 1


@dataclass(frozen=True)
class Query:
    price: float
    platform: str | None = None
    category: str | None = None
    currency: str = "TWD"


def normalize_category(raw: str | None) -> str | None:
    """Accept either a taxonomy name ("dining") or an item name ("除濕機").

    Returns None when the text matches nothing known, which simply means only
    platform-specific and general rules will apply -- better than forcing the
    purchase into a category it may not belong to.
    """
    if not raw:
        return None
    text = raw.strip().lower()
    if text in _CATEGORY_KEYWORDS:
        return text
    for name, words in _CATEGORY_KEYWORDS.items():
        if any(w in text for w in words):
            return name
    return None


def _rule_match(rule: dict[str, Any], query: Query, category: str | None) -> int:
    """0 when the rule cannot apply to this purchase."""
    # The query has no notion of travelling abroad, so an overseas-only rate
    # would overstate what a local purchase earns.
    if rule.get("scope") == "overseas":
        return 0
    min_spend = rule.get("min_spend")
    if min_spend and query.price < min_spend:
        return 0

    platforms = rule.get("platforms") or []
    categories = rule.get("categories") or []
    if query.platform and query.platform in platforms:
        return _MATCH_PLATFORM
    if category and category in categories:
        return _MATCH_CATEGORY
    if not platforms and not categories:
        return _MATCH_GENERAL
    return 0


def _reward_amount(rule: dict[str, Any], price: float) -> float:
    amount = price * float(rule.get("rate") or 0)
    cap = rule.get("cap_amount")
    if cap is not None and not rule.get("unlimited"):
        # Caps are usually monthly; treating one as a per-purchase ceiling is
        # conservative, which is the right direction for a recommendation.
        amount = min(amount, float(cap))
    return round(amount, 2)


def _best_rule_for_sale(sale: Sale, query: Query, category: str | None):
    best = None
    for rule in sale.reward_rules or []:
        specificity = _rule_match(rule, query, category)
        if not specificity:
            continue
        amount = _reward_amount(rule, query.price)
        candidate = (amount, specificity, rule)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best


def evaluate_card(
    card_sales: list[Sale], query: Query, category: str | None
) -> tuple[float | None, int, dict[str, Any] | None, Sale | None, list[Sale]]:
    """Best achievable reward for one card, plus the campaigns behind it."""
    best_amount: float | None = None
    best_spec = 0
    best_rule: dict[str, Any] | None = None
    best_sale: Sale | None = None
    matched: list[Sale] = []

    for sale in card_sales:
        found = _best_rule_for_sale(sale, query, category)
        if found is None:
            continue
        amount, specificity, rule = found
        matched.append(sale)
        if best_amount is None or (amount, specificity) > (best_amount, best_spec):
            best_amount, best_spec, best_rule, best_sale = amount, specificity, rule, sale

    return best_amount, best_spec, best_rule, best_sale, matched


def _reason(rule: dict[str, Any] | None, amount: float | None, currency: str) -> str:
    if rule is None or amount is None:
        return "此卡的活動文字未載明可計算的回饋率，請參考活動說明。"
    rate = f"{float(rule.get('rate') or 0) * 100:g}%"
    bits = [f"回饋 {rate}，約 {currency} {amount:g}"]
    if rule.get("unlimited"):
        bits.append("無上限")
    elif rule.get("cap_amount"):
        bits.append(f"上限 {rule['cap_amount']:g}{rule.get('cap_unit') or ''}")
    if rule.get("requires_registration"):
        bits.append("需登錄")
    return "；".join(bits)


def rank(
    cards: list[tuple[UserCard | None, Any, list[Sale]]], query: Query
) -> list[dict[str, Any]]:
    """Rank (user_card, card, its sales) triples best-first.

    Cards with a computable reward come first by money earned, then by how
    specifically the rule matched, then preferring offers needing no
    registration. Unquantifiable cards follow, so they stay visible.
    """
    category = normalize_category(query.category)
    scored = []
    for user_card, card, card_sales in cards:
        amount, spec, rule, sale, matched = evaluate_card(card_sales, query, category)
        scored.append(
            {
                "user_card_id": user_card.id if user_card else None,
                "owned": user_card is not None,
                "card": card,
                "estimated_reward": None
                if amount is None
                else {
                    "amount": amount,
                    "rate": rule.get("rate"),
                    "rate_max": rule.get("rate_max"),
                    "currency": query.currency,
                    "unit": rule.get("cap_unit"),
                    "capped": bool(rule.get("cap_amount")) and not rule.get("unlimited"),
                    "requires_registration": bool(rule.get("requires_registration")),
                    "source_text": rule.get("source_text"),
                },
                "matched_sales": matched,
                "best_sale": sale,
                "reason": _reason(rule, amount, query.currency),
                "_sort": (
                    amount is not None,
                    amount or 0.0,
                    spec,
                    not (rule or {}).get("requires_registration", False),
                ),
            }
        )
    scored.sort(key=lambda row: row["_sort"], reverse=True)
    for row in scored:
        row.pop("_sort")
    return scored
