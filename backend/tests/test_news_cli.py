"""news.py command line: --card-key selection and merge behaviour. No LLM, no network."""

import asyncio
import importlib.util
import json
import logging
from datetime import date
from pathlib import Path

import pytest

from app.services.campaign_crawler import (
    CardResult,
    CrawlTarget,
    OpenedPage,
    crawler_targets,
    validate_extraction,
)

NEWS_PATH = Path(__file__).resolve().parents[1] / "skills" / "credit_card_campaigns" / "news.py"
TODAY = date(2026, 9, 20)
CTBC = CrawlTarget("ctbc-linepay", "中國信託銀行", "LINE Pay 聯名卡")
ESUN = CrawlTarget("esun-unicard", "玉山銀行", "Unicard")
MOMO = CrawlTarget("fubon-momo", "台北富邦銀行", "momo 卡")
TARGETS = [CTBC, ESUN, MOMO]
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
    def item(kind, key, title):
        return {"id": f"{kind}-{key}", "card_key": key, "title": title}

    return {
        "generated_at": "2026-08-01T00:00:00+00:00",
        "base_benefits": [
            item("benefit", CTBC.card_key, "舊 CTBC 基本回饋"),
            item("benefit", ESUN.card_key, "舊玉山基本回饋"),
        ],
        "campaigns": [
            item("campaign", CTBC.card_key, "舊 CTBC 活動"),
            item("campaign", MOMO.card_key, "舊 momo 活動"),
        ],
    }


@pytest.fixture
def env(news, tmp_path, monkeypatch):
    """A temp output JSON, targets from a fake catalog read, and a crawl that records which
    cards it was asked for. The LLM extractor must not be built unless a crawl really starts."""
    out = tmp_path / "credit_card_campaigns.json"
    out.write_text(json.dumps(old_dataset(), ensure_ascii=False), "utf-8")
    monkeypatch.setattr(news, "OUTPUT_PATH", out)

    state = {"built": 0, "crawled": [], "out": out}

    async def load_targets():
        return list(TARGETS)

    def build_extractor(today):
        state["built"] += 1
        return lambda payload: {}

    async def fake_crawl(target, **kwargs):
        state["crawled"].append(target.card_key)
        if target.card_key != ESUN.card_key:  # only Esun "succeeds" in these tests
            return CardResult(target.bank, target.card, target.card_key, error="no official page")
        item = {
            "title": "Unicard 新基本回饋",
            "reward": "國內一般消費享 1% 回饋，無上限",
            "source_url": PAGE.url,
            "evidence": "國內一般消費享 1% 回饋，無上限",
            "validity_evidence": "目前國內一般消費享 1% 回饋，無上限",
            "confidence": 0.9,
        }
        return validate_extraction(
            {"base_benefits": [item]},
            card_key=target.card_key,
            bank=target.bank,
            card=target.card,
            pages=[PAGE],
            today=TODAY,
        )

    async def no_sleep(_):
        return None

    monkeypatch.setattr(news, "load_targets", load_targets)
    monkeypatch.setattr(news, "build_extractor", build_extractor)
    monkeypatch.setattr(news, "crawl_card", fake_crawl)
    monkeypatch.setattr(news.asyncio, "sleep", no_sleep)
    return state


def run(news, *argv):
    return asyncio.run(news.main(list(argv)))


# --- argument handling ------------------------------------------------------------


def test_no_arguments_means_every_enabled_card(news):
    assert news.parse_args([]).card_key is None


def test_card_key_is_parsed(news):
    assert news.parse_args(["--card-key", "ctbc-linepay"]).card_key == "ctbc-linepay"


@pytest.mark.parametrize("argv", [["--bank", "玉山銀行"], ["--card", "Unicard"]])
def test_bank_and_card_are_no_longer_accepted_as_identity(news, argv, capsys):
    with pytest.raises(SystemExit) as exc:
        news.parse_args(argv)
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_select_targets_matches_the_exact_key(news):
    assert news.select_targets(TARGETS, None) == TARGETS
    assert news.select_targets(TARGETS, "ctbc-linepay") == [CTBC]
    assert news.select_targets(TARGETS, "ctbc") == []  # no partial match
    assert news.select_targets(TARGETS, "CTBC-LINEPAY") == []  # case matters
    assert news.select_targets(TARGETS, "LINE Pay 聯名卡") == []  # a name is not a key


