"""Central base-benefit page metadata, and the crawler path that reads it. No LLM, no network."""

import re
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.cli.verify_benefit_coverage import check_coverage, format_report
from app.models import Card, CardBenefit
from app.services import campaign_crawler as crawler
from app.services.campaign_crawler import (
    CrawlTarget,
    OpenedPage,
    crawl_card,
    crawler_targets,
    extraction_payload,
    gather_official_pages,
    model_excerpt,
)
from app.services.card_identity import crawler_identities
from app.services.crawl_sources import SOURCES_PATH, base_urls_for, load_base_benefit_sources
from app.services.official_pages import PageResult
from app.services.official_sources import is_official_url
from app.services.sales_import import import_benefits

TEN = {
    "ctbc-linepay",
    "esun-kumamon",
    "esun-pi-card",
    "esun-ubear",
    "esun-unicard",
    "fubon-costco",
    "fubon-j",
    "fubon-momo",
    "taishin-pxmart",
    "taishin-richart",
}
TODAY = date(2026, 9, 20)
NOW = datetime(2026, 9, 20, tzinfo=UTC)
BANK, CARD, KEY = "玉山銀行", "Unicard", "esun-unicard"
BASE_URL = "https://www.esunbank.com/zh-tw/personal/credit-card/intro/bank-card/unicard"
QUOTE = "國內一般消費享 1% 回饋，無上限"
PAGE_TEXT = f"Unicard 玉山銀行信用卡。目前{QUOTE}。" * 4


def ok(url, text=PAGE_TEXT, final=None):
    return PageResult(True, final or url, 200, "Unicard", text)


def bad(url, status=404):
    return PageResult(False, url, status, error=f"HTTP {status}")


# --- the metadata ------------------------------------------------------


def test_all_ten_crawler_cards_have_an_official_base_benefit_url():
    sources = load_base_benefit_sources()
    enabled = {i.catalog_key for i in crawler_identities()}
    assert enabled == TEN, "the crawler-enabled set changed; update the coverage target"
    assert set(sources) == TEN
    banks = {i.catalog_key: i.search_bank_name for i in crawler_identities()}
    for key, urls in sources.items():
        assert urls, key
        for url in urls:
            assert url.startswith("https://"), url
            assert is_official_url(banks[key], url), (key, url)
    assert all(base_urls_for(k) for k in TEN)


def test_metadata_holds_urls_only_never_a_reward():
    raw = SOURCES_PATH.read_text("utf-8")
    assert raw.splitlines()[0] == "catalog_key,url"
    assert all(line.count(",") == 1 for line in raw.strip().splitlines())
    assert not re.search(r"\d+(?:\.\d+)?\s*%(?![0-9A-Fa-f]{2})", raw), (
        "no reward rate may live here"
    )
    assert "回饋" not in raw


def write(tmp_path, body):
    path = tmp_path / "sources.csv"
    path.write_text(body, "utf-8")
    return path


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("catalog_key,url,rate\nesun-unicard,https://www.esunbank.com/a,1%\n", "header"),
        ("catalog_key\nesun-unicard\n", "header"),
        ("catalog_key,url\nno-such-card,https://www.esunbank.com/a\n", "not a crawler-enabled"),
        ("catalog_key,url\nesun-unicard,http://www.esunbank.com/a\n", "official https"),
        ("catalog_key,url\nesun-unicard,https://www.ctbcbank.com/a\n", "official https"),
        (
            "catalog_key,url\nesun-unicard,https://www.esunbank.com.evil.example/a\n",
            "official https",
        ),
        (
            "catalog_key,url\nesun-unicard,https://www.ptt.cc/bbs/creditcard/M.1.html\n",
            "official https",
        ),
        (
            "catalog_key,url\nesun-unicard,https://www.esunbank.com/a\n"
            "esun-unicard,https://www.esunbank.com/a\n",
            "duplicate",
        ),
        ("catalog_key,url\nesun-unicard,https://www.esunbank.com/a,extra\n", "does not match"),
    ],
)
def test_loader_rejects_anything_that_is_not_an_official_url_for_that_card(tmp_path, body, why):
    with pytest.raises(ValueError, match=why):
        load_base_benefit_sources(write(tmp_path, body))


async def test_targets_from_the_catalog_carry_their_base_urls(session, catalog):
    targets = await crawler_targets(session)
    assert {t.card_key for t in targets} == TEN
    assert all(t.base_urls for t in targets)
    assert next(t for t in targets if t.card_key == "esun-unicard").base_urls == base_urls_for(
        "esun-unicard"
    )


