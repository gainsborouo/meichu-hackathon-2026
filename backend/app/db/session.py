from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def require_database_url() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Build the engine lazily so importing the app never needs a database."""
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine = create_async_engine(require_database_url(), pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = _sessionmaker = None
