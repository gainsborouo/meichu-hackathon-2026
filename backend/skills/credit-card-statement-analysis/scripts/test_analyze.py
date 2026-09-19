#!/usr/bin/env python3
"""Regression test for analyze_transactions.py, run with: python scripts/test_analyze.py

The expected values are hand-computed from evals/fixture_transactions.json, so a
change that silently shifts the arithmetic fails here rather than in someone's
spending summary.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from analyze_transactions import InputError, aggregate

FIXTURE = Path(__file__).parent.parent / "evals" / "fixture_transactions.json"

failures = []


def check(label, actual, expected):
    if actual != expected:
        failures.append(f"{label}: expected {expected!r}, got {actual!r}")


s = aggregate(json.loads(FIXTURE.read_text(encoding="utf-8")))

# 89+320+390+1250+65+1000+120+960+450+1200 = 5844, minus the 200 refund.
check("gross_spend", s["totals"]["gross_spend"], 5844)
check("refunds", s["totals"]["refunds"], 200)
check("net_spend", s["totals"]["net_spend"], 5644)
check("fees", s["totals"]["fees"], 1200)
# The NT$5,000 bill payment is not consumption and must stay out of the total.
check("txn_count", s["totals"]["txn_count"], 11)
check("excluded_payments", s["totals"]["excluded_payments"], 5000)
check("categories reconcile", sum(c["amount"] for c in s["categories"]), 5644)
check("reconciliation", s["reconciliation"]["matches"], True)
check("convenience total", next(c["amount"] for c in s["categories"] if c["name"] == "convenience"), 274)
check("refund offsets category", next(c["amount"] for c in s["categories"] if c["name"] == "online_shopping"), -200)
check("repeat merchant found", [m["merchant"] for m in s["repeat_merchants"]], ["全家便利商店"])
check("subscription found", [m["merchant"] for m in s["possible_subscriptions"]], ["Netflix"])
check("largest txn", s["patterns"]["largest_transaction"]["amount"], 1250)
# The annual-fee row is a charge, not a merchant, so it must not appear here.
check("fees excluded from merchants", [m for m in s["merchants"] if m["merchant"] == "年費"], [])
check("fee still in category total", next(c["amount"] for c in s["categories"] if c["name"] == "financial_fees"), 1200)
check("foreign currency", s["foreign_currency"], [{"currency": "USD", "amount": 29.99, "txn_count": 1}])

# Undated rows must not invent a busiest weekday.
undated = aggregate({"currency": "TWD", "transactions": [{"merchant": "X", "amount": 50}]})
check("no phantom weekday", undated["patterns"]["busiest_weekday"], None)

# Malformed input must fail loudly rather than produce a plausible-looking total.
for bad, why in [
    ({"transactions": [{"merchant": "X", "amount": -50}]}, "negative amount"),
    ({"transactions": [{"merchant": "X", "amount": 50, "type": "cashback"}]}, "unknown type"),
    ({"transactions": []}, "empty list"),
]:
    try:
        aggregate(bad)
        failures.append(f"{why}: expected InputError, got a summary")
    except InputError:
        pass

if failures:
    print("FAIL\n  " + "\n  ".join(failures))
    raise SystemExit(1)
print("all checks passed")
