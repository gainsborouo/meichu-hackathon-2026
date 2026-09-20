import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RecommendationRequest(BaseModel):
    product_name: str = Field(min_length=1, max_length=200)
    store_name: str = Field(min_length=1, max_length=200)
    price: float = Field(gt=0)
    currency: Literal["TWD"] = "TWD"
    locale: Literal["zh-TW", "en-US"] = "zh-TW"


class CardRef(BaseModel):
    id: uuid.UUID
    bank_name: str | None
    name: str
    issuer_en: str | None = None
    name_en: str | None = None
    artwork_id: str | None = None


class OfficialSource(BaseModel):
    title: str
    url: str


class BestNow(BaseModel):
    # "base_benefit": the card's standing reward; "campaign": a limited-time offer.
    candidate_type: Literal["base_benefit", "campaign"]
    card: CardRef
    sale_id: str | None = None  # set for campaigns
    benefit_id: str | None = None  # set for base benefits
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


class RecommendationChatMessage(BaseModel):
    """A short browser-only follow-up conversation turn."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class RecommendationChatRequest(BaseModel):
    """Context supplied by the recommendation page for a follow-up answer.

    The context is validated but still treated as untrusted text by the model. It is
    never used to select cards, write data, invoke tools, or make external requests.
    """

    model_config = ConfigDict(extra="forbid")

    purchase: RecommendationRequest
    recommendation: RecommendationResponse
    messages: list[RecommendationChatMessage] = Field(default_factory=list, max_length=12)
    question: str = Field(min_length=1, max_length=2000)