# --- gathering: search must not matter for base pages ------------------


def broken_search(query):
    raise RuntimeError("No results found.")


def test_base_page_is_opened_even_when_search_raises_every_time():
    pages = gather_official_pages(
        BANK, CARD, year=2026, search=broken_search, fetch=ok, base_urls=(BASE_URL,)
    )
    assert [p.url for p in pages] == [BASE_URL] and pages[0].base is True


def test_base_page_is_opened_when_search_returns_nothing():
    pages = gather_official_pages(
        BANK, CARD, year=2026, search=lambda q: [], fetch=ok, base_urls=(BASE_URL,)
    )
    assert len(pages) == 1 and pages[0].base


def test_base_pages_are_first_and_never_squeezed_out_by_search_results():
    hits = [f"https://www.esunbank.com/zh-tw/promo/{n}" for n in range(9)]
    pages = gather_official_pages(
        BANK, CARD, year=2026, search=lambda q: hits, fetch=ok, base_urls=(BASE_URL,)
    )
    assert pages[0].url == BASE_URL and pages[0].base
    assert len(pages) == crawler.MAX_PAGES_PER_CARD
    assert not any(p.base for p in pages[1:])


def test_unreadable_base_page_yields_no_page_at_all():
    for result in (
        bad(BASE_URL, 404),
        bad(BASE_URL, 500),
        PageResult(False, BASE_URL, 202, error="no readable content"),
    ):
        pages = gather_official_pages(
            BANK,
            CARD,
            year=2026,
            search=lambda q: [],
            fetch=lambda u, r=result: r,
            base_urls=(BASE_URL,),
        )
        assert pages == []


def test_base_url_that_redirects_off_the_bank_is_dropped():
    def fetch(url):
        return ok(url, final="https://www.ctbcbank.com/somewhere")

    assert (
        gather_official_pages(
            BANK, CARD, year=2026, search=lambda q: [], fetch=fetch, base_urls=(BASE_URL,)
        )
        == []
    )


def test_a_misconfigured_base_url_is_skipped_without_being_fetched():
    fetched = []

    def fetch(url):
        fetched.append(url)
        return ok(url)

    pages = gather_official_pages(
        BANK,
        CARD,
        year=2026,
        search=lambda q: [],
        fetch=fetch,
        base_urls=("https://www.ctbcbank.com/wrong-bank", "http://www.esunbank.com/insecure"),
    )
    assert pages == [] and fetched == []


# --- crawl_card end to end ---------------------------------------------


def base_item(**over):
    return {
        "title": "國內一般消費",
        "reward": QUOTE,
        "source_url": BASE_URL,
        "evidence": QUOTE,
        "validity_evidence": "目前" + QUOTE,
        "confidence": 0.9,
        **over,
    }


TARGET = CrawlTarget(KEY, BANK, CARD, (BASE_URL,))


async def test_search_failure_still_produces_the_verified_base_benefit():
    seen = {}

    async def extract(payload):
        seen.update(payload)
        return {"base_benefits": [base_item()], "campaigns": []}

    result = await crawl_card(
        TARGET, today=TODAY, search=broken_search, fetch=ok, extract=extract, now=NOW
    )
    assert result.error is None and len(result.base_benefits) == 1
    benefit = result.base_benefits[0]
    assert benefit["card_key"] == KEY and benefit["source_url"] == BASE_URL
    assert seen["pages"][0]["kind"] == "base_benefit_page"


async def test_unreadable_pages_never_reach_the_model_or_produce_data():
    called = []

    async def extract(payload):
        called.append(payload)
        return {"base_benefits": [base_item()], "campaigns": []}

    for fetch in (
        lambda u: bad(u, 404),
        lambda u: PageResult(False, u, 200, error="not a text page"),
    ):
        result = await crawl_card(
            TARGET, today=TODAY, search=broken_search, fetch=fetch, extract=extract, now=NOW
        )
        assert result.error == "no official page could be opened"
        assert result.base_benefits == [] and result.campaigns == []
    assert called == [], "the model must not be asked to invent data from nothing"


async def test_model_cannot_add_a_reward_the_base_page_does_not_state():
    async def extract(payload):
        invented = base_item(reward="國內一般消費享 9% 回饋", evidence="國內一般消費享 9% 回饋")
        return {"base_benefits": [invented], "campaigns": []}

    result = await crawl_card(
        TARGET, today=TODAY, search=lambda q: [], fetch=ok, extract=extract, now=NOW
    )
    assert result.base_benefits == [] and any("not on the opened page" in d for d in result.dropped)


