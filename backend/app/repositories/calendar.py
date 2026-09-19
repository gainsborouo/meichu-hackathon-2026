import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CalendarEvent


async def add_event(session: AsyncSession, **fields) -> CalendarEvent:
    row = CalendarEvent(**fields)
    session.add(row)
    await session.flush()
    await session.refresh(row, ["sale"])
    return row


async def list_events(session: AsyncSession, user_id: uuid.UUID) -> list[CalendarEvent]:
    result = await session.scalars(
        select(CalendarEvent)
        .where(CalendarEvent.user_id == user_id)
        .order_by(CalendarEvent.starts_at.desc())
    )
    return list(result)


async def get_event(
    session: AsyncSession, user_id: uuid.UUID, event_id: uuid.UUID
) -> CalendarEvent | None:
    return await session.scalar(
        select(CalendarEvent).where(
            CalendarEvent.id == event_id, CalendarEvent.user_id == user_id
        )
    )
