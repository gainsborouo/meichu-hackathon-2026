"""Crawler rules: what may reach credit_card_campaigns.json. No LLM, no network."""

import json
import logging
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import CardBenefit, Sale
from app.services import campaign_crawler as crawler
from app.services.campaign_crawler import (
    CardResult,
    CrawlTarget,
    OpenedPage,
    build_queries,
    card_aliases,
    crawl_card,
    finalize,
    gather_official_pages,
    validate_extraction,
)
from app.services.official_pages import PageResult
from app.services.sales_import import import_dataset, load_dataset

TODAY = date(2026, 9, 20)
NOW = datetime(2026, 9, 20, 3, tzinfo=UTC)
BANK, CARD = "玉山銀行", "Unicard"
KEY = "esun-unicard"
TARGET = CrawlTarget(KEY, BANK, CARD)
PAGE_URL = "https://www.esunbank.com/zh-tw/credit-cards/unicard"
PROMO_URL = "https://www.esunbank.com/zh-tw/promo/online"
BASE_QUOTE = "國內一般消費享 1% 回饋，無上限"
VALID_QUOTE = "目前國內一般消費享 1% 回饋，無上限"
PAGE_TEXT = (
    f"Unicard 玉山銀行信用卡。{VALID_QUOTE}。"
    "指定網購加碼活動：活動期間 2026/09/01 至 2026/12/31，網購享 3% 回饋。"
) * 3
PAGES = [OpenedPage(PAGE_URL, "Unicard", PAGE_TEXT), OpenedPage(PROMO_URL, "活動", PAGE_TEXT)]


def base(**over):
    return {
        "title": "國內一般消費",
        "reward": "國內一般消費享 1% 回饋，無上限",
        "conditions": None,
        "effective_start": None,
        "effective_end": None,
        "source_url": PAGE_URL,
        "evidence": BASE_QUOTE,
        "validity_evidence": VALID_QUOTE,
        "confidence": 0.9,
        **over,
    }


def camp(**over):
    return {
        "title": "指定網購加碼活動",
        "reward": "網購享 3% 回饋",
        "campaign_start": "2026-09-01",
        "campaign_end": "2026-12-31",
        "register_url": None,
        "recurrence": "once",
        "quota_limited": False,
        "source_url": PROMO_URL,
        "evidence": "網購享 3% 回饋",
        "confidence": 0.9,
        **over,
    }


def check(raw, pages=PAGES, **kw):
    return validate_extraction(
        raw, card_key=KEY, bank=BANK, card=CARD, pages=pages, today=TODAY, **kw
    )


def c_key_ok(item):
    return item["source_bank_name"] == BANK and item["source_card_name"] == CARD


def reasons(result):
    return " | ".join(result.dropped)


# --- identity and queries ----------------------------------------------------


def test_card_aliases_cover_name_variants():
    assert "LINE Pay" in card_aliases("LINE Pay 聯名卡")
    assert {"玫瑰卡", "太陽卡"} <= set(card_aliases("玫瑰卡 / 太陽卡"))
    assert {"@GoGo 卡", "黑狗卡"} <= set(card_aliases("@GoGo 卡（黑狗卡）"))
    assert card_aliases("Unicard") == ["Unicard"]
    assert all(len(a.strip()) >= 2 for a in card_aliases("U Bear 信用卡"))


def test_queries_are_structured_official_site_year_and_nothing_else():
    queries = build_queries("玉山銀行", "Unicard", 2026)
    assert queries and all(q.startswith("site:") for q in queries)
    assert all("Unicard" in q for q in queries)
    assert any("2026" in q for q in queries)
    assert any("2026" not in q for q in queries)  # current product pages often omit a year
    assert {q.split()[0] for q in queries} <= {"site:esunbank.com", "site:esunbank.com.tw"}
    assert all("@" not in q and "$" not in q for q in queries)
    assert build_queries("不支援銀行", "X", 2026) == []


# --- gathering opened pages --------------------------------------------------


