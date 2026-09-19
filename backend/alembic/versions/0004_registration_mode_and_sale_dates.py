"""registration mode on users; structured dates + verification stamp on sales

Revision ID: 0004
Revises: 0003
"""

from datetime import date

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "calendar_push_enabled",
        new_column_name="registration_campaigns_enabled",
    )
    # The old flag meant "calendar push opted in"; the new one picks which kind
    # of campaign is recommended. Carrying the old values over would silently
    # switch people into registration-only mode, so start everyone at false.
    op.execute("UPDATE users SET registration_campaigns_enabled = false")

    op.add_column("sales", sa.Column("campaign_start", sa.Date(), nullable=True))
    op.add_column("sales", sa.Column("campaign_end", sa.Date(), nullable=True))
    op.add_column(
        "sales", sa.Column("official_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    _backfill_campaign_dates()


def _iso_date(value) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _backfill_campaign_dates() -> None:
    """Fill the new columns from each sale's preserved source_payload.

    Without this, existing rows keep NULL dates until someone reruns import_sales, and
    NULL dates mean "no expiry known": expired campaigns would be treated as current.
    Parsing happens in Python so one malformed date cannot abort the migration; an
    unparseable date stays NULL, exactly as the importer does.
    """
    if op.get_context().as_sql:
        # A SQL script cannot run Python. Say so in the script instead of silently
        # leaving expired campaigns undated; import_sales fills them idempotently.
        op.execute("-- campaign_start/campaign_end NOT backfilled offline: run import_sales")
        return
    bind = op.get_bind()
    sales = sa.table(
        "sales",
        sa.column("id", sa.Text),
        sa.column("source_payload", sa.JSON),
        sa.column("campaign_start", sa.Date),
        sa.column("campaign_end", sa.Date),
    )
    for sale_id, payload in bind.execute(sa.select(sales.c.id, sales.c.source_payload)).all():
        if not isinstance(payload, dict):
            continue
        start, end = (
            _iso_date(payload.get("campaign_start")),
            _iso_date(payload.get("campaign_end")),
        )
        if start is None and end is None:
            continue
        bind.execute(
            sales.update()
            .where(sales.c.id == sale_id)
            .values(campaign_start=start, campaign_end=end)
        )


def downgrade() -> None:
    op.drop_column("sales", "official_verified_at")
    op.drop_column("sales", "campaign_end")
    op.drop_column("sales", "campaign_start")
    op.alter_column(
        "users",
        "registration_campaigns_enabled",
        new_column_name="calendar_push_enabled",
    )
