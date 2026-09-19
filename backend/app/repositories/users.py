import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def upsert_by_google_uid(session: AsyncSession, *, google_uid: str, email: str) -> User:
    """Create the user on first login; otherwise keep the email in sync with Google."""
    user = await session.scalar(select(User).where(User.google_uid == google_uid))
    if user is None:
        user = User(google_uid=google_uid, email=email)
        session.add(user)
    elif user.email != email:
        user.email = email
    await session.flush()
    return user
