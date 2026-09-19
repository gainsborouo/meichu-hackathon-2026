"""Migration 0006: duplicate-card repair, and the model-level identity constraints."""

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Card, CardBenefit, Sale, User, UserCard

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0006_card_identity.py"
OLD, NEW = datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("migration_0006", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db():
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session, engine
    engine.dispose()


def make_card(session, bank, name, key, artwork=None):
    card = Card(bank_name=bank, name=name, catalog_key=key, artwork_id=artwork)
    session.add(card)
    session.flush()
    return card


def benefit(session, card, title, fetched_at, reward="1%"):
    row = CardBenefit(
        card_id=card.id, title=title, reward=reward, source_url="https://x", fetched_at=fetched_at
    )
    session.add(row)
    session.flush()
    return row


def repair(migration, engine, session):
    session.commit()
    with engine.begin() as conn:
        migration._repair_duplicates(conn, migration._identity())
    session.expire_all()


def test_ctbc_duplicates_merge_into_the_canonical_card(migration, db):
    session, engine = db
    canonical = make_card(
        session, "中國信託銀行", "中國信託 LINE Pay 信用卡", "ctbc-linepay", "ctbc-linepay-ve8710"
    )
    dup_a = make_card(session, "中國信託銀行", "LINE Pay 聯名卡", "legacy-a")
    dup_b = make_card(session, "中國信託商業銀行", "LINE Pay 聯名卡", "legacy-b")
    unrelated = make_card(session, "玉山銀行", "Unicard", "adhoc-unrelated")

    for sid, card in (("s-a", dup_a), ("s-b", dup_b), ("s-x", unrelated)):
        session.add(Sale(id=sid, card_id=card.id, bank_name="b", card_name="c", title="t"))
    benefit(session, dup_a, "只在重複卡上", OLD)
    benefit(session, dup_a, "衝突：重複卡較新", NEW, reward="來自重複卡（新）")
    benefit(session, canonical, "衝突：重複卡較新", OLD, reward="來自正規卡（舊）")
    benefit(session, dup_b, "衝突：正規卡較新", OLD, reward="來自重複卡（舊）")
    benefit(session, canonical, "衝突：正規卡較新", NEW, reward="來自正規卡（新）")
    session.flush()

    repair(migration, engine, session)

    sales = {s.id: s.card_id for s in session.scalars(sa.select(Sale))}
    assert sales == {"s-a": canonical.id, "s-b": canonical.id, "s-x": unrelated.id}

    rows = {b.title: b for b in session.scalars(sa.select(CardBenefit))}
    assert {b.card_id for b in rows.values()} == {canonical.id}
    assert set(rows) == {"只在重複卡上", "衝突：重複卡較新", "衝突：正規卡較新"}
    # unique(card_id, title) conflicts keep the more recently fetched row.
    assert rows["衝突：重複卡較新"].reward == "來自重複卡（新）"
    assert rows["衝突：正規卡較新"].reward == "來自正規卡（新）"

    # Nothing references the duplicates any more, so they are gone; unrelated cards are not.
    remaining = {c.name for c in session.scalars(sa.select(Card))}
    assert "LINE Pay 聯名卡" not in remaining
    assert {"中國信託 LINE Pay 信用卡", "Unicard"} <= remaining


def test_holders_are_moved_but_a_user_card_is_never_deleted(migration, db):
    session, engine = db
    canonical = make_card(
        session, "中國信託銀行", "中國信託 LINE Pay 信用卡", "ctbc-linepay", "ctbc-linepay-ve8710"
    )
    dup = make_card(session, "中國信託銀行", "LINE Pay 聯名卡", "legacy-a")
    only_dup = User(google_uid="a", email="a@x.com")
    both = User(google_uid="b", email="b@x.com")
    session.add_all([only_dup, both])
    session.flush()
    session.add_all(
        [
            UserCard(user_id=only_dup.id, card_id=dup.id),
            UserCard(user_id=both.id, card_id=dup.id),
            UserCard(user_id=both.id, card_id=canonical.id),
        ]
    )
    session.flush()
    held_before = session.scalar(sa.select(sa.func.count()).select_from(UserCard))

    repair(migration, engine, session)

    assert session.scalar(sa.select(sa.func.count()).select_from(UserCard)) == held_before
    by_user = {}
    for uc in session.scalars(sa.select(UserCard)):
        by_user.setdefault(uc.user_id, set()).add(uc.card_id)
    assert by_user[only_dup.id] == {canonical.id}  # re-pointed to the canonical card
    # This user already held both: their duplicate user_card stays, so the duplicate card must too.
    assert by_user[both.id] == {canonical.id, dup.id}
    assert session.get(Card, dup.id) is not None


def test_repair_is_idempotent_and_needs_a_canonical_card(migration, db):
    session, engine = db
    dup = make_card(session, "中國信託銀行", "LINE Pay 聯名卡", "legacy-a")
    dup_id = dup.id
    session.add(Sale(id="s", card_id=dup_id, bank_name="b", card_name="c", title="t"))
    session.flush()

    repair(migration, engine, session)  # no canonical card exists: nothing to merge into
    assert session.get(Sale, "s").card_id == dup_id and session.get(Card, dup_id) is not None

    canonical = make_card(
        session, "中國信託銀行", "中國信託 LINE Pay 信用卡", "ctbc-linepay", "ctbc-linepay-ve8710"
    )
    repair(migration, engine, session)
    repair(migration, engine, session)
    assert session.get(Sale, "s").card_id == canonical.id
    assert session.get(Card, dup_id) is None


def test_identity_file_seeds_the_migration_consistently(migration):
    rows = {r["artwork_id"]: r for r in migration._identity()}
    ctbc = rows["ctbc-linepay-ve8710"]
    assert ctbc["catalog_key"] == "ctbc-linepay" and ctbc["crawler_enabled"] is True
    assert ("中國信託銀行", "LINE Pay 聯名卡") in ctbc["aliases"]
    assert ("中國信託商業銀行", "LINE Pay 聯名卡") in ctbc["aliases"]
    assert len({r["catalog_key"] for r in rows.values()}) == len(rows)


def test_catalog_key_is_unique_and_never_null(db):
    session, _ = db
    make_card(session, "A", "one", "same-key")
    session.commit()
    session.add(Card(bank_name="B", name="two", catalog_key="same-key"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    a, b = Card(bank_name="C", name="x"), Card(bank_name="C", name="y")  # no key given
    session.add_all([a, b])
    session.flush()
    assert a.catalog_key.startswith("adhoc-") and a.catalog_key != b.catalog_key
    assert uuid.UUID(a.catalog_key.removeprefix("adhoc-"))

    # The ORM fills a missing key; the database itself still refuses NULL.
    with pytest.raises(IntegrityError):
        session.execute(
            sa.insert(Card.__table__).values(
                id=uuid.uuid4(), bank_name="D", name="z", catalog_key=None
            )
        )