def ok(url, text=PAGE_TEXT, final=None):
    return PageResult(True, final or url, 200, "t", text)


def test_only_official_urls_are_fetched_and_final_urls_are_kept():
    fetched: list[str] = []
    final = "https://www.esunbank.com/zh-tw/new-home"

    def search(query):
        return [
            "https://www.ptt.cc/bbs/creditcard/M.1.html",
            "https://www.esunbank.com.evil.example/promo",
            "http://www.esunbank.com/insecure",
            "https://www.esunbank.com/old-promo",
            "https://www.esunbank.com/gone",
        ]

    def fetch(url):
        fetched.append(url)
        if url.endswith("old-promo"):
            return ok(url, final=final)
        return PageResult(False, url, 404, error="HTTP 404")

    pages = gather_official_pages(BANK, CARD, year=2026, search=search, fetch=fetch)
    assert fetched == ["https://www.esunbank.com/old-promo", "https://www.esunbank.com/gone"]
    assert [p.url for p in pages] == [final]  # the redirect input is not an opened page


def test_gather_continues_after_one_search_query_is_rate_limited():
    calls = []

    def search(query):
        calls.append(query)
        if len(calls) == 1:
            raise RuntimeError("No results found")
        return [PAGE_URL]

    pages = gather_official_pages(BANK, CARD, year=2026, search=search, fetch=ok)
    assert len(calls) > 1 and [page.url for page in pages] == [PAGE_URL]


def test_gather_drops_pages_whose_final_url_left_the_bank():
    def fetch(url):
        return ok(url, final="https://www.ctbcbank.com/somewhere")

    pages = gather_official_pages(
        BANK, CARD, year=2026, search=lambda q: ["https://www.esunbank.com/a"], fetch=fetch
    )
    assert pages == []


# --- validation ----------------------------------------------------------------


def test_valid_items_pass_and_source_url_is_the_opened_page():
    result = check({"base_benefits": [base()], "campaigns": [camp()]}, now=NOW)
    assert result.dropped == [] and result.error is None
    [b], [c] = result.base_benefits, result.campaigns
    assert b["source_url"] == PAGE_URL and b["effective_end"] is None
    assert b["card_key"] == KEY and c_key_ok(b)  # identity is the catalog key, not the model's
    assert "bank" not in b and "card" not in b
    assert c["campaign_start"] == "2026-09-01" and c["campaign_end"] == "2026-12-31"
    assert c["source_url"] == PROMO_URL and c["id"].startswith("campaign-")
    assert "official_verified_at" not in b and "official_verified_at" not in c


def test_expired_campaigns_are_never_written():
    end_today = TODAY.strftime("%Y/%m/%d")
    page = OpenedPage(
        PROMO_URL,
        "活動",
        f"Unicard 活動期間 2026/09/01 至 {end_today}，網購享 3% 回饋。"
        "Unicard 活動期間 2025/01/01 至 2025/12/31，網購享 3% 回饋。" * 2,
    )
    result = check(
        {
            "campaigns": [
                camp(
                    campaign_start="2025-01-01",
                    campaign_end="2025-12-31",
                    evidence="活動期間 2025/01/01 至 2025/12/31，網購享 3% 回饋",
                ),
                camp(title="昨天結束的活動", campaign_end=(TODAY - timedelta(days=1)).isoformat()),
                camp(
                    title="今天結束的活動仍有效",
                    campaign_end=TODAY.isoformat(),
                    evidence=f"活動期間 2026/09/01 至 {end_today}，網購享 3% 回饋",
                ),
            ]
        },
        pages=[page],
    )
    assert [c["title"] for c in result.campaigns] == ["今天結束的活動仍有效"]
    assert "already ended 2025-12-31" in reasons(result)


def test_campaigns_need_real_dates():
    result = check(
        {
            "campaigns": [
                camp(campaign_start=None),
                camp(title="b", campaign_end=None),
                camp(title="c", campaign_start="sometime", campaign_end="later"),
                camp(title="d", campaign_start="2026-12-31", campaign_end="2026-09-01"),
            ]
        }
    )
    assert result.campaigns == []
    assert reasons(result).count("campaign") >= 4


