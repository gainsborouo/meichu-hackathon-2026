#!/usr/bin/env python3
"""Render facts as a zh-TW report, without a model.

Two jobs. It is the skeleton the written summary is built on -- the table is
tedious to type and easy to get subtly wrong, so it is generated rather than
written. And it is the fallback: when no model is reachable, this still produces
something truthful and useful, which matters because the report is refreshed on
every analysis write and a write must not fail just because a gateway is down.

What it deliberately does not do is interpret. "支出增加 28%" is arithmetic;
"因為八月買了除濕機" is judgement, and that is the part worth a model.

Usage:
    python render_report.py facts.json
    python aggregate_analyses.py rows.json | python render_report.py -
"""

from __future__ import annotations

import argparse
import json
import sys

CATEGORY_ZH = {
    "dining": "餐飲",
    "convenience": "便利商店",
    "groceries": "超市",
    "transport": "交通",
    "fuel": "加油",
    "online_shopping": "網購",
    "shopping_retail": "零售",
    "entertainment": "娛樂",
    "software_services": "訂閱服務",
    "utilities_telecom": "電信與帳單",
    "healthcare": "醫療",
    "beauty_personal": "美妝個人",
    "travel_lodging": "旅遊住宿",
    "education": "教育",
    "home": "居家",
    "insurance": "保險",
    "financial_fees": "手續費與年費",
    "pets": "寵物",
    "charity": "捐款",
    "other": "其他",
    "uncategorized": "未分類",
}


def zh(name: str | None) -> str:
    return CATEGORY_ZH.get(name or "", name or "—")


def money(currency: str, amount) -> str:
    return f"{currency} {amount:,.0f}" if isinstance(amount, (int, float)) else "—"


def render(facts: dict) -> str:
    currency = facts.get("currency") or "TWD"
    months = facts.get("months") or []
    totals = facts.get("totals") or {}
    lines = ["## 近三個月消費總結", ""]

    if not months:
        return "## 近三個月消費總結\n\n目前沒有可用的消費分析。"

    span = f"{months[0]['month']} 至 {months[-1]['month']}"
    head = f"{span} 共 {money(currency, totals.get('net_spend'))}"
    if totals.get("mean_monthly") is not None:
        head += f"，平均每月 {money(currency, totals['mean_monthly'])}"
    pct = totals.get("change_pct")
    if pct is not None and len(months) > 1:
        direction = "增加" if pct >= 0 else "減少"
        head += f"；最後一個月較第一個月{direction} {abs(pct) * 100:.0f}%"
    lines += [head + "。", ""]

    lines += ["| 月份 | 支出 | 最大類別 |", "|---|---|---|"]
    for row in months:
        top = zh(row.get("top_category"))
        if row.get("top_category_amount") is not None:
            top += f"（{money(currency, row['top_category_amount'])}）"
        lines.append(f"| {row['month']} | {money(currency, row['net_spend'])} | {top} |")
    lines.append("")

    subs = facts.get("subscriptions") or []
    every_month = [s for s in subs if s["months_present"] >= len(months) > 1]
    if every_month:
        parts = ", ".join(
            f"{s['merchant']}（{s['months_present']} 個月共 {money(currency, s['total'])}）"
            for s in every_month[:3]
        )
        lines.append(f"- 每月固定扣款：{parts}")

    biggest = facts.get("largest_transaction")
    if biggest:
        lines.append(
            f"- 最大單筆：{biggest.get('merchant') or '—'} "
            f"{money(currency, biggest.get('amount'))}"
            f"（{biggest.get('month')}，{biggest.get('card')}）"
        )

    trend = facts.get("category_trend") or []
    movers = [c for c in trend if c.get("change")]
    if movers and len(months) > 1:
        up = max(movers, key=lambda c: c["change"])
        down = min(movers, key=lambda c: c["change"])
        if up["change"] > 0:
            lines.append(f"- 增加最多：{zh(up['name'])} +{money(currency, up['change'])}")
        if down["change"] < 0:
            lines.append(f"- 減少最多：{zh(down['name'])} {money(currency, down['change'])}")

    fees = totals.get("fees")
    if fees:
        lines.append(f"- 期間手續費與年費共 {money(currency, fees)}")

    for note in (facts.get("data_quality") or {}).get("notes") or []:
        lines.append(f"- 註：{note}")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help='facts JSON path, or "-" for stdin')
    args = parser.parse_args()
    raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    print(render(json.loads(raw)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
