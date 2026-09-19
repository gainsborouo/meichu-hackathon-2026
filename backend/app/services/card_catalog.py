import csv
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Card
from app.services.card_identity import apply_identities, load_identities

CATALOG_FIELDS = (
    "artwork_id",
    "bank_name",
    "name",
    "display_name",
    "issuer_en",
    "variant",
    "network",
    "tier",
    "official_image_url",
    "image_is_composite",
)
DEFAULT_CARD_CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "data" / "0003_card_catalog.csv"
)
CARD_UUID_NAMESPACE = uuid.UUID("f8cbb032-9362-5de4-b3b1-3f234b1f199e")
ALIASES_BY_ARTWORK_ID = {
    "ctbc-linepay-ve8710": ("中國信託商業銀行", "LINE Pay 聯名卡"),
    "fubon-momo": ("台北富邦銀行", "momo卡"),
    "esun-unicard-white": ("玉山銀行", "Unicard"),
    "esun-kumamon-ku01": ("玉山銀行", "玉山熊本熊卡"),
}
ALIAS_TO_ARTWORK_ID = {alias: artwork_id for artwork_id, alias in ALIASES_BY_ARTWORK_ID.items()}


@dataclass(frozen=True)
class CardCatalogRow:
    artwork_id: str
    bank_name: str
    name: str
    display_name: str
    issuer_en: str
    variant: str | None
    network: str | None
    tier: str | None
    official_image_url: str
    image_is_composite: bool


def _required(value: str | None, field: str, line: int) -> str:
    if value is None or not value.strip():
        raise ValueError(f"line {line}: {field} is required")
    return value


def _boolean(value: str | None, line: int) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"line {line}: image_is_composite must be true or false")


def load_card_catalog(path: Path = DEFAULT_CARD_CATALOG_PATH) -> list[CardCatalogRow]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != CATALOG_FIELDS:
            raise ValueError("card catalog has an invalid header")

        rows: list[CardCatalogRow] = []
        artwork_ids: set[str] = set()
        card_names: set[tuple[str, str]] = set()
        for line, raw in enumerate(reader, 2):
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError(f"line {line}: card catalog row does not match the header")
            artwork_id = _required(raw["artwork_id"], "artwork_id", line)
            bank_name = _required(raw["bank_name"], "bank_name", line)
            name = _required(raw["name"], "name", line)
            key = (bank_name, name)
            if artwork_id in artwork_ids:
                raise ValueError(f"line {line}: duplicate artwork_id {artwork_id}")
            if key in card_names:
                raise ValueError(f"line {line}: duplicate bank_name and name")
            artwork_ids.add(artwork_id)
            card_names.add(key)
            rows.append(
                CardCatalogRow(
                    artwork_id=artwork_id,
                    bank_name=bank_name,
                    name=name,
                    display_name=_required(raw["display_name"], "display_name", line),
                    issuer_en=_required(raw["issuer_en"], "issuer_en", line),
                    variant=raw["variant"] or None,
                    network=raw["network"] or None,
                    tier=raw["tier"] or None,
                    official_image_url=_required(
                        raw["official_image_url"], "official_image_url", line
                    ),
                    image_is_composite=_boolean(raw["image_is_composite"], line),
                )
            )
    return rows


async def import_card_catalog(session: AsyncSession, rows: list[CardCatalogRow]) -> dict[str, int]:
    identities = {i.artwork_id: i for i in load_identities()}
    missing = [row.artwork_id for row in rows if row.artwork_id not in identities]
    if missing:
        raise ValueError(f"card identity data has no catalog_key for: {', '.join(missing)}")

    created = updated = 0
    for row in rows:
        artwork_match = await session.scalar(select(Card).where(Card.artwork_id == row.artwork_id))
        name_match = await session.scalar(
            select(Card).where(Card.bank_name == row.bank_name, Card.name == row.name)
        )
        if name_match is None and row.artwork_id in ALIASES_BY_ARTWORK_ID:
            alias_bank, alias_name = ALIASES_BY_ARTWORK_ID[row.artwork_id]
            name_match = await session.scalar(
                select(Card).where(Card.bank_name == alias_bank, Card.name == alias_name)
            )

        if name_match is not None and name_match.artwork_id not in (None, row.artwork_id):
            raise ValueError(f"card catalog conflict for {row.artwork_id}")
        if (
            artwork_match is not None
            and name_match is not None
            and artwork_match.id != name_match.id
        ):
            raise ValueError(f"card catalog conflict for {row.artwork_id}")

        card = artwork_match or name_match
        if card is None:
            card = Card(
                id=uuid.uuid5(CARD_UUID_NAMESPACE, row.artwork_id),
                bank_name=row.bank_name,
                name=row.name,
            )
            session.add(card)
            created += 1
        else:
            updated += 1

        card.artwork_id = row.artwork_id
        card.display_name = row.display_name
        card.issuer_en = row.issuer_en
        card.variant = row.variant
        card.network = row.network
        card.tier = row.tier
        card.official_image_url = row.official_image_url
        card.image_is_composite = row.image_is_composite

    await session.flush()
    await apply_identities(session, [identities[row.artwork_id] for row in rows])
    return {"created": created, "updated": updated, "total": len(rows)}
