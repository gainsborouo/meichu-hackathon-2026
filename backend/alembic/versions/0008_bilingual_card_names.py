"""新增雙語卡名，並移除第一銀行與遠東商銀卡片。

Revision ID: 0008
Revises: 0007
"""

import csv
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

CARD_NAMES_PATH = Path(__file__).resolve().parents[1] / "data" / "0008_card_names.csv"
CARD_NAME_FIELDS = (
    "artwork_id",
    "bank_name",
    "name",
    "issuer_en",
    "name_en",
    "official_source_url",
)
RETIRED_BANK_NAMES = ("第一銀行", "遠東商銀")


def _load_card_names() -> list[dict[str, str | None]]:
    with CARD_NAMES_PATH.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != CARD_NAME_FIELDS:
            raise RuntimeError("0008 card names have an invalid header")

        rows: list[dict[str, str | None]] = []
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise RuntimeError(f"0008 card names line {line} is malformed")
            if not row["bank_name"] or not row["name"] or not row["issuer_en"]:
                raise RuntimeError(f"0008 card names line {line} is missing required data")
            rows.append(
                {
                    "artwork_id": row["artwork_id"] or None,
                    "bank_name": row["bank_name"],
                    "name": row["name"],
                    "issuer_en": row["issuer_en"],
                    "name_en": row["name_en"] or None,
                }
            )
    return rows


def _literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def _card_name_update_sql(row: dict[str, str | None]) -> str:
    if row["artwork_id"] is not None:
        match = f"artwork_id = {_literal(row['artwork_id'])}"
    else:
        match = f"bank_name = {_literal(row['bank_name'])} AND name = {_literal(row['name'])}"
    return (
        "UPDATE cards SET "
        f"issuer_en = {_literal(row['issuer_en'])}, "
        f"name_en = {_literal(row['name_en'])} "
        f"WHERE {match}"
    )


def upgrade() -> None:
    op.add_column("cards", sa.Column("name_en", sa.Text(), nullable=True))
    for row in _load_card_names():
        op.execute(_card_name_update_sql(row))

    retired_banks = ", ".join(_literal(name) for name in RETIRED_BANK_NAMES)
    op.execute(
        "DELETE FROM user_cards WHERE card_id IN "
        f"(SELECT id FROM cards WHERE bank_name IN ({retired_banks}))"
    )
    op.execute(f"DELETE FROM cards WHERE bank_name IN ({retired_banks})")


def downgrade() -> None:
    # 已刪除的持卡紀錄與帳單分析無法安全重建，降版只移除新增欄位。
    op.drop_column("cards", "name_en")
