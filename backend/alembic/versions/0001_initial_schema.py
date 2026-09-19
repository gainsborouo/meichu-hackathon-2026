"""initial schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def _ts(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=NOW, nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("google_uid", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("calendar_push_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("latest_spend_report", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("google_uid", name="uq_users_google_uid"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "cards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bank_name", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_cards"),
        sa.UniqueConstraint("bank_name", "name", name="uq_cards_bank_name"),
    )
    op.create_table(
        "user_cards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        _ts("created_at"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_cards_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name="fk_user_cards_card_id_cards", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_cards"),
        sa.UniqueConstraint("user_id", "card_id", name="uq_user_cards_user_id"),
    )
    op.create_table(
        "user_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_card_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_month", sa.Date(), nullable=False),
        sa.Column("report", sa.Text(), nullable=False),
        sa.Column("analysis_data", postgresql.JSONB(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("EXTRACT(DAY FROM analysis_month) = 1", name="month_first_day"),
        sa.ForeignKeyConstraint(
            ["user_card_id"],
            ["user_cards.id"],
            name="fk_user_analyses_user_card_id_user_cards",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_analyses"),
        sa.UniqueConstraint("user_card_id", "analysis_month", name="uq_user_analyses_user_card_id"),
    )
    op.create_index(
        "ix_user_analyses_user_card_id_analysis_month",
        "user_analyses",
        ["user_card_id", sa.text("analysis_month DESC")],
    )
    op.create_table(
        "sales",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=True),
        sa.Column("bank_name", sa.Text(), nullable=False),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("reward", sa.Text(), nullable=True),
        sa.Column("conditions", sa.Text(), nullable=True),
        sa.Column("campaign_period", sa.Text(), nullable=True),
        sa.Column("register_url", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column(
            "source_payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        _ts("fetched_at"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name="fk_sales_card_id_cards", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sales"),
    )
    op.create_index("ix_sales_card_id", "sales", ["card_id"])
    op.create_table(
        "user_sales",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("sale_id", sa.Text(), nullable=False),
        _ts("notified_at"),
        sa.Column("notification_channel", sa.Text(), server_default="calendar", nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_sales_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sale_id"], ["sales.id"], name="fk_user_sales_sale_id_sales", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_sales"),
        sa.UniqueConstraint("user_id", "sale_id", name="uq_user_sales_user_id"),
    )
    op.create_index("ix_user_sales_user_id", "user_sales", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_sales")
    op.drop_table("sales")
    op.drop_table("user_analyses")
    op.drop_table("user_cards")
    op.drop_table("cards")
    op.drop_table("users")