def test_future_campaigns_are_kept_for_wait_suggestions():
    page = OpenedPage(
        PROMO_URL,
        "活動",
        "Unicard 秋季加碼：活動期間 2026/10/05 至 2026/11/30，網購享 3% 回饋。" * 2,
    )
    result = check(
        {
            "campaigns": [
                camp(
                    campaign_start="2026-10-05",
                    campaign_end="2026-11-30",
                    evidence="活動期間 2026/10/05 至 2026/11/30，網購享 3% 回饋",
                )
            ]
        },
        pages=[page],
    )
    assert len(result.campaigns) == 1


@pytest.mark.parametrize(
    "cited",
    [
        "https://www.esunbank.com/zh-tw/made-up-by-the-model",  # never opened
        "https://www.ptt.cc/bbs/creditcard/M.1.html",  # not official
        "http://www.esunbank.com/zh-tw/credit-cards/unicard",  # not https
        "https://www.esunbank.com.evil.example/zh-tw/credit-cards/unicard",  # look-alike
        "https://www.esunbank.com/old-promo",  # the redirect INPUT; only the final was opened
        "",
        None,
    ],
)
def test_source_url_must_be_an_official_page_the_backend_opened(cited):
    result = check(
        {"base_benefits": [base(source_url=cited)], "campaigns": [camp(source_url=cited)]}
    )
    assert result.base_benefits == [] and result.campaigns == []
    assert reasons(result).count("not an official page the backend opened") == 2


def test_unreadable_pages_cannot_be_sources():
    # A page that failed to open is never in `pages`, so citing it is the same as inventing it.
    result = check({"base_benefits": [base(source_url="https://www.esunbank.com/404")]})
    assert result.base_benefits == []


def test_trailing_slash_and_host_case_still_match_the_opened_final_url():
    slash = check({"base_benefits": [base(source_url=PAGE_URL + "/")]})
    assert slash.base_benefits and slash.base_benefits[0]["source_url"] == PAGE_URL

    host_case = check(
        {"base_benefits": [base(source_url=PAGE_URL.replace("www.esun", "WWW.Esun"))]}
    )
    assert host_case.base_benefits and host_case.base_benefits[0]["source_url"] == PAGE_URL

    other_path = check({"base_benefits": [base(source_url=PAGE_URL + "/other")]})
    assert other_path.base_benefits == []  # a different page was never opened


def test_rewards_must_have_a_parseable_rate():
    result = check(
        {
            "base_benefits": [base(reward="享有多項優惠")],
            "campaigns": [camp(reward="免費停車")],
        }
    )
    assert result.base_benefits == [] and result.campaigns == []
    assert reasons(result).count("no parseable rate") == 2


def test_base_benefit_without_end_date_is_valid_but_dates_are_enforced():
    result = check(
        {
            "base_benefits": [
                base(title="無結束日"),
                base(title="已結束", effective_end="2026-01-31"),
                base(title="尚未生效", effective_start="2026-12-01"),
                base(title="今天起", effective_start=TODAY.isoformat()),
                base(title="壞日期", effective_end="soon"),
            ]
        }
    )
    assert {b["title"] for b in result.base_benefits} == {"無結束日", "今天起"}
    assert "already ended" in reasons(result) and "not effective until" in reasons(result)
    assert "unparseable effective_end" in reasons(result)


def test_base_benefit_page_must_mention_the_card_and_contain_the_evidence():
    other = [
        OpenedPage(
            PAGE_URL, "銀行首頁", "玉山銀行 提供多種服務，國內一般消費享 1% 回饋，無上限。" * 3
        )
    ]
    assert check({"base_benefits": [base()]}, pages=other).base_benefits == []
    assert "does not mention the card" in reasons(check({"base_benefits": [base()]}, pages=other))

    invented = check({"base_benefits": [base(evidence="一般消費 9% 回饋")]})
    assert invented.base_benefits == [] and "evidence is not on the opened page" in reasons(
        invented
    )
    assert check({"base_benefits": [base(evidence="")]}).base_benefits == []


