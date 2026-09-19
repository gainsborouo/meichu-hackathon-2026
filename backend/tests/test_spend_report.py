"""Merging three months of per-card analyses into one summary."""

import datetime as dt
import json
from pathlib import Path

import pytest

from app.repositories import analyses as analyses_repo
from app.repositories import cards as cards_repo
from app.services import spend_report
from app.services.spend_report import refresh_latest_spend_report
from app.services.spend_report_agent import (
    InputError,
    SpendReportAgentError,
    build_facts,
    render_deterministic,
)
from app.services.users import upsert_user

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "spend-report-summary"
    / "evals"
    / "fixture_rows.json"
)
ROWS = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]


# --- aggregation --------------------------------------------------------


def test_months_sum_across_every_card() -> None:
    """August has two cards: 5,728 on the 玉山 card plus 1,200 on the 中信 one."""
    facts = build_facts(ROWS)
    by_month = {m["month"]: m["net_spend"] for m in facts["months"]}
    assert by_month == {"2026-06": 5420, "2026-07": 3360, "2026-08": 6928}
    assert facts["totals"]["net_spend"] == 15708
    assert facts["totals"]["mean_monthly"] == 5236


def test_month_over_month_change_is_first_to_last() -> None:
    facts = build_facts(ROWS)
    assert facts["totals"]["change_first_to_last"] == 1508
    assert round(facts["totals"]["change_pct"], 3) == 0.278


def test_subscription_present_every_month_is_flagged() -> None:
    facts = build_facts(ROWS)
    [sub] = facts["subscriptions"]
    assert sub["merchant"] == "ANTHROPIC* CLAUDE SUB"
    assert sub["months_present"] == 3 and sub["total"] == 1924


def test_recurring_merchants_need_two_months() -> None:
    facts = build_facts(ROWS)
    names = {m["merchant"]: m for m in facts["recurring_merchants"]}
    assert names["ANTHROPIC* CLAUDE SUB"]["every_month"] is True
    assert names["SUICA MOBILE PAYMENT"]["months_present"] == 2
    assert "樂天市場" not in names, "a single-month merchant is not recurring"


def test_category_trend_tracks_what_started_and_stopped() -> None:
    facts = build_facts(ROWS)
    trend = {c["name"]: c for c in facts["category_trend"]}
    assert trend["transport"]["change"] == -2965  # the trip ended
    assert trend["online_shopping"]["change"] == 2900  # 樂天 appeared in August
    assert trend["transport"]["by_month"] == {"2026-06": 2965, "2026-07": 990, "2026-08": 0}


def test_largest_transaction_is_found_across_cards_and_months() -> None:
    biggest = build_facts(ROWS)["largest_transaction"]
    assert biggest["merchant"] == "樂天市場" and biggest["amount"] == 2900
    assert biggest["month"] == "2026-08" and "Pi" in biggest["card"]


def test_rows_without_structured_data_are_counted_not_dropped() -> None:
    rows = ROWS + [
        {"analysis_month": "2026-08-01", "card": {"bank_name": "B", "name": "C"},
         "report": "手寫報告", "analysis_data": None}
    ]
    facts = build_facts(rows)
    assert facts["data_quality"]["analyses_read"] == 5
    assert any("沒有結構化資料" in n for n in facts["data_quality"]["notes"])
    # It must not silently change the arithmetic.
    assert facts["totals"]["net_spend"] == 15708


def test_thin_history_is_flagged_rather_than_padded() -> None:
    facts = build_facts(ROWS[:1])
    assert facts["month_count"] == 1
    assert any("只有 1 個月" in n for n in facts["data_quality"]["notes"])


def test_empty_input_is_an_error_not_a_blank_report() -> None:
    with pytest.raises(InputError):
        build_facts([])


# --- rendering ----------------------------------------------------------


