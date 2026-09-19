import asyncio
import csv
import json
import logging
import os
import re
from enum import Enum
from typing import Optional
from dotenv import load_dotenv
from duckduckgo_search import DDGS
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import BaseModel, Field

# ---------------------------
# 0. Setup & Configuration
# ---------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("card_crawler")

load_dotenv()


def get_required_setting(name: str) -> str:
    val = os.environ.get(name)
    if not val or val.startswith("replace-with-"):
        raise SystemExit(f"Missing required environment variable: {name}")
    return val


BASE_URL = get_required_setting("LLM_BASE_URL")
API_KEY = get_required_setting("LLM_API_KEY")
MODEL_NAME = get_required_setting("LLM_MODEL")
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE_PATH = os.path.join(CURRENT_DIR, "cards.csv")


# ---------------------------
# 1. Pydantic Schemas
# ---------------------------
class RecurrenceType(str, Enum):
    ONCE = "once"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class CreditCardCampaign(BaseModel):
    id: str = Field(
        description="Unique id format example: bankcode-cardcode-YYYYMM-abcd"
    )
    bank: str = Field(description="Bank name")
    card: str = Field(description="Credit card name")
    title: str = Field(description="Campaign or privilege title")
    register_from: Optional[str] = Field(None, description="Registration start date (YYYY-MM-DD) or null")
    register_until: Optional[str] = Field(None, description="Registration end date (YYYY-MM-DD) or null")
    register_url: Optional[str] = Field(None, description="Registration URL or null")
    recurrence: RecurrenceType = Field(description="Recurrence: once, monthly, quarterly, yearly")
    campaign_start: str = Field(description="Start date (YYYY-MM-DD)")
    campaign_end: str = Field(description="End date (YYYY-MM-DD)")
    reward: str = Field(description="Reward details, rates, caps, and minimum spend conditions")
    quota_limited: bool = Field(description="True if limited quota or requires early registration")
    source_url: str = Field(description="Source URL")
    evidence: str = Field(description="Direct text quote from the page supporting the claim")
    confidence: float = Field(description="Confidence score (0.0 to 1.0)")


class CardCampaignResults(BaseModel):
    campaigns: list[CreditCardCampaign]


# ---------------------------
# 2. Tools for ADK Agent
# ---------------------------
def web_search(query: str) -> str:
    """Search the web for up-to-date credit card promotions and benefits.

    Args:
        query: The search keywords, e.g. "中國信託 LINE Pay 聯名卡 權益 活動".
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
            if not results:
                return "No search results found."
            formatted = []
            for r in results:
                formatted.append(f"Title: {r.get('title')}\nURL: {r.get('href')}\nSnippet: {r.get('body')}\n")
            return "\n---\n".join(formatted)
    except Exception as exc:
        return f"Search error: {exc}"


# ---------------------------
# 3. ADK Agent & Runner Builder
# ---------------------------
# 注意：這裡不要放任何包含大括號的 JSON Schema，避免 ADK 觸發 KeyError
AGENT_INSTRUCTION = """
You are an expert financial research assistant.
Your goal is to find active, up-to-date standard reward tiers and registration promotions for the requested credit card.

Instructions:
1. Always call `web_search` with precise keywords to locate the official bank terms and promotions.
2. Extract dates, caps, thresholds, and limits accurately.
3. You MUST reply ONLY with a valid raw JSON object conforming strictly to the requested JSON Schema provided in the prompt.
4. Do NOT wrap the JSON in Markdown code fences like ```json ... ```. Output raw JSON only.
"""

model = LiteLlm(
    model=f"openai/{MODEL_NAME}",
    api_base=BASE_URL,
    api_key=API_KEY,
)

card_agent = Agent(
    name="card_crawler_agent",
    model=model,
    instruction=AGENT_INSTRUCTION,
    tools=[web_search],
)

app = App(name="card_crawler_app", root_agent=card_agent)
runner = InMemoryRunner(app=app)


# ---------------------------
# 4. Agent Execution Handler
# ---------------------------
def clean_json_string(text: str) -> str:
    """Strip potential markdown code blocks if the model outputs them."""
    pattern = r"^```(?:json)?\s*(.*?)\s*```$"
    match = re.search(pattern, text.strip(), re.DOTALL)
    return match.group(1).strip() if match else text.strip()


async def process_single_card(bank: str, card: str, feature: str, session_id: str) -> list[dict]:
    schema_str = json.dumps(
        CardCampaignResults.model_json_schema(), ensure_ascii=False, indent=2
    )

    prompt = f"""Target Credit Card:
        Bank: {bank}
        Card: {card}
        Focus: {feature}

        CRITICAL TASK REQUIREMENTS:
        1. Do NOT just return static tier benefits. You MUST actively search for current "登錄" (registration) campaigns.
        2. Formulate multiple precise search queries via `web_search`:
        - Query 1: "{bank} {card} 登錄加碼 活動"
        - Query 2: "{bank} 信用卡 活動登錄專區 最新"
        - Query 3: "{bank} {card} 回饋 上限"
        3. For banks like 玉山銀行 or 國泰世華銀行, there are usually separate registration promotions for online shopping, dining, or travel. Extract each distinct campaign as an independent entry in the list.

        Strictly format your response to match this JSON Schema:
        {schema_str}
    """
    accumulated_text = ""

    async for event in runner.run_async(
        user_id="local_developer",
        session_id=session_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)],
        ),
    ):
        if event.author == card_agent.name and event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    accumulated_text += part.text

    cleaned_json = clean_json_string(accumulated_text)
    try:
        parsed_data = CardCampaignResults.model_validate_json(cleaned_json)
        return [c.model_dump() for c in parsed_data.campaigns]
    except Exception as exc:
        logger.warning("Failed to parse JSON output for %s %s: %s", bank, card, exc)
        logger.debug("Raw output received: %s", accumulated_text)
        return []


# ---------------------------
# 5. Dataset & Main Pipeline
# ---------------------------
def load_cards_from_csv(file_path: str) -> list[dict]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"找不到卡片清單檔案: {file_path}")

    cards = []
    current_bank = ""

    with open(file_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bank = row.get("發卡銀行", "").strip()
            if bank:
                current_bank = bank

            card_name = row.get("核心主力卡款", "").strip()
            feature = row.get("主要主打場景 / 特色", "").strip()

            if card_name:
                cards.append({
                    "bank": current_bank,
                    "card": card_name,
                    "feature": feature,
                })

    return cards


async def main():
    cards = load_cards_from_csv(CSV_FILE_PATH)
    all_campaigns = []

    print(f"Starting batch crawler for {len(cards)} credit cards via ADK...")

    for idx, item in enumerate(cards, start=1):
        session_id = f"session_card_{idx}"
        await runner.session_service.create_session(
            app_name=app.name,
            user_id="local_developer",
            session_id=session_id,
        )

        logger.info("[%d/%d] Fetching: %s - %s", idx, len(cards), item["bank"], item["card"])
        campaigns = await process_single_card(
            bank=item["bank"],
            card=item["card"],
            feature=item["feature"],
            session_id=session_id,
        )
        all_campaigns.extend(campaigns)
        logger.info("Found %d campaigns", len(campaigns))

        await asyncio.sleep(1)

    output_path = os.path.join(CURRENT_DIR, "credit_card_campaigns.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_campaigns, f, ensure_ascii=False, indent=2)

    print(f"\nCompleted! Saved {len(all_campaigns)} records to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())