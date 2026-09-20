import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def _ts() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


def _ts_updated() -> Mapped[datetime]:
    return mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    google_uid: Mapped[str] = mapped_column(sa.Text, unique=True)
    email: Mapped[str] = mapped_column(sa.Text, unique=True)
    # false: only campaigns needing no registration are recommended; true: only those that do.
    registration_campaigns_enabled: Mapped[bool] = mapped_column(
        default=False, server_default=sa.false()
    )
    # Firebase sign-in does not grant the Calendar scope, so connecting a
    # calendar is a separate OAuth consent whose refresh token is kept here.
    google_refresh_token: Mapped[str | None] = mapped_column(sa.Text)
    latest_spend_report: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts_updated()

    user_cards: Mapped[list["UserCard"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


def _adhoc_key() -> str:
    return f"adhoc-{uuid.uuid4().hex}"


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (UniqueConstraint("bank_name", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # The one stable identity: unique, never a display name, independent of artwork_id.
    # Catalog cards get it from app.services.card_identity; anything else gets an adhoc key.
    catalog_key: Mapped[str] = mapped_column(sa.Text, unique=True, default=_adhoc_key)
    crawler_enabled: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    # Wording the crawler searches with (may differ from the catalog display name).
    search_bank_name: Mapped[str | None] = mapped_column(sa.Text)
    search_card_name: Mapped[str | None] = mapped_column(sa.Text)
    bank_name: Mapped[str | None] = mapped_column(sa.Text)
    name: Mapped[str] = mapped_column(sa.Text)
    artwork_id: Mapped[str | None] = mapped_column(sa.Text, unique=True)
    display_name: Mapped[str | None] = mapped_column(sa.Text)
    issuer_en: Mapped[str | None] = mapped_column(sa.Text)
    name_en: Mapped[str | None] = mapped_column(sa.Text)
    variant: Mapped[str | None] = mapped_column(sa.Text)
    network: Mapped[str | None] = mapped_column(sa.Text)
    tier: Mapped[str | None] = mapped_column(sa.Text)
    official_image_url: Mapped[str | None] = mapped_column(sa.Text)
    image_is_composite: Mapped[bool | None] = mapped_column(sa.Boolean)
    created_at: Mapped[datetime] = _ts()


class UserCard(Base):
    __tablename__ = "user_cards"
    __table_args__ = (UniqueConstraint("user_id", "card_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = _ts()

    user: Mapped[User] = relationship(back_populates="user_cards")
    card: Mapped[Card] = relationship(lazy="joined")


class UserAnalysis(Base):
    __tablename__ = "user_analyses"
    __table_args__ = (
        UniqueConstraint("user_card_id", "analysis_month"),
        sa.CheckConstraint(
            sa.extract("day", sa.column("analysis_month")) == 1, name="month_first_day"
        ),
        Index(
            "ix_user_analyses_user_card_id_analysis_month",
            "user_card_id",
            sa.text("analysis_month DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_cards.id", ondelete="CASCADE"))
    analysis_month: Mapped[date] = mapped_column(sa.Date)
    report: Mapped[str] = mapped_column(sa.Text)
    analysis_data: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts_updated()

    user_card: Mapped["UserCard"] = relationship()


class CardBenefit(Base):
    """A card's standing reward (e.g. 1% on everyday spend), as opposed to a limited campaign.

    `effective_end` NULL is valid and means "no end date published": long-running base
    rewards often have none. It is not a promise the reward still exists; that is what
    `official_verified_at` and the crawler's page-level checks are for.
    """

    __tablename__ = "card_benefits"
    __table_args__ = (
        UniqueConstraint("card_id", "title"),
        sa.CheckConstraint(
            "effective_end IS NULL OR effective_start IS NULL OR effective_end >= effective_start",
            name="valid_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(sa.Text)
    reward: Mapped[str] = mapped_column(sa.Text)
    conditions: Mapped[str | None] = mapped_column(sa.Text)
    reward_rules: Mapped[list] = mapped_column(
        JSONType, default=list, server_default=sa.text("'[]'")
    )
    effective_start: Mapped[date | None] = mapped_column(sa.Date)
    effective_end: Mapped[date | None] = mapped_column(sa.Date)
    source_url: Mapped[str] = mapped_column(sa.Text)
    source_payload: Mapped[dict] = mapped_column(
        JSONType, default=dict, server_default=sa.text("'{}'")
    )
    fetched_at: Mapped[datetime] = _ts()
    official_verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts_updated()

    card: Mapped[Card] = relationship()


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    card_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cards.id", ondelete="SET NULL"), index=True
    )
    bank_name: Mapped[str] = mapped_column(sa.Text)
    card_name: Mapped[str] = mapped_column(sa.Text)
    title: Mapped[str] = mapped_column(sa.Text)
    reward: Mapped[str | None] = mapped_column(sa.Text)
    conditions: Mapped[str | None] = mapped_column(sa.Text)
    campaign_period: Mapped[str | None] = mapped_column(sa.Text)
    register_url: Mapped[str | None] = mapped_column(sa.Text)
    source_url: Mapped[str | None] = mapped_column(sa.Text)
    evidence: Mapped[str | None] = mapped_column(sa.Text)
    source_payload: Mapped[dict] = mapped_column(
        JSONType, default=dict, server_default=sa.text("'{}'")
    )
    # Machine-rankable form of `reward`, derived by app.services.reward_rules.
    # Empty when nothing quantifiable could be extracted; `reward` stays the truth.
    reward_rules: Mapped[list] = mapped_column(
        JSONType, default=list, server_default=sa.text("'[]'")
    )
    # Parsed from the campaign's own dates; NULL when the source had none we could read.
    campaign_start: Mapped[date | None] = mapped_column(sa.Date)
    campaign_end: Mapped[date | None] = mapped_column(sa.Date)
    # Set only by a check against the bank's official page, never by local import.
    official_verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    fetched_at: Mapped[datetime] = _ts()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts_updated()

    card: Mapped[Card | None] = relationship()


class UserSale(Base):
    """A sale whose notification was successfully delivered; not a queue."""

    __tablename__ = "user_sales"
    __table_args__ = (UniqueConstraint("user_id", "sale_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"))
    notified_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    notification_channel: Mapped[str] = mapped_column(
        sa.Text, default="calendar", server_default="calendar"
    )

    sale: Mapped[Sale] = relationship(lazy="joined")


class CalendarEvent(Base):
    """An event this app created in the user's calendar.

    Kept locally so the app can list and cancel its own events; the calendar
    provider remains the source of truth for the event itself. `sale_id` is set
    when the reminder came from a campaign, and null for a plain "remind me to
    buy this" -- which is why user_sales alone was not enough.
    """

    __tablename__ = "calendar_events"
    __table_args__ = (UniqueConstraint("user_id", "provider", "provider_event_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    sale_id: Mapped[str | None] = mapped_column(ForeignKey("sales.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(sa.Text, default="google", server_default="google")
    provider_event_id: Mapped[str] = mapped_column(sa.Text)
    title: Mapped[str] = mapped_column(sa.Text)
    starts_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    notes: Mapped[str | None] = mapped_column(sa.Text)
    html_link: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts_updated()

    sale: Mapped[Sale | None] = relationship(lazy="joined")
