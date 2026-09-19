import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRead(_ORM):
    id: uuid.UUID
    email: str
    registration_campaigns_enabled: bool
    latest_spend_report: str | None


class UserUpdate(BaseModel):
    registration_campaigns_enabled: bool


class CardRead(_ORM):
    id: uuid.UUID
    bank_name: str | None
    name: str
    artwork_id: str | None
    display_name: str | None
    issuer_en: str | None
    variant: str | None
    network: str | None
    tier: str | None
    official_image_url: str | None
    image_is_composite: bool | None


class UserCardCreate(BaseModel):
    card_id: uuid.UUID


class UserCardRead(_ORM):
    id: uuid.UUID
    card: CardRead
    created_at: datetime


class AnalysisUpsert(BaseModel):
    analysis_month: date  # any day in the month; stored as the 1st
    report: str
    analysis_data: dict[str, Any] | None = None


class AnalysisRead(_ORM):
    id: uuid.UUID
    user_card_id: uuid.UUID
    analysis_month: date
    report: str
    analysis_data: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class SaleRead(_ORM):
    id: str
    card_id: uuid.UUID | None
    bank_name: str
    card_name: str
    title: str
    reward: str | None
    conditions: str | None
    campaign_period: str | None
    register_url: str | None
    source_url: str | None
    evidence: str | None


class UserSaleRead(_ORM):
    id: uuid.UUID
    sale: SaleRead
    notified_at: datetime
    notification_channel: str


# --- calendar -----------------------------------------------------------


class CalendarConnect(BaseModel):
    code: str = Field(description="OAuth authorization code from the Google consent popup.")
    redirect_uri: str | None = Field(
        default=None,
        description='Must match the URI used to obtain the code; defaults to "postmessage".',
    )


class CalendarStatus(BaseModel):
    connected: bool


class CalendarEventCreate(BaseModel):
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    all_day: bool = False
    notes: str | None = None
    sale_id: str | None = Field(
        default=None, description="Link the reminder to a campaign; records the notification."
    )
    reminders_minutes: list[int] = Field(
        default_factory=lambda: [1440],
        description="Popup reminders, in minutes before the start. Empty uses calendar defaults.",
    )


class CalendarEventRead(_ORM):
    id: uuid.UUID
    sale_id: str | None
    provider: str
    provider_event_id: str
    title: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    notes: str | None
    html_link: str | None
    created_at: datetime


class CalendarEventCreated(BaseModel):
    event: CalendarEventRead
    already_notified: bool = Field(
        description="True when this sale had already been notified; no duplicate was recorded."
    )
    user_sale: UserSaleRead | None
