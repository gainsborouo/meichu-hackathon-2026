import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRead(_ORM):
    id: uuid.UUID
    email: str
    calendar_push_enabled: bool
    latest_spend_report: str | None


class UserUpdate(BaseModel):
    calendar_push_enabled: bool


class CardRead(_ORM):
    id: uuid.UUID
    bank_name: str | None
    name: str


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
