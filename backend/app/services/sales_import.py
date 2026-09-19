import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Sale
from app.repositories.cards import get_or_create_card

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


def load_campaigns(path: Path = DEFAULT_CAMPAIGNS_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


async def import_campaigns(session: AsyncSession, items: list[dict[str, Any]]) -> dict[str, int]:
    """Idempotent upsert of cards + sales. JSON `bank`/`card` map to bank_name/card_name;
    the campaign JSON has no separate conditions field, so `conditions` stays NULL and
    everything (including confidence, recurrence, dates) is kept in source_payload."""
    now = datetime.now(UTC)
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
        sale.fetched_at = now
    await session.flush()
    return {"created": created, "updated": updated, "total": len(items)}
