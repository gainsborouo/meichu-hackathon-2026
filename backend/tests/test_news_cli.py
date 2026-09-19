"""news.py command line: --bank/--card selection and merge behaviour. No LLM, no network."""

import asyncio
import importlib.util
import json
import logging
from datetime import date
from pathlib import Path

import pytest

from app.services.campaign_crawler import CardResult, OpenedPage, validate_extraction

NEWS_PATH = Path(__file__).resolve().parents[1] / "skills" / "credit_card_campaigns" / "news.py"
CSV = """發卡銀行,核心主力卡款,主要主打場景 / 特色
中國信託銀行,LINE Pay 聯名卡,行動支付
,中油聯名卡,加油
玉山銀行,Unicard,自選百大特店
台新銀行,@GoGo 卡（黑狗卡）,網購
"""
CTBC = ("中國信託銀行", "LINE Pay 聯名卡")
ESUN = ("玉山銀行", "Unicard")
TODAY = date(2026, 9, 20)
PAGE = OpenedPage(
    "https://www.esunbank.com/zh-tw/unicard",
    "Unicard",
    "Unicard 玉山銀行信用卡。目前國內一般消費享 1% 回饋，無上限。" * 3,
)


@pytest.fixture(scope="module")
def news():
    spec = importlib.util.spec_from_file_location("news_under_test", NEWS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def old_dataset():
    def item(kind, bank, card, title):
        return {"id": f"{kind}-{card}", "bank": bank, "card": card, "title": title}

    return {
        "generated_at": "2026-08-01T00:00:00+00:00",
        "base_benefits": [
            item("benefit", *CTBC, "舊 CTBC 基本回饋"),
            item("benefit", *ESUN, "舊玉山基本回饋"),
        ],
        "campaigns": [
            item("campaign", *CTBC, "舊 CTBC 活動"),
            item("campaign", "台新銀行", "@GoGo 卡（黑狗卡）", "舊台新活動"),
        ],
    }


@pytest.fixture
def env(news, tmp_path, monkeypatch):
    """A temp cards.csv and output JSON, an extractor that must not be built unless asked, and
    a crawl that records which cards it was asked for."""
    csv_path = tmp_path / "cards.csv"
    csv_path.write_text(CSV, "utf-8")
    out = tmp_path / "credit_card_campaigns.json"
    out.write_text(json.dumps(old_dataset(), ensure_ascii=False), "utf-8")
    monkeypatch.setattr(news, "CSV_FILE_PATH", csv_path)
    monkeypatch.setattr(news, "OUTPUT_PATH", out)

    state = {"built": 0, "crawled": []}

    def build_extractor(today):
        state["built"] += 1
        return lambda payload: {}

    async def fake_crawl(bank, card, **kwargs):
        state["crawled"].append((bank, card))
        item = {
            "title": f"{card} 新基本回饋",
            "reward": "國內一般消費享 1% 回饋，無上限",
            "source_url": PAGE.url,
            "evidence": "國內一般消費享 1% 回饋，無上限",
            "validity_evidence": "目前國內一般消費享 1% 回饋，無上限",
            "confidence": 0.9,
        }
        return (
            validate_extraction(
                {"base_benefits": [item]},
                bank="玉山銀行",
                card="Unicard",
                pages=[PAGE],
                today=TODAY,
            )
            if (bank, card) == ESUN
            else CardResult(bank, card, error="no official page could be opened")
        )

    monkeypatch.setattr(news, "build_extractor", build_extractor)
    monkeypatch.setattr(news, "crawl_card", fake_crawl)
    state["out"] = out
    return state


def run(news, *argv):
    return asyncio.run(news.main(list(argv)))


# --- argument handling ------------------------------------------------------------


def test_no_arguments_means_every_card(news):
    args = news.parse_args([])
    assert args.bank is None and args.card is None


def test_both_arguments_are_parsed(news):
    args = news.parse_args(["--bank", "中國信託銀行", "--card", "LINE Pay 聯名卡"])
    assert (args.bank, args.card) == CTBC


@pytest.mark.parametrize("argv", [["--bank", "玉山銀行"], ["--card", "Unicard"]])
def test_bank_and_card_must_come_together(news, argv, capsys):
    with pytest.raises(SystemExit) as exc:
        news.parse_args(argv)
    assert exc.value.code == 2
    assert "must be given together" in capsys.readouterr().err


def test_select_cards_needs_an_exact_bank_and_card_match(news):
    cards = [
        {"bank": "中國信託銀行", "card": "LINE Pay 聯名卡"},
        {"bank": "中國信託銀行", "card": "中油聯名卡"},
        {"bank": "玉山銀行", "card": "Unicard"},
    ]
    assert news.select_cards(cards, None, None) == cards
    assert news.select_cards(cards, *CTBC) == [cards[0]]
    assert news.select_cards(cards, "中國信託銀行", "LINE Pay") == []  # partial name
    assert news.select_cards(cards, "中國信託", "LINE Pay 聯名卡") == []  # partial bank
    assert news.select_cards(cards, "玉山銀行", "unicard") == []  # case matters
    assert news.select_cards(cards, "玉山銀行", "LINE Pay 聯名卡") == []  # wrong pairing


# --- running ---------------------------------------------------------------------------


def test_unknown_card_exits_2_lists_available_cards_and_touches_nothing(news, env, capsys):
    before = env["out"].read_text("utf-8")
    code = run(news, "--bank", "玉山銀行", "--card", "不存在的卡")
    err = capsys.readouterr().err

    assert code == 2
    for line in (
        "中國信託銀行 / LINE Pay 聯名卡",
        "中國信託銀行 / 中油聯名卡",
        "玉山銀行 / Unicard",
        "台新銀行 / @GoGo 卡（黑狗卡）",
    ):
        assert line in err, line
    assert env["built"] == 0, "no LLM client should be built for a bad selection"
    assert env["crawled"] == []
    assert env["out"].read_text("utf-8") == before


def test_selected_card_is_the_only_one_crawled_and_others_are_preserved(news, env, caplog):
    with caplog.at_level(logging.INFO):
        code = run(news, "--bank", ESUN[0], "--card", ESUN[1])

    assert code == 0
    assert env["crawled"] == [ESUN]
    assert "Crawling 1 selected card" in caplog.text
    assert "Crawling 4 cards" not in caplog.text

    out = json.loads(env["out"].read_text("utf-8"))
    benefit_ids = {b["id"] for b in out["base_benefits"]}
    campaign_ids = {c["id"] for c in out["campaigns"]}
    # Not crawled this run: everything survives, including the other cards' rows.
    assert "benefit-LINE Pay 聯名卡" in benefit_ids
    assert {"campaign-LINE Pay 聯名卡", "campaign-@GoGo 卡（黑狗卡）"} <= campaign_ids
    # The selected card was refreshed: its stale row is gone and the new one is present.
    assert "benefit-Unicard" not in benefit_ids
    assert any(b["title"] == "Unicard 新基本回饋" for b in out["base_benefits"])


def test_selected_card_that_fails_leaves_the_file_unchanged_and_exits_1(news, env):
    before = env["out"].read_text("utf-8")
    code = run(news, "--bank", CTBC[0], "--card", CTBC[1])  # fake crawl fails for CTBC
    assert code == 1
    assert env["crawled"] == [CTBC]
    assert env["out"].read_text("utf-8") == before


def test_without_arguments_every_card_is_crawled(news, env, caplog, monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(news.asyncio, "sleep", no_sleep)
    with caplog.at_level(logging.INFO):
        code = run(news)

    assert code == 0  # Esun succeeds, the other three fail but the run is still non-empty
    assert env["crawled"] == [
        CTBC,
        ("中國信託銀行", "中油聯名卡"),
        ESUN,
        ("台新銀行", "@GoGo 卡（黑狗卡）"),
    ]
    assert "Crawling 4 cards" in caplog.text
    assert "selected card" not in caplog.text
    out = json.loads(env["out"].read_text("utf-8"))
    assert "campaign-LINE Pay 聯名卡" in {c["id"] for c in out["campaigns"]}  # failed card kept


def test_single_card_run_does_not_sleep(news, env, monkeypatch):
    slept = []

    async def record_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(news.asyncio, "sleep", record_sleep)
    run(news, "--bank", ESUN[0], "--card", ESUN[1])
    assert slept == []
