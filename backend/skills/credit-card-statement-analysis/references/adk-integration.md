# Using this skill from Google ADK

The skill's split maps onto an ADK agent directly: the model reads the images and produces
rows, function tools wrap the scripts for the arithmetic.

Verified against `google-adk` 2.9.2, the version in `llm-gateway/adk-example`. That example
reaches an OpenAI-compatible gateway through ADK's LiteLLM adapter, so the wiring below
follows the same pattern rather than the native Gemini client.

## The tools

ADK builds a tool's schema from the Python signature and docstring, so the docstring is
load-bearing — it's what the model reads to decide how to call this. Import the functions
directly rather than shelling out; both scripts are pure stdlib and return plain dicts.

```python
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent / "skills" / "credit-card-statement-analysis"
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from analyze_transactions import InputError, aggregate
from compare_statements import compare


def summarize_statement(transactions: dict) -> dict:
    """Aggregate one statement's transcribed transactions into a spending summary.

    Call this once per statement, after reading every row off the image. It
    computes totals, the per-category breakdown, merchants, recurring charges,
    and a reconciliation check against the statement's printed total.

    Args:
        transactions: An object with a "currency" string, a "statement" object
            (issuer, card_last4, period_start, period_end, printed_new_charges),
            and a "transactions" array. Each transaction needs date
            (YYYY-MM-DD), description, merchant, amount (always positive),
            category, and type (purchase, installment, fee, interest, refund,
            or payment). Leave out previous-balance lines; record bill payments
            with type "payment".

    Returns:
        On success, {"status": "ok", "summary": {...}}. On failure,
        {"status": "error", "message": "..."} naming the offending row so the
        transcription can be corrected and the tool called again.
    """
    try:
        return {"status": "ok", "summary": aggregate(transactions)}
    except InputError as exc:
        return {"status": "error", "message": str(exc)}


def compare_statements(summaries: list[dict]) -> dict:
    """Compare two or more statement summaries into a spending trend.

    Call this when the user sent statements for more than one month, after
    summarize_statement has run for each. It reports spend per period, category
    movement, merchants that are new or gone, and merchants charging in every
    period at a stable amount -- which is what a subscription looks like.

    Args:
        summaries: The "summary" objects returned by summarize_statement, in any
            order; they are sorted by period internally.

    Returns:
        {"status": "ok", "trend": {...}} with periods, totals, category_trend,
        recurring_across_periods, new_merchants and dropped_merchants.
    """
    labelled = [(s.get("period", {}).get("start", str(i))[:7], s) for i, s in enumerate(summaries)]
    return {"status": "ok", "trend": compare(sorted(labelled, key=lambda pair: pair[0]))}
```

Returning an error dict rather than raising is deliberate: the model gets a specific,
actionable message (`transactions[4].amount is negative...`) and can fix the rows and call
again, which is the recovery you want.

## The agent

Load SKILL.md as the instruction so the agent follows the workflow it documents instead of
a prompt string that drifts from it:

```python
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import InMemoryRunner

SKILL_DIR = Path(...)
WORKFLOW = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
CATEGORIES = (SKILL_DIR / "references" / "categories.md").read_text(encoding="utf-8")

agent = Agent(
    name="statement_analyst",
    model=LiteLlm(model=f"openai/{model_name}", api_base=base_url, api_key=api_key),
    instruction=(
        "You analyze credit-card statement images.\n\n"
        f"{WORKFLOW}\n\n# Category reference\n\n{CATEGORIES}\n\n"
        "Call summarize_statement for the arithmetic -- never total the amounts "
        "yourself. An image with no accompanying text is a request for the full "
        "analysis; produce the summary JSON and then the narrative."
    ),
    tools=[summarize_statement, compare_statements],
)

app = App(name="statement_analyst", root_agent=agent)
runner = InMemoryRunner(app=app)
```

The category reference is inlined because the agent has no file-browsing step the way a
coding assistant does; it needs the taxonomy in context at the moment it categorizes.

## Sending the images

Statements arrive as image parts. The user usually sends nothing else, so a text part is
optional — the instruction above already tells the agent what a bare image means:

```python
from google.genai import types

content = types.Content(
    role="user",
    parts=[types.Part.from_bytes(data=path.read_bytes(), mime_type="image/jpeg")
           for path in statement_paths],
)
```

Several images in one message is the multi-month case. The agent should transcribe each
separately, call `summarize_statement` per statement, then `compare_statements`.

**Send a text part with the images.** ADK encodes an image-only message correctly (the
content becomes a single `image_url` object), but many OpenAI-compatible gateways validate
that a message contains text and reject it otherwise:

```
litellm.BadRequestError: OpenAIException -
{"error":{"message":"Request must contain at least one non-empty message.", ...}}
```

A one-line carrier prompt alongside the image fixes it. This does not undo the "a bare
image is the request" behaviour — the person still sends only an image; the carrier text
is added by the calling code, and the instruction still tells the agent what to do with an
image that arrives without a question.

**Check that your gateway model accepts images.** Routing through LiteLLM to an
OpenAI-compatible endpoint means vision support depends on the model behind it; a
text-only model fails here in a way that has nothing to do with the skill.

## Wiring it to the FastAPI backend

- `POST /api/v1/statements/analyze` — accepts one or more `UploadFile`s, runs the agent,
  returns `{"summary": {...}, "trend": {...} | null, "narrative": "..."}`.
- The `daily` and `categories` arrays, and `category_trend` in the trend object, are
  already shaped for charting — the frontend can pass them to a chart component as-is.
- Keep uploaded statement images out of long-term storage unless the user asked for
  history. These are about as sensitive as personal documents get.
