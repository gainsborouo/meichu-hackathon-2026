"""canonical card identity: cards.catalog_key + crawler metadata, and duplicate-card repair

Revision ID: 0006
Revises: 0005

Adds cards.catalog_key (unique, NOT NULL), crawler_enabled, search_bank_name and
search_card_name, filled from alembic/data/0006_card_identity.csv. Then repairs cards that
older crawler imports created from raw bank / card spellings (for example
"中國信託銀行 / LINE Pay 聯名卡" next to the catalog's "中國信託 LINE Pay 信用卡"): their sales,
card_benefits and (where it cannot collide) user_cards move to the canonical card, and a
duplicate is deleted only when nothing references it any more.

downgrade() drops the new columns. It does not un-merge repaired duplicates: that data
was wrong and there is nothing to restore it to.
"""

import csv
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

IDENTITY_PATH = Path(__file__).resolve().parents[1] / "data" / "0006_card_identity.csv"
FIELDS = (
    "artwork_id",
    "catalog_key",
    "crawler_enabled",
    "search_bank_name",
    "search_card_name",
    "source_aliases",
)

cards = sa.table(
    "cards",
    sa.column("id", sa.Uuid),
    sa.column("bank_name", sa.Text),
    sa.column("name", sa.Text),
    sa.column("artwork_id", sa.Text),
    sa.column("catalog_key", sa.Text),
    sa.column("crawler_enabled", sa.Boolean),
    sa.column("search_bank_name", sa.Text),
    sa.column("search_card_name", sa.Text),
)
sales = sa.table("sales", sa.column("id", sa.Text), sa.column("card_id", sa.Uuid))
benefits = sa.table(
    "card_benefits",
    sa.column("id", sa.Uuid),
    sa.column("card_id", sa.Uuid),
    sa.column("title", sa.Text),
    sa.column("fetched_at", sa.DateTime(timezone=True)),
)
user_cards = sa.table(
    "user_cards",
    sa.column("id", sa.Uuid),
    sa.column("user_id", sa.Uuid),
    sa.column("card_id", sa.Uuid),
)


def _identity() -> list[dict]:
    with IDENTITY_PATH.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise RuntimeError("0006 card identity file has an invalid header")
        rows = []
        for raw in reader:
            aliases = []
            for entry in filter(None, raw["source_aliases"].split("||")):
                bank, _, card = entry.partition("::")
                aliases.append((bank, card))
            if raw["search_bank_name"] and raw["search_card_name"]:
                aliases.append((raw["search_bank_name"], raw["search_card_name"]))
            rows.append(
                {
                    "artwork_id": raw["artwork_id"],
                    "catalog_key": raw["catalog_key"],
                    "crawler_enabled": raw["crawler_enabled"] == "true",
                    "search_bank_name": raw["search_bank_name"] or None,
                    "search_card_name": raw["search_card_name"] or None,
                    "aliases": aliases,
                }
            )
        return rows


def _merge_duplicate(bind, duplicate, canonical) -> None:
    bind.execute(sales.update().where(sales.c.card_id == duplicate).values(card_id=canonical))

    for benefit in bind.execute(
        sa.select(benefits.c.id, benefits.c.title, benefits.c.fetched_at).where(
            benefits.c.card_id == duplicate
        )
    ).all():
        clash = bind.execute(
            sa.select(benefits.c.id, benefits.c.fetched_at).where(
                benefits.c.card_id == canonical, benefits.c.title == benefit.title
            )
        ).first()
        if clash is None:
            bind.execute(
                benefits.update().where(benefits.c.id == benefit.id).values(card_id=canonical)
            )
        elif benefit.fetched_at > clash.fetched_at:  # keep the more recently fetched row
            bind.execute(benefits.delete().where(benefits.c.id == clash.id))
            bind.execute(
                benefits.update().where(benefits.c.id == benefit.id).values(card_id=canonical)
            )
        else:
            bind.execute(benefits.delete().where(benefits.c.id == benefit.id))

    # Re-point a holder only where they do not already hold the canonical card. A user_card
    # is never deleted; a clashing one keeps the duplicate alive.
    for held in bind.execute(
        sa.select(user_cards.c.id, user_cards.c.user_id).where(user_cards.c.card_id == duplicate)
    ).all():
        has_canonical = bind.execute(
            sa.select(user_cards.c.id).where(
                user_cards.c.user_id == held.user_id, user_cards.c.card_id == canonical
            )
        ).first()
        if has_canonical is None:
            bind.execute(
                user_cards.update().where(user_cards.c.id == held.id).values(card_id=canonical)
            )

    still_referenced = any(
        bind.execute(sa.select(t.c.id).where(t.c.card_id == duplicate).limit(1)).first()
        for t in (user_cards, sales, benefits)
    )
    if not still_referenced:
        bind.execute(cards.delete().where(cards.c.id == duplicate))


def _repair_duplicates(bind, identity: list[dict]) -> None:
    for row in identity:
        canonical = bind.execute(
            sa.select(cards.c.id).where(cards.c.artwork_id == row["artwork_id"])
        ).scalar()
        if canonical is None:
            continue
        for bank, name in row["aliases"]:
            duplicates = (
                bind.execute(
                    sa.select(cards.c.id).where(
                        cards.c.bank_name == bank, cards.c.name == name, cards.c.id != canonical
                    )
                )
                .scalars()
                .all()
            )
            for duplicate in duplicates:
                _merge_duplicate(bind, duplicate, canonical)


def upgrade() -> None:
    op.add_column("cards", sa.Column("catalog_key", sa.Text(), nullable=True))
    op.add_column(
        "cards",
        sa.Column("crawler_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("cards", sa.Column("search_bank_name", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("search_card_name", sa.Text(), nullable=True))

    identity = _identity()
    for row in identity:
        op.execute(
            cards.update()
            .where(cards.c.artwork_id == row["artwork_id"])
            .values(
                catalog_key=row["catalog_key"],
                crawler_enabled=row["crawler_enabled"],
                search_bank_name=row["search_bank_name"],
                search_card_name=row["search_card_name"],
            )
        )
    # Everything without catalog data (duplicates, ad-hoc rows) gets a key that can never
    # collide with a catalog key.
    op.execute(
        sa.text(
            "UPDATE cards SET catalog_key = 'legacy-' || CAST(id AS TEXT) WHERE catalog_key IS NULL"
        )
    )

    if op.get_context().as_sql:
        # A SQL script cannot read rows to decide what to merge.
        op.execute("-- duplicate-card repair NOT applied offline: run alembic online")
    else:
        _repair_duplicates(op.get_bind(), identity)

    op.alter_column("cards", "catalog_key", nullable=False)
    op.create_unique_constraint("uq_cards_catalog_key", "cards", ["catalog_key"])


def downgrade() -> None:
    op.drop_constraint("uq_cards_catalog_key", "cards", type_="unique")
    op.drop_column("cards", "search_card_name")
    op.drop_column("cards", "search_bank_name")
    op.drop_column("cards", "crawler_enabled")
    op.drop_column("cards", "catalog_key")
