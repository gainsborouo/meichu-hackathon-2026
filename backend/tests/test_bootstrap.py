from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.engine import make_url

from app import main
from app.db import bootstrap


class PostgreSQLError(Exception):
    def __init__(self, sqlstate: str):
        self.sqlstate = sqlstate


async def test_existing_database_is_migrated_without_creation(monkeypatch) -> None:
    url = make_url("postgresql+asyncpg://app:secret@db/example")
    probe = AsyncMock()
    create = AsyncMock()
    migrate = AsyncMock()
    monkeypatch.setattr(bootstrap, "_postgres_url", lambda: url)
    monkeypatch.setattr(bootstrap, "_probe_database", probe)
    monkeypatch.setattr(bootstrap, "_create_database", create)
    monkeypatch.setattr(bootstrap, "_migrate_database", migrate)

    await bootstrap.initialize_database()

    probe.assert_awaited_once_with(url)
    create.assert_not_awaited()
    migrate.assert_awaited_once_with(url)


async def test_missing_database_is_created_before_migration(monkeypatch) -> None:
    url = make_url("postgresql+asyncpg://app:secret@db/example")
    monkeypatch.setattr(bootstrap, "_postgres_url", lambda: url)
    monkeypatch.setattr(
        bootstrap, "_probe_database", AsyncMock(side_effect=PostgreSQLError("3D000"))
    )
    create = AsyncMock()
    migrate = AsyncMock()
    monkeypatch.setattr(bootstrap, "_create_database", create)
    monkeypatch.setattr(bootstrap, "_migrate_database", migrate)

    await bootstrap.initialize_database()

    create.assert_awaited_once_with(url)
    migrate.assert_awaited_once_with(url)


async def test_database_probe_propagates_non_missing_database_errors(monkeypatch) -> None:
    url = make_url("postgresql+asyncpg://app:secret@db/example")
    monkeypatch.setattr(bootstrap, "_postgres_url", lambda: url)
    monkeypatch.setattr(
        bootstrap, "_probe_database", AsyncMock(side_effect=PostgreSQLError("28P01"))
    )
    create = AsyncMock()
    migrate = AsyncMock()
    monkeypatch.setattr(bootstrap, "_create_database", create)
    monkeypatch.setattr(bootstrap, "_migrate_database", migrate)

    with pytest.raises(PostgreSQLError):
        await bootstrap.initialize_database()
    create.assert_not_awaited()
    migrate.assert_not_awaited()


class FakeConnection:
    def __init__(self, create_error: Exception | None = None):
        self.create_error = create_error
        self.statements: list[str] = []
        self.dialect = SimpleNamespace(
            identifier_preparer=SimpleNamespace(quote=lambda value: f'"{value}"')
        )

    async def exec_driver_sql(self, statement: str) -> None:
        self.statements.append(statement)
        if self.create_error:
            raise self.create_error

    async def execute(self, statement, parameters) -> None:
        self.statements.append(str(statement))


class FakeConnectionContext:
    def __init__(self, connection: FakeConnection):
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *_args) -> None:
        return None


class FakeEngine:
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self.disposed = False

    def connect(self) -> FakeConnectionContext:
        return FakeConnectionContext(self.connection)

    async def dispose(self) -> None:
        self.disposed = True


async def test_concurrent_database_creation_tolerates_duplicate_database(monkeypatch) -> None:
    connection = FakeConnection(PostgreSQLError("42P04"))
    engine = FakeEngine(connection)
    factory_calls = []

    def factory(url, **kwargs):
        factory_calls.append((url, kwargs))
        return engine

    monkeypatch.setattr(bootstrap, "create_async_engine", factory)
    url = make_url("postgresql+asyncpg://app:secret@db/example")

    await bootstrap._create_database(url)

    assert factory_calls[0][0].database == "postgres"
    assert factory_calls[0][1]["isolation_level"] == "AUTOCOMMIT"
    assert connection.statements == ['CREATE DATABASE "example"']
    assert engine.disposed


async def test_migration_failure_releases_lock_and_engine(monkeypatch) -> None:
    connection = FakeConnection()
    engine = FakeEngine(connection)
    monkeypatch.setattr(bootstrap, "create_async_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(
        bootstrap.asyncio,
        "to_thread",
        AsyncMock(side_effect=RuntimeError("migration failed")),
    )

    with pytest.raises(RuntimeError, match="migration failed"):
        await bootstrap._migrate_database(
            make_url("postgresql+asyncpg://app:secret@db/example")
        )

    assert connection.statements == [
        "SELECT pg_advisory_lock(:lock_id)",
        "SELECT pg_advisory_unlock(:lock_id)",
    ]
    assert engine.disposed


async def test_lifespan_initializes_and_disposes_database(monkeypatch) -> None:
    initialize = AsyncMock()
    dispose = AsyncMock()
    monkeypatch.setattr(main, "initialize_database", initialize)
    monkeypatch.setattr(main, "dispose_engine", dispose)

    async with main.lifespan(SimpleNamespace()):
        initialize.assert_awaited_once()
        dispose.assert_not_awaited()

    dispose.assert_awaited_once()