async def test_a_stale_base_page_is_not_turned_into_a_current_benefit():
    stale = f"Unicard 玉山銀行信用卡。{QUOTE}。活動期間 2025/01/01 至 2025/12/31。" * 3

    async def extract(payload):
        return {"base_benefits": [base_item(validity_evidence=QUOTE)], "campaigns": []}

    result = await crawl_card(
        TARGET,
        today=TODAY,
        search=lambda q: [],
        fetch=lambda u: ok(u, text=stale),
        extract=extract,
        now=NOW,
    )
    assert result.base_benefits == []
    assert any("end date that has passed" in d or "does not show" in d for d in result.dropped)


# --- what the model reads ----------------------------------------------


def test_reward_text_deep_in_a_long_page_is_still_shown_to_the_model():
    filler = "銀行服務說明與條款，與回報無關的內容。" * 900  # ~16k characters of nothing useful
    page = f"Unicard 玉山銀行。{filler}目前{QUOTE}，詳見權益說明。{filler}"
    assert page.index(QUOTE) > 10_000  # beyond the old 6,000 character cut
    excerpt = model_excerpt(page, 6000)
    assert QUOTE in excerpt and len(excerpt) <= 6000 + 3 * len(crawler._EXCERPT_GAP)
    assert excerpt.startswith("Unicard 玉山銀行")
    for window in excerpt.split(crawler._EXCERPT_GAP):
        assert window in page, "every window must be a verbatim slice, or a quote could not verify"


def test_short_pages_are_passed_through_untouched():
    assert model_excerpt(PAGE_TEXT, 6000) == PAGE_TEXT


def test_base_pages_get_a_larger_budget_and_are_labelled():
    long_text = "x" * 20_000 + QUOTE
    payload = extraction_payload(
        BANK,
        CARD,
        [
            OpenedPage(BASE_URL, "t", long_text, base=True),
            OpenedPage("https://www.esunbank.com/p", "t", long_text),
        ],
        TODAY,
    )
    kinds = [p["kind"] for p in payload["pages"]]
    assert kinds == ["base_benefit_page", "search_result"]
    # 20,000 characters of filler: the base page's larger budget still ends at its own cap.
    assert len(payload["pages"][0]["text"]) <= crawler.MAX_CHARS_PER_BASE_PAGE_FOR_MODEL + 200
    assert len(payload["pages"][1]["text"]) <= crawler.MAX_CHARS_PER_PAGE_FOR_MODEL + 200


# --- coverage verifier -------------------------------------------------


def benefit_item(key, **over):
    identities = {i.catalog_key: i for i in crawler_identities()}
    return {
        "card_key": key,
        "title": "國內一般消費",
        "reward": QUOTE,
        "source_url": base_urls_for(key)[0],
        "evidence": QUOTE,
        "validity_evidence": "目前" + QUOTE,
        "source_bank_name": identities[key].search_bank_name,
        "source_card_name": identities[key].search_card_name,
        **over,
    }


async def test_coverage_passes_only_when_every_enabled_card_has_a_usable_benefit(session, catalog):
    await import_benefits(session, [benefit_item(k) for k in sorted(TEN)])
    report = await check_coverage(session, today=TODAY)
    assert {r.card_key for r in report} == TEN and all(r.ok for r in report)
    assert "coverage: 10/10" in format_report(report)


async def test_coverage_reports_exactly_which_card_is_missing_and_why(session, catalog):
    await import_benefits(session, [benefit_item(k) for k in sorted(TEN - {"taishin-richart"})])
    report = await check_coverage(session, today=TODAY)
    missing = [r for r in report if not r.ok]
    assert [(r.card_key, r.reason) for r in missing] == [
        ("taishin-richart", "no card_benefits rows")
    ]
    assert "coverage: 9/10" in format_report(
        report
    ) and "missing: taishin-richart" in format_report(report)


