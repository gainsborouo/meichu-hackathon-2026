"""Where each crawler-enabled card's official base-benefit page lives.

A card's standing reward (its base benefit) is published on the bank's own card page, and
that page does not move like a campaign does. It is versioned here, in one place, as URLs
only: never a reward rate, cap or date. The crawler opens these pages itself and the model
may only quote what the backend read from them; nothing in this file can make a reward
true, it only says where to look.

The file has exactly two columns on purpose, so a rate has nowhere to be written down.
"""

from __future__ import annotations

import csv
import functools
from pathlib import Path

from app.services.card_identity import crawler_identities
from app.services.official_sources import is_official_url

SOURCES_PATH = Path(__file__).resolve().parents[1] / "data" / "card_benefit_sources.csv"
FIELDS = ("catalog_key", "url")


def load_base_benefit_sources(
    path: Path = SOURCES_PATH, *, identities=None
) -> dict[str, tuple[str, ...]]:
    """catalog_key -> official https URLs, validated against the card's own bank domain."""
    banks = {
        i.catalog_key: i.search_bank_name
        for i in (identities if identities is not None else crawler_identities())
    }
    sources: dict[str, list[str]] = {}
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f"{path.name}: header must be exactly {','.join(FIELDS)}")
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"line {line}: row does not match the header")
            key, url = row["catalog_key"].strip(), row["url"].strip()
            if key not in banks:
                raise ValueError(f"line {line}: {key!r} is not a crawler-enabled card")
            if not is_official_url(banks[key], url):
                raise ValueError(f"line {line}: {url!r} is not an official https URL for {key}")
            urls = sources.setdefault(key, [])
            if url in urls:
                raise ValueError(f"line {line}: duplicate url for {key}")
            urls.append(url)
    return {key: tuple(urls) for key, urls in sources.items()}


@functools.lru_cache(maxsize=1)
def _default_sources() -> dict[str, tuple[str, ...]]:
    return load_base_benefit_sources()


def base_urls_for(card_key: str) -> tuple[str, ...]:
    return _default_sources().get(card_key, ())