def test_card_alias_is_enough_to_recognise_the_card():
    url = "https://www.ctbcbank.com/linepay"
    text = f"LINE Pay 是綁定方式。{VALID_QUOTE}。" * 3
    result = validate_extraction(
        {"base_benefits": [base(source_url=url)]},
        card_key="ctbc-linepay",
        bank="中國信託銀行",
        card="LINE Pay 聯名卡",  # the page says "LINE Pay", not the full card name
        pages=[OpenedPage(url, "t", text)],
        today=TODAY,
    )
    assert len(result.base_benefits) == 1


def test_campaign_needs_the_card_or_a_recognisable_title_on_the_opened_page():
    generic = [
        OpenedPage(
            PROMO_URL,
            "活動總覽",
            "雙十一購物節 滿額回饋活動說明，活動期間 2026/09/01 至 2026/12/31，網購享 3% 回饋。"
            * 3,
        )
    ]
    named = check({"campaigns": [camp(title="雙十一購物節")]}, pages=generic)
    assert len(named.campaigns) == 1  # title is on the page even though the card name is not

    neither = check({"campaigns": [camp(title="完全無關的活動名稱")]}, pages=generic)
    assert neither.campaigns == []
    assert "mentions neither the card nor the campaign" in reasons(neither)


def test_campaign_register_url_must_be_official_or_the_campaign_is_dropped():
    good = check({"campaigns": [camp(register_url="https://www.esunbank.com/register")]})
    assert good.campaigns[0]["register_url"] == "https://www.esunbank.com/register"
    bad = check({"campaigns": [camp(register_url="https://evil.example/register")]})
    assert bad.campaigns == [] and "register_url" in reasons(bad)


def test_malformed_model_output_is_contained():
    assert check("nope").error
    assert check({"base_benefits": ["x", 3], "campaigns": [None]}).dropped


# --- end to end with fakes ------------------------------------------------------


async def test_crawl_card_end_to_end_with_fakes():
    async def extract(payload):
        assert payload["today"] == "2026-09-20" and payload["bank"] == BANK
        assert {p["url"] for p in payload["pages"]} == {PAGE_URL, PROMO_URL}
        return {
            "base_benefits": [base()],
            "campaigns": [
                camp(),
                camp(title="舊活動", campaign_start="2024-01-01", campaign_end="2025-01-01"),
            ],
        }

    result = await crawl_card(
        TARGET,
        today=TODAY,
        search=lambda q: [PAGE_URL, PROMO_URL, "https://www.ptt.cc/x"],
        fetch=lambda url: ok(url),
        extract=extract,
        now=NOW,
    )
    assert len(result.base_benefits) == 1 and len(result.campaigns) == 1
    assert "already ended" in reasons(result)


async def test_crawl_card_reports_failures_instead_of_raising():
    no_pages = await crawl_card(
        TARGET, today=TODAY, search=lambda q: [], fetch=lambda u: ok(u), extract=lambda p: {}
    )
    assert no_pages.error == "no official page could be opened"

    def boom(query):
        raise RuntimeError("rate limited")

    searched = await crawl_card(TARGET, today=TODAY, search=boom, extract=lambda p: {})
    assert searched.error == "no official page could be opened"

    async def bad_model(payload):
        raise ValueError("bad json")

    extracted = await crawl_card(
        TARGET,
        today=TODAY,
        search=lambda q: [PAGE_URL],
        fetch=lambda u: ok(u),
        extract=bad_model,
    )
    assert "extraction failed" in extracted.error


# --- output file -------------------------------------------------------------------


def good_result():
    return check({"base_benefits": [base()], "campaigns": [camp()]}, now=NOW)


