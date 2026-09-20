#!/usr/bin/env python3
"""Roll several cards' monthly analyses up into one set of facts.

A user holds more than one card and each card is analyzed separately, so
`user_analyses` holds a grid: one row per (card, month). Anyone asking "how have
I been spending lately" wants that grid collapsed -- totals per month across all
cards, categories that moved, charges that repeat every month.

The arithmetic lives here rather than in the model because the model's job is to
notice what matters and say it well; adding up nine months' worth of category
totals by hand is where quiet errors come from, and a summary that misstates
someone's spending is worse than no summary.

Input (stdin or a path): the rows as JSON, oldest first or any order:

    {"rows": [
      {"analysis_month": "2026-08-01",
       "card": {"bank_name": "玉山銀行", "name": "Pi 信用卡"},
       "report": "...",
       "analysis_data": {"summary": { ... }, "trend": { ... }}}
    ]}

Output: facts JSON, ready to write prose from.

Usage:
    python aggregate_analyses.py rows.json -o facts.json
    cat rows.json | python aggregate_analyses.py -
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any


class InputError(Exception):
    """The rows cannot be trusted enough to summarize."""


def dec(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def num(value: Decimal):
    value = value.quantize(Decimal("0.01"))
    return int(value) if value == value.to_integral_value() else float(value)


def month_key(row: dict) -> str:
    raw = str(row.get("analysis_month") or "")[:7]
    if len(raw) != 7:
        raise InputError(f"analysis_month {row.get('analysis_month')!r} is not YYYY-MM-DD")
    return raw


def card_label(row: dict) -> str:
    card = row.get("card") or {}
    bank, name = (card.get("bank_name") or "").strip(), (card.get("name") or "").strip()
    return " ".join(p for p in (bank, name) if p) or "未命名卡片"


def summary_of(row: dict) -> dict | None:
    """The structured summary, when the analysis carries one.

    Rows written by hand through the API may have no analysis_data, or data in a
    shape this never saw. Those still count as evidence -- their report text is
    real -- so they are reported as text-only rather than dropped.
    """
    data = row.get("analysis_data")
    if not isinstance(data, dict):
        return None
    summary = data.get("summary")
    return summary if isinstance(summary, dict) else None


def aggregate(payload: dict) -> dict:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise InputError('input must be an object with a "rows" array')
    if not rows:
        raise InputError("no analyses to summarize")

    months: dict[str, dict] = defaultdict(
        lambda: {
            "net_spend": Decimal(0),
            "txn_count": 0,
            "fees": Decimal(0),
            "cards": [],
            "categories": defaultdict(Decimal),
            "structured_cards": 0,
            "text_only_cards": 0,
        }
    )
    merchant_months: dict[str, dict] = defaultdict(
        lambda: {"display": "", "by_month": defaultdict(Decimal), "txn_count": 0}
    )
    subscriptions: dict[str, dict] = {}
    largest: dict | None = None
    currencies: set[str] = set()

    for row in rows:
        key = month_key(row)
        bucket = months[key]
        label = card_label(row)
        summary = summary_of(row)

        if summary is None:
            bucket["text_only_cards"] += 1
            bucket["cards"].append({"card": label, "net_spend": None, "structured": False})
            continue

        bucket["structured_cards"] += 1
        totals = summary.get("totals") or {}
        net = dec(totals.get("net_spend"))
        bucket["net_spend"] += net
        bucket["txn_count"] += int(totals.get("txn_count") or 0)
        bucket["fees"] += dec(totals.get("fees"))
        bucket["cards"].append({"card": label, "net_spend": num(net), "structured": True})
        if summary.get("currency"):
            currencies.add(str(summary["currency"]))

        for cat in summary.get("categories") or []:
            bucket["categories"][cat.get("name") or "uncategorized"] += dec(cat.get("amount"))

        for merchant in summary.get("merchants") or summary.get("top_merchants") or []:
            name = (merchant.get("merchant") or "").strip()
            if not name:
                continue
            entry = merchant_months[name.casefold()]
            entry["display"] = entry["display"] or name
            entry["by_month"][key] += dec(merchant.get("amount"))
            entry["txn_count"] += int(merchant.get("txn_count") or 0)

        for sub in summary.get("possible_subscriptions") or []:
            name = (sub.get("merchant") or "").strip()
            if name:
                slot = subscriptions.setdefault(
                    name.casefold(), {"merchant": name, "months": set(), "total": Decimal(0)}
                )
                slot["months"].add(key)
                slot["total"] += dec(sub.get("amount"))

        big = (summary.get("patterns") or {}).get("largest_transaction")
        if big and (largest is None or dec(big.get("amount")) > dec(largest.get("amount"))):
            largest = {**big, "month": key, "card": label}

    ordered = sorted(months)
    month_rows = []
    for key in ordered:
        bucket = months[key]
        cats = sorted(bucket["categories"].items(), key=lambda kv: kv[1], reverse=True)
        month_rows.append(
            {
                "month": key,
                "net_spend": num(bucket["net_spend"]),
                "txn_count": bucket["txn_count"],
                "fees": num(bucket["fees"]),
                "top_category": cats[0][0] if cats else None,
                "top_category_amount": num(cats[0][1]) if cats else None,
                "categories": [{"name": n, "amount": num(a)} for n, a in cats],
                "cards": bucket["cards"],
                "text_only_cards": bucket["text_only_cards"],
            }
        )

    # Category movement across the window: what grew, what stopped.
    category_trend: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for row in month_rows:
        for cat in row["categories"]:
            category_trend[cat["name"]][row["month"]] = dec(cat["amount"])
    trend_rows = []
    for name, by_month in category_trend.items():
        first = by_month.get(ordered[0], Decimal(0))
        last = by_month.get(ordered[-1], Decimal(0))
        trend_rows.append(
            {
                "name": name,
                "by_month": {m: num(by_month.get(m, Decimal(0))) for m in ordered},
                "total": num(sum(by_month.values(), Decimal(0))),
                "change": num(last - first),
                "months_present": sum(1 for m in ordered if by_month.get(m)),
            }
        )
    trend_rows.sort(key=lambda c: c["total"], reverse=True)

    recurring = [
        {
            "merchant": entry["display"],
            "months_present": len([m for m in ordered if entry["by_month"].get(m)]),
            "by_month": {m: num(entry["by_month"].get(m, Decimal(0))) for m in ordered},
            "total": num(sum(entry["by_month"].values(), Decimal(0))),
            "every_month": len([m for m in ordered if entry["by_month"].get(m)]) == len(ordered),
        }
        for entry in merchant_months.values()
        if len([m for m in ordered if entry["by_month"].get(m)]) >= 2
    ]
    recurring.sort(key=lambda m: (m["months_present"], m["total"]), reverse=True)

    spends = [dec(r["net_spend"]) for r in month_rows]
    total = sum(spends, Decimal(0))
    change = spends[-1] - spends[0] if len(spends) > 1 else Decimal(0)

    notes = []
    if len(ordered) < 3:
        notes.append(
            f"只有 {len(ordered)} 個月的資料，趨勢判斷有限。"
        )
    text_only = sum(r["text_only_cards"] for r in month_rows)
    if text_only:
        notes.append(
            f"{text_only} 筆分析沒有結構化資料，僅以文字報告計入，未納入金額統計。"
        )
    if len(currencies) > 1:
        notes.append(f"混合幣別 {sorted(currencies)}，金額未換算，合計僅供參考。")

    return {
        "currency": next(iter(currencies), "TWD"),
        "months": month_rows,
        "month_count": len(ordered),
        "totals": {
            "net_spend": num(total),
            "mean_monthly": num(total / len(spends)) if spends else 0,
            "change_first_to_last": num(change),
            "change_pct": float(round(change / spends[0], 4)) if spends and spends[0] else None,
            "fees": num(sum(dec(r["fees"]) for r in month_rows)),
        },
        "category_trend": trend_rows,
        "recurring_merchants": recurring,
        "subscriptions": sorted(
            (
                {
                    "merchant": s["merchant"],
                    "months_present": len(s["months"]),
                    "total": num(s["total"]),
                }
                for s in subscriptions.values()
            ),
            key=lambda s: (s["months_present"], s["total"]),
            reverse=True,
        ),
        "largest_transaction": (
            {**largest, "amount": num(dec(largest.get("amount")))} if largest else None
        ),
        "data_quality": {
            "analyses_read": len(rows),
            "currencies": sorted(currencies),
            "notes": notes,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help='rows JSON path, or "-" for stdin')
    parser.add_argument("-o", "--out", help="write facts JSON here (default: stdout)")
    args = parser.parse_args()

    raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    try:
        facts = aggregate(json.loads(raw))
    except json.JSONDecodeError as exc:
        print(f"error: input is not valid JSON: {exc}", file=sys.stderr)
        return 1
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(facts, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
