"""The LLM extraction step shared by the crawler and live refresh.

The model gets pages the backend already opened and returns base benefits / campaigns as
JSON. It has no tools: it cannot search or browse, so it cannot introduce a source the
backend did not read. Everything it returns is re-validated by
app.services.campaign_crawler.validate_extraction.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any

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


class ExtractorConfigError(RuntimeError):
    """The LLM gateway is not configured (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL)."""


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or value.startswith("replace-with-"):
        raise ExtractorConfigError(f"Missing required environment variable: {name}")
    return value


def clean_json_string(text: str) -> str:
    """Strip potential markdown code blocks if the model outputs them."""
    match = re.search(r"^```(?:json)?\s*(.*?)\s*```$", text.strip(), re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def build_extractor(today: date):
    """Async callable payload -> parsed JSON. `today` is stated in the instruction."""
    from google.adk.agents import Agent
    from google.adk.apps import App
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import InMemoryRunner
    from google.genai import types

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
