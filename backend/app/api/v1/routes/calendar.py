"""Calendar reminders: connect a calendar, then put reminders in it.

Two kinds of reminder share this endpoint. One is linked to a campaign
(`sale_id`), which also records the notification in user_sales so the same
offer is never pushed twice. The other is a plain "remind me to buy this",
which has no sale and lives only in calendar_events.
"""

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.repositories import calendar as calendar_repo
from app.repositories import sales as sales_repo
from app.schemas.db import (
    CalendarConnect,
    CalendarEventCreate,
    CalendarEventCreated,
    CalendarEventRead,
    CalendarStatus,
    UserSaleRead,
)
from app.services import google_calendar
from app.services.google_calendar import CalendarError
from app.services.notifications import record_sale_notification

router = APIRouter(prefix="/me/calendar")


def _require_connection(user) -> str:
    if not user.calendar_push_enabled:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Calendar push is off. Enable it with PATCH /me first.",
        )
    if not user.google_refresh_token:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No calendar connected. Complete POST /me/calendar/connect first.",
        )
    return user.google_refresh_token


@router.get("", response_model=CalendarStatus)
async def calendar_status(user: CurrentUser) -> CalendarStatus:
    return CalendarStatus(
        connected=bool(user.google_refresh_token),
        calendar_push_enabled=user.calendar_push_enabled,
    )


@router.post("/connect", response_model=CalendarStatus)
async def connect_calendar(
    body: CalendarConnect, user: CurrentUser, session: SessionDep
) -> CalendarStatus:
    """Exchange a Google OAuth code for a refresh token and store it.

    The frontend runs the consent popup with `access_type=offline` and
    `prompt=consent`, then posts the code here, so the long-lived token never
    reaches the browser.
    """
    redirect_uri = body.redirect_uri or get_settings().google_oauth_redirect_uri
    try:
        token = await asyncio.to_thread(
            google_calendar.exchange_code, body.code, redirect_uri
        )
    except CalendarError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    user.google_refresh_token = token
    # Connecting is an explicit opt-in; turning the switch on here saves a round trip.
    user.calendar_push_enabled = True
    await session.flush()
    return CalendarStatus(connected=True, calendar_push_enabled=True)


@router.delete("/connect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_calendar(user: CurrentUser, session: SessionDep) -> None:
    """Forget the stored token. Events already in the calendar stay there."""
    user.google_refresh_token = None
    user.calendar_push_enabled = False
    await session.flush()


@router.get("/events", response_model=list[CalendarEventRead])
async def list_events(user: CurrentUser, session: SessionDep) -> list[CalendarEventRead]:
    rows = await calendar_repo.list_events(session, user.id)
    return [CalendarEventRead.model_validate(r) for r in rows]


@router.post("/events", response_model=CalendarEventCreated, status_code=status.HTTP_201_CREATED)
async def create_event(
    body: CalendarEventCreate, user: CurrentUser, session: SessionDep, response: Response
) -> CalendarEventCreated:
    """Create a reminder in the user's calendar.

    When `sale_id` is set, the calendar event is created first and only then
    recorded in user_sales -- that table means "already notified", so writing it
    before delivery would suppress a reminder that never arrived. A sale that
    was already notified returns 200 with `already_notified: true` rather than
    an error: silent de-duplication is the point of the unique key.
    """
    token = _require_connection(user)

    if body.sale_id is not None and await sales_repo.get_sale(session, body.sale_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sale not found")

    try:
        created = await google_calendar.create_event(
            token,
            title=body.title,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            all_day=body.all_day,
            notes=body.notes,
            reminders_minutes=body.reminders_minutes,
        )
    except CalendarError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    row = await calendar_repo.add_event(
        session,
        user_id=user.id,
        sale_id=body.sale_id,
        provider="google",
        provider_event_id=created["id"],
        title=body.title,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        all_day=body.all_day,
        notes=body.notes,
        html_link=created.get("htmlLink"),
    )

    user_sale = None
    already = False
    if body.sale_id is not None:
        user_sale, created_row = await record_sale_notification(
            session, user.id, body.sale_id, channel="calendar"
        )
        already = not created_row
        if already:
            response.status_code = status.HTTP_200_OK

    return CalendarEventCreated(
        event=CalendarEventRead.model_validate(row),
        already_notified=already,
        user_sale=UserSaleRead.model_validate(user_sale) if user_sale else None,
    )


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> None:
    """Remove the event from the calendar and forget it.

    Any user_sales row stays: "this offer was notified" is a historical fact,
    and clearing it would let the same campaign be pushed again.
    """
    row = await calendar_repo.get_event(session, user.id, event_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if user.google_refresh_token:
        try:
            await google_calendar.delete_event(user.google_refresh_token, row.provider_event_id)
        except CalendarError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    await session.delete(row)
    await session.flush()
