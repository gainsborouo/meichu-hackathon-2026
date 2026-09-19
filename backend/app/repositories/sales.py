import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Sale, UserCard, UserSale


async def list_sales(
    session: AsyncSession, *, card_id: uuid.UUID | None = None, limit: int = 100, offset: int = 0
) -> list[Sale]:
    stmt = select(Sale).order_by(Sale.bank_name, Sale.card_name, Sale.id)
    if card_id is not None:
        stmt = stmt.where(Sale.card_id == card_id)
    return list(await session.scalars(stmt.limit(limit).offset(offset)))


async def list_sales_for_user_cards(session: AsyncSession, user_id: uuid.UUID) -> list[Sale]:
    """Sales attached to any card the user holds (users -> user_cards -> cards -> sales)."""
    result = await session.scalars(
        select(Sale)
        .join(UserCard, UserCard.card_id == Sale.card_id)
        .where(UserCard.user_id == user_id)
        .order_by(Sale.bank_name, Sale.card_name, Sale.id)
    )
    return list(result)


async def get_sale(session: AsyncSession, sale_id: str) -> Sale | None:
    return await session.get(Sale, sale_id)


async def list_user_sales(session: AsyncSession, user_id: uuid.UUID) -> list[UserSale]:
    stmt = select(UserSale).where(UserSale.user_id == user_id).order_by(UserSale.notified_at)
    return list(await session.scalars(stmt))


async def get_user_sale(session: AsyncSession, user_id: uuid.UUID, sale_id: str) -> UserSale | None:
    return await session.scalar(
        select(UserSale).where(UserSale.user_id == user_id, UserSale.sale_id == sale_id)
    )


async def add_user_sale(
    session: AsyncSession, user_id: uuid.UUID, sale_id: str, channel: str = "calendar"
) -> tuple[UserSale, bool]:
    existing = await get_user_sale(session, user_id, sale_id)
    if existing is not None:
        return existing, False
    row = UserSale(user_id=user_id, sale_id=sale_id, notification_channel=channel)
    session.add(row)
    await session.flush()
    await session.refresh(row, ["sale"])
    return row, True
