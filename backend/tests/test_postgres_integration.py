import asyncio
import os
import uuid

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.core.config import get_settings
from app.db.bootstrap import ALEMBIC_INI, initialize_database
from app.db.session import dispose_engine
from app.models import Card
from app.services.card_catalog import import_card_catalog, load_card_catalog

ADMIN_URL = os.getenv("TEST_POSTGRES_ADMIN_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_POSTGRES_ADMIN_URL is not configured")


async def test_postgres_bootstrap_catalog_and_downgrade(monkeypatch) -> None:
    admin_url = make_url(ADMIN_URL)
    if admin_url.drivername == "postgresql":
        admin_url = admin_url.set(drivername="postgresql+asyncpg")
    database_name = f"test_card_catalog_{uuid.uuid4().hex}"
    target_url = admin_url.set(database=database_name)
    monkeypatch.setenv("DATABASE_URL", target_url.render_as_string(hide_password=False))
    get_settings.cache_clear()

    target_engine = create_async_engine(target_url, poolclass=NullPool)
    try:
        await asyncio.gather(initialize_database(), initialize_database())
        async with target_engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM cards")) == 94
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert (
                revision == ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
            )

        session_factory = async_sessionmaker(target_engine, expire_on_commit=False)
        async with session_factory() as session:
            async with session.begin():
                await session.execute(delete(Card))
            async with session.begin():
                first = await import_card_catalog(session, load_card_catalog())
            async with session.begin():
                second = await import_card_catalog(session, load_card_catalog())
            assert first["created"] == 94 and second["created"] == 0
            assert await session.scalar(select(func.count()).select_from(Card)) == 94

        await asyncio.to_thread(command.downgrade, Config(str(ALEMBIC_INI)), "0002")
        async with target_engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM cards")) == 94
    finally:
        await dispose_engine()
        await target_engine.dispose()
        cleanup_engine = create_async_engine(
            admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
        try:
            async with cleanup_engine.connect() as connection:
                await connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                    ),
                    {"database_name": database_name},
                )
                identifier = connection.dialect.identifier_preparer.quote(database_name)
                await connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {identifier}")
        finally:
            await cleanup_engine.dispose()
            get_settings.cache_clear()
