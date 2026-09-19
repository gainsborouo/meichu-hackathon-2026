from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories import users as users_repo


async def upsert_user(session: AsyncSession, *, google_uid: str, email: str) -> User:
    """Entry point for the Firebase-authenticated request path (see api/deps.py)."""
    return await users_repo.upsert_by_google_uid(session, google_uid=google_uid, email=email)
