"""Enable crawler metadata for common catalog cards.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

cards = sa.table(
    "cards",
    sa.column("artwork_id", sa.Text),
    sa.column("crawler_enabled", sa.Boolean),
    sa.column("search_bank_name", sa.Text),
    sa.column("search_card_name", sa.Text),
)

COMMON_CRAWLER_CARDS = (
    ("taishin-richart", "台新銀行", "Richart 信用卡"),
    ("taishin-pxmart", "台新銀行", "大全聯信用卡"),
    ("esun-pi-card", "玉山銀行", "Pi 拍錢包信用卡"),
)


def upgrade() -> None:
    for artwork_id, bank_name, card_name in COMMON_CRAWLER_CARDS:
        op.execute(
            cards.update()
            .where(cards.c.artwork_id == artwork_id)
            .values(
                crawler_enabled=True,
                search_bank_name=bank_name,
                search_card_name=card_name,
            )
        )


def downgrade() -> None:
    for artwork_id, _bank_name, _card_name in COMMON_CRAWLER_CARDS:
        op.execute(
            cards.update()
            .where(cards.c.artwork_id == artwork_id)
            .values(
                crawler_enabled=False,
                search_bank_name=None,
                search_card_name=None,
            )
        )
