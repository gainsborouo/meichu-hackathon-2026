"""Derive structured reward rules from the free-text `reward` field of a campaign.

`sales.reward` is prose written for humans ("國內一般消費享 1% LINE POINTS 回饋無上限；
海外實體/線上消費享 2.8%"). /search needs a number it can rank on, and parsing 43 of
these per request would be slow and non-reproducible, so the extraction runs once at
import time and the result is stored in `sales.reward_rules`.

The extraction is deliberately heuristic and conservative: it reports what it is
confident about and leaves the rest empty rather than inventing a rate. The original
text always survives in `sales.reward` and `sales.source_payload`, so a caller can
always fall back to showing the prose -- which is what /search does when no rule
matches.
"""

from __future__ import annotations

import re
from typing import Any

# Clause separators. Rewards pack several offers into one sentence, and a rate
# belongs to the clause it appears in -- splitting first keeps "海外 2.8%" from
# being attached to the domestic rule.
_CLAUSE_SPLIT = re.compile(r"[；;。\n]+")

_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# 9折 = 10% off, 95折 = 5% off. Department-store campaigns quote discounts this way.
_DISCOUNT = re.compile(r"(\d{1,2})\s*折")
_CAP = re.compile(r"上限[^\d]{0,6}(?:NT\$)?\s*([\d,]+)\s*(點|元|胖達幣|點數)?")
_MIN_SPEND = re.compile(r"滿\s*(?:NT\$)?\s*([\d,]+)")
_UNLIMITED = re.compile(r"無上限")
_REGISTRATION = re.compile(r"登錄")

# Category keywords use the same vocabulary as the statement-analysis skill, so a
# spending category from one feature lines up with a reward rule from the other.
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "dining": ("餐飲", "美食", "外送", "foodpanda", "熊貓", "ubereats", "uber eats", "餐廳"),
    "fuel": ("加油", "中油", "台塑", "加油站", "汽油", "柴油"),
    "transport": ("交通", "大眾運輸", "捷運", "公車", "一卡通", "悠遊卡", "高鐵", "台鐵", "停車"),
    "groceries": ("超市", "量販", "全聯", "家樂福", "生鮮"),
    "convenience": ("超商", "便利商店", "7-eleven", "統一超商", "全家"),
    "online_shopping": ("網購", "線上購物", "電商", "momo", "蝦皮", "pchome", "博客來", "購物網"),
    "shopping_retail": (
        "百貨", "專櫃", "購物中心", "outlet", "高島屋", "漢神",
        "3c", "電腦", "筆電", "手機",
    ),
    "travel_lodging": ("訂房", "旅遊", "飯店", "機票", "agoda", "booking", "klook", "住宿"),
    "beauty_personal": ("藥妝", "美妝", "屈臣氏", "康是美", "寶雅"),
    "entertainment": ("影音", "串流", "電影", "遊戲", "娛樂"),
    "utilities_telecom": ("電信", "繳費", "水電"),
    # Item words, so a query like {"category": "除濕機"} resolves to something
    # the caller can see rather than silently falling back to "unknown".
    "home": ("家電", "除濕機", "冷氣", "冰箱", "洗衣機", "家具", "寢具", "廚具"),
}

# Platform aliases -> canonical slug the API accepts in {"platform": ...}.
_PLATFORM_ALIASES: dict[str, tuple[str, ...]] = {
    "momo": ("momo",),
    "shopee": ("蝦皮", "shopee"),
    "pchome": ("pchome", "24h購物"),
    "foodpanda": ("foodpanda", "熊貓", "胖達"),
    "ubereats": ("uber eats", "ubereats", "優食"),
    # Deliberately not "line points": that is the reward currency, and matching
    # it would mis-tag every general LINE Pay card clause as platform-specific.
    "line_pay": ("line pay", "linepay", "綁定 line"),
    "jkopay": ("街口",),
    "apple_pay": ("apple pay",),
    "google_pay": ("google pay",),
    "cpc": ("中油",),
    "books": ("博客來",),
}

_SCOPE_OVERSEAS = ("海外", "國外", "境外")
_SCOPE_DOMESTIC = ("國內", "本國")
_CHANNEL_ONLINE = ("線上", "網購", "電商", "網路")
_CHANNEL_PHYSICAL = ("實體", "門市", "店家", "專櫃")


def _matches(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def _rates(clause: str) -> list[float]:
    """Every rate the clause quotes, as fractions. Percentages and 折 discounts."""
    rates = [float(v) / 100 for v in _PERCENT.findall(clause)]
    for raw in _DISCOUNT.findall(clause):
        # 9折 -> 10% off; 95折 -> 5% off.
        rates.append((10 - int(raw)) / 10 if len(raw) == 1 else (100 - int(raw)) / 100)
    # Round away binary-float noise (2.2/100 -> 0.022000000000000002) so the
    # stored JSON and the API show the rate the campaign actually quotes.
    return [round(r, 6) for r in rates if 0 < r < 1]


def _cap(clause: str) -> tuple[float | None, str | None, bool]:
    if _UNLIMITED.search(clause):
        return None, None, True
    m = _CAP.search(clause)
    if not m:
        return None, None, False
    return float(m.group(1).replace(",", "")), (m.group(2) or "元"), False


def extract_rules(reward: str | None, *, register_url: str | None = None) -> list[dict[str, Any]]:
    """Turn one campaign's reward prose into zero or more structured rules.

    Returns an empty list when nothing quantifiable was found -- a campaign offering
    "免費停車" has real value but no rate, and guessing one would be worse than
    admitting the rule is unquantified.
    """
    if not reward:
        return []

    rules: list[dict[str, Any]] = []
    for clause in _CLAUSE_SPLIT.split(reward):
        clause = clause.strip()
        if not clause:
            continue
        rates = _rates(clause)
        if not rates:
            continue

        cap_amount, cap_unit, unlimited = _cap(clause)
        lowered = clause.lower()
        min_spend = _MIN_SPEND.search(clause)

        categories = sorted(
            name for name, words in _CATEGORY_KEYWORDS.items() if _matches(lowered, words)
        )
        platforms = sorted(
            slug for slug, words in _PLATFORM_ALIASES.items() if _matches(lowered, words)
        )

        channels = []
        if _matches(clause, _CHANNEL_ONLINE):
            channels.append("online")
        if _matches(clause, _CHANNEL_PHYSICAL):
            channels.append("physical")

        if _matches(clause, _SCOPE_OVERSEAS):
            scope = "overseas"
        elif _matches(clause, _SCOPE_DOMESTIC):
            scope = "domestic"
        else:
            scope = "any"

        rules.append(
            {
                # rate_max is what the marketing shouts; rate is what we rank on,
                # because "最高 15%" usually is not what a given purchase earns.
                "rate": min(rates),
                "rate_max": max(rates),
                "scope": scope,
                "channels": channels,
                "categories": categories,
                "platforms": platforms,
                "cap_amount": cap_amount,
                "cap_unit": cap_unit,
                "unlimited": unlimited,
                "min_spend": float(min_spend.group(1).replace(",", "")) if min_spend else None,
                "requires_registration": bool(_REGISTRATION.search(clause)) or bool(register_url),
                "source_text": clause,
            }
        )
    return rules
