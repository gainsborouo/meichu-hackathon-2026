import asyncio
import logging
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session
from app.models import User
from app.services.users import upsert_user

logger = logging.getLogger(__name__)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
_bearer = HTTPBearer(auto_error=False)


async def verify_firebase_token(token: str) -> dict:
    """Verify a Firebase ID token (the frontend signs in with Firebase Auth + Google)."""
    project_id = get_settings().firebase_project_id
    try:
        return await asyncio.to_thread(
            id_token.verify_firebase_token,
            token,
            google_requests.Request(),
            audience=project_id,
        )
    except ValueError as exc:
        # The response stays generic on purpose, but the operator needs the real reason
        # (expired, "used too early" from a skewed clock, wrong audience, bad signature).
        # google-auth's messages carry claims and timestamps, never the token itself.
        logger.warning("Firebase ID token rejected (expected audience %r): %s", project_id, exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Firebase ID token") from exc


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    claims = await verify_firebase_token(credentials.credentials)
    email = claims.get("email")
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has no email claim")
    return await upsert_user(session, google_uid=claims["sub"], email=email)


CurrentUser = Annotated[User, Depends(get_current_user)]
