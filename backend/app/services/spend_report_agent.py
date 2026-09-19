"""ADK agent that writes the merged three-month spend summary.

Same split as the statement agent: a bundled script does the arithmetic across
cards and months, the model writes the two or three sentences of insight that
make the summary worth reading.

The agent is optional by design. `refresh_latest_spend_report` runs on every
analysis write, so an unreachable gateway must not fail the write -- callers
fall back to the deterministic renderer, which is truthful, just less insightful.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import InMemoryRunner
from google.genai import types

from app.core.config import get_settings

logger = logging.getLogger(__name__)

APP_NAME = "spend_report_writer"
SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "spend-report-summary"
DEFAULT_TIMEOUT_SECONDS = 120


class SpendReportAgentError(RuntimeError):
    """The agent could not run or returned nothing usable."""


def _load(name: str) -> ModuleType:
    path = SKILL_DIR / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_spend_skill_{name}", path)
    if spec is None or spec.loader is None:
        raise SpendReportAgentError(f"could not load skill script: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_aggregate_mod = _load("aggregate_analyses")
_render_mod = _load("render_report")

aggregate = _aggregate_mod.aggregate
InputError = _aggregate_mod.InputError
render = _render_mod.render


def build_facts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll the rows up. Raises InputError when there is nothing to summarize."""
    return aggregate({"rows": rows})


def render_deterministic(facts: dict[str, Any]) -> str:
    """The report without a model: heading, table, bullet facts. Always available."""
    return render(facts)


def _instruction() -> str:
    workflow = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    return (
        "You write a user's three-month spending summary.\n\n"
        f"{workflow}\n\n"
        "# How to work here\n\n"
        "You have no filesystem and no tools. The aggregation in step 2 and the "
        "skeleton in step 3 have already been run for you and are given below. "
        "Your job is step 4: write the two or three sentences of insight, then "
        "reproduce the skeleton beneath them unchanged.\n"
        "Reply with the finished Markdown report and nothing else -- no preamble, "
        "no code fences, no commentary about what you did."
    )


def _clean(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


async def write_report(
    facts: dict[str, Any],
    *,
    skeleton: str | None = None,
    model: str | None = None,
    timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Ask the model to turn facts into the written summary.

    Raises SpendReportAgentError when the gateway is unconfigured or unreachable,
    so the caller can fall back rather than storing a broken report.
    """
    import json

    load_dotenv()
    settings = get_settings()
    base_url, api_key = settings.llm_base_url, settings.llm_api_key
    model_name = model or settings.llm_model
    if not (base_url and api_key and model_name):
        raise SpendReportAgentError("LLM_BASE_URL / LLM_API_KEY / LLM_MODEL are not set")

    skeleton = skeleton if skeleton is not None else render_deterministic(facts)
    agent = Agent(
        name=APP_NAME,
        model=LiteLlm(model=f"openai/{model_name}", api_base=base_url, api_key=api_key),
        instruction=_instruction(),
        tools=[],
    )
    app = App(name=APP_NAME, root_agent=agent)
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="system", session_id="spend_report"
    )

    prompt = (
        "以下是彙總後的事實（facts.json）：\n\n```json\n"
        + json.dumps(facts, ensure_ascii=False, indent=2)
        + "\n```\n\n以下是已產生的報告骨架，請保留其中的表格與條列，"
        "並在標題下方加入你的兩到三句洞察：\n\n"
        + skeleton
    )

    chunks: list[str] = []

    async def _drive() -> None:
        async for event in runner.run_async(
            user_id=session.user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        chunks.append(part.text)

    try:
        await asyncio.wait_for(_drive(), timeout=timeout)
    except TimeoutError as exc:
        raise SpendReportAgentError(f"agent timed out after {timeout:.0f}s") from exc
    except Exception as exc:  # gateway errors surface as arbitrary client exceptions
        raise SpendReportAgentError(f"agent call failed: {exc}") from exc

    report = _clean("".join(chunks))
    if not report:
        raise SpendReportAgentError("agent returned an empty report")
    return report
