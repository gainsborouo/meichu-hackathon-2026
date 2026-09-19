import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories import analyses as analyses_repo
from app.services.spend_report_agent import (
    InputError,
    SpendReportAgentError,
    build_facts,
    render_deterministic,
    write_report,
)

logger = logging.getLogger(__name__)


def _rows(analyses) -> list[dict]:
    """Shape analysis rows the way the skill's aggregation expects."""
    return [
        {
            "analysis_month": row.analysis_month.isoformat(),
            "card": {
                "bank_name": row.user_card.card.bank_name,
                "name": row.user_card.card.name,
            }
            if row.user_card and row.user_card.card
            else {},
            "report": row.report,
            "analysis_data": row.analysis_data,
        }
        for row in analyses
    ]


async def refresh_latest_spend_report(
    session: AsyncSession, user_id: uuid.UUID, *, use_agent: bool = True
) -> str | None:
    """Rebuild users.latest_spend_report from the newest three months of analyses.

    The column is a cache; user_analyses stays the source of truth, so this is
    always safe to recompute. It runs on every analysis write, which is why the
    model is best-effort: the deterministic renderer produces a truthful report
    on its own, and a write must not fail because a gateway is down.

    Pass use_agent=False to skip the model entirely -- useful in tests and for
    bulk backfills where one call per row would be wasteful.
    """
    rows = await analyses_repo.latest_months_for_user(session, user_id, months=3)

    report: str | None
    if not rows:
        report = None
    else:
        try:
            facts = build_facts(_rows(rows))
            report = render_deterministic(facts)
        except InputError as exc:
            logger.warning("spend report aggregation failed: %s", exc)
            report = None
        else:
            if use_agent:
                try:
                    report = await write_report(facts, skeleton=report)
                except SpendReportAgentError as exc:
                    # Expected whenever the gateway is unset or unreachable. The
                    # deterministic report is already in hand, so keep it.
                    logger.info("spend report written without the model: %s", exc)

    user = await session.get(User, user_id)
    if user is not None:
        user.latest_spend_report = report
        await session.flush()
    return report
