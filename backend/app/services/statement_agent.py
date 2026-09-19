"""ADK agent that turns credit-card statement images into spending analysis.

Mirrors the wiring in ``llm-gateway/adk-example``: an ``Agent`` backed by
LiteLLM against an OpenAI-compatible gateway, driven by an ``InMemoryRunner``.
The agent's instruction is the credit-card-statement-analysis skill itself, and
the skill's two scripts are exposed as function tools so the arithmetic stays
deterministic instead of being done in the model's head.

Typical use:

    from app.services.statement_agent import analyze_statement

    result = analyze_statement("/path/to/statement.jpg")
    result["summary"]    # structured breakdown for the API/frontend
    result["narrative"]  # the short written analysis

Configuration comes from the environment (a .env file is loaded if present),
using the same names as the gateway example: LLM_BASE_URL, LLM_API_KEY,
LLM_MODEL.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import mimetypes
import os
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import InMemoryRunner
from google.genai import types

logger = logging.getLogger(__name__)

APP_NAME = "statement_analyst"
SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "credit-card-statement-analysis"

# Statement images are large; a single page can take a while through a gateway.
DEFAULT_TIMEOUT_SECONDS = 300

# Sent alongside the images. ADK encodes an image-only message correctly, but
# many OpenAI-compatible gateways treat a message whose content carries no text
# element as empty and reject the request ("Request must contain at least one
# non-empty message"). This carrier line keeps the wire format acceptable.
#
# It does not weaken the skill's "a bare image is the request" behaviour: the
# person still sends only an image, and the instruction still tells the agent
# what that means. Pass prompt="" to send the images alone if your gateway
# accepts that.
DEFAULT_PROMPT = "Analyze the attached credit-card statement image(s)."


class StatementAgentError(RuntimeError):
    """Raised when the agent cannot run or produced nothing usable."""


def _load_skill_module(name: str) -> ModuleType:
    """Import a script from the skill directory without putting it on sys.path.

    The skill owns these files, so they are loaded by location rather than
    copied into the backend -- editing the skill changes the behaviour here.
    """
    path = SKILL_DIR / "scripts" / f"{name}.py"
    if not path.is_file():
        raise StatementAgentError(f"skill script not found: {path}")
    spec = importlib.util.spec_from_file_location(f"_skill_{name}", path)
    if spec is None or spec.loader is None:
        raise StatementAgentError(f"could not load skill script: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_analyze = _load_skill_module("analyze_transactions")
_compare = _load_skill_module("compare_statements")


def _read_skill_text() -> str:
    """The SKILL.md workflow plus the category taxonomy, as one instruction.

    The taxonomy is inlined because the agent has no file-browsing step the way
    a coding assistant does; it needs the categories in context at the moment it
    classifies a row.
    """
    workflow = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    categories = (SKILL_DIR / "references" / "categories.md").read_text(encoding="utf-8")
    return (
        "You analyze credit-card statement images.\n\n"
        f"{workflow}\n\n"
        "# Category reference\n\n"
        f"{categories}\n\n"
        "# How to work here\n\n"
        "You have no filesystem. Do not try to write transactions.json or run "
        "the scripts yourself -- call summarize_statement with the transcribed "
        "rows instead, once per statement, and compare_statements when there is "
        "more than one. Never total the amounts yourself.\n"
        "An image with no accompanying text is a request for the full analysis.\n"
        "SKILL.md describes the output as summary.json plus a narrative. Here "
        "the summary is captured automatically from the tool result, so your "
        "reply is the narrative only: a few sentences of plain prose. Do not "
        "repeat the JSON, quote it in a code block, or restate the row-by-row "
        "table -- that is already stored, and the person reading your reply "
        "wants the finding, not the data dump."
    )


_FENCED_BLOCK = re.compile(r"```.*?```", re.DOTALL)


def _clean_narrative(text: str) -> str:
    """Strip fenced code blocks the model may have pasted into its reply.

    The instruction asks for prose only, but models reproduce the JSON they just
    produced often enough that the API contract shouldn't depend on their
    restraint -- the structured data is already captured from the tool result.
    """
    return _FENCED_BLOCK.sub("", text).strip()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or value.startswith("replace-with-"):
        raise StatementAgentError(
            f"Missing {name}. Set it in the environment or in backend/.env "
            "(see llm-gateway/adk-example/.env.example for the format)."
        )
    return value


def _image_part(path: Path) -> types.Part:
    if not path.is_file():
        raise FileNotFoundError(f"statement image not found: {path}")
    mime, _ = mimetypes.guess_type(path.name)
    if not (mime or "").startswith("image/"):
        # A wrong mime type fails inside the gateway with an opaque error, so
        # be explicit about what was passed.
        raise StatementAgentError(
            f"{path.name} does not look like an image (detected {mime or 'unknown'})"
        )
    return types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)


def _build_tools(collected: dict[str, Any]) -> list:
    """Build the tool functions, capturing their results as they are called.

    The structured output is taken at the tool boundary rather than parsed back
    out of the model's prose -- the model may summarize, round, or reformat when
    it writes, and the API contract should carry the numbers the script actually
    computed.
    """

    def summarize_statement(transactions: dict) -> dict:
        """Aggregate one statement's transcribed transactions into a summary.

        Call this once per statement, after reading every row off the image. It
        computes totals, the per-category breakdown, merchants, recurring
        charges, and a reconciliation check against the printed statement total.

        Args:
            transactions: An object with a "currency" string, a "statement"
                object (issuer, card_last4, period_start, period_end,
                printed_new_charges), and a "transactions" array. Each
                transaction needs date (YYYY-MM-DD), description, merchant,
                amount (always a positive number), category, and type
                (purchase, installment, fee, interest, refund, or payment).
                Leave previous-balance lines out; record bill payments with
                type "payment".

        Returns:
            On success {"status": "ok", "summary": {...}}. On failure
            {"status": "error", "message": "..."} naming the offending row, so
            the transcription can be corrected and this called again.
        """
        try:
            summary = _analyze.aggregate(transactions)
        except _analyze.InputError as exc:
            logger.warning("summarize_statement rejected input: %s", exc)
            return {"status": "error", "message": str(exc)}
        collected["summaries"].append(summary)
        return {"status": "ok", "summary": summary}

    def compare_statements(summaries: list[dict]) -> dict:
        """Compare two or more statement summaries into a spending trend.

        Call this when the user sent statements for more than one month, after
        summarize_statement has run for each one. It reports spend per period,
        category movement, merchants that are new or gone, and merchants
        charging in every period at a stable amount, which is what a
        subscription looks like from the outside.

        Args:
            summaries: The "summary" objects returned by summarize_statement,
                in any order; they are sorted by period internally.

        Returns:
            {"status": "ok", "trend": {...}} with periods, totals,
            category_trend, recurring_across_periods, new_merchants and
            dropped_merchants.
        """
        usable = summaries or collected["summaries"]
        if len(usable) < 2:
            return {
                "status": "error",
                "message": "need at least two statement summaries to compare",
            }
        labelled = [
            ((s.get("period") or {}).get("start") or str(i), s) for i, s in enumerate(usable)
        ]
        labelled.sort(key=lambda pair: str(pair[0])[:7])
        trend = _compare.compare([(str(label)[:7], s) for label, s in labelled])
        collected["trend"] = trend
        return {"status": "ok", "trend": trend}

    return [summarize_statement, compare_statements]


def _build_agent(tools: list, model: str | None) -> tuple[Agent, str]:
    load_dotenv()
    base_url = _require_env("LLM_BASE_URL")
    api_key = _require_env("LLM_API_KEY")
    model_name = model or _require_env("LLM_MODEL")

    agent = Agent(
        name=APP_NAME,
        # The gateway speaks OpenAI Chat Completions, so ADK uses LiteLLM.
        model=LiteLlm(model=f"openai/{model_name}", api_base=base_url, api_key=api_key),
        instruction=_read_skill_text(),
        tools=tools,
    )
    return agent, model_name


def _as_paths(image_paths: str | Path | Iterable[str | Path]) -> list[Path]:
    if isinstance(image_paths, (str, Path)):
        return [Path(image_paths)]
    paths = [Path(p) for p in image_paths]
    if not paths:
        raise ValueError("no statement images given")
    return paths


async def analyze_statement_images_async(
    image_paths: str | Path | Sequence[str | Path],
    *,
    model: str | None = None,
    user_id: str = "api",
    session_id: str = "statement_analysis",
    timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Analyze one or more statement images and return the structured result.

    Args:
        image_paths: A single image path, or several when the user sent
            multiple statements (each additional month enables the trend).
        model: Override the LLM_MODEL environment variable.
        prompt: Text sent with the images; defaults to DEFAULT_PROMPT. Pass ""
            to send images only, which some gateways reject.
        user_id, session_id: ADK session identifiers; irrelevant for one-shot
            calls but useful if you later keep conversation state.
        timeout: Seconds to wait before giving up, or None to wait forever.

    Returns:
        {"summary": ..., "summaries": [...], "trend": ... | None,
         "narrative": "...", "images": [...]}. "summary" is the first (or only)
        statement; "summaries" holds every one, oldest first.

    Raises:
        StatementAgentError: configuration is missing, or the agent finished
            without producing a summary.
        FileNotFoundError: an image path does not exist.
    """
    paths = _as_paths(image_paths)
    parts = [_image_part(p) for p in paths]
    carrier = DEFAULT_PROMPT if prompt is None else prompt
    if carrier:
        parts.append(types.Part.from_text(text=carrier))

    collected: dict[str, Any] = {"summaries": [], "trend": None}
    agent, model_name = _build_agent(_build_tools(collected), model)

    app = App(name=APP_NAME, root_agent=agent)
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id=user_id, session_id=session_id
    )

    logger.info("analyzing %d image(s) | model=%s", len(parts), model_name)

    narrative_buffer: list[str] = []

    async def _drive() -> None:
        async for event in runner.run_async(
            user_id=session.user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=parts),
        ):
            if not (event.content and event.content.parts):
                continue
            for part in event.content.parts:
                if part.function_call is not None or part.function_response is not None:
                    # Anything said before a tool call is working-out, not the
                    # answer; keep only what comes after the last one.
                    narrative_buffer.clear()
                elif part.text:
                    narrative_buffer.append(part.text)

    try:
        await asyncio.wait_for(_drive(), timeout=timeout)
    except TimeoutError as exc:
        raise StatementAgentError(
            f"agent did not finish within {timeout:.0f}s"
        ) from exc

    summaries = sorted(
        collected["summaries"], key=lambda s: str((s.get("period") or {}).get("start") or "")
    )
    if not summaries:
        raise StatementAgentError(
            "the agent produced no statement summary -- it may not have called "
            "summarize_statement, or the image was unreadable. Narrative was: "
            f"{''.join(narrative_buffer).strip()[:400] or '(empty)'}"
        )

    return {
        "summary": summaries[0],
        "summaries": summaries,
        "trend": collected["trend"],
        "narrative": _clean_narrative("".join(narrative_buffer)),
        "images": [str(p) for p in paths],
    }


