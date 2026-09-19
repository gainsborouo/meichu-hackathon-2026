import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class RecommendationRequest(BaseModel):
    product_name: str = Field(min_length=1, max_length=200)
    store_name: str = Field(min_length=1, max_length=200)
    price: float = Field(gt=0)
    currency: Literal["TWD"] = "TWD"


class CardRef(BaseModel):
    id: uuid.UUID
    bank_name: str | None
    name: str


class OfficialSource(BaseModel):
    title: str
    url: str


class BestNow(BaseModel):
    card: CardRef
    sale_id: str
    campaign_title: str
    estimated_reward_twd: float
    rate_display: str
    cap_description: str | None
    requires_registration: bool
    registration_url: str | None
    reason: str
    verification_status: Literal["verified", "unverified"]
    official_sources: list[OfficialSource]


class CalendarDraft(BaseModel):
    """A suggestion only. Nothing is created until the client calls POST /me/calendar/events."""

    title: str
    starts_at: datetime
    notes: str


class WaitSuggestion(BaseModel):
    recommended: Literal[True] = True
    card: CardRef
    sale_id: str
    starts_at: date
    estimated_reward_twd: float
    estimated_extra_reward_twd: float
    reason: str
    official_sources: list[OfficialSource]
    calendar_draft: CalendarDraft


class RecommendationResponse(BaseModel):
    mode: Literal["no_registration", "registration"]
    best_now: BestNow | None
    wait_suggestion: WaitSuggestion | None
    explanation: str | None = None
