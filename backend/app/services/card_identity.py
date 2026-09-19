"""Canonical card identity and crawler metadata, from one central data file.

`cards.catalog_key` is the only identity everything else joins on. It is stable across
environments and independent of display names and of `artwork_id`. This module owns:

  * the key for every catalog card,
  * which cards the crawler may look up, and what bank / card wording to search with,
  * the raw (bank, card) spellings older data used, so legacy rows can be resolved to a key
    in ONE place instead of by ad-hoc mappings in each importer.

The same file seeds migration 0006, so the database and this module agree.
"""

from __future__ import annotations

import csv
import functools
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Card

IDENTITY_FIELDS = (
    "artwork_id",
    "catalog_key",
    "crawler_enabled",
    "search_bank_name",
    "search_card_name",
    "source_aliases",
)
DEFAULT_IDENTITY_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "data" / "0006_card_identity.csv"
)


@dataclass(frozen=True)
class CardIdentity:
    artwork_id: str
    catalog_key: str
    crawler_enabled: bool
    search_bank_name: str | None
    search_card_name: str | None
    source_aliases: tuple[tuple[str, str], ...]


def _parse_aliases(raw: str, line: int) -> tuple[tuple[str, str], ...]:
    pairs = []
    for entry in filter(None, raw.split("||")):
        bank, sep, card = entry.partition("::")
        if not sep or not bank.strip() or not card.strip():
            raise ValueError(f"line {line}: bad source alias {entry!r}")
        pairs.append((bank.strip(), card.strip()))
    return tuple(pairs)


def load_identities(path: Path = DEFAULT_IDENTITY_PATH) -> list[CardIdentity]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != IDENTITY_FIELDS:
            raise ValueError("card identity file has an invalid header")
        rows: list[CardIdentity] = []
        keys: set[str] = set()
        artworks: set[str] = set()
        aliases: dict[tuple[str, str], str] = {}
        for line, raw in enumerate(reader, 2):
            key, artwork = raw["catalog_key"].strip(), raw["artwork_id"].strip()
            if not key or not artwork:
                raise ValueError(f"line {line}: catalog_key and artwork_id are required")
            if key in keys or artwork in artworks:
                raise ValueError(f"line {line}: duplicate catalog_key or artwork_id")
            if raw["crawler_enabled"] not in {"true", "false"}:
                raise ValueError(f"line {line}: crawler_enabled must be true or false")
            enabled = raw["crawler_enabled"] == "true"
            search_bank = raw["search_bank_name"].strip() or None
            search_card = raw["search_card_name"].strip() or None
            if enabled and not (search_bank and search_card):
                raise ValueError(f"line {line}: crawler-enabled card needs search names")
            pairs = _parse_aliases(raw["source_aliases"], line)
            for pair in pairs:
                if aliases.setdefault(pair, key) != key:
                    raise ValueError(f"line {line}: alias {pair} already belongs to another card")
            keys.add(key)
            artworks.add(artwork)
            rows.append(CardIdentity(artwork, key, enabled, search_bank, search_card, pairs))
    return rows


@functools.lru_cache(maxsize=1)
def _default_identities() -> tuple[CardIdentity, ...]:
    return tuple(load_identities())


@functools.lru_cache(maxsize=1)
def _alias_index() -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for identity in _default_identities():
        for pair in identity.source_aliases:
            index[pair] = identity.catalog_key
        if identity.search_bank_name and identity.search_card_name:
            index[(identity.search_bank_name, identity.search_card_name)] = identity.catalog_key
    return index


def resolve_source_card_key(bank: str | None, card: str | None) -> str | None:
    """Catalog key for a raw (bank, card) spelling, or None. Exact match only."""
    if not bank or not card:
        return None
    return _alias_index().get((bank.strip(), card.strip()))


def crawler_identities() -> list[CardIdentity]:
    return [i for i in _default_identities() if i.crawler_enabled]


async def apply_identities(
    session: AsyncSession, identities: list[CardIdentity] | None = None
) -> dict[str, int]:
    """Write catalog_key and crawler metadata onto the catalog cards (matched by artwork_id).
    Never creates a card."""
    identities = list(identities if identities is not None else _default_identities())
    updated = 0
    for identity in identities:
        card = await session.scalar(select(Card).where(Card.artwork_id == identity.artwork_id))
        if card is None:
            continue
        card.catalog_key = identity.catalog_key
        card.crawler_enabled = identity.crawler_enabled
        card.search_bank_name = identity.search_bank_name
        card.search_card_name = identity.search_card_name
        updated += 1
    await session.flush()
    return {"updated": updated, "total": len(identities)}
