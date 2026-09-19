import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories import analyses as analyses_repo


async def refresh_latest_spend_report(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    """Rebuild users.latest_spend_report from the user's newest three months of analyses.

    The column is a cache: user_analyses stays the source of truth, so call this
    after any analysis write and it is always safe to recompute.
    """
    rows = await analyses_repo.latest_months_for_user(session, user_id, months=3)
    sections: list[str] = []
    current_month = None
    for row in rows:
        if row.analysis_month != current_month:
            current_month = row.analysis_month
            sections.append(f"## {current_month:%Y-%m}")
        sections.append(row.report.strip())
    report = "\n\n".join(sections) if sections else None

    user = await session.get(User, user_id)
    if user is not None:
        user.latest_spend_report = report
        await session.flush()
    return report
