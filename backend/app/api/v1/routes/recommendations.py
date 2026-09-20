"""POST /recommendations/stream: which held card to use for a purchase, as SSE.

It never creates a Calendar event, never calls the Calendar service and never writes
user_sales; `wait_suggestion.calendar_draft` is a proposal the client may submit to
POST /me/calendar/events after the user confirms.

Writes: stamping `official_verified_at` when a pick is backed by an official page the
backend opened, and (only when there is no current candidate) caching the result of a live
official lookup for the user's held cards into card_benefits / sales. That lookup never
creates a Card and never runs when a candidate already exists.
"""

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, SessionDep
from app.schemas.recommendations import RecommendationChatRequest, RecommendationRequest
from app.services import live_refresh
from app.services import purchase_recommendation as service
from app.services.recommendation_agent import get_recommendation_agent
from app.services.recommendation_chat import get_recommendation_chat_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/recommendations")

AgentDep = Annotated[service.RecommendationAgent, Depends(get_recommendation_agent)]
ChatAgentDep = Annotated[Any, Depends(get_recommendation_chat_agent)]
LookupDep = Annotated[live_refresh.LiveLookup, Depends(live_refresh.get_live_lookup)]


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def stream_recommendation_chat(
    body: RecommendationChatRequest,
    user: CurrentUser,
    agent: ChatAgentDep,
) -> StreamingResponse:
    """Stream a follow-up answer about an already displayed recommendation.

    It authenticates the caller but performs no database writes, candidate recomputation,
    live lookup, web search, or Calendar action.
    """
    del user  # Identity is deliberately not sent to the model.
    payload = body.model_dump(mode="json")

    async def events() -> AsyncIterator[str]:
        sent = False
        try:
            async for text in agent(payload):
                sent = True
                yield _sse("delta", {"text": text})
        except service.RecommendationError as exc:
            yield _sse("error", {"message": str(exc)})
            return
        except Exception:
            logger.exception("recommendation follow-up chat failed")
            yield _sse("error", {"message": "Chat failed."})
            return
        if not sent:
            yield _sse("error", {"message": "Chat returned no answer."})
            return
        yield _sse("done", {})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/stream")
async def stream_recommendation(
    body: RecommendationRequest,
    user: CurrentUser,
    session: SessionDep,
    agent: AgentDep,
    lookup: LookupDep,
) -> StreamingResponse:
    # Reads happen here, before streaming starts. Commit now so the connection goes
    # back to the pool instead of sitting idle-in-transaction for the whole model run
    # (up to two minutes). The generator reuses the session only to stamp
    # official_verified_at, and commits that itself.
    pre = await service.preprocess(session, user, body)
    # Fast/demo mode intentionally uses only imported data. In normal mode, only
    # when nothing current is on file do we look the user's own cards up live.
    targets = (
        []
        if pre.now_candidates or not body.web_search_enabled
        else await live_refresh.lookup_targets(session, user.id)
    )
    await session.commit()

    async def events() -> AsyncIterator[str]:
        nonlocal pre
        yield _sse(
            "searching",
            {
                "stage": "preprocessing",
                "mode": pre.mode,
                "now_candidates": len(pre.now_candidates),
                "future_candidates": len(pre.future_candidates),
                "web_search_enabled": body.web_search_enabled,
            },
        )
        if targets:
            yield _sse("searching", {"stage": "live_card_lookup", "cards": len(targets)})
            try:
                now = datetime.now(UTC)
                results = await live_refresh.run_lookup(targets, lookup, today=pre.today, now=now)
                await live_refresh.persist_results(session, results, now=now)
                refreshed = await service.preprocess(session, user, body, today=pre.today)
                await session.commit()
                refreshed.lookup_attempted = True
                pre = refreshed
                yield _sse(
                    "searching",
                    {
                        "stage": "reprocessing",
                        "now_candidates": len(pre.now_candidates),
                        "future_candidates": len(pre.future_candidates),
                    },
                )
            except Exception:
                # A lookup problem must never turn into a 500: fall back to what we had.
                logger.exception("live card lookup failed")
                await session.rollback()
                pre.lookup_attempted = True
        try:
            if pre.has_candidates and body.web_search_enabled:
                yield _sse("searching", {"stage": "official_verification"})
            result, verified = await service.recommend(pre, agent)
            await service.mark_verified(session, verified.sale_ids, verified.benefit_ids)
        except service.RecommendationError as exc:
            yield _sse("error", {"message": str(exc)})
            return
        except Exception:
            logger.exception("recommendation failed")
            yield _sse("error", {"message": "Recommendation failed."})
            return
        yield _sse("recommendation", result.model_dump(mode="json"))
        yield _sse("done", {})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