# --- where the cards come from ---------------------------------------------------------


async def test_targets_come_from_crawler_enabled_catalog_cards(session, catalog):
    targets = await crawler_targets(session)
    assert {t.card_key for t in targets} == {
        "ctbc-linepay",
        "esun-unicard",
        "esun-kumamon",
        "esun-ubear",
        "fubon-costco",
        "fubon-j",
        "fubon-momo",
        "taishin-richart",
        "taishin-pxmart",
        "esun-pi-card",
    }
    linepay = next(t for t in targets if t.card_key == "ctbc-linepay")
    assert (linepay.bank, linepay.card) == ("中國信託銀行", "LINE Pay 聯名卡")  # search wording


def test_without_a_database_the_identity_file_supplies_the_same_targets(news, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config, "get_settings", lambda: type("S", (), {"database_url": None})())
    targets = asyncio.run(news.load_targets())
    assert "ctbc-linepay" in {t.card_key for t in targets} and len(targets) == 10


# --- running ---------------------------------------------------------------------------


def test_unknown_card_key_exits_2_lists_keys_and_touches_nothing(news, env, capsys):
    before = env["out"].read_text("utf-8")
    code = run(news, "--card-key", "no-such-card")
    err = capsys.readouterr().err

    assert code == 2
    for key in ("ctbc-linepay", "esun-unicard", "fubon-momo"):
        assert key in err
    assert env["built"] == 0, "no LLM client should be built for a bad selection"
    assert env["crawled"] == []
    assert env["out"].read_text("utf-8") == before


def test_selected_card_is_the_only_one_crawled_and_others_are_preserved(news, env, caplog):
    with caplog.at_level(logging.INFO):
        code = run(news, "--card-key", ESUN.card_key)

    assert code == 0
    assert env["crawled"] == [ESUN.card_key]
    assert "Crawling 1 selected card" in caplog.text
    assert "Crawling 3 cards" not in caplog.text

    out = json.loads(env["out"].read_text("utf-8"))
    benefits = {b["id"] for b in out["base_benefits"]}
    campaigns = {c["id"] for c in out["campaigns"]}
    # Not crawled this run: CTBC and momo keep everything.
    assert "benefit-ctbc-linepay" in benefits
    assert {"campaign-ctbc-linepay", "campaign-fubon-momo"} <= campaigns
    # The selected card was refreshed: stale row gone, new row present, and it has a card_key.
    assert "benefit-esun-unicard" not in benefits
    new = [b for b in out["base_benefits"] if b["title"] == "Unicard 新基本回饋"]
    assert len(new) == 1 and new[0]["card_key"] == ESUN.card_key
    assert new[0]["source_card_name"] == "Unicard" and "card" not in new[0]


def test_selected_card_that_fails_leaves_the_file_unchanged_and_exits_1(news, env):
    before = env["out"].read_text("utf-8")
    assert run(news, "--card-key", CTBC.card_key) == 1  # the fake crawl fails for CTBC
    assert env["crawled"] == [CTBC.card_key]
    assert env["out"].read_text("utf-8") == before


def test_without_arguments_every_enabled_card_is_crawled(news, env, caplog):
    with caplog.at_level(logging.INFO):
        code = run(news)

    assert code == 0  # Esun succeeds; the others fail but keep their old data
    assert env["crawled"] == [CTBC.card_key, ESUN.card_key, MOMO.card_key]
    assert "Crawling 3 cards" in caplog.text and "selected card" not in caplog.text
    out = json.loads(env["out"].read_text("utf-8"))
    assert "campaign-ctbc-linepay" in {c["id"] for c in out["campaigns"]}  # failed card kept


def test_single_card_run_does_not_sleep(news, env, monkeypatch):
    slept = []

    async def record_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(news.asyncio, "sleep", record_sleep)
    run(news, "--card-key", ESUN.card_key)
    assert slept == []
