"""ADK agent that ranks the backend's pre-approved candidate cards.

Wiring mirrors credit_card_campaigns/news.py and statement_agent.py: an ADK `Agent`
on LiteLLM against the OpenAI-compatible gateway configured by LLM_BASE_URL,
LLM_API_KEY and LLM_MODEL, with a DuckDuckGo `web_search` function tool. No extra
key is needed. The instruction is the credit-card-purchase-recommendation skill.

The agent only *proposes*; app.services.purchase_recommendation validates it.

Live search here verifies and supplements campaigns already in `sales`. It does not
discover or store new campaigns; the crawler (news.py) + import_sales own that.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import InMemoryRunner
from google.genai import types

from app.services.purchase_recommendation import RecommendationAgent, RecommendationError

logger = logging.getLogger(__name__)

APP_NAME = "purchase_recommender"
SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "credit-card-purchase-recommendation"
DEFAULT_TIMEOUT_SECONDS = 120
MAX_QUERY_CHARS = 150

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def read_skill_text() -> str:
    parts = [(SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")]
    parts[0] = _FRONTMATTER.sub("", parts[0])
    for name in ("input-contract", "output-contract", "official-source-policy"):
        parts.append((SKILL_DIR / "references" / f"{name}.md").read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    match = _FENCE.match(cleaned)
    if match:
        cleaned = match.group(1).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RecommendationError("model did not return valid JSON") from exc
    if not isinstance(value, dict):
        raise RecommendationError("model did not return a JSON object")
    return value


def query_problem(query: str, held_card_names: list[str]) -> str | None:
    """Why a search query must not be sent, or None if it is acceptable.

    Queries may carry product, store, one card, bank, and campaign terms. They must not
    carry an email, and must not enumerate the user's cards.
    """
    if "@" in query:
        return "queries must not contain email addresses"
    if len(query) > MAX_QUERY_CHARS:
        return "query too long; use only product, store, card, bank and campaign terms"
    if sum(1 for name in held_card_names if name and name in query) > 1:
        return "query names several cards; search for one card at a time"
    return None


def _make_web_search(held_card_names: list[str]):
    def web_search(query: str) -> str:
        """Search the web to confirm a credit-card campaign on the bank's official site.

        Args:
            query: Product, store, card name, bank name and campaign terms only.
        """
        problem = query_problem(query, held_card_names)
        if problem:
            return f"Search refused: {problem}."
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
        except Exception as exc:  # network / rate limit: the model may proceed unverified
            return f"Search error: {exc}"
        if not results:
            return "No search results found."
        return "\n---\n".join(
            f"Title: {r.get('title')}\nURL: {r.get('href')}\nSnippet: {r.get('body')}"
            for r in results
        )

    return web_search


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or value.startswith("replace-with-"):
        raise RecommendationError(f"Missing {name}. Set it in the environment or backend/.env.")
    return value


async def run_recommendation_agent(
    payload: dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> dict[str, Any]:
    load_dotenv()
    base_url = _require_env("LLM_BASE_URL")
    api_key = _require_env("LLM_API_KEY")
    model_name = _require_env("LLM_MODEL")
    held = [c["name"] for c in payload.get("held_cards", [])]

    text = read_skill_text()
    agent = Agent(
        name=APP_NAME,
        model=LiteLlm(model=f"openai/{model_name}", api_base=base_url, api_key=api_key),
        # A callable provider is used verbatim; a plain string would have its {...}
        # (the JSON examples in the contract) treated as ADK state placeholders.
        instruction=lambda _ctx: text,
        tools=[_make_web_search(held)],
    )
    app = App(name=APP_NAME, root_agent=agent)
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="api", session_id="purchase_recommendation"
    )

    answer: list[str] = []

    async def _drive() -> None:
        message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=json.dumps(payload, ensure_ascii=False))],
        )
        async for event in runner.run_async(
            user_id=session.user_id, session_id=session.id, new_message=message
        ):
            if not (event.content and event.content.parts):
                continue
            for part in event.content.parts:
                if part.function_call is not None or part.function_response is not None:
                    answer.clear()
                elif part.text:
                    answer.append(part.text)

    try:
        await asyncio.wait_for(_drive(), timeout=timeout)
    except TimeoutError as exc:
        raise RecommendationError(f"agent did not finish within {timeout:.0f}s") from exc
    return parse_model_json("".join(answer))


def get_recommendation_agent() -> RecommendationAgent:
    """FastAPI dependency; tests override it with a fake."""
    return run_recommendation_agent
