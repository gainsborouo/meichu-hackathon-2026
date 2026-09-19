"""Thin Google Calendar client.

Firebase sign-in authenticates the user but grants no Calendar scope, so this
uses a separate OAuth consent whose refresh token is stored on the user row.

Calls go through google-auth's AuthorizedSession (already a dependency) in a
worker thread, matching how app.api.deps verifies Firebase tokens. That avoids
pulling in google-api-python-client for two REST calls.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials

from app.core.config import get_settings

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
_API = "https://www.googleapis.com/calendar/v3"
_TOKEN_URI = "https://oauth2.googleapis.com/token"


class CalendarError(RuntimeError):
    """Calendar is not connected, not configured, or the provider rejected a call."""


def _credentials(refresh_token: str) -> Credentials:
    settings = get_settings()
    if not (settings.google_client_id and settings.google_client_secret):
        raise CalendarError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set; the server cannot "
            "refresh calendar access tokens."
        )
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=_TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=[CALENDAR_SCOPE],
    )


def _rfc3339(value: datetime) -> str:
    return value.isoformat()


def _body(
    *,
    title: str,
    starts_at: datetime,
    ends_at: datetime | None,
    all_day: bool,
    notes: str | None,
    reminders_minutes: list[int],
) -> dict[str, Any]:
    end = ends_at or starts_at
    if all_day:
        when = {
            "start": {"date": starts_at.date().isoformat()},
            "end": {"date": end.date().isoformat()},
        }
    else:
        when = {"start": {"dateTime": _rfc3339(starts_at)}, "end": {"dateTime": _rfc3339(end)}}
    return {
        "summary": title,
        "description": notes or "",
        **when,
        "reminders": {
            "useDefault": not reminders_minutes,
            "overrides": [{"method": "popup", "minutes": m} for m in reminders_minutes],
        },
    }


def exchange_code(code: str, redirect_uri: str) -> str:
    """Trade an OAuth authorization code for a refresh token.

    The frontend runs the consent popup and posts the resulting code here, so
    the refresh token never reaches the browser.
    """
    import requests  # google-auth pulls this in

    settings = get_settings()
    if not (settings.google_client_id and settings.google_client_secret):
        raise CalendarError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set")
    response = requests.post(
        _TOKEN_URI,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise CalendarError(f"Token exchange failed: {response.text[:300]}")
    token = response.json().get("refresh_token")
    if not token:
        # Google only returns one when the consent screen was shown; the client
        # must request access_type=offline and prompt=consent.
        raise CalendarError(
            "Google returned no refresh_token. Request access_type=offline and "
            "prompt=consent when starting the consent flow."
        )
    return token


def _insert(refresh_token: str, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
    session = AuthorizedSession(_credentials(refresh_token))
    response = session.post(f"{_API}/calendars/{calendar_id}/events", json=body, timeout=30)
    if response.status_code >= 400:
        raise CalendarError(f"Calendar rejected the event: {response.text[:300]}")
    return response.json()


def _delete(refresh_token: str, calendar_id: str, event_id: str) -> None:
    session = AuthorizedSession(_credentials(refresh_token))
    response = session.delete(f"{_API}/calendars/{calendar_id}/events/{event_id}", timeout=30)
    # 410 means it is already gone, which is the state we wanted.
    if response.status_code >= 400 and response.status_code not in (404, 410):
        raise CalendarError(f"Calendar rejected the delete: {response.text[:300]}")


async def create_event(
    refresh_token: str,
    *,
    title: str,
    starts_at: datetime,
    ends_at: datetime | None = None,
    all_day: bool = False,
    notes: str | None = None,
    reminders_minutes: list[int] | None = None,
    calendar_id: str = "primary",
) -> dict[str, Any]:
    body = _body(
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        notes=notes,
        reminders_minutes=reminders_minutes or [],
    )
    return await asyncio.to_thread(_insert, refresh_token, calendar_id, body)


async def delete_event(
    refresh_token: str, event_id: str, *, calendar_id: str = "primary"
) -> None:
    await asyncio.to_thread(_delete, refresh_token, calendar_id, event_id)
