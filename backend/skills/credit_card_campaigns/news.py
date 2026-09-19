"""Manual crawler: builds credit_card_campaigns.json from official bank pages.

    uv run python skills/credit_card_campaigns/news.py     (run from backend/)
    uv run python skills/credit_card_campaigns/news.py --bank "中國信託銀行" --card "LINE Pay 聯名卡"

With --bank and --card (both required together) only that one card from cards.csv is
crawled; without them every card in cards.csv is. Either way the result is merged into the
existing file, so cards that were not crawled keep their data.

Pipeline per card (all rules live in app/services/campaign_crawler.py):
  1. The backend generates the search queries (site:<official bank domain> + card + year).
  2. The backend opens the official https pages itself (app.services.official_pages).
  3. The model only extracts base benefits and campaigns from those opened pages.
  4. Every extracted item is re-validated against the opened pages; anything unofficial,
     unopened, expired, undated (campaigns) or without a parseable reward is dropped.
  5. Results are MERGED into the canonical file: a card that crawled successfully replaces its
     own entries, a card that failed keeps what the file already had. The merged JSON is
     written to a temp file and atomically replaces the canonical one, and only if this run
     produced at least one verified item and the old file was readable. Otherwise the old
     file is kept and the exit code is non-zero.

Import the result with `uv run python -m app.cli.import_sales`.
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402
from ddgs import DDGS  # noqa: E402
from google.adk.agents import Agent  # noqa: E402
from google.adk.apps import App  # noqa: E402
from google.adk.models.lite_llm import LiteLlm  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from app.services.campaign_crawler import crawl_card, finalize  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("card_crawler")

CURRENT_DIR = Path(__file__).resolve().parent
CSV_FILE_PATH = CURRENT_DIR / "cards.csv"
OUTPUT_PATH = CURRENT_DIR / "credit_card_campaigns.json"
SEARCH_RESULTS_PER_QUERY = 8

EXTRACTION_INSTRUCTION = """
You extract credit-card rewards from official bank web pages that were already opened for you.
The user message is one JSON object: today, bank, card, and pages (each with url, title, text).

Rules:
1. Use ONLY the supplied pages. Do not search, browse, or use outside knowledge.
2. "source_url" must be exactly one of the supplied page urls, the page the item comes from.
3. "evidence" must be a short, non-empty quote copied verbatim from that page's text.
4. base_benefits: the card's standing everyday rewards (for example 1% on general spending).
   They may have no end date (effective_end null), but you must also give "validity_evidence":
   a short quote copied verbatim from the page that shows the reward is in force TODAY, such as
   a start date on or before today, an end date on or after today, or wording like "目前" /
   "現行". If the page states an end date that has passed, or only describes past years, do
   not output the benefit at all; effective_end=null does not fix that.
5. campaigns: limited-time offers. Each needs campaign_start and campaign_end (YYYY-MM-DD)
   that the page actually states, and an "evidence" quote (verbatim) that contains, or sits
   right next to, those dates and the reward rate. If the page states no dates, or you only
   see a campaign title, do not output it. Never output a campaign whose campaign_end is
   before today.
6. Put the reward wording in "reward" exactly with its rates, caps and thresholds. Do not
   invent numbers. A reward with no rate or percentage is not usable, so skip it.
7. If a campaign needs registration, put the registration page in register_url.
8. Reply with ONE raw JSON object and nothing else (no code fences):
   base_benefits: list of objects with title, reward, conditions, effective_start,
   effective_end, source_url, evidence, validity_evidence, confidence (0 to 1).
   campaigns: list of objects with title, register_from, register_until, register_url,
   recurrence (once|monthly|quarterly|yearly), campaign_start, campaign_end, reward,
   quota_limited, source_url, evidence, confidence (0 to 1).
   Use empty lists when nothing qualifies.
