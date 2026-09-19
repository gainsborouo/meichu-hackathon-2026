import uuid
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CardBenefit, UserCard


def is_effective(benefit: CardBenefit, today: date) -> bool:
    """effective_start (if any) not after today; effective_end (if any) not before today."""
    if benefit.effective_start is not None and benefit.effective_start > today:
        return False
    return benefit.effective_end is None or benefit.effective_end >= today


async def list_effective_for_user_cards(
    session: AsyncSession, user_id: uuid.UUID, today: date
) -> list[CardBenefit]:
    """Effective base benefits of every card the user holds."""
    result = await session.scalars(
        select(CardBenefit)
        .join(UserCard, UserCard.card_id == CardBenefit.card_id)
        .where(
            UserCard.user_id == user_id,
            or_(CardBenefit.effective_start.is_(None), CardBenefit.effective_start <= today),
            or_(CardBenefit.effective_end.is_(None), CardBenefit.effective_end >= today),
        )
        .order_by(CardBenefit.card_id, CardBenefit.title)
    )
    return list(result)


async def get_by_card_and_title(
    session: AsyncSession, card_id: uuid.UUID, title: str
) -> CardBenefit | None:
    return await session.scalar(
        select(CardBenefit).where(CardBenefit.card_id == card_id, CardBenefit.title == title)
    )
