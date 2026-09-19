"""Real PostgreSQL: migration 0006 (identity + duplicate repair, up/down/up) and the live
refresh SSE path on the canonical CTBC card. Runs only when TEST_POSTGRES_ADMIN_URL is set."""

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.api.deps as deps
from alembic import command
from app.core.config import get_settings
from app.db.bootstrap import ALEMBIC_INI
from app.db.session import dispose_engine
from app.main import create_app
from app.services import live_refresh
from app.services.recommendation_agent import get_recommendation_agent
from tests.test_live_refresh import CTBC_URL, FakeLookup, pick_first_base
from tests.test_recommendations import FakeAgent, parse_sse

ADMIN_URL = os.getenv("TEST_POSTGRES_ADMIN_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_POSTGRES_ADMIN_URL is not configured")

P = get_settings().api_v1_prefix
REQ = {"product_name": "AirPods Pro", "store_name": "momo", "price": 7490, "currency": "TWD"}
USER_CARD_SQL = "INSERT INTO user_cards(id, user_id, card_id) VALUES (gen_random_uuid(), :u, :c)"
INSERT_DUP_CARD = (
    "INSERT INTO cards(id, bank_name, name) VALUES (:id, '中國信託銀行', 'LINE Pay 聯名卡')"
)
OLD, NEW = datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)


async def _drop(admin_url, name):
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            await conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await engine.dispose()