async def test_coverage_does_not_count_expired_or_unofficially_sourced_rows(session, catalog):
    items = [benefit_item(k) for k in sorted(TEN - {"fubon-j", "fubon-momo"})]
    items += [
        benefit_item("fubon-j", effective_end=(TODAY - timedelta(days=1)).isoformat()),
        benefit_item("fubon-momo", source_url="https://www.ptt.cc/bbs/creditcard/M.1.html"),
    ]
    await import_benefits(session, items)
    # The importer stores these rows; it is the verifier that refuses to count them.
    assert len((await session.scalars(select(CardBenefit))).all()) == 10
    report = {r.card_key: r for r in await check_coverage(session, today=TODAY)}
    assert (
        not report["fubon-j"].ok
        and report["fubon-j"].reason == "rows exist but none is in force today"
    )
    assert (
        not report["fubon-momo"].ok
        and report["fubon-momo"].reason == "rows exist but no official source_url"
    )
    assert sum(r.ok for r in report.values()) == 8


async def test_coverage_can_be_required_for_specific_keys_and_rejects_unknown_ones(
    session, catalog
):
    await import_benefits(session, [benefit_item("esun-unicard")])
    only = await check_coverage(session, today=TODAY, require=["esun-unicard"])
    assert [r.card_key for r in only] == ["esun-unicard"] and only[0].ok

    unknown = await check_coverage(session, today=TODAY, require=["esun-unicard", "mega-one"])
    assert [(r.card_key, r.reason) for r in unknown if not r.ok] == [
        ("mega-one", "not a crawler-enabled card")
    ]
    card = (await session.scalars(select(Card).where(Card.catalog_key == "mega-one"))).one()
    assert card.crawler_enabled is False


# --- one bad URL must not lose the card; the informed retry ------------------------------------


def test_a_fetch_that_raises_on_one_url_does_not_lose_the_base_page():
    hits = ["https://www.esunbank.com/zh-tw/活動/壞網址", "https://www.esunbank.com/zh-tw/promo/1"]

    def fetch(url):
        if "壞網址" in url:
            raise UnicodeEncodeError("ascii", url, 0, 1, "boom")
        return ok(url)

    pages = gather_official_pages(
        BANK, CARD, year=2026, search=lambda q: hits, fetch=fetch, base_urls=(BASE_URL,)
    )
    assert pages[0].url == BASE_URL and pages[0].base
    assert all("壞網址" not in p.url for p in pages)


def test_a_base_url_whose_fetch_raises_is_skipped_not_fatal():
    def fetch(url):
        raise RuntimeError("boom")

    assert (
        gather_official_pages(
            BANK, CARD, year=2026, search=lambda q: [], fetch=fetch, base_urls=(BASE_URL,)
        )
        == []
    )


STALE_NEAR_QUOTE = f"Unicard 玉山銀行信用卡。{QUOTE}。優惠期間 2025/01/01 至 2025/12/31。"
CLEAN_QUOTE = "現行一般消費回饋 1.5%，即日起適用"
MIXED_PAGE = (
    STALE_NEAR_QUOTE * 2
    + "。"
    + "無關的說明文字，" * 80
    + f"Unicard {CLEAN_QUOTE}，詳見權益說明。" * 2
)


async def test_a_rejected_first_answer_gets_one_informed_retry_with_the_same_validation():
    calls = []

    async def extract(payload):
        calls.append(payload)
        if "rejected_previous" not in payload:
            return {"base_benefits": [base_item(validity_evidence=QUOTE)], "campaigns": []}
        return {
            "base_benefits": [
                base_item(
                    reward=CLEAN_QUOTE,
                    evidence=CLEAN_QUOTE,
                    validity_evidence=CLEAN_QUOTE,
                    title="現行一般消費",
                )
            ],
            "campaigns": [],
        }

    result = await crawl_card(
        TARGET,
        today=TODAY,
        search=lambda q: [],
        fetch=lambda u: ok(u, text=MIXED_PAGE),
        extract=extract,
        now=NOW,
    )
    assert len(calls) == 2
    reasons = calls[1]["rejected_previous"]
    assert reasons and all(r.startswith("base_benefit") for r in reasons)
    assert any("end date that has passed" in r for r in reasons), "the model is told why"
    assert [b["title"] for b in result.base_benefits] == ["現行一般消費"]
    assert result.base_benefits[0]["validity_evidence"] == CLEAN_QUOTE


async def test_the_retry_cannot_smuggle_in_what_the_validator_rejects():
    async def extract(payload):
        # Both answers repeat the same stale quote.
        return {"base_benefits": [base_item(validity_evidence=QUOTE)], "campaigns": []}

    result = await crawl_card(
        TARGET,
        today=TODAY,
        search=lambda q: [],
        fetch=lambda u: ok(u, text=STALE_NEAR_QUOTE * 3),
        extract=extract,
        now=NOW,
    )
    assert result.base_benefits == []
    assert any(d.startswith("(retry 1) base_benefit") for d in result.dropped)
    assert any(d.startswith("(retry 2) base_benefit") for d in result.dropped)


