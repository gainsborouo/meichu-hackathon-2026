"""card_benefits: model constraints, repository windows, and the two-format importer."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Card, CardBenefit, Sale
from app.repositories import benefits as benefits_repo
from app.repositories import cards as cards_repo
from app.services.sales_import import (
    import_benefits,
    import_dataset,
    load_dataset,
    parse_dataset,
)
from app.services.users import upsert_user

TODAY = date(2026, 9, 20)


def base_item(**over):
    return {
        "bank": "玉山銀行",
        "card": "Unicard",
        "title": "國內一般消費",
        "reward": "國內一般消費享 1% 回饋，無上限",
        "conditions": None,
        "effective_start": None,
        "effective_end": None,
        "source_url": "https://www.esunbank.com/zh-tw/credit-cards/unicard",
        "evidence": "一般消費 1% 回饋",
        "confidence": 0.9,
        **over,
    }


def campaign_item(**over):
    return {
        "id": "c-1",
        "bank": "玉山銀行",
        "card": "Unicard",
        "title": "指定網購加碼",
        "register_from": None,
        "register_until": None,
        "register_url": None,
        "recurrence": "once",
        "campaign_start": "2026-09-01",
        "campaign_end": "2026-12-31",
        "reward": "網購享 3% 回饋",
        "quota_limited": False,
        "source_url": "https://www.esunbank.com/promo",
        "evidence": "網購 3%",
        "confidence": 0.9,
        **over,
    }


async def test_import_benefits_upserts_and_never_marks_verified(session):
    first = await import_benefits(session, [base_item()])
    second = await import_benefits(session, [base_item(reward="國內一般消費享 1.5% 回饋，無上限")])
    assert first == {"created": 1, "updated": 0, "skipped": 0, "total": 1}
    assert second == {"created": 0, "updated": 1, "skipped": 0, "total": 1}

    rows = (await session.scalars(select(CardBenefit))).all()
    assert len(rows) == 1
    b = rows[0]
    assert b.effective_end is None and b.effective_start is None  # legal: no end date
    assert b.reward_rules[0]["rate"] == 0.015 and b.reward_rules[0]["unlimited"] is True
    assert b.official_verified_at is None, "a local JSON import verifies nothing"
    assert b.source_payload["evidence"] == "一般消費 1% 回饋"
    assert b.source_url.startswith("https://www.esunbank.com")


async def test_import_benefits_skips_invalid_entries_without_aborting(session):
    items = [
        base_item(title="ok"),
        base_item(title="no-source", source_url=""),
        base_item(title="no-reward", reward=None),
        base_item(title="backwards", effective_start="2026-06-01", effective_end="2026-01-01"),
        "not-a-dict",
    ]
    stats = await import_benefits(session, items)
    assert stats == {"created": 1, "updated": 0, "skipped": 4, "total": 5}
    assert [b.title for b in (await session.scalars(select(CardBenefit))).all()] == ["ok"]


async def test_benefit_table_rejects_a_backwards_range_and_duplicate_titles(session):
    card = await cards_repo.get_or_create_card(session, bank_name="B", name="C")
    session.add(
        CardBenefit(
            card_id=card.id,
            title="t",
            reward="1%",
            source_url="https://x",
            effective_start=date(2026, 6, 1),
            effective_end=date(2026, 1, 1),
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    card = await cards_repo.get_or_create_card(session, bank_name="B", name="C")
    for _ in range(2):
        session.add(CardBenefit(card_id=card.id, title="dup", reward="1%", source_url="https://x"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_repository_returns_only_effective_benefits_of_held_cards(session):
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    held = await cards_repo.get_or_create_card(session, bank_name="玉山銀行", name="Unicard")
    other = await cards_repo.get_or_create_card(session, bank_name="台新銀行", name="@GoGo 卡")
    await cards_repo.add_user_card(session, user.id, held.id)

    def add(card, title, start=None, end=None):
        session.add(
            CardBenefit(
                card_id=card.id,
                title=title,
                reward="1%",
                source_url="https://x",
                effective_start=start,
                effective_end=end,
            )
        )

    day = timedelta(days=1)
    add(held, "no dates")
    add(held, "open ended", start=TODAY - 30 * day)
    add(held, "ends today", end=TODAY)
    add(held, "starts today", start=TODAY)
    add(held, "ended yesterday", end=TODAY - day)
    add(held, "starts tomorrow", start=TODAY + day)
    add(other, "not held")
    await session.flush()

    got = await benefits_repo.list_effective_for_user_cards(session, user.id, TODAY)
    assert {b.title for b in got} == {"no dates", "open ended", "ends today", "starts today"}
    for b in got:
        assert benefits_repo.is_effective(b, TODAY)
    assert not benefits_repo.is_effective(
        CardBenefit(effective_start=TODAY + day, effective_end=None), TODAY
    )


def test_parse_dataset_accepts_object_and_legacy_list():
    obj = parse_dataset(
        {"generated_at": "2026-09-20T00:00:00+00:00", "base_benefits": [1], "campaigns": [2]}
    )
    assert obj == {
        "generated_at": "2026-09-20T00:00:00+00:00",
        "base_benefits": [1],
        "campaigns": [2],
    }

    legacy = parse_dataset([{"id": "old"}])
    assert legacy == {"generated_at": None, "base_benefits": [], "campaigns": [{"id": "old"}]}
    assert parse_dataset({})["campaigns"] == []
    with pytest.raises(ValueError):
        parse_dataset("nope")


async def test_import_dataset_reports_benefits_and_sales_separately(session):
    dataset = parse_dataset(
        {
            "generated_at": "2026-09-20T03:00:00Z",
            "base_benefits": [base_item()],
            "campaigns": [campaign_item(), campaign_item(id="c-2", title="另一個活動")],
        }
    )
    stats = await import_dataset(session, dataset)
    assert stats == {
        "benefits": {"created": 1, "updated": 0, "skipped": 0, "total": 1},
        "sales": {"created": 2, "updated": 0, "total": 2},
    }
    again = await import_dataset(session, dataset)
    assert again["benefits"]["updated"] == 1 and again["sales"]["updated"] == 2
    assert await session.scalar(select(func.count()).select_from(CardBenefit)) == 1
    assert await session.scalar(select(func.count()).select_from(Sale)) == 2

    sale = await session.get(Sale, "c-1")
    assert sale.campaign_start == date(2026, 9, 1) and sale.official_verified_at is None
    assert sale.source_payload["title"] == "指定網購加碼" and sale.reward_rules
    assert sale.fetched_at.replace(tzinfo=UTC) == datetime(2026, 9, 20, 3, tzinfo=UTC)
    # Both rows hang off one card row.
    assert await session.scalar(select(func.count()).select_from(Card)) == 1


async def test_legacy_list_json_still_imports_as_campaigns_only(session, tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps([campaign_item(id="old-1"), campaign_item(id="old-2")]), "utf-8")
    dataset = load_dataset(path)
    assert dataset["base_benefits"] == []

    stats = await import_dataset(session, dataset)
    assert stats["benefits"] == {"created": 0, "updated": 0, "skipped": 0, "total": 0}
    assert stats["sales"]["created"] == 2
    assert await session.scalar(select(func.count()).select_from(CardBenefit)) == 0


async def test_existing_general_campaigns_are_not_promoted_to_base_benefits(session, tmp_path):
    """The stale 2025 'general' campaigns stay campaigns: nothing proves they still apply."""
    legacy = [
        campaign_item(
            id="ctbc-linepay-2025-general",
            bank="中國信託商業銀行",
            card="LINE Pay 聯名卡",
            title="國內外一般消費基本回饋",
            campaign_start="2025-01-01",
            campaign_end="2025-12-31",
        )
    ]
    path = tmp_path / "old.json"
    path.write_text(json.dumps(legacy), "utf-8")
    await import_dataset(session, load_dataset(path))
    assert await session.scalar(select(func.count()).select_from(CardBenefit)) == 0
    sale = await session.get(Sale, "ctbc-linepay-2025-general")
    assert sale.campaign_end == date(2025, 12, 31)


def test_canonical_json_in_the_repo_is_still_loadable():
    dataset = load_dataset()
    assert dataset["campaigns"] or dataset["base_benefits"]
