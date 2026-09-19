"""card_benefits: a card's standing rewards, separate from limited campaigns

Revision ID: 0005
Revises: 0004

No data is moved here. Existing `sales` rows (including expired general campaigns) stay
where they are: nothing about an old campaign is reliable evidence that the same reward
still applies today. card_benefits is filled by `python -m app.cli.import_sales` from a
crawler run that has opened and read the bank's official page.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "card_benefits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("reward", sa.Text(), nullable=False),
        sa.Column("conditions", sa.Text(), nullable=True),
        sa.Column(
            "reward_rules",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("effective_start", sa.Date(), nullable=True),
        sa.Column("effective_end", sa.Date(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column(
            "source_payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("official_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint(
            "effective_end IS NULL OR effective_start IS NULL OR effective_end >= effective_start",
            name="valid_range",
        ),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name="fk_card_benefits_card_id_cards", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_card_benefits"),
        sa.UniqueConstraint("card_id", "title", name="uq_card_benefits_card_id"),
    )
    op.create_index("ix_card_benefits_card_id", "card_benefits", ["card_id"])


def downgrade() -> None:
    op.drop_index("ix_card_benefits_card_id", table_name="card_benefits")
    op.drop_table("card_benefits")
