#!/usr/bin/env python3
"""Aggregate transcribed credit-card transactions into a spending summary.

Reads the transactions JSON produced during transcription (schema in SKILL.md)
and writes a summary JSON: totals, per-category breakdown, top merchants,
repeat/subscription candidates, a daily series for charting, a foreign-currency
rollup, and a reconciliation check against the statement's printed total.

The arithmetic lives here rather than in the model because summing dozens of
amounts by hand is exactly the kind of thing language models get subtly wrong,
and a spending summary that is off by NT$300 is worse than no summary at all.

Usage:
    python analyze_transactions.py transactions.json -o summary.json
    cat transactions.json | python analyze_transactions.py -
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

# Charges that represent money leaving the account.
SPEND_TYPES = {"purchase", "installment", "fee", "interest"}
# Money coming back: reduces the category it came from.
REFUND_TYPES = {"refund"}
# Paying off the card is not consumption -- counting it would double the spend.
EXCLUDED_TYPES = {"payment"}
KNOWN_TYPES = SPEND_TYPES | REFUND_TYPES | EXCLUDED_TYPES

FEE_TYPES = {"fee", "interest"}

SUBSCRIPTION_HINTS = (
    "netflix", "spotify", "youtube", "kkbox", "icloud", "google one", "dropbox",
    "adobe", "openai", "chatgpt", "claude", "anthropic", "github", "notion",
    "disney", "apple music", "prime", "訂閱", "月費", "會員費",
)

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class InputError(Exception):
    """Raised when the transactions JSON cannot be trusted enough to aggregate."""


def money(value, field: str) -> Decimal:
    if value is None:
        raise InputError(f"missing amount in {field}")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InputError(f"amount {value!r} in {field} is not a number") from exc


def num(value: Decimal):
    """Render a Decimal as int when it is whole, else a 2dp float.

    Statements in TWD are almost always whole NT$, and emitting 8420.0 in the
    JSON makes downstream UI code render "8420.00" for no reason.
    """
    value = value.quantize(Decimal("0.01"))
    return int(value) if value == value.to_integral_value() else float(value)


def parse_date(raw, field: str):
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        raise InputError(f"date {raw!r} in {field} is not ISO YYYY-MM-DD") from None


def merchant_key(txn: dict) -> str:
    name = (txn.get("merchant") or txn.get("description") or "").strip()
    return " ".join(name.split()).casefold()


def load(path: str) -> dict:
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(f"input is not valid JSON: {exc}") from exc
    return data


def validate(transactions: list) -> None:
    problems = []
    for i, txn in enumerate(transactions):
        where = f"transactions[{i}]"
        if not isinstance(txn, dict):
            problems.append(f"{where} is not an object")
            continue
        kind = txn.get("type", "purchase")
        if kind not in KNOWN_TYPES:
            problems.append(f"{where}.type={kind!r} (expected one of {sorted(KNOWN_TYPES)})")
        if txn.get("amount") is None:
            problems.append(f"{where}.amount is missing")
        elif money(txn["amount"], where) < 0:
            problems.append(
                f"{where}.amount is negative -- record the magnitude and set "
                f'type to "refund" instead, so signs are applied in one place'
            )
    if problems:
        raise InputError("invalid transactions:\n  - " + "\n  - ".join(problems))


def aggregate(data: dict) -> dict:
    # Checked here rather than at load time so callers that pass a dict straight
    # in (an ADK function tool, say) get the same specific errors as the CLI.
    if not isinstance(data, dict) or not isinstance(data.get("transactions"), list):
        raise InputError('input must be an object with a "transactions" array')
    if not data["transactions"]:
        raise InputError("no transactions found -- transcription produced an empty list")

    transactions = data["transactions"]
    validate(transactions)

    statement = data.get("statement") or {}
    currency = data.get("currency") or statement.get("currency") or "TWD"

    spend = Decimal(0)
    refunds = Decimal(0)
    fees = Decimal(0)
    excluded = Decimal(0)
    counted = 0
    excluded_count = 0

    by_category: dict[str, dict] = defaultdict(lambda: {"amount": Decimal(0), "txn_count": 0})
    by_merchant: dict[str, dict] = defaultdict(
        lambda: {"amount": Decimal(0), "txn_count": 0, "display": "", "dates": []}
    )
    by_day: dict[date, Decimal] = defaultdict(Decimal)
    by_weekday: dict[int, Decimal] = defaultdict(Decimal)
    amounts: list[Decimal] = []
    foreign: dict[str, dict] = defaultdict(lambda: {"amount": Decimal(0), "txn_count": 0})
    largest = None
    uncertain = 0
    undated = 0
    mobile_amount = Decimal(0)
    mobile_count = 0

    for i, txn in enumerate(transactions):
        where = f"transactions[{i}]"
        kind = txn.get("type", "purchase")
        amount = money(txn["amount"], where)
        when = parse_date(txn.get("date"), where)

        if txn.get("confidence") == "low":
            uncertain += 1

        if kind in EXCLUDED_TYPES:
            excluded += amount
            excluded_count += 1
            continue

        signed = -amount if kind in REFUND_TYPES else amount
        if kind in REFUND_TYPES:
            refunds += amount
        else:
            spend += amount
            if kind in FEE_TYPES:
                fees += amount
        counted += 1

        category = (txn.get("category") or "uncategorized").strip() or "uncategorized"
        by_category[category]["amount"] += signed
        by_category[category]["txn_count"] += 1

        # Fees and interest are charges, not places you shopped. Grouping them
        # with merchants makes "國外交易服務費" look like a top merchant and
        # pollutes cross-month recurrence with rows nobody chose to spend at.
        key = "" if kind in FEE_TYPES else merchant_key(txn)
        if key:
            entry = by_merchant[key]
            entry["amount"] += signed
            entry["txn_count"] += 1
            entry["display"] = entry["display"] or (txn.get("merchant") or txn.get("description"))
            if when:
                entry["dates"].append(when.isoformat())

        if when:
            by_day[when] += signed
            by_weekday[when.weekday()] += signed
        else:
            undated += 1

        if kind not in REFUND_TYPES:
            amounts.append(amount)
            if largest is None or amount > money(largest["amount"], where):
                largest = {
                    "date": when.isoformat() if when else None,
                    "merchant": txn.get("merchant") or txn.get("description"),
                    "amount": amount,
                    "category": category,
                }

        if txn.get("mobile_payment"):
            mobile_amount += signed
            mobile_count += 1

        fx = txn.get("foreign") or {}
        if fx.get("currency") and fx.get("amount") is not None:
            slot = foreign[fx["currency"]]
            slot["amount"] += money(fx["amount"], f"{where}.foreign")
            slot["txn_count"] += 1

    net = spend - refunds

    categories = sorted(by_category.items(), key=lambda kv: kv[1]["amount"], reverse=True)
    category_rows = [
        {
            "name": name,
            "amount": num(vals["amount"]),
            "share": float(round(vals["amount"] / net, 4)) if net > 0 else 0.0,
            "txn_count": vals["txn_count"],
            "avg_txn": num(vals["amount"] / vals["txn_count"]) if vals["txn_count"] else 0,
        }
        for name, vals in categories
    ]

    merchants = sorted(by_merchant.values(), key=lambda v: v["amount"], reverse=True)
    merchant_rows = [
        {
            "merchant": m["display"],
            "amount": num(m["amount"]),
            "txn_count": m["txn_count"],
            "share": float(round(m["amount"] / net, 4)) if net > 0 else 0.0,
        }
        for m in merchants
    ]

    repeat = [
        {"merchant": m["display"], "txn_count": m["txn_count"], "amount": num(m["amount"])}
        for m in merchants
        if m["txn_count"] >= 3
    ]
    subscriptions = [
        {"merchant": m["display"], "txn_count": m["txn_count"], "amount": num(m["amount"])}
        for m in merchants
        if any(hint in (m["display"] or "").casefold() for hint in SUBSCRIPTION_HINTS)
    ]

    ordered = sorted(amounts)
    median = ordered[len(ordered) // 2] if ordered else Decimal(0)
    if ordered and len(ordered) % 2 == 0:
        median = (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2

    # Read with .get: indexing a defaultdict would create zero entries and make
    # an undated statement look like it had a busiest weekday.
    weekend = sum((by_weekday.get(d, Decimal(0)) for d in (5, 6)), Decimal(0))
    weekday = sum((by_weekday.get(d, Decimal(0)) for d in range(5)), Decimal(0))
    busiest = max(by_weekday.items(), key=lambda kv: kv[1], default=(None, Decimal(0)))
    busiest_weekday = WEEKDAY_NAMES[busiest[0]] if busiest[0] is not None and by_day else None

    summary = {
        "currency": currency,
        "period": {
            "start": statement.get("period_start"),
            "end": statement.get("period_end"),
        },
        "card": {
            "issuer": statement.get("issuer"),
            "last4": statement.get("card_last4"),
        },
        "totals": {
            "gross_spend": num(spend),
            "refunds": num(refunds),
            "net_spend": num(net),
            "fees": num(fees),
            "txn_count": counted,
            "avg_txn": num(net / counted) if counted else 0,
            "median_txn": num(median),
            "excluded_payments": num(excluded),
            "excluded_payment_count": excluded_count,
        },
        "categories": category_rows,
        # top_merchants is the display slice; merchants is the full list, which
        # compare_statements.py needs to match a merchant across months.
        "top_merchants": merchant_rows[:10],
        "merchants": merchant_rows,
        "repeat_merchants": repeat,
        "possible_subscriptions": subscriptions,
        "patterns": {
            "largest_transaction": (
                {**largest, "amount": num(largest["amount"])} if largest else None
            ),
            "weekday_spend": num(weekday),
            "weekend_spend": num(weekend),
            "busiest_weekday": busiest_weekday,
            "active_days": len(by_day),
            "mobile_payment": {
                "amount": num(mobile_amount),
                "txn_count": mobile_count,
                "share": float(round(mobile_amount / net, 4)) if net > 0 else 0.0,
            },
        },
        "daily": [
            {"date": d.isoformat(), "amount": num(v)} for d, v in sorted(by_day.items())
        ],
        "foreign_currency": [
            {"currency": cur, "amount": num(v["amount"]), "txn_count": v["txn_count"]}
            for cur, v in sorted(foreign.items())
        ],
        "data_quality": {
            "transactions_read": len(transactions),
            "low_confidence_rows": uncertain,
            "undated_rows": undated,
            "uncategorized_rows": by_category.get("uncategorized", {}).get("txn_count", 0),
            "notes": [],
        },
        "insights": [],
    }

    printed = statement.get("printed_new_charges")
    if printed is not None:
        printed_dec = money(printed, "statement.printed_new_charges")
        diff = net - printed_dec
        tolerance = max(Decimal(1), printed_dec.copy_abs() * Decimal("0.005"))
        summary["reconciliation"] = {
            "printed_new_charges": num(printed_dec),
            "computed_net_spend": num(net),
            "difference": num(diff),
            "matches": bool(diff.copy_abs() <= tolerance),
        }
        if not summary["reconciliation"]["matches"]:
            summary["data_quality"]["notes"].append(
                "Computed total does not match the printed statement total -- "
                "some rows were probably missed or misread."
            )

    if uncertain:
        summary["data_quality"]["notes"].append(
            f"{uncertain} row(s) were hard to read; their amounts may be wrong."
        )

    dupes = [key for key, n in Counter(
        (t.get("date"), merchant_key(t), str(t.get("amount"))) for t in transactions
    ).items() if n > 1]
    if dupes:
        summary["data_quality"]["notes"].append(
            f"{len(dupes)} identical row(s) appear more than once -- genuine repeat "
            "purchases are common, but check for accidental double transcription."
        )

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help='transactions JSON path, or "-" for stdin')
    parser.add_argument("-o", "--out", help="write summary JSON here (default: stdout)")
    args = parser.parse_args()

    try:
        summary = aggregate(load(args.input))
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
