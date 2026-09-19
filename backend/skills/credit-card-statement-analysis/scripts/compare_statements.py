#!/usr/bin/env python3
"""Compare several monthly summaries into a spending trend.

People usually send more than one statement, and the interesting question shifts
from "where did my money go" to "what changed". This reads two or more summary
JSON files produced by analyze_transactions.py and reports per-period totals,
category movement, and -- the part worth the most -- merchants that recur across
every period, which is how forgotten subscriptions surface.

Usage:
    python compare_statements.py june.json july.json august.json -o trend.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path


def dec(value) -> Decimal:
    return Decimal(str(value or 0))


def num(value: Decimal):
    value = value.quantize(Decimal("0.01"))
    return int(value) if value == value.to_integral_value() else float(value)


def period_label(summary: dict, fallback: str) -> str:
    """Name the period, preferring the statement's own dates over the filename."""
    start = (summary.get("period") or {}).get("start")
    if start:
        return str(start)[:7]
    months = Counter(row["date"][:7] for row in summary.get("daily", []) if row.get("date"))
    if months:
        return months.most_common(1)[0][0]
    return fallback


def compare(summaries: list[tuple[str, dict]]) -> dict:
    labels = [label for label, _ in summaries]
    currencies = {s.get("currency") for _, s in summaries if s.get("currency")}
    if len(currencies) > 1:
        print(
            f"warning: summaries mix currencies {sorted(currencies)}; totals are not "
            "comparable across them",
            file=sys.stderr,
        )

    periods = [
        {
            "label": label,
            "net_spend": s["totals"]["net_spend"],
            "txn_count": s["totals"]["txn_count"],
            "fees": s["totals"]["fees"],
        }
        for label, s in summaries
    ]

    by_category: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    by_merchant: dict[str, dict] = defaultdict(
        lambda: {"display": "", "amounts": defaultdict(Decimal), "txns": 0}
    )
    for label, s in summaries:
        for cat in s.get("categories", []):
            by_category[cat["name"]][label] += dec(cat["amount"])
        for m in s.get("merchants", s.get("top_merchants", [])):
            key = (m["merchant"] or "").strip().casefold()
            if not key:
                continue
            entry = by_merchant[key]
            entry["display"] = entry["display"] or m["merchant"]
            entry["amounts"][label] += dec(m["amount"])
            entry["txns"] += m.get("txn_count", 0)

    first, last = labels[0], labels[-1]

    category_trend = []
    for name, amounts in by_category.items():
        start, end = amounts.get(first, Decimal(0)), amounts.get(last, Decimal(0))
        category_trend.append(
            {
                "name": name,
                "by_period": {label: num(amounts.get(label, Decimal(0))) for label in labels},
                "total": num(sum(amounts.values(), Decimal(0))),
                "change": num(end - start),
                "present_in_periods": sum(1 for label in labels if amounts.get(label)),
            }
        )
    category_trend.sort(key=lambda c: c["total"], reverse=True)

    recurring = []
    for entry in by_merchant.values():
        present = [label for label in labels if entry["amounts"].get(label)]
        if len(present) < 2:
            continue
        values = [entry["amounts"][label] for label in present]
        spread = max(values) - min(values)
        recurring.append(
            {
                "merchant": entry["display"],
                "periods_present": len(present),
                "by_period": {label: num(entry["amounts"].get(label, Decimal(0))) for label in labels},
                "total": num(sum(values, Decimal(0))),
                # A near-identical charge every period is what a subscription
                # looks like; a merchant you simply visit often varies more.
                "stable_amount": bool(max(values) > 0 and spread / max(values) <= Decimal("0.1")),
                "every_period": len(present) == len(labels),
            }
        )
    recurring.sort(key=lambda m: (m["periods_present"], m["total"]), reverse=True)

    seen_before = {
        key for key, e in by_merchant.items() if any(e["amounts"].get(l) for l in labels[:-1])
    }
    in_last = {key for key, e in by_merchant.items() if e["amounts"].get(last)}
    new_merchants = [by_merchant[k]["display"] for k in in_last - seen_before]
    dropped = [
        by_merchant[k]["display"]
        for k in seen_before - in_last
        if by_merchant[k]["amounts"].get(first)
    ]

    spends = [dec(p["net_spend"]) for p in periods]
    change = spends[-1] - spends[0]

    return {
        "currency": next(iter(currencies), None),
        "periods": periods,
        "totals": {
            "net_spend": num(sum(spends, Decimal(0))),
            "mean_net_spend": num(sum(spends, Decimal(0)) / len(spends)),
            "change_first_to_last": num(change),
            "change_pct": float(round(change / spends[0], 4)) if spends[0] else None,
            "total_fees": num(sum(dec(p["fees"]) for p in periods)),
        },
        "category_trend": category_trend,
        "recurring_across_periods": recurring,
        "new_merchants": sorted(new_merchants),
        "dropped_merchants": sorted(dropped),
        "insights": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summaries", nargs="+", help="summary JSON files, oldest first")
    parser.add_argument("-o", "--out", help="write trend JSON here (default: stdout)")
    args = parser.parse_args()

    if len(args.summaries) < 2:
        print("error: need at least two summaries to compare", file=sys.stderr)
        return 1

    loaded = []
    for path in args.summaries:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        loaded.append((period_label(data, Path(path).stem), data))
    loaded.sort(key=lambda pair: pair[0])

    text = json.dumps(compare(loaded), ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
