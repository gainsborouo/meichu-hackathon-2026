import csv
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from app.models import Card, Sale
from app.services.card_catalog import (
    ALIASES_BY_ARTWORK_ID,
    CARD_NAME_FIELDS,
    CARD_UUID_NAMESPACE,
    CATALOG_FIELDS,
    DEFAULT_CARD_CATALOG_PATH,
    DEFAULT_CARD_NAMES_PATH,
    RETIRED_BANK_NAMES,
    import_card_catalog,
    load_card_catalog,
    load_card_names,
)
from app.services.sales_import import import_campaigns, load_campaigns


def test_versioned_catalog_has_expected_shape() -> None:
    rows = load_card_catalog()
    assert len(rows) == 47
    assert len({row.artwork_id for row in rows}) == 47
    assert len({(row.bank_name, row.name) for row in rows}) == 47
    assert sum(row.name_en is not None for row in rows) == 38
    assert not ({row.bank_name for row in rows} & RETIRED_BANK_NAMES)
    assert rows[0].artwork_id == "ctbc-linepay-ve8710"
    assert rows[1].variant is None
    assert {row.image_is_composite for row in rows} == {False, True}

    with DEFAULT_CARD_CATALOG_PATH.open(encoding="utf-8", newline="") as source:
        assert tuple(next(csv.reader(source))) == CATALOG_FIELDS


def test_versioned_card_names_have_official_sources() -> None:
    rows = load_card_names()
    assert len(rows) == 54
    assert sum(row.artwork_id is not None for row in rows) == 47
    assert sum(row.name_en is not None for row in rows) == 41
    assert sum(row.name_en is None for row in rows) == 13
    assert all(row.official_source_url for row in rows if row.name_en is not None)

    with DEFAULT_CARD_NAMES_PATH.open(encoding="utf-8", newline="") as source:
        assert tuple(next(csv.reader(source))) == CARD_NAME_FIELDS


@pytest.mark.parametrize(
    ("header", "row", "message"),
    [
        ("artwork_id", "x", "invalid header"),
        (
            ",".join(CATALOG_FIELDS),
            "x,B,N,D,I,,,,https://example.com/x.png,yes",
            "must be true or false",
        ),
        (
            ",".join(CATALOG_FIELDS),
            "x,B,N,D,I,,,,https://example.com/x.png,false\n"
            "x,B2,N2,D2,I2,,,,https://example.com/y.png,false",
            "duplicate artwork_id",
        ),
        (
            ",".join(CATALOG_FIELDS),
            "x,B,N,D,I,,,,https://example.com/x.png,false\n"
            "y,B,N,D2,I2,,,,https://example.com/y.png,false",
            "duplicate bank_name and name",
        ),
    ],
)
def test_catalog_validation_is_all_or_nothing(tmp_path, header, row, message) -> None:
    path = tmp_path / "cards.csv"
    path.write_text(f"{header}\n{row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_card_catalog(path)


async def test_import_is_repeatable_and_uses_stable_uuids(session) -> None:
    rows = load_card_catalog()
    first = await import_card_catalog(session, rows)
    ids = {card.artwork_id: card.id for card in await session.scalars(select(Card))}
    second = await import_card_catalog(session, rows)

    assert first == {"created": 47, "updated": 0, "total": 47}
    assert second == {"created": 0, "updated": 47, "total": 47}
    assert await session.scalar(select(func.count()).select_from(Card)) == 47
    assert ids == {card.artwork_id: card.id for card in await session.scalars(select(Card))}
    assert ids[rows[0].artwork_id] == uuid.uuid5(CARD_UUID_NAMESPACE, rows[0].artwork_id)


async def test_import_updates_aliases_without_changing_identity_or_extra_rows(session) -> None:
    alias_cards = []
    for bank_name, name in ALIASES_BY_ARTWORK_ID.values():
        card = Card(bank_name=bank_name, name=name)
        session.add(card)
        alias_cards.append(card)
    extra = Card(bank_name="額外銀行", name="CSV 外卡片")
    session.add(extra)
    await session.flush()
    original_ids = {card.id for card in alias_cards}

    await import_card_catalog(session, load_card_catalog())

    assert {card.id for card in alias_cards} == original_ids
    assert {
        (card.bank_name, card.name, card.artwork_id) for card in alias_cards
    } == {
        (*alias, artwork_id) for artwork_id, alias in ALIASES_BY_ARTWORK_ID.items()
    }
    assert await session.get(Card, extra.id) is extra
    assert await session.scalar(select(func.count()).select_from(Card)) == 48


async def test_import_rejects_artwork_and_name_matching_different_cards(session) -> None:
    row = load_card_catalog()[0]
    artwork_card = Card(bank_name="其他銀行", name="其他卡", artwork_id=row.artwork_id)
    name_card = Card(bank_name=row.bank_name, name=row.name)
    session.add_all([artwork_card, name_card])
    await session.flush()

    with pytest.raises(ValueError, match=row.artwork_id):
        await import_card_catalog(session, [replace(row, display_name="不應寫入")])
    assert artwork_card.display_name is None
    assert name_card.artwork_id is None


async def test_import_transaction_rolls_back_rows_before_a_conflict(session) -> None:
    first, conflict = load_card_catalog()[:2]
    first_card = Card(bank_name=first.bank_name, name=first.name)
    session.add_all(
        [
            first_card,
            Card(bank_name="其他銀行", name="其他卡", artwork_id=conflict.artwork_id),
            Card(bank_name=conflict.bank_name, name=conflict.name),
        ]
    )
    await session.commit()

    with pytest.raises(ValueError, match=conflict.artwork_id):
        async with session.begin():
            await import_card_catalog(session, [first, conflict])

    await session.refresh(first_card)
    assert first_card.artwork_id is None


async def test_campaign_import_reuses_catalog_aliases_without_creating_cards(session) -> None:
    await import_card_catalog(session, load_card_catalog())
    await import_campaigns(session, load_campaigns())
    cards = list(await session.scalars(select(Card)))
    ctbc = next(card for card in cards if card.artwork_id == "ctbc-linepay-ve8710")
    assert not any(
        card.bank_name == "中國信託商業銀行" and card.name == "LINE Pay 聯名卡"
        for card in cards
    )
    sale_count = await session.scalar(
        select(func.count()).select_from(Sale).where(Sale.card_id == ctbc.id)
    )
    assert sale_count
    assert ctbc.name_en == "CTBC LINE Pay card"
    assert ctbc.issuer_en == "CTBC Bank"
    assert len(cards) == 47
