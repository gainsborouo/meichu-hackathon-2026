import asyncio
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.db.session import require_database_url

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
ADVISORY_LOCK_ID = 0x4D4549434855


def _sqlstate(error: BaseException) -> str | None:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        state = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if state:
            return str(state)
        for nested in (
            getattr(current, "orig", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return None


def _postgres_url() -> URL:
    url = make_url(require_database_url())
    if not url.drivername.startswith("postgresql") or not url.database:
        raise RuntimeError("DATABASE_URL must reference a PostgreSQL database")
    return url


async def _probe_database(url: URL) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


async def _create_database(url: URL) -> None:
    engine = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        async with engine.connect() as connection:
            database = connection.dialect.identifier_preparer.quote(url.database)
            try:
                await connection.exec_driver_sql(f"CREATE DATABASE {database}")
            except Exception as error:
                if _sqlstate(error) != "42P04":
                    raise
    finally:
        await engine.dispose()


def _upgrade_database() -> None:
    command.upgrade(Config(str(ALEMBIC_INI)), "head")


async def _migrate_database(url: URL) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": ADVISORY_LOCK_ID}
            )
            try:
                await asyncio.to_thread(_upgrade_database)
            finally:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": ADVISORY_LOCK_ID},
                )
    finally:
        await engine.dispose()


async def initialize_database() -> None:
    url = _postgres_url()
    try:
        await _probe_database(url)
    except Exception as error:
        if _sqlstate(error) != "3D000":
            raise
        await _create_database(url)
    await _migrate_database(url)
