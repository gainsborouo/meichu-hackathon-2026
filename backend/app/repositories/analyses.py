import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserAnalysis, UserCard


def month_start(value: date) -> date:
    return value.replace(day=1)


async def upsert_analysis(
    session: AsyncSession,
    *,
    user_card_id: uuid.UUID,
    analysis_month: date,
    report: str,
    analysis_data: dict | None,
) -> UserAnalysis:
    month = month_start(analysis_month)
    row = await session.scalar(
        select(UserAnalysis).where(
            UserAnalysis.user_card_id == user_card_id, UserAnalysis.analysis_month == month
        )
    )
    if row is None:
        row = UserAnalysis(user_card_id=user_card_id, analysis_month=month)
        session.add(row)
    row.report = report
    row.analysis_data = analysis_data
    await session.flush()
    await session.refresh(row)
    return row


async def list_for_user_card(
    session: AsyncSession, user_card_id: uuid.UUID, limit: int = 3
) -> list[UserAnalysis]:
    result = await session.scalars(
        select(UserAnalysis)
        .where(UserAnalysis.user_card_id == user_card_id)
        .order_by(UserAnalysis.analysis_month.desc())
        .limit(limit)
    )
    return list(result)


async def get_for_user_card(
    session: AsyncSession, user_card_id: uuid.UUID, analysis_id: uuid.UUID
) -> UserAnalysis | None:
    return await session.scalar(
        select(UserAnalysis).where(
            UserAnalysis.id == analysis_id, UserAnalysis.user_card_id == user_card_id
        )
    )


async def latest_months_for_user(
    session: AsyncSession, user_id: uuid.UUID, months: int = 3
) -> list[UserAnalysis]:
    """All of a user's analyses that fall in their most recent `months` distinct months."""
    recent = (
        select(UserAnalysis.analysis_month)
        .join(UserCard, UserCard.id == UserAnalysis.user_card_id)
        .where(UserCard.user_id == user_id)
        .distinct()
        .order_by(UserAnalysis.analysis_month.desc())
        .limit(months)
        .scalar_subquery()
    )
    result = await session.scalars(
        select(UserAnalysis)
        .join(UserCard, UserCard.id == UserAnalysis.user_card_id)
        .where(UserCard.user_id == user_id, UserAnalysis.analysis_month.in_(recent))
        .order_by(UserAnalysis.analysis_month.desc(), UserCard.created_at)
    )
    return list(result)
