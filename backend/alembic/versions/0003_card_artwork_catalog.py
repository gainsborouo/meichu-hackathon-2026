"""Card artwork catalog.

Revision ID: 0003
Revises: 0002
"""

import csv
import uuid
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "0003_card_catalog.csv"
CATALOG_FIELDS = (
    "artwork_id",
    "bank_name",
    "name",
    "display_name",
    "issuer_en",
    "variant",
    "network",
    "tier",
    "official_image_url",
    "image_is_composite",
)
CARD_UUID_NAMESPACE = uuid.UUID("f8cbb032-9362-5de4-b3b1-3f234b1f199e")
ALIASES_BY_ARTWORK_ID = {
    "ctbc-linepay-ve8710": ("中國信託商業銀行", "LINE Pay 聯名卡"),
    "fubon-momo": ("台北富邦銀行", "momo卡"),
    "esun-unicard-white": ("玉山銀行", "Unicard"),
    "esun-kumamon-ku01": ("玉山銀行", "玉山熊本熊卡"),
}


def _load_catalog() -> list[dict[str, str | bool | None]]:
    with CATALOG_PATH.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != CATALOG_FIELDS:
            raise RuntimeError("0003 card catalog has an invalid header")
        rows: list[dict[str, str | bool | None]] = []
        artwork_ids: set[str] = set()
        card_names: set[tuple[str, str]] = set()
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise RuntimeError(f"0003 card catalog line {line} is malformed")
            if any(not row[field] for field in CATALOG_FIELDS[:5]) or not row[
                "official_image_url"
            ]:
                raise RuntimeError(f"0003 card catalog line {line} is missing required data")
            if row["image_is_composite"] not in {"true", "false"}:
                raise RuntimeError(f"0003 card catalog line {line} has an invalid boolean")
            key = (row["bank_name"], row["name"])
            if row["artwork_id"] in artwork_ids or key in card_names:
                raise RuntimeError(f"0003 card catalog line {line} is duplicated")
            artwork_ids.add(row["artwork_id"])
            card_names.add(key)
            rows.append(
                {
                    **row,
                    "variant": row["variant"] or None,
                    "network": row["network"] or None,
                    "tier": row["tier"] or None,
                    "image_is_composite": row["image_is_composite"] == "true",
                }
            )
    return rows


def _literal(value: str | bool | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return "'" + value.replace("'", "''") + "'"


def _catalog_upsert_sql(row: dict[str, str | bool | None]) -> str:
    artwork_id = str(row["artwork_id"])
    exact = (
        f"bank_name = {_literal(row['bank_name'])} AND name = {_literal(row['name'])}"
    )
    alias = ALIASES_BY_ARTWORK_ID.get(artwork_id)
    match = exact
    if alias is not None:
        match += (
            f" OR (bank_name = {_literal(alias[0])} AND name = {_literal(alias[1])})"
        )
    assignments = ",\n            ".join(
        f"{field} = {_literal(row[field])}"
        for field in (
            "artwork_id",
            "display_name",
            "issuer_en",
            "variant",
            "network",
            "tier",
            "official_image_url",
            "image_is_composite",
        )
    )
    columns = ", ".join(CATALOG_FIELDS)
    values = ", ".join(_literal(row[field]) for field in CATALOG_FIELDS)
    card_id = uuid.uuid5(CARD_UUID_NAMESPACE, artwork_id)
    return f"""
DO $card_catalog$
DECLARE
    artwork_card uuid;
    name_card uuid;
    name_artwork text;
BEGIN
    SELECT id INTO artwork_card FROM cards WHERE artwork_id = {_literal(artwork_id)};
    SELECT id, artwork_id INTO name_card, name_artwork
      FROM cards
     WHERE {match}
     ORDER BY CASE WHEN {exact} THEN 0 ELSE 1 END
     LIMIT 1;

    IF name_artwork IS NOT NULL AND name_artwork <> {_literal(artwork_id)} THEN
        RAISE EXCEPTION 'card catalog conflict for %', {_literal(artwork_id)};
    END IF;
    IF artwork_card IS NOT NULL AND name_card IS NOT NULL AND artwork_card <> name_card THEN
        RAISE EXCEPTION 'card catalog conflict for %', {_literal(artwork_id)};
    END IF;

    IF artwork_card IS NOT NULL THEN
        UPDATE cards SET
            {assignments}
        WHERE id = artwork_card;
    ELSIF name_card IS NOT NULL THEN
        UPDATE cards SET
            {assignments}
        WHERE id = name_card;
    ELSE
        INSERT INTO cards (id, {columns})
        VALUES ('{card_id}', {values});
    END IF;
END
$card_catalog$;
"""


def upgrade() -> None:
    op.add_column("cards", sa.Column("artwork_id", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("issuer_en", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("variant", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("network", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("tier", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("official_image_url", sa.Text(), nullable=True))
    op.add_column("cards", sa.Column("image_is_composite", sa.Boolean(), nullable=True))
    op.create_unique_constraint("uq_cards_artwork_id", "cards", ["artwork_id"])
    for row in _load_catalog():
        op.execute(_catalog_upsert_sql(row))


def downgrade() -> None:
    op.drop_constraint("uq_cards_artwork_id", "cards", type_="unique")
    for column in (
        "image_is_composite",
        "official_image_url",
        "tier",
        "network",
        "variant",
        "issuer_en",
        "display_name",
        "artwork_id",
    ):
        op.drop_column("cards", column)
