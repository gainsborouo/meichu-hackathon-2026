"""reward rules, calendar OAuth token, calendar events

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NOW = sa.text("now()")
JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _ts(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=NOW, nullable=nullable)


def upgrade() -> None:
    # Structured form of sales.reward, so /search can rank without parsing prose
    # on every request. Backfilled by `python -m app.cli.import_sales`.
    op.add_column(
        "sales",
        sa.Column(
            "reward_rules", JSON, server_default=sa.text("'[]'"), nullable=False
        ),
    )
    # Firebase sign-in carries no Calendar scope; connecting a calendar is a
    # separate consent, and this holds the resulting refresh token.
    op.add_column("users", sa.Column("google_refresh_token", sa.Text(), nullable=True))

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("sale_id", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), server_default="google", nullable=False),
        sa.Column("provider_event_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("all_day", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("html_link", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        # The reminder outlives the campaign: keep the event, drop the link.
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "provider_event_id"),
    )
    op.create_index("ix_calendar_events_user_id", "calendar_events", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_calendar_events_user_id", table_name="calendar_events")
    op.drop_table("calendar_events")
    op.drop_column("users", "google_refresh_token")
    op.drop_column("sales", "reward_rules")