def test_zero_verified_items_keeps_the_existing_json_and_exits_nonzero(tmp_path, caplog):
    target = tmp_path / "credit_card_campaigns.json"
    original = json.dumps([{"id": "old"}], ensure_ascii=False)
    target.write_text(original, "utf-8")

    results = [
        CardResult(BANK, CARD, KEY, error="no official page could be opened"),
        check({"campaigns": [camp(campaign_end="2025-12-31")]}),  # expired: nothing survives
    ]
    with caplog.at_level(logging.INFO):
        code = finalize(results, target, now=NOW)

    assert code == 1
    assert target.read_text("utf-8") == original, "existing JSON must not be overwritten"
    assert not list(tmp_path.glob("*.tmp"))
    assert "FAILED esun-unicard" in caplog.text and "left unchanged" in caplog.text


def test_missing_target_is_not_created_when_nothing_verified(tmp_path):
    target = tmp_path / "credit_card_campaigns.json"
    assert finalize([CardResult(BANK, CARD, KEY, error="x")], target, now=NOW) == 1
    assert not target.exists()


def test_partial_failure_still_writes_the_verified_cards_and_lists_failures(tmp_path, caplog):
    target = tmp_path / "credit_card_campaigns.json"
    target.write_text("[]", "utf-8")
    results = [
        good_result(),
        CardResult(
            "台新銀行", "@GoGo 卡", "taishin-gogo", error="no official page could be opened"
        ),
    ]
    with caplog.at_level(logging.INFO):
        code = finalize(results, target, now=NOW)

    assert code == 0
    data = json.loads(target.read_text("utf-8"))
    assert set(data) == {"generated_at", "base_benefits", "campaigns"}
    assert data["generated_at"] == NOW.isoformat()
    assert len(data["base_benefits"]) == 1 and len(data["campaigns"]) == 1
    assert "1 cards failed: taishin-gogo" in caplog.text
    assert not list(tmp_path.glob("*.tmp"))