async def test_no_retry_when_the_first_answer_already_has_a_base_benefit():
    calls = []

    async def extract(payload):
        calls.append(payload)
        return {"base_benefits": [base_item()], "campaigns": []}

    result = await crawl_card(
        TARGET, today=TODAY, search=lambda q: [], fetch=ok, extract=extract, now=NOW
    )
    assert len(result.base_benefits) == 1 and len(calls) == 1


async def test_no_retry_without_a_base_page_and_a_failing_retry_keeps_the_first_result():
    calls = []

    async def extract(payload):
        calls.append(payload)
        if "rejected_previous" in payload:
            raise RuntimeError("gateway down")
        return {"base_benefits": [], "campaigns": []}

    no_base = CrawlTarget(KEY, BANK, CARD, ())  # nothing from central metadata
    hit = "https://www.esunbank.com/zh-tw/promo/1"
    result = await crawl_card(
        no_base, today=TODAY, search=lambda q: [hit], fetch=ok, extract=extract, now=NOW
    )
    assert len(calls) == 1 and result.error is None  # no base page, so no retry

    calls.clear()
    result = await crawl_card(
        TARGET, today=TODAY, search=lambda q: [], fetch=ok, extract=extract, now=NOW
    )
    assert len(calls) == 2 and result.error is None and result.base_benefits == []


async def test_a_second_retry_can_succeed_and_feedback_accumulates():
    calls = []
    good = base_item(
        reward=CLEAN_QUOTE,
        evidence=CLEAN_QUOTE,
        validity_evidence=CLEAN_QUOTE,
        title="現行一般消費",
    )

    async def extract(payload):
        calls.append(payload)
        if len(calls) < 3:  # first answer and first retry both reuse the stale quote
            return {"base_benefits": [base_item(validity_evidence=QUOTE)], "campaigns": []}
        return {"base_benefits": [good], "campaigns": []}

    result = await crawl_card(
        TARGET,
        today=TODAY,
        search=lambda q: [],
        fetch=lambda u: ok(u, text=MIXED_PAGE),
        extract=extract,
        now=NOW,
    )
    assert len(calls) == 3
    assert "rejected_previous" not in calls[0] and calls[1]["rejected_previous"]
    assert len(calls[2]["rejected_previous"]) >= len(calls[1]["rejected_previous"])
    assert [b["title"] for b in result.base_benefits] == ["現行一般消費"]


async def test_retries_are_bounded_and_rejected_quotes_are_reported():
    calls = []

    async def extract(payload):
        calls.append(payload)
        return {"base_benefits": [base_item(validity_evidence=QUOTE)], "campaigns": []}

    result = await crawl_card(
        TARGET,
        today=TODAY,
        search=lambda q: [],
        fetch=lambda u: ok(u, text=STALE_NEAR_QUOTE * 3),
        extract=extract,
        now=NOW,
    )
    assert len(calls) == 1 + crawler.MAX_BASE_RETRIES
    assert result.base_benefits == []
    first = next(d for d in result.dropped if d.startswith("base_benefit"))
    assert "| quoted:" in first and QUOTE[:10] in first, "the model is shown what it quoted"


# --- scope guard, suggested pairs, pair_id -------------------------------------------------------

from app.services.campaign_crawler import (  # noqa: E402
    freshness_checked_pairs,
    narrow_scope_word,
    pair_registry,
    validate_extraction,
)


def check_base(items, pages, **kw):
    return validate_extraction(
        {"base_benefits": items}, card_key=KEY, bank=BANK, card=CARD, pages=pages, today=TODAY, **kw
    )


@pytest.mark.parametrize(
    "quote",
    [
        "保費一次付清享 1.2%P幣回饋無上限",
        "新戶首次申辦享 5% 回饋",
        "指定通路享 3% 回饋",
        "加碼 2% 回饋",
    ],
)
def test_scoped_rewards_are_not_accepted_as_base_benefits(quote):
    page = OpenedPage(BASE_URL, "t", f"Unicard 玉山銀行信用卡。目前{quote}。" * 3, base=True)
    result = check_base(
        [base_item(reward=quote, evidence=quote, validity_evidence=f"目前{quote}")], [page]
    )
    assert result.base_benefits == []
    assert any("not the card's base benefit" in d for d in result.dropped)


