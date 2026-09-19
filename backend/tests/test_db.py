import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Card, Sale, User, UserAnalysis
from app.repositories import analyses as analyses_repo
from app.repositories import cards as cards_repo
from app.repositories import sales as sales_repo
from app.services.notifications import record_sale_notification
from app.services.sales_import import import_campaigns, load_campaigns
from app.services.spend_report import refresh_latest_spend_report
from app.services.users import upsert_user

BACKEND = Path(__file__).resolve().parents[1]


def test_migration_renders_postgres_sql() -> None:
    env = {**os.environ, "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db"}
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for needle in (
        "CREATE TABLE users",
        "CREATE TABLE user_analyses",
        "CREATE TABLE user_sales",
        "JSONB",
        "EXTRACT(DAY FROM analysis_month) = 1",
        "analysis_month DESC",
        "ON DELETE RESTRICT",
        "CREATE TABLE calendar_events",
        "reward_rules",
        "registration_campaigns_enabled",
        "campaign_start",
        "official_verified_at",
        "CREATE TABLE card_benefits",
        "uq_cards_catalog_key",
        "search_bank_name",
        "ctbc-linepay",
        "ck_card_benefits_valid_range",
        "uq_card_benefits_card_id",
        "google_refresh_token",
        "ON DELETE SET NULL",
        "artwork_id",
        "official_image_url",
        "uq_cards_artwork_id",
        "DO $card_catalog$",
        "ctbc-linepay-ve8710",
    ):
        assert needle in out


async def test_upsert_user_is_idempotent_and_syncs_email(session) -> None:
    a = await upsert_user(session, google_uid="g1", email="a@x.com")
    b = await upsert_user(session, google_uid="g1", email="new@x.com")
    assert a.id == b.id and b.email == "new@x.com" and b.registration_campaigns_enabled is False


async def test_sales_import_is_repeatable_and_keeps_payload(session, catalog) -> None:
    from app.services.card_identity import resolve_source_card_key
    from app.services.sales_import import source_names

    items = load_campaigns()
    resolvable = [i for i in items if resolve_source_card_key(*source_names(i))]
    assert resolvable and len(resolvable) < len(items), "legacy data mixes known and unknown cards"

    cards_before = await session.scalar(select(func.count()).select_from(Card))
    first = await import_campaigns(session, items)
    second = await import_campaigns(session, items)
    assert first == {
        "created": len(resolvable),
        "updated": 0,
        "skipped": len(items) - len(resolvable),
        "total": len(items),
    }
    assert second["created"] == 0 and second["updated"] == len(resolvable)
    sale = await session.get(Sale, resolvable[0]["id"])
    assert sale.source_payload == resolvable[0]
    assert sale.card_id is not None
    assert await session.scalar(select(func.count()).select_from(Card)) == cards_before


