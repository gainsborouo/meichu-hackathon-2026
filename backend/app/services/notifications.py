import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserSale
from app.repositories import sales as sales_repo


async def record_sale_notification(
    session: AsyncSession, user_id: uuid.UUID, sale_id: str, channel: str = "calendar"
) -> tuple[UserSale, bool]:
    """Call only after the notification was actually delivered.

    user_sales means "already notified"; the (user_id, sale_id) unique key is the
    dedupe. Returns (row, created); created=False means it was already sent.
    """
    return await sales_repo.add_user_sale(session, user_id, sale_id, channel)


async def already_notified(session: AsyncSession, user_id: uuid.UUID, sale_id: str) -> bool:
    return await sales_repo.get_user_sale(session, user_id, sale_id) is not None
