import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CardBenefit, Sale
from app.repositories import benefits as benefits_repo
from app.repositories.cards import get_or_create_card
from app.services.reward_rules import extract_rules

DEFAULT_CAMPAIGNS_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "credit_card_campaigns"
    / "credit_card_campaigns.json"
)


def campaign_period(item: dict[str, Any]) -> str | None:
    start, end = item.get("campaign_start"), item.get("campaign_end")
    if start and end:
        return f"{start} ~ {end}"
    return start or end or None


def parse_date(value: Any) -> date | None:
    """ISO YYYY-MM-DD only; anything else stays NULL rather than being guessed."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def parse_dataset(raw: Any) -> dict[str, Any]:
    """Normalize either JSON shape to {"generated_at", "base_benefits", "campaigns"}.

    New crawler output is an object with base_benefits and campaigns. The older format is a
    bare list, and every entry in it is a campaign: it never carried base benefits, and
    nothing in it is promoted to one.
    """
    if isinstance(raw, list):
        return {"generated_at": None, "base_benefits": [], "campaigns": raw}
    if isinstance(raw, dict):
        return {
            "generated_at": raw.get("generated_at"),
            "base_benefits": list(raw.get("base_benefits") or []),
            "campaigns": list(raw.get("campaigns") or []),
        }
    raise ValueError("campaign JSON must be an object or a list")


def load_dataset(path: Path = DEFAULT_CAMPAIGNS_PATH) -> dict[str, Any]:
    return parse_dataset(json.loads(path.read_text(encoding="utf-8")))


def load_campaigns(path: Path = DEFAULT_CAMPAIGNS_PATH) -> list[dict[str, Any]]:
    return load_dataset(path)["campaigns"]


def _generated_at(dataset: dict[str, Any]) -> datetime:
    raw = dataset.get("generated_at")
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


async def import_campaigns(
    session: AsyncSession, items: list[dict[str, Any]], *, fetched_at: datetime | None = None
) -> dict[str, int]:
    """Idempotent upsert of cards + sales. JSON `bank`/`card` map to bank_name/card_name;
    the campaign JSON has no separate conditions field, so `conditions` stays NULL and
    everything (including confidence, recurrence, dates) is kept in source_payload.
    `reward_rules` is derived from the prose reward so recommendations have structured facts.
    `official_verified_at` is left untouched: a local JSON import verifies nothing."""
    now = fetched_at or datetime.now(UTC)
    created = updated = 0
    for item in items:
        card = await get_or_create_card(session, bank_name=item["bank"], name=item["card"])
        sale = await session.get(Sale, item["id"])
        if sale is None:
            sale = Sale(id=item["id"])
            session.add(sale)
            created += 1
        else:
            updated += 1
        sale.card_id = card.id
        sale.bank_name = item["bank"]
        sale.card_name = item["card"]
        sale.title = item["title"]
        sale.reward = item.get("reward")
        sale.campaign_period = campaign_period(item)
        sale.register_url = item.get("register_url")
        sale.source_url = item.get("source_url")
        sale.evidence = item.get("evidence")
        sale.source_payload = item
        sale.campaign_start = parse_date(item.get("campaign_start"))
        sale.campaign_end = parse_date(item.get("campaign_end"))
        sale.reward_rules = extract_rules(item.get("reward"), register_url=item.get("register_url"))
        sale.fetched_at = now
    await session.flush()
    return {"created": created, "updated": updated, "total": len(items)}


_REQUIRED_BENEFIT_FIELDS = ("bank", "card", "title", "reward", "source_url")


def _valid_benefit(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if any(
        not isinstance(item.get(f), str) or not item[f].strip() for f in _REQUIRED_BENEFIT_FIELDS
    ):
        return False
    start, end = parse_date(item.get("effective_start")), parse_date(item.get("effective_end"))
    return not (start and end and end < start)


async def import_benefits(
    session: AsyncSession, items: list[dict[str, Any]], *, fetched_at: datetime | None = None
) -> dict[str, int]:
    """Idempotent upsert of base benefits keyed by (card, title).

    effective_end may be absent: a standing reward with no published end date is valid.
    Entries missing a required field, or with an end before the start, are skipped and
    counted rather than aborting the whole import. `official_verified_at` is never set here.
    """
    now = fetched_at or datetime.now(UTC)
    created = updated = skipped = 0
    for item in items:
        if not _valid_benefit(item):
            skipped += 1
            continue
        card = await get_or_create_card(session, bank_name=item["bank"], name=item["card"])
        benefit = await benefits_repo.get_by_card_and_title(session, card.id, item["title"])
        if benefit is None:
            benefit = CardBenefit(card_id=card.id, title=item["title"])
            session.add(benefit)
            created += 1
        else:
            updated += 1
        benefit.reward = item["reward"]
        benefit.conditions = item.get("conditions")
        benefit.reward_rules = extract_rules(item["reward"])
        benefit.effective_start = parse_date(item.get("effective_start"))
        benefit.effective_end = parse_date(item.get("effective_end"))
        benefit.source_url = item["source_url"]
        benefit.source_payload = item
        benefit.fetched_at = now
    await session.flush()
    return {"created": created, "updated": updated, "skipped": skipped, "total": len(items)}


async def import_dataset(
    session: AsyncSession, dataset: dict[str, Any]
) -> dict[str, dict[str, int]]:
    """Import both kinds of data; a legacy list dataset simply has no benefits."""
    fetched_at = _generated_at(dataset)
    return {
        "benefits": await import_benefits(session, dataset["base_benefits"], fetched_at=fetched_at),
        "sales": await import_campaigns(session, dataset["campaigns"], fetched_at=fetched_at),
    }
