"""ADK agent that ranks the backend's pre-approved candidate cards.

Wiring mirrors credit_card_campaigns/news.py and statement_agent.py: an ADK `Agent`
on LiteLLM against the OpenAI-compatible gateway configured by LLM_BASE_URL,
LLM_API_KEY and LLM_MODEL, with a DuckDuckGo `web_search` function tool. No extra
key is needed. The instruction is the credit-card-purchase-recommendation skill.

The agent only *proposes*; app.services.purchase_recommendation validates it.

Verification is page-level: an official source only counts if the backend itself opened
it during the run with `open_official_page` and got a readable 2xx page (`opened_urls`).
A URL that merely appeared in search results, or that the model wrote down, is not enough.

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

from app.services.official_pages import fetch_page
from app.services.official_sources import normalize_url
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


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WINDOW = 6
# Vocabulary a campaign query legitimately needs; excluded from the copied-text check.
_GENERIC_TERMS = ("信用卡", "回饋", "活動", "登錄", "官方", "優惠", "加碼", "現金回饋", "點數")


def query_problem(
    query: str,
    held_card_names: list[str],
    *,
    price: float | None = None,
    allowed_terms: tuple[str, ...] = (),
    context_text: str = "",
) -> str | None:
    """Why a search query must not be sent, or None if it is acceptable.

    Queries may carry product, store, one card, bank, and campaign terms. Enforced here,
    at the tool boundary, rather than trusting the model to follow the instruction:
    no email, no purchase price, no spending-sized numbers, no several-cards lists, and
    no text copied from the user's spending summary (`context_text`). `allowed_terms`
    (product, store, card, bank, campaign wording) are exempt from the copied-text check.
    """
    if "@" in query:
        return "queries must not contain email addresses"
    if len(query) > MAX_QUERY_CHARS:
        return "query too long; use only product, store, card, bank and campaign terms"
    if sum(1 for name in held_card_names if name and name in query) > 1:
        return "query names several cards; search for one card at a time"

    allowed_text = " ".join(allowed_terms).lower()
    for raw_number in _NUMBER.findall(query):
        digits = raw_number.replace(",", "")
        try:
            value = float(digits)
        except ValueError:
            continue
        if price is not None and abs(value - price) < 0.005:
            return "queries must not contain the purchase price"
        whole = digits.split(".")[0]
        is_year = len(whole) == 4 and 1990 <= int(whole) <= 2100
        if len(whole) >= 4 and not is_year and digits not in allowed_text:
            return "queries must not contain amounts"

    if context_text:
        remainder = query.lower()
        for term in sorted((*allowed_terms, *_GENERIC_TERMS), key=len, reverse=True):
            if term:
                remainder = remainder.replace(term.lower(), " ")
        compact = re.sub(r"\s+", "", remainder)
        context = re.sub(r"\s+", "", context_text.lower())
        if any(
            compact[i : i + _WINDOW] in context for i in range(max(len(compact) - _WINDOW + 1, 0))
        ):
            return "queries must not reuse spending-summary text"
    return None


def screening_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    """What query_problem needs, derived from the same payload the model receives."""
    request = payload.get("request", {})
    terms: list[str] = [str(request.get("product_name", "")), str(request.get("store_name", ""))]
    cards = list(payload.get("held_cards", []))
    for candidate in (*payload.get("now_candidates", []), *payload.get("future_candidates", [])):
        cards.append(candidate.get("card", {}))
        terms += [str(candidate.get("title") or ""), str(candidate.get("conditions") or "")]
    for card in cards:
        terms += [str(card.get("bank_name") or ""), str(card.get("name") or "")]
    return {
        "held_card_names": [c["name"] for c in payload.get("held_cards", [])],
        "price": request.get("price"),
        "allowed_terms": tuple(t for t in terms if t),
        "context_text": json.dumps(payload.get("spend_context", {}), ensure_ascii=False),
    }


def _make_web_search(screen: dict[str, Any]):
    def web_search(query: str) -> str:
        """Search the web to confirm a credit-card campaign on the bank's official site.

        Args:
            query: Product, store, card name, bank name and campaign terms only.
        """
        problem = query_problem(query, **screen)
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


MAX_PAGE_CHARS_TO_MODEL = 3000


def _make_open_official_page(opened: set[str]):
    def open_official_page(url: str) -> str:
        """Open an official bank page and return its text, to confirm a campaign.

        Only https pages on a supported bank's own domain can be opened (redirects
        included). Cite as an official source only the final URL that this returns
        after OPENED; a URL that redirected, or that returned NOT OPENED, is not evidence.

        Args:
            url: The bank page URL, e.g. one found by web_search or a candidate's
                registration_url / source_url.
        """
        result = fetch_page(url)
        if not result.ok:
            return f"NOT OPENED: {result.error}"
        # Evidence is the FINAL url only: the page the backend actually read. The URL that
        # was asked for, and any redirect hop in between, prove nothing about that page.
        opened.add(normalize_url(result.url))
        return (
            f"OPENED {result.url}\n"
            "Cite this final URL (not the one you asked for) as the official source.\n"
            f"Title: {result.title}\n\n{result.text[:MAX_PAGE_CHARS_TO_MODEL]}"
        )

    return open_official_page


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
    opened: set[str] = set()

    text = read_skill_text()
    agent = Agent(
        name=APP_NAME,
        model=LiteLlm(model=f"openai/{model_name}", api_base=base_url, api_key=api_key),
        # A callable provider is used verbatim; a plain string would have its {...}
        # (the JSON examples in the contract) treated as ADK state placeholders.
        instruction=lambda _ctx: text,
        tools=[_make_web_search(screening_inputs(payload)), _make_open_official_page(opened)],
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
    raw = parse_model_json("".join(answer))
    # Evidence of what the backend really opened. Set here, after parsing, so nothing the
    # model wrote under this key can survive.
    raw["opened_urls"] = sorted(opened)
    return raw


def get_recommendation_agent() -> RecommendationAgent:
    """FastAPI dependency; tests override it with a fake."""
    return run_recommendation_agent