def test_rendered_report_carries_the_real_numbers() -> None:
    report = render_deterministic(build_facts(ROWS))
    assert "## 近三個月消費總結" in report
    for figure in ("15,708", "5,420", "3,360", "6,928", "5,236"):
        assert figure in report, f"{figure} missing from the report"
    assert "| 2026-08 | TWD 6,928 |" in report
    assert "ANTHROPIC* CLAUDE SUB" in report
    assert "交通" in report and "網購" in report  # categories rendered in zh-TW


def test_rendering_survives_having_no_months() -> None:
    assert "沒有可用" in render_deterministic({"months": [], "totals": {}})


# --- the service --------------------------------------------------------


async def _seed(session, months=(6, 7, 8)):
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    card = await cards_repo.get_or_create_card(session, bank_name="玉山銀行", name="Pi 信用卡")
    uc, _ = await cards_repo.add_user_card(session, user.id, card.id)
    for month in months:
        await analyses_repo.upsert_analysis(
            session,
            user_card_id=uc.id,
            analysis_month=dt.date(2026, month, 1),
            report=f"月報 {month}",
            analysis_data={"summary": {
                "currency": "TWD",
                "totals": {"net_spend": month * 1000, "txn_count": 5, "fees": 10},
                "categories": [{"name": "dining", "amount": month * 1000}],
                "merchants": [{"merchant": "foodpanda", "amount": month * 1000, "txn_count": 5}],
            }},
        )
    return user


async def test_report_is_a_merged_summary_not_concatenated_text(session) -> None:
    user = await _seed(session)
    report = await refresh_latest_spend_report(session, user.id)
    assert "## 近三個月消費總結" in report
    assert "| 2026-08 |" in report and "| 2026-06 |" in report
    assert "月報 8" not in report, "raw per-card text must not be pasted in"
    assert user.latest_spend_report == report


async def test_only_the_newest_three_months_are_included(session) -> None:
    user = await _seed(session, months=(5, 6, 7, 8))
    report = await refresh_latest_spend_report(session, user.id)
    assert "2026-05" not in report
    for month in ("2026-06", "2026-07", "2026-08"):
        assert month in report


async def test_a_failing_model_still_leaves_a_truthful_report(session) -> None:
    """The refresh runs on every write, so a dead gateway must not break it."""
    user = await _seed(session)
    report = await refresh_latest_spend_report(session, user.id)  # agent refuses via conftest
    assert report and "8,000" in report
    assert user.latest_spend_report == report


async def test_model_output_is_used_when_the_agent_succeeds(session, monkeypatch) -> None:
    user = await _seed(session)

    async def _write(facts, **kwargs):
        assert facts["totals"]["net_spend"] == 21000, "the model is given real facts"
        return "## 近三個月消費總結\n\n模型寫的洞察。\n\n| 月份 | 支出 |\n|---|---|\n"

    monkeypatch.setattr(spend_report, "write_report", _write)
    report = await refresh_latest_spend_report(session, user.id)
    assert "模型寫的洞察" in report


async def test_use_agent_false_skips_the_model_entirely(session, monkeypatch) -> None:
    user = await _seed(session)

    async def _explode(*args, **kwargs):
        raise AssertionError("the model must not be called when use_agent=False")

    monkeypatch.setattr(spend_report, "write_report", _explode)
    assert await refresh_latest_spend_report(session, user.id, use_agent=False)


async def test_a_user_with_no_analyses_gets_null_not_an_empty_string(session) -> None:
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    assert await refresh_latest_spend_report(session, user.id) is None
    assert user.latest_spend_report is None


def test_agent_error_is_raised_when_the_gateway_is_unconfigured(monkeypatch) -> None:
    import asyncio

    from app.core.config import get_settings
    from app.services import spend_report_agent

    monkeypatch.setattr(get_settings(), "llm_base_url", None)
    with pytest.raises(SpendReportAgentError, match="are not set"):
        asyncio.run(spend_report_agent.write_report(build_facts(ROWS)))
