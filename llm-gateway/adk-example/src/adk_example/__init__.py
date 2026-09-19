"""A minimal Google ADK agent backed by an OpenAI-compatible endpoint."""

import asyncio
import logging
import os
import sys
from datetime import datetime
from time import perf_counter
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import InMemoryRunner
from google.genai import types

APP_NAME = "adk_example"
logger = logging.getLogger(__name__)


def get_current_time(timezone: str) -> dict[str, str]:
    """Return the current time for an IANA timezone.

    Use this tool whenever the user asks for the current time. For example,
    ``Asia/Taipei`` or ``America/New_York``.
    """
    now = datetime.now(ZoneInfo(timezone))
    return {"timezone": timezone, "time": now.isoformat(timespec="seconds")}


def get_required_setting(name: str) -> str:
    value = os.environ.get(name)
    if not value or value.startswith("replace-with-"):
        raise SystemExit(
            f"Missing {name}. Copy .env.example to .env and fill in your gateway key."
        )
    return value


async def run(prompt: str) -> None:
    load_dotenv()

    base_url = get_required_setting("LLM_BASE_URL")
    api_key = get_required_setting("LLM_API_KEY")
    model_name = get_required_setting("LLM_MODEL")

    # The gateway speaks OpenAI Chat Completions, so ADK uses its LiteLLM adapter.
    # `openai/` is a LiteLLM provider prefix; the gateway receives `model_name`.
    model = LiteLlm(
        model=f"openai/{model_name}",
        api_base=base_url,
        api_key=api_key,
    )
    agent = Agent(
        name="assistant",
        model=model,
        instruction=(
            "You are a concise, helpful assistant. "
            "When the user asks for the current time, call get_current_time "
            "with an IANA timezone before answering."
        ),
        tools=[get_current_time],
    )
    app = App(name=APP_NAME, root_agent=agent)
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id="local_developer",
        session_id="example_session",
    )

    started_at = perf_counter()
    first_model_event_at: float | None = None
    logger.info("request sent | model=%s", model_name)

    async for event in runner.run_async(
        user_id=session.user_id,
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part.from_text(text=prompt)]
        ),
    ):
        if event.author == agent.name and first_model_event_at is None:
            first_model_event_at = perf_counter()
            logger.info(
                "first model event received after %.2fs",
                first_model_event_at - started_at,
            )
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    print(f"{event.author}: {part.text}")

    completed_at = perf_counter()
    logger.info("request completed in %.2fs", completed_at - started_at)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    prompt = " ".join(sys.argv[1:]) or "Please introduce yourself in one sentence."
    asyncio.run(run(prompt))