def analyze_statement(
    image_path: str | Path,
    *,
    model: str | None = None,
    timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Analyze a single statement image. Blocking wrapper for sync callers.

    Do not call this from inside a running event loop (a FastAPI ``async def``
    endpoint, for example) -- await :func:`analyze_statement_images_async`
    there instead.
    """
    return asyncio.run(
        analyze_statement_images_async(
            image_path, model=model, timeout=timeout, prompt=prompt
        )
    )


def analyze_statements(
    image_paths: Sequence[str | Path],
    *,
    model: str | None = None,
    timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Analyze several statement images together, including the trend between them."""
    return asyncio.run(
        analyze_statement_images_async(
            image_paths, model=model, timeout=timeout, prompt=prompt
        )
    )


def main() -> None:
    """CLI for a quick check: python -m app.services.statement_agent <image>..."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = sys.argv[1:]
    if not args:
        print("usage: python -m app.services.statement_agent <image> [image ...]")
        raise SystemExit(2)

    import json

    data = asyncio.run(analyze_statement_images_async(args))
    print(json.dumps(data["summary"], ensure_ascii=False, indent=2))
    if data["trend"]:
        print("\n--- trend ---")
        print(json.dumps(data["trend"], ensure_ascii=False, indent=2))
    print("\n--- narrative ---")
    print(data["narrative"])


if __name__ == "__main__":
    main()