"""


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or value.startswith("replace-with-"):
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def clean_json_string(text: str) -> str:
    """Strip potential markdown code blocks if the model outputs them."""
    match = re.search(r"^```(?:json)?\s*(.*?)\s*```$", text.strip(), re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def ddgs_search(query: str) -> list[str]:
    with DDGS() as client:
        results = client.text(query, max_results=SEARCH_RESULTS_PER_QUERY)
    return [r["href"] for r in results if isinstance(r.get("href"), str)]


def load_cards_from_csv(file_path: Path) -> list[dict]:
    if not file_path.exists():
        raise FileNotFoundError(f"找不到卡片清單檔案: {file_path}")

    cards = []
    current_bank = ""
    with file_path.open(mode="r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            bank = row.get("發卡銀行", "").strip()
            if bank:
                current_bank = bank
            card_name = row.get("核心主力卡款", "").strip()
            if card_name:
                cards.append({"bank": current_bank, "card": card_name})
    return cards


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl official bank pages into credit_card_campaigns.json."
    )
    parser.add_argument("--bank", help="bank name exactly as in cards.csv (requires --card)")
    parser.add_argument("--card", help="card name exactly as in cards.csv (requires --bank)")
    args = parser.parse_args(argv)
    if (args.bank is None) != (args.card is None):
        parser.error("--bank and --card must be given together")
    return args


def select_cards(cards: list[dict], bank: str | None, card: str | None) -> list[dict]:
    """All cards when no selection is given; otherwise only exact bank + card matches."""
    if bank is None and card is None:
        return cards
    return [c for c in cards if c["bank"] == bank and c["card"] == card]


def build_extractor(today: date):
    """Model call: extraction only, no tools. `today` is stated in the instruction."""
    instruction = (
        f"Today is {today.isoformat()}. "
        "Never output a campaign whose campaign_end is before today.\n" + EXTRACTION_INSTRUCTION
    )
    agent = Agent(
        name="card_extractor",
        model=LiteLlm(
            model=f"openai/{require_env('LLM_MODEL')}",
            api_base=require_env("LLM_BASE_URL"),
            api_key=require_env("LLM_API_KEY"),
        ),
        # A callable is used verbatim; a plain string's {...} would be read as ADK state.
        instruction=lambda _ctx: instruction,
    )
    app = App(name="card_extractor_app", root_agent=agent)
    runner = InMemoryRunner(app=app)
    counter = 0

    async def extract(payload: dict[str, Any]) -> Any:
        nonlocal counter
        counter += 1
        session_id = f"extract_{counter}"
        await runner.session_service.create_session(
            app_name=app.name, user_id="crawler", session_id=session_id
        )
        text = ""
        async for event in runner.run_async(
            user_id="crawler",
            session_id=session_id,
            new_message=types.Content(
                role="user",
                parts=[types.Part.from_text(text=json.dumps(payload, ensure_ascii=False))],
            ),
        ):
            if event.author == agent.name and event.content and event.content.parts:
                text += "".join(part.text or "" for part in event.content.parts)
        return json.loads(clean_json_string(text))

    return extract


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv()
    today = date.today()
    all_cards = load_cards_from_csv(CSV_FILE_PATH)
    cards = select_cards(all_cards, args.bank, args.card)

    if not cards:
        print(
            f"No card in {CSV_FILE_PATH.name} matches --bank {args.bank!r} --card {args.card!r}.",
            file=sys.stderr,
        )
        print("Available cards (bank / card):", file=sys.stderr)
        for c in all_cards:
            print(f"  {c['bank']} / {c['card']}", file=sys.stderr)
        return 2

    extract = build_extractor(today)
    if args.bank is None:
        logger.info("Crawling %d cards as of %s", len(cards), today)
    else:
        logger.info(
            "Crawling %d selected card%s as of %s",
            len(cards),
            "" if len(cards) == 1 else "s",
            today,
        )
    results = []
    for idx, item in enumerate(cards, start=1):
        logger.info("[%d/%d] %s - %s", idx, len(cards), item["bank"], item["card"])
        result = await crawl_card(
            item["bank"], item["card"], today=today, search=ddgs_search, extract=extract
        )
        logger.info(
            "  -> %d base benefits, %d campaigns, %d dropped%s",
            len(result.base_benefits),
            len(result.campaigns),
            len(result.dropped),
            f" (FAILED: {result.error})" if result.error else "",
        )
        results.append(result)
        if idx < len(cards):
            await asyncio.sleep(1)

    return finalize(results, OUTPUT_PATH, now=datetime.now(UTC))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
