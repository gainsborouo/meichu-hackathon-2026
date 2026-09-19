import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Card, UserCard


async def get_or_create_card(session: AsyncSession, *, bank_name: str | None, name: str) -> Card:
    card = await session.scalar(select(Card).where(Card.bank_name == bank_name, Card.name == name))
    if card is None:
        card = Card(bank_name=bank_name, name=name)
        session.add(card)
        await session.flush()
    return card


async def list_cards(session: AsyncSession) -> list[Card]:
    return list(await session.scalars(select(Card).order_by(Card.bank_name, Card.name)))


async def list_user_cards(session: AsyncSession, user_id: uuid.UUID) -> list[UserCard]:
    result = await session.scalars(
        select(UserCard).where(UserCard.user_id == user_id).order_by(UserCard.created_at)
    )
    return list(result)


async def get_user_card(
    session: AsyncSession, user_id: uuid.UUID, user_card_id: uuid.UUID
) -> UserCard | None:
    return await session.scalar(
        select(UserCard).where(UserCard.id == user_card_id, UserCard.user_id == user_id)
    )


async def add_user_card(
    session: AsyncSession, user_id: uuid.UUID, card_id: uuid.UUID
) -> tuple[UserCard, bool]:
    """Idempotent: returns (row, created)."""
    existing = await session.scalar(
        select(UserCard).where(UserCard.user_id == user_id, UserCard.card_id == card_id)
    )
    if existing is not None:
        return existing, False
    user_card = UserCard(user_id=user_id, card_id=card_id)
    session.add(user_card)
    await session.flush()
    await session.refresh(user_card, ["card"])
    return user_card, True


async def delete_user_card(session: AsyncSession, user_card: UserCard) -> None:
    await session.delete(user_card)
    await session.flush()
