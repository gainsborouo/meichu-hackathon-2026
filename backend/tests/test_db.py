import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
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
        "google_refresh_token",
        "ON DELETE SET NULL",
    ):
        assert needle in out


async def test_upsert_user_is_idempotent_and_syncs_email(session) -> None:
    a = await upsert_user(session, google_uid="g1", email="a@x.com")
    b = await upsert_user(session, google_uid="g1", email="new@x.com")
    assert a.id == b.id and b.email == "new@x.com" and b.calendar_push_enabled is False


async def test_sales_import_is_repeatable_and_keeps_payload(session) -> None:
    items = load_campaigns()
    first = await import_campaigns(session, items)
    second = await import_campaigns(session, items)
    assert first == {"created": len(items), "updated": 0, "total": len(items)}
    assert second["created"] == 0 and second["updated"] == len(items)
    sale = await session.get(Sale, items[0]["id"])
    assert sale.source_payload == items[0]
    assert sale.bank_name == items[0]["bank"] and sale.card_id is not None
    assert len((await session.scalars(select(Card))).all()) <= len(items)


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
    assert "m8" in report and "m6" in report and "m5" not in report
    assert user.latest_spend_report == report
    assert len(await analyses_repo.list_for_user_card(session, uc.id, limit=10)) == 4
    assert len(await analyses_repo.list_for_user_card(session, uc.id)) == 3


async def test_user_sale_unique_and_user_card_idempotent(session) -> None:
    await import_campaigns(session, load_campaigns()[:2])
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    sale = (await sales_repo.list_sales(session))[0]
    first, created1 = await record_sale_notification(session, user.id, sale.id)
    _, created2 = await record_sale_notification(session, user.id, sale.id)
    assert created1 and not created2
    assert first.notified_at is not None
    assert len(await sales_repo.list_user_sales(session, user.id)) == 1
    assert isinstance(await session.get(User, user.id), User)
