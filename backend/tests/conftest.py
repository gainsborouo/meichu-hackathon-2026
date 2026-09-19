from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.db.base import Base


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch):
    """Keep the test suite off the network.

    refresh_latest_spend_report runs on every analysis write and will happily
    call a real gateway if backend/.env has credentials -- which turns `pytest`
    into billable API traffic. Failing the agent here exercises the fallback
    path, which is the behaviour every other test wants anyway; the tests that
    care about the model patch it themselves.
    """
    from app.services import spend_report
    from app.services.spend_report_agent import SpendReportAgentError

    async def _refuse(*args, **kwargs):
        raise SpendReportAgentError("model calls are disabled in tests")

    monkeypatch.setattr(spend_report, "write_report", _refuse)
