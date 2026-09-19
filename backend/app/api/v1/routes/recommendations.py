"""POST /recommendations/stream: which held card to use for a purchase, as SSE.

It never creates a Calendar event, never calls the Calendar service and never writes
user_sales; `wait_suggestion.calendar_draft` is a proposal the client may submit to
POST /me/calendar/events after the user confirms. Its one write is stamping
`sales.official_verified_at` when a pick is backed by an allow-listed official URL.

Live search verifies and supplements campaigns already in `sales`; it does not add new
ones. The latest campaigns come from the crawler + `import_sales`.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, SessionDep
from app.schemas.recommendations import RecommendationRequest
from app.services import purchase_recommendation as service
from app.services.recommendation_agent import get_recommendation_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/recommendations")

AgentDep = Annotated[service.RecommendationAgent, Depends(get_recommendation_agent)]


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def stream_recommendation(
    body: RecommendationRequest, user: CurrentUser, session: SessionDep, agent: AgentDep
) -> StreamingResponse:
    # Reads happen here, before streaming starts. Commit now so the connection goes
    # back to the pool instead of sitting idle-in-transaction for the whole model run
    # (up to two minutes). The generator reuses the session only to stamp
    # official_verified_at, and commits that itself.
    pre = await service.preprocess(session, user, body)
    await session.commit()

    async def events() -> AsyncIterator[str]:
        yield _sse(
            "searching",
            {
                "stage": "preprocessing",
                "mode": pre.mode,
                "now_candidates": len(pre.now_candidates),
                "future_candidates": len(pre.future_candidates),
            },
        )
        try:
            if pre.has_candidates:
                yield _sse("searching", {"stage": "official_verification"})
            result, verified = await service.recommend(pre, agent)
            await service.mark_verified(session, verified)
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
