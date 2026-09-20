"""Manual crawler: builds credit_card_campaigns.json from official bank pages.

    uv run python skills/credit_card_campaigns/news.py                       (run from backend/)
    uv run python skills/credit_card_campaigns/news.py --card-key ctbc-linepay

Which cards are crawled comes from the card catalog (cards.crawler_enabled, with the
search_bank_name / search_card_name wording), not from a hand-kept list. Every output entry
carries `card_key`, the catalog identity; the bank / card wording is kept only as
source_bank_name / source_card_name.

Pipeline per card (all rules live in app/services/campaign_crawler.py):
  1. The backend generates the search queries (site:<official bank domain> + card + year).
  2. The backend opens the official https pages itself (app.services.official_pages).
  3. The model only extracts base benefits and campaigns from those opened pages.
  4. Every extracted item is re-validated against the opened pages; anything unofficial,
     unopened, expired, undated (campaigns), unsupported by page text or without a
     parseable reward is dropped.
  5. Results are MERGED into the canonical file: a card that crawled successfully replaces
     its own entries (matched by card_key), a card that failed keeps what the file already
     had. The merged JSON is written to a temp file and atomically replaces the canonical
     one, and only if this run produced at least one verified item and the old file was
     readable. Otherwise the old file is kept and the exit code is non-zero.

Import the result with `uv run python -m app.cli.import_sales`.
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

from app.services.campaign_crawler import (  # noqa: E402
    CrawlTarget,
    crawl_card,
    crawler_targets,
    ddgs_search,
    finalize,
)
from app.services.card_extractor import ExtractorConfigError, build_extractor  # noqa: E402
from app.services.card_identity import crawler_identities  # noqa: E402
from app.services.crawl_sources import base_urls_for  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("card_crawler")

CURRENT_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = CURRENT_DIR / "credit_card_campaigns.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl official bank pages into credit_card_campaigns.json.",
        # "--card Unicard" must not silently mean "--card-key Unicard": a name is not a key.
        allow_abbrev=False,
    )
    parser.add_argument(
        "--card-key",
        help="catalog_key of one crawler-enabled card; omit to crawl every enabled card",
    )
    return parser.parse_args(argv)


async def load_targets() -> list[CrawlTarget]:
    """Crawler-enabled catalog cards. From the database when one is configured; otherwise
    from the card identity file the database is seeded from (so a checkout without a
    database, such as the scheduled workflow, still works)."""
    from app.core.config import get_settings

    if get_settings().database_url:
        from app.db.session import dispose_engine, get_sessionmaker

        try:
            async with get_sessionmaker()() as session:
                return await crawler_targets(session)
        finally:
            await dispose_engine()

    logger.warning("DATABASE_URL is not set; reading crawler cards from the card identity file")
    return [
        CrawlTarget(
            i.catalog_key, i.search_bank_name, i.search_card_name, base_urls_for(i.catalog_key)
        )
        for i in crawler_identities()
    ]


def select_targets(targets: list[CrawlTarget], card_key: str | None) -> list[CrawlTarget]:
    if card_key is None:
        return targets
    return [t for t in targets if t.card_key == card_key]


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv()
    today = date.today()
    all_targets = await load_targets()
    targets = select_targets(all_targets, args.card_key)

    if not targets:
        print(f"No crawler-enabled card has card_key {args.card_key!r}.", file=sys.stderr)
        print("Available card keys:", file=sys.stderr)
        for t in all_targets:
            print(f"  {t.card_key}  ({t.bank} / {t.card})", file=sys.stderr)
        return 2

    try:
        extract = build_extractor(today)
    except ExtractorConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.card_key is None:
        logger.info("Crawling %d cards as of %s", len(targets), today)
    else:
        logger.info(
            "Crawling %d selected card%s as of %s",
            len(targets),
            "" if len(targets) == 1 else "s",
            today,
        )
    results = []
    for idx, target in enumerate(targets, start=1):
        logger.info(
            "[%d/%d] %s (%s / %s)", idx, len(targets), target.card_key, target.bank, target.card
        )
        result = await crawl_card(target, today=today, search=ddgs_search, extract=extract)
        logger.info(
            "  -> %d base benefits, %d campaigns, %d dropped%s",
            len(result.base_benefits),
            len(result.campaigns),
            len(result.dropped),
            f" (FAILED: {result.error})" if result.error else "",
        )
        results.append(result)
        if idx < len(targets):
            await asyncio.sleep(1)

    return finalize(results, OUTPUT_PATH, now=datetime.now(UTC))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