def test_a_general_reward_is_not_mistaken_for_a_scoped_one():
    assert narrow_scope_word("國內一般消費享 1% 回饋，無上限", "基本回饋 1%P幣無上限") is None
    assert narrow_scope_word("保費享 1.2%") == "保費"


STALE_NOTE_PAGE = (
    "Unicard 玉山銀行信用卡。活動期間：2026/9/1～2027/2/28 基本回饋 1%無上限。"
    + "與回饋無關的說明。" * 40
    + "已登錄期間 2026/2/28~2026/8/31 無須再次登錄。"
)


def test_pairs_only_include_quotes_that_pass_the_validator_they_will_face():
    from app.services.campaign_crawler import page_context, validity_problem

    pairs = freshness_checked_pairs(STALE_NOTE_PAGE, TODAY)
    assert pairs, "the base statement sits far enough from the old date to pass"
    for pair in pairs:
        ctx = page_context(STALE_NOTE_PAGE, pair["evidence"], pair["validity_evidence"])
        assert validity_problem(pair["validity_evidence"], ctx, TODAY) is None
        assert crawler.verbatim_on_page(pair["evidence"], STALE_NOTE_PAGE)
        assert crawler.verbatim_on_page(pair["validity_evidence"], STALE_NOTE_PAGE)
    assert any("基本回饋" in p["evidence"] for p in pairs)


def test_no_pair_is_offered_when_an_old_end_date_sits_next_to_every_statement():
    crowded = "Unicard 基本回饋 1% 活動期間 2026/9/1～2027/2/28，已登錄 2026/2/28~2026/8/31。" * 3
    assert freshness_checked_pairs(crowded, TODAY) == []


def test_registry_ids_and_payload_agree_and_only_base_pages_get_pairs():
    base_page = OpenedPage(BASE_URL, "t", STALE_NOTE_PAGE, base=True)
    other = OpenedPage("https://www.esunbank.com/zh-tw/promo/1", "t", STALE_NOTE_PAGE)
    registry = pair_registry([other, base_page], TODAY)
    assert registry and all(pid.startswith("1.") for pid in registry)  # index of the base page
    payload = extraction_payload(BANK, CARD, [other, base_page], TODAY)
    assert "suggested_quote_pairs" not in payload["pages"][0]
    offered = {p["id"] for p in payload["pages"][1]["suggested_quote_pairs"]}
    assert offered == set(registry)


def test_pair_id_uses_the_backends_own_quotes_and_page():
    page = OpenedPage(BASE_URL, "t", STALE_NOTE_PAGE, base=True)
    registry = pair_registry([page], TODAY)
    pid = next(i for i, (_, p) in registry.items() if "基本回饋" in p["evidence"])
    pair = registry[pid][1]
    result = check_base(
        [
            {
                "title": "基本回饋",
                "reward": "基本回饋 1%無上限",
                "pair_id": pid,
                "source_url": "https://evil.example/x",
            }
        ],
        [page],
    )
    assert len(result.base_benefits) == 1, result.dropped
    got = result.base_benefits[0]
    assert (
        got["evidence"] == pair["evidence"]
        and got["validity_evidence"] == pair["validity_evidence"]
    )
    assert got["source_url"] == BASE_URL, "the model's own source_url is ignored for a pair"


def test_pair_id_cannot_smuggle_past_the_other_gates():
    page = OpenedPage(BASE_URL, "t", STALE_NOTE_PAGE, base=True)
    pid = next(iter(pair_registry([page], TODAY)))

    unknown = check_base([{"title": "t", "reward": "1%", "pair_id": "9.9"}], [page])
    assert unknown.base_benefits == [] and "not one of the suggested pairs" in unknown.dropped[0]

    invented_rate = check_base(
        [{"title": "基本回饋", "reward": "基本回饋 9%", "pair_id": pid}], [page]
    )
    assert invented_rate.base_benefits == []
    assert any("not on the opened page" in d for d in invented_rate.dropped)

    scoped = check_base([{"title": "保費回饋", "reward": "基本回饋 1%", "pair_id": pid}], [page])
    assert scoped.base_benefits == []
    assert any("not the card's base benefit" in d for d in scoped.dropped)

    no_rate = check_base([{"title": "基本回饋", "reward": "享有優惠", "pair_id": pid}], [page])
    assert no_rate.base_benefits == [] and any("no parseable rate" in d for d in no_rate.dropped)