def test_write_is_atomic_and_never_leaves_a_partial_file(tmp_path, monkeypatch):
    target = tmp_path / "credit_card_campaigns.json"
    target.write_text('{"old": true}', "utf-8")

    def explode(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(crawler.os, "replace", explode)
    with pytest.raises(OSError):
        crawler.write_dataset_atomically(crawler.build_dataset([good_result()], now=NOW), target)
    assert target.read_text("utf-8") == '{"old": true}'
    assert not list(tmp_path.glob("*.tmp"))


def test_duplicate_items_across_cards_are_written_once():
    dataset = crawler.build_dataset([good_result(), good_result()], now=NOW)
    assert len(dataset["base_benefits"]) == 1 and len(dataset["campaigns"]) == 1


async def test_crawler_output_imports_and_is_not_marked_verified(session, catalog, tmp_path):
    target = tmp_path / "out.json"
    assert finalize([good_result()], target, now=NOW) == 0

    stats = await import_dataset(session, load_dataset(target))
    assert stats["benefits"]["created"] == 1 and stats["sales"]["created"] == 1
    benefit = (await session.scalars(select(CardBenefit))).one()
    sale = (await session.scalars(select(Sale))).one()
    assert benefit.official_verified_at is None and sale.official_verified_at is None
    assert benefit.effective_end is None
    assert await session.scalar(select(func.count()).select_from(Sale)) == 1


# --- review fix 1: a stale page must not become an open-ended base benefit ----------


def stale_2025_page(extra=""):
    return OpenedPage(
        PAGE_URL,
        "Unicard",
        f"Unicard 玉山銀行信用卡。{BASE_QUOTE}。活動期間 2025/01/01 至 2025/12/31。{extra}" * 2,
    )


def test_2025_page_with_a_1_percent_reward_cannot_be_an_open_ended_base_benefit():
    page = stale_2025_page()
    # The model returns the reward with effective_end null, as a standing benefit.
    for validity in ("活動期間 2025/01/01 至 2025/12/31", BASE_QUOTE, "Unicard 玉山銀行信用卡"):
        result = check(
            {"base_benefits": [base(effective_end=None, validity_evidence=validity)]},
            pages=[page],
        )
        assert result.base_benefits == [], validity
        assert result.dropped, validity

    end_date = check(
        {
            "base_benefits": [
                base(effective_end=None, validity_evidence="活動期間 2025/01/01 至 2025/12/31")
            ]
        },
        pages=[page],
    )
    assert "end date that has passed (2025-12-31)" in reasons(end_date)


def test_expired_end_date_near_the_reward_blocks_it_even_when_the_quote_hides_it():
    # The validity quote is the innocuous "目前…" sentence, but the page states the end date
    # right beside it.
    page = OpenedPage(PAGE_URL, "t", f"Unicard {VALID_QUOTE}，優惠至 2025/12/31 止。" * 2)
    result = check({"base_benefits": [base(effective_end=None)]}, pages=[page])
    assert result.base_benefits == []
    assert "end date that has passed" in reasons(result)


def test_year_only_past_references_are_treated_as_stale():
    page = OpenedPage(PAGE_URL, "t", f"Unicard 2025年度 {VALID_QUOTE}。" * 2)
    result = check({"base_benefits": [base()]}, pages=[page])
    assert result.base_benefits == []
    assert "only refers to past years" in reasons(result)


@pytest.mark.parametrize(
    "validity",
    [
        f"自 2026/01/01 起，{BASE_QUOTE}",  # started before today
        f"至 2026/12/31 止，{BASE_QUOTE}",  # ends after today
        VALID_QUOTE,  # explicit current wording
    ],
)
def test_validity_evidence_that_shows_the_benefit_is_current_is_accepted(validity):
    page = OpenedPage(PAGE_URL, "t", f"Unicard 玉山銀行信用卡。{validity}。" * 2)
    result = check({"base_benefits": [base(validity_evidence=validity)]}, pages=[page])
    assert len(result.base_benefits) == 1, reasons(result)
    assert result.base_benefits[0]["validity_evidence"] == validity
    assert result.base_benefits[0]["effective_end"] is None  # null end is fine once valid today


def test_validity_evidence_must_exist_be_verbatim_and_actually_prove_currency():
    assert check({"base_benefits": [base(validity_evidence="")]}).base_benefits == []
    invented = check({"base_benefits": [base(validity_evidence="目前永久享有 5% 回饋")]})
    assert (
        invented.base_benefits == []
        and "validity_evidence is missing or not verbatim" in reasons(invented)
    )

    undated = OpenedPage(PAGE_URL, "t", f"Unicard 玉山銀行信用卡。{BASE_QUOTE}。" * 2)
    weak = check({"base_benefits": [base(validity_evidence=BASE_QUOTE)]}, pages=[undated])
    assert weak.base_benefits == []
    assert "does not show the benefit is currently valid" in reasons(weak)


def test_base_benefit_reward_rate_must_appear_in_the_quote():
    result = check({"base_benefits": [base(reward="國內一般消費享 5% 回饋，無上限")]})
    assert result.base_benefits == [] and "5% is not on the opened page" in reasons(result)


# --- review fix 2: campaign evidence binding ---------------------------------------


def test_campaign_with_only_a_title_on_the_page_cannot_carry_invented_rate_and_dates():
    title_only = OpenedPage(
        PROMO_URL, "活動", "Unicard 雙十一購物節 活動說明，詳情請洽各分行。" * 3
    )

    hallucinated = camp(
        title="雙十一購物節",
        reward="網購享 3% 回饋",
        campaign_start="2026-10-01",
        campaign_end="2026-12-31",
        evidence="雙十一購物節",  # real text, but it says nothing about a rate or dates
    )
    result = check({"campaigns": [hallucinated]}, pages=[title_only])
    assert result.campaigns == []
    assert "campaign_start 2026-10-01 is not stated" in reasons(result)

    fabricated_quote = check(
        {
            "campaigns": [
                {**hallucinated, "evidence": "活動期間 2026/10/01 至 2026/12/31，網購享 3% 回饋"}
            ]
        },
        pages=[title_only],
    )
    assert fabricated_quote.campaigns == []
    assert "evidence is missing or not verbatim" in reasons(fabricated_quote)


def test_campaign_evidence_is_required_and_must_be_on_the_page():
    assert check({"campaigns": [camp(evidence="")]}).campaigns == []
    assert check({"campaigns": [camp(evidence=None)]}).campaigns == []
    other = check({"campaigns": [camp(evidence="頁面上沒有的一句話")]})
    assert other.campaigns == [] and "evidence is missing or not verbatim" in reasons(other)


def test_campaign_dates_and_reward_must_match_the_page():
    page = OpenedPage(
        PROMO_URL,
        "活動",
        "Unicard 指定網購加碼：活動期間 2026/10/01 至 2026/10/31，網購享 3% 回饋。" * 2,
    )
    quote = "活動期間 2026/10/01 至 2026/10/31，網購享 3% 回饋"
    right = camp(campaign_start="2026-10-01", campaign_end="2026-10-31", evidence=quote)
    assert len(check({"campaigns": [right]}, pages=[page]).campaigns) == 1

    wrong_dates = {**right, "campaign_start": "2026-09-01", "campaign_end": "2026-12-31"}
    r1 = check({"campaigns": [wrong_dates]}, pages=[page])
    assert r1.campaigns == [] and "campaign_start 2026-09-01 is not stated" in reasons(r1)

    wrong_end = {**right, "campaign_end": "2026-12-31"}
    r2 = check({"campaigns": [wrong_end]}, pages=[page])
    assert r2.campaigns == [] and "campaign_end 2026-12-31 is not stated" in reasons(r2)

    wrong_reward = {**right, "reward": "網購享 5% 回饋"}
    r3 = check({"campaigns": [wrong_reward]}, pages=[page])
    assert r3.campaigns == [] and "5% is not on the opened page" in reasons(r3)


def test_year_month_day_kanji_dates_are_understood():
    page = OpenedPage(
        PROMO_URL, "活動", "Unicard 2026年10月1日至2026年12月31日，網購享 3% 回饋。" * 2
    )
    quote = "2026年10月1日至2026年12月31日，網購享 3% 回饋"
    item = camp(campaign_start="2026-10-01", campaign_end="2026-12-31", evidence=quote)
    assert len(check({"campaigns": [item]}, pages=[page]).campaigns) == 1


def test_republic_of_china_calendar_dates_are_converted():
    from app.services.campaign_crawler import dated_mentions

    mentions = dated_mentions("活動期間民國115年10月1日至民國115年12月31日")
    assert (date(2026, 10, 1), "start") in mentions and (date(2026, 12, 31), "end") in mentions


# --- review fix 3: a partial crawl must not truncate the canonical dataset ---------

CTBC_BANK, CTBC_CARD = "中國信託銀行", "LINE Pay 聯名卡"
CTBC_KEY = "ctbc-linepay"


def old_dataset():
    return {
        "generated_at": "2026-08-01T00:00:00+00:00",
        "base_benefits": [
            {
                "id": "benefit-ctbc-old",
                "card_key": CTBC_KEY,
                "title": "舊 CTBC 基本回饋",
            },
            {"id": "benefit-esun-old", "card_key": KEY, "title": "舊玉山基本回饋"},
        ],
        "campaigns": [
            {
                "id": "campaign-ctbc-old",
                "card_key": CTBC_KEY,
                "title": "舊 CTBC 活動",
            },
            {"id": "campaign-esun-old", "card_key": KEY, "title": "舊玉山活動"},
            {
                "id": "campaign-other-old",
                "card_key": "taishin-gogo",
                "title": "沒爬的卡",
            },
        ],
    }


def ids_of(dataset, kind):
    return {item["id"] for item in dataset[kind]}


def test_failed_card_keeps_its_old_entries_and_successful_card_is_updated(tmp_path, caplog):
    target = tmp_path / "credit_card_campaigns.json"
    target.write_text(json.dumps(old_dataset(), ensure_ascii=False), "utf-8")

    results = [
        CardResult(
            CTBC_BANK, CTBC_CARD, CTBC_KEY, error="no official page could be opened"
        ),  # this run fails
        good_result(),  # 玉山 succeeds with new data
    ]
    with caplog.at_level(logging.INFO):
        assert finalize(results, target, now=NOW) == 0

    out = json.loads(target.read_text("utf-8"))
    # CTBC failed: its previous data survives untouched.
    assert "benefit-ctbc-old" in ids_of(out, "base_benefits")
    assert "campaign-ctbc-old" in ids_of(out, "campaigns")
    # 玉山 succeeded: old rows are replaced by the new ones.
    assert "benefit-esun-old" not in ids_of(out, "base_benefits")
    assert "campaign-esun-old" not in ids_of(out, "campaigns")
    new_titles = {b["title"] for b in out["base_benefits"]} | {c["title"] for c in out["campaigns"]}
    assert {"國內一般消費", "指定網購加碼活動"} <= new_titles
    # A card that was not crawled at all is also kept.
    assert "campaign-other-old" in ids_of(out, "campaigns")
    assert out["generated_at"] == NOW.isoformat()
    assert "kept 2 existing entries for failed card ctbc-linepay" in caplog.text
    assert not list(tmp_path.glob("*.tmp"))


def test_all_cards_failing_leaves_the_file_byte_identical_and_exits_nonzero(tmp_path):
    target = tmp_path / "credit_card_campaigns.json"
    original = json.dumps(old_dataset(), ensure_ascii=False, indent=2)
    target.write_text(original, "utf-8")
    results = [
        CardResult(CTBC_BANK, CTBC_CARD, CTBC_KEY, error="x"),
        CardResult(BANK, CARD, KEY, error="y"),
    ]
    assert finalize(results, target, now=NOW) == 1
    assert target.read_text("utf-8") == original


def test_unreadable_existing_file_is_never_overwritten(tmp_path, caplog):
    target = tmp_path / "credit_card_campaigns.json"
    target.write_text("{ this is not json", "utf-8")
    with caplog.at_level(logging.INFO):
        assert finalize([good_result()], target, now=NOW) == 1
    assert target.read_text("utf-8") == "{ this is not json"
    assert "cannot merge" in caplog.text


def test_legacy_list_file_is_merged_as_campaigns_and_preserved(tmp_path):
    target = tmp_path / "credit_card_campaigns.json"
    legacy = [
        {"id": "legacy-1", "bank": "中國信託商業銀行", "card": CTBC_CARD, "title": "舊版活動"}
    ]
    target.write_text(json.dumps(legacy, ensure_ascii=False), "utf-8")
    assert finalize([good_result()], target, now=NOW) == 0
    out = json.loads(target.read_text("utf-8"))
    assert "legacy-1" in ids_of(out, "campaigns")  # different bank string: never touched
    assert len(out["base_benefits"]) == 1


def test_successful_card_with_nothing_valid_clears_only_its_own_stale_entries(tmp_path):
    target = tmp_path / "credit_card_campaigns.json"
    target.write_text(json.dumps(old_dataset(), ensure_ascii=False), "utf-8")
    results = [
        CardResult(CTBC_BANK, CTBC_CARD, CTBC_KEY),  # crawled fine, nothing verified this time
        good_result(),  # keeps the run non-empty
    ]
    assert finalize(results, target, now=NOW) == 0
    out = json.loads(target.read_text("utf-8"))
    assert "campaign-ctbc-old" not in ids_of(out, "campaigns")
    assert "campaign-other-old" in ids_of(out, "campaigns")


def test_merge_is_idempotent_across_runs(tmp_path):
    target = tmp_path / "credit_card_campaigns.json"
    assert finalize([good_result()], target, now=NOW) == 0
    first = json.loads(target.read_text("utf-8"))
    assert finalize([good_result()], target, now=NOW) == 0
    second = json.loads(target.read_text("utf-8"))
    assert first == second
    assert len(second["base_benefits"]) == 1 and len(second["campaigns"]) == 1