async def test_analysis_month_normalised_and_check_constraint(session) -> None:
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    card = await cards_repo.get_or_create_card(session, bank_name="B", name="C")
    uc, _ = await cards_repo.add_user_card(session, user.id, card.id)
    row = await analyses_repo.upsert_analysis(
        session,
        user_card_id=uc.id,
        analysis_month=dt.date(2026, 9, 17),
        report="r",
        analysis_data=None,
    )
    assert row.analysis_month == dt.date(2026, 9, 1)
    session.add(UserAnalysis(user_card_id=uc.id, analysis_month=dt.date(2026, 8, 15), report="x"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_spend_report_uses_latest_three_months_and_keeps_history(session) -> None:
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    card = await cards_repo.get_or_create_card(session, bank_name="B", name="C")
    uc, _ = await cards_repo.add_user_card(session, user.id, card.id)
    for m in (5, 6, 7, 8):
        await analyses_repo.upsert_analysis(
            session,
            user_card_id=uc.id,
            analysis_month=dt.date(2026, m, 1),
            report=f"m{m}",
            analysis_data={"m": m},
        )
    report = await refresh_latest_spend_report(session, user.id)
    # The report is now a merged summary, so assert on the months it covers
    # rather than on the raw per-card text it used to concatenate.
    assert "2026-08" in report and "2026-06" in report and "2026-05" not in report
    assert user.latest_spend_report == report
    assert len(await analyses_repo.list_for_user_card(session, uc.id, limit=10)) == 4
    assert len(await analyses_repo.list_for_user_card(session, uc.id)) == 3


async def test_user_sale_unique_and_user_card_idempotent(session, catalog) -> None:
    await import_campaigns(session, load_campaigns()[:2])
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    sale = (await sales_repo.list_sales(session))[0]
    first, created1 = await record_sale_notification(session, user.id, sale.id)
    _, created2 = await record_sale_notification(session, user.id, sale.id)
    assert created1 and not created2
    assert first.notified_at is not None
    assert len(await sales_repo.list_user_sales(session, user.id)) == 1
    assert isinstance(await session.get(User, user.id), User)


async def test_import_fills_campaign_dates_and_leaves_verification_null(session, catalog) -> None:
    from datetime import date

    from app.services.sales_import import parse_date

    items = [
        {
            **load_campaigns()[0],
            "id": "dated",
            "campaign_start": "2026-10-01",
            "campaign_end": "2026-12-31",
        },
        {**load_campaigns()[0], "id": "no-dates", "campaign_start": None, "campaign_end": None},
        {
            **load_campaigns()[0],
            "id": "bad-dates",
            "campaign_start": "next spring",
            "campaign_end": "2026-13-45",
        },
    ]
    await import_campaigns(session, items)
    await import_campaigns(session, items)  # idempotent
    dated = await session.get(Sale, "dated")
    none = await session.get(Sale, "no-dates")
    bad = await session.get(Sale, "bad-dates")
    assert (dated.campaign_start, dated.campaign_end) == (date(2026, 10, 1), date(2026, 12, 31))
    assert (none.campaign_start, none.campaign_end) == (None, None)
    assert (bad.campaign_start, bad.campaign_end) == (None, None)
    assert dated.official_verified_at is None
    assert dated.source_payload == items[0]
    assert parse_date(None) is None and parse_date("2026-02-30") is None


def test_migration_0004_backfills_dates_from_source_payload() -> None:
    import importlib.util
    from datetime import date

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = BACKEND / "alembic" / "versions" / "0004_registration_mode_and_sale_dates.py"
    spec = importlib.util.spec_from_file_location("migration_0004", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    sales = sa.Table(
        "sales",
        meta,
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("source_payload", sa.JSON),
        sa.Column("campaign_start", sa.Date),
        sa.Column("campaign_end", sa.Date),
    )
    meta.create_all(engine)
    rows = [
        ("ok", {"campaign_start": "2025-01-01", "campaign_end": "2025-12-31"}),
        ("only-end", {"campaign_start": None, "campaign_end": "2026-06-30"}),
        ("bad", {"campaign_start": "next spring", "campaign_end": "2026-13-45"}),
        ("none", {}),
        ("not-a-dict", ["weird"]),
    ]
    with engine.begin() as conn:
        conn.execute(sales.insert(), [{"id": i, "source_payload": p} for i, p in rows])
        with Operations.context(MigrationContext.configure(conn)):
            module._backfill_campaign_dates()
        got = {r.id: (r.campaign_start, r.campaign_end) for r in conn.execute(sales.select())}

    assert got["ok"] == (date(2025, 1, 1), date(2025, 12, 31))
    assert got["only-end"] == (None, date(2026, 6, 30))
    assert got["bad"] == (None, None) and got["none"] == (None, None)
    assert got["not-a-dict"] == (None, None)


def test_alembic_has_a_single_head_and_card_identity_chains_from_0005() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert script.get_heads() == ["0006"]
    assert script.get_revision("0006").down_revision == "0005"