async def test_identity_migration_and_canonical_live_refresh(monkeypatch) -> None:
    admin = make_url(ADMIN_URL)
    if admin.drivername == "postgresql":
        admin = admin.set(drivername="postgresql+asyncpg")
    name = f"test_identity_{uuid.uuid4().hex}"
    target = admin.set(database=name)

    create = create_async_engine(admin, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with create.connect() as conn:
        await conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    await create.dispose()

    monkeypatch.setenv("DATABASE_URL", target.render_as_string(hide_password=False))
    get_settings.cache_clear()
    await dispose_engine()
    engine = create_async_engine(target, poolclass=NullPool)
    cfg = Config(str(ALEMBIC_INI))

    async def q(sql, **params):
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params)).all()

    try:
        # --- the old world: revision 0005, a duplicate CTBC card carrying data -----------
        await asyncio.to_thread(command.upgrade, cfg, "0005")
        [(canonical,)] = await q("SELECT id FROM cards WHERE artwork_id = 'ctbc-linepay-ve8710'")
        dup, u1, u2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with engine.begin() as conn:
            await conn.execute(
                text(INSERT_DUP_CARD),
                {"id": dup},
            )
            for uid, mail in ((u1, "a@x.com"), (u2, "b@x.com")):
                await conn.execute(
                    text("INSERT INTO users(id, google_uid, email) VALUES (:i, :g, :e)"),
                    {"i": uid, "g": mail, "e": mail},
                )
            # u1 holds only the duplicate; u2 holds both.
            for uid, cid in ((u1, dup), (u2, dup), (u2, canonical)):
                await conn.execute(
                    text(USER_CARD_SQL),
                    {"u": uid, "c": cid},
                )
            await conn.execute(
                text(
                    "INSERT INTO sales(id, card_id, bank_name, card_name, title) "
                    "VALUES ('dup-sale', :c, '中國信託銀行', 'LINE Pay 聯名卡', 't')"
                ),
                {"c": dup},
            )
        # card_benefits exists at 0005; the old crawler put its rows on the duplicate.
        async with engine.begin() as conn:
            for cid, title, when, reward in (
                (dup, "only-on-dup", OLD, "1%"),
                (dup, "conflict-dup-newer", NEW, "dup-new"),
                (canonical, "conflict-dup-newer", OLD, "canon-old"),
                (dup, "conflict-canon-newer", OLD, "dup-old"),
                (canonical, "conflict-canon-newer", NEW, "canon-new"),
            ):
                await conn.execute(
                    text(
                        "INSERT INTO card_benefits(id, card_id, title, reward, source_url, "
                        "fetched_at) "
                        "VALUES (gen_random_uuid(), :c, :t, :r, 'https://x', :f)"
                    ),
                    {"c": cid, "t": title, "r": reward, "f": when},
                )

        # --- upgrade: identity backfilled, duplicates merged ----------------------------------
        await asyncio.to_thread(command.upgrade, cfg, "head")
        assert await q(
            "SELECT catalog_key, crawler_enabled, search_card_name FROM cards WHERE id = :i",
            i=canonical,
        ) == [("ctbc-linepay", True, "LINE Pay 聯名卡")]
        assert await q("SELECT count(*) FROM cards WHERE catalog_key IS NULL") == [(0,)]
        assert await q("SELECT count(*) FROM cards WHERE name = 'LINE Pay 聯名卡'") == [(1,)], (
            "u2 holds both, so the duplicate card must survive with its user_card"
        )
        assert await q("SELECT card_id FROM sales WHERE id = 'dup-sale'") == [(canonical,)]
        rows = dict(await q("SELECT title, reward FROM card_benefits"))
        assert rows == {
            "only-on-dup": "1%",
            "conflict-dup-newer": "dup-new",
            "conflict-canon-newer": "canon-new",
        }
        assert set(r[0] for r in await q("SELECT DISTINCT card_id FROM card_benefits")) == {
            canonical
        }
        held = await q("SELECT user_id, card_id FROM user_cards ORDER BY user_id")
        assert (u1, canonical) in held and (u2, canonical) in held and (u2, dup) in held
        assert await q("SELECT count(*) FROM user_cards") == [(3,)], "no user_card was deleted"
        # The constraints are real.
        async with engine.connect() as conn:
            with pytest.raises(Exception, match="catalog_key"):
                await conn.execute(
                    text("UPDATE cards SET catalog_key = NULL WHERE id = :i"), {"i": canonical}
                )

        # --- downgrade / upgrade round trip ---------------------------------------------------
        await asyncio.to_thread(command.downgrade, cfg, "0005")
        assert await q(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'cards' AND column_name IN "
            "('catalog_key','crawler_enabled','search_bank_name','search_card_name')"
        ) == [(0,)]
        await asyncio.to_thread(command.upgrade, cfg, "head")
        assert await q("SELECT catalog_key FROM cards WHERE id = :i", i=canonical) == [
            ("ctbc-linepay",)
        ]
        assert await q("SELECT count(*) FROM cards WHERE catalog_key IS NULL") == [(0,)]

        # --- SSE: nothing on file for a CTBC holder -> live lookup on the CANONICAL card ------
        holder = uuid.uuid4()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users(id, google_uid, email) VALUES (:i, 'uid-live', 'live@x.com')"
                ),
                {"i": holder},
            )
            await conn.execute(
                text(USER_CARD_SQL),
                {"u": holder, "c": canonical},
            )
            await conn.execute(text("DELETE FROM card_benefits"))
            await conn.execute(text("DELETE FROM sales"))
        cards_before = (await q("SELECT count(*) FROM cards"))[0][0]

        async def fake_verify(token):
            return {"sub": "uid-live", "email": "live@x.com"}

        monkeypatch.setattr(deps, "verify_firebase_token", fake_verify)
        live_refresh.reset_lookup_memo()
        lookup, agent = FakeLookup(), FakeAgent()
        agent.answer = pick_first_base
        app = create_app()
        app.dependency_overrides[get_recommendation_agent] = lambda: agent
        app.dependency_overrides[live_refresh.get_live_lookup] = lambda: lookup
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                f"{P}/recommendations/stream", json=REQ, headers={"Authorization": "Bearer t"}
            )
        assert r.status_code == 200
        events = parse_sse(r.text)
        assert [d.get("stage") for n, d in events if n == "searching"] == [
            "preprocessing",
            "live_card_lookup",
            "reprocessing",
            "official_verification",
        ]
        best = events[-2][1]["best_now"]
        assert best["candidate_type"] == "base_benefit" and best["card"]["id"] == str(canonical)
        assert lookup.fetched == [CTBC_URL]

        assert (await q("SELECT count(*) FROM cards"))[0][0] == cards_before, "no Card created"
        [(card_id, source, stamped)] = await q(
            "SELECT card_id, source_url, official_verified_at IS NOT NULL FROM card_benefits"
        )
        assert card_id == canonical and source == CTBC_URL and stamped is True
        assert await q("SELECT count(*) FROM user_sales") == [(0,)]
        assert await q("SELECT count(*) FROM calendar_events") == [(0,)]
    finally:
        await dispose_engine()
        await engine.dispose()
        await _drop(admin, name)
        get_settings.cache_clear()
