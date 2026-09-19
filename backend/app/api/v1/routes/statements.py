"""Statement analysis endpoint.

Uploaded images are never persisted. They are streamed to a temporary
directory, analyzed, and deleted when the request ends -- the analysis needs
the bytes for one call, and a credit-card statement is not something to leave
lying in a bucket.
"""

import logging
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.api.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.repositories import analyses as analyses_repo
from app.repositories import cards as cards_repo
from app.schemas.db import AnalysisRead, SpendReportRead
from app.schemas.statements import StatementAnalysisResponse
from app.services.spend_report import refresh_latest_spend_report
from app.services.statement_agent import (
    StatementAgentError,
    analyze_statement_images_async,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Magic bytes for the formats a phone or scanner actually produces. Checking
# these as well as the declared content type means a mislabelled upload fails
# here with a clear message instead of deep inside the model gateway.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff(head: bytes) -> str | None:
    for signature, mime in _SIGNATURES:
        if head.startswith(signature):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


async def _save(upload: UploadFile, directory: Path, limit: int) -> Path:
    """Stream one upload to disk, rejecting anything too large or not an image."""
    head = await upload.read(32)
    mime = _sniff(head)
    if mime is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"{upload.filename or 'file'} is not a JPEG, PNG, WebP or GIF image "
                f"(declared {upload.content_type or 'no content type'})"
            ),
        )

    suffix = {"image/jpeg": ".jpg", "image/png": ".png",
              "image/webp": ".webp", "image/gif": ".gif"}[mime]
    target = directory / f"{Path(upload.filename or 'statement').stem}{suffix}"

    written = 0
    with target.open("wb") as fh:
        chunk = head
        while chunk:
            written += len(chunk)
            if written > limit:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=(
                        f"{upload.filename or 'file'} exceeds the "
                        f"{limit // (1024 * 1024)}MB limit"
                    ),
                )
            fh.write(chunk)
            chunk = await upload.read(64 * 1024)
    return target


@router.post(
    "/statements/analyze",
    response_model=StatementAnalysisResponse,
    summary="Analyze credit-card statement images",
)
async def analyze_statements(
    files: Annotated[
        list[UploadFile],
        File(
            description=(
                "One or more statement images. Send several months together to "
                "get the cross-month trend."
            )
        ),
    ],
) -> StatementAnalysisResponse:
    """Turn statement images into a spending breakdown and a short written analysis.

    Send one image for a single month, or several for a trend across months.
    Nothing is stored: the images live in a temporary directory for the
    duration of this request only.
    """
    settings = get_settings()

    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="no files uploaded")
    if len(files) > settings.max_statement_files:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"at most {settings.max_statement_files} statements per request",
        )

    with tempfile.TemporaryDirectory(prefix="statements-") as tmp:
        directory = Path(tmp)
        paths = [await _save(upload, directory, settings.max_upload_bytes) for upload in files]

        try:
            result = await analyze_statement_images_async(
                paths, timeout=settings.statement_timeout_seconds
            )
        except StatementAgentError as exc:
            # Configuration problems and unusable model output both land here;
            # neither is the caller's fault, so don't dress it up as a 400.
            logger.exception("statement analysis failed")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return StatementAnalysisResponse(**result)


def _analysis_month(summary: dict[str, Any]) -> date:
    """The month a statement belongs to, as the first of that month.

    Prefers the statement's own period; falls back to the earliest transaction
    date in the daily series. Raises if neither is present, because guessing
    would silently overwrite a different month's analysis -- the
    (user_card_id, analysis_month) key makes that destructive.
    """
    start = (summary.get("period") or {}).get("start")
    if not start:
        daily = summary.get("daily") or []
        start = daily[0]["date"] if daily else None
    if not start:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Could not determine which month this statement covers; "
            "use PUT /me/cards/{id}/analyses to set it explicitly.",
        )
    return date.fromisoformat(str(start)[:10]).replace(day=1)


def _report(summary: dict[str, Any], narrative: str, *, newest: bool) -> str:
    """A per-month report. `report` is NOT NULL, so this never returns empty.

    The narrative describes the whole upload, so it is attached to the newest
    month only; earlier months fall back to their own insights, then to a plain
    statement of the totals.
    """
    insights = summary.get("insights") or []
    if insights:
        return "\n".join(str(i) for i in insights)
    if newest and narrative:
        return narrative
    totals = summary.get("totals") or {}
    return (
        f"{summary.get('currency', 'TWD')} {totals.get('net_spend', 0)} "
        f"across {totals.get('txn_count', 0)} transactions."
    )


def _normalize(name: str | None) -> str:
    """Bank names vary across sources (玉山銀行 vs 玉山商業銀行), so compare loosely."""
    return "".join((name or "").split()).replace("商業", "").replace("股份有限公司", "")


def _match_card(summary: dict[str, Any], user_cards: list) -> list:
    """User cards plausibly matching the card this statement belongs to.

    Matching is on the issuer only: the statement prints a masked number, and we
    never store the last four digits, so two cards from the same bank are
    genuinely indistinguishable here. That case returns both and the caller is
    asked to choose rather than being given a coin flip.
    """
    issuer = _normalize((summary.get("card") or {}).get("issuer"))
    if not issuer:
        return list(user_cards)
    matches = [
        uc
        for uc in user_cards
        if (bank := _normalize(uc.card.bank_name))
        and (bank in issuer or issuer in bank)
    ]
    return matches or list(user_cards)


def _resolve_card(summary: dict[str, Any], user_cards: list):
    candidates = _match_card(summary, user_cards)
    if len(candidates) == 1:
        return candidates[0]
    detected = summary.get("card") or {}
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        {
            "message": (
                "Could not tell which of your cards this statement belongs to. "
                "Resend with user_card_id."
            ),
            "detected": {"issuer": detected.get("issuer"), "last4": detected.get("last4")},
            "period": summary.get("period"),
            "candidates": [
                {
                    "user_card_id": str(uc.id),
                    "bank_name": uc.card.bank_name,
                    "name": uc.card.name,
                }
                for uc in candidates
            ],
        },
    )


@router.post(
    "/me/statements",
    response_model=list[AnalysisRead],
    status_code=status.HTTP_201_CREATED,
    summary="Analyze statement images and store them as analyses",
)
async def analyze_and_store(
    user: CurrentUser,
    session: SessionDep,
    files: Annotated[list[UploadFile], File(description="One or more statement images.")],
    user_card_id: Annotated[
        uuid.UUID | None,
        Form(description="Which of your cards these belong to. Omit to detect it from the image."),
    ] = None,
) -> list[AnalysisRead]:
    """Run the statement skill and persist the result against one of the user's cards.

    The card is not in the URL because the statement itself names it: the skill
    reads the issuer off the page, and each statement in the upload is matched
    to a card independently -- so two cards' statements can be sent together.

    Resolution order, most explicit first:

    1. `user_card_id` in the form, if given;
    2. the only card the user holds;
    3. the single card whose bank matches the issuer printed on the statement.

    Anything else returns 409 listing the candidates, because storing a
    statement against the wrong card silently corrupts that card's history.
    Nothing is written unless every statement in the upload resolves.
    """
    settings = get_settings()
    if len(files) > settings.max_statement_files:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"at most {settings.max_statement_files} statements per request",
        )

    user_cards = await cards_repo.list_user_cards(session, user.id)
    if not user_cards:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You have no cards yet. Add one with POST /me/cards first.",
        )

    explicit = None
    if user_card_id is not None:
        explicit = await cards_repo.get_user_card(session, user.id, user_card_id)
        if explicit is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Card not found")

    with tempfile.TemporaryDirectory(prefix="statements-") as tmp:
        directory = Path(tmp)
        paths = [await _save(upload, directory, settings.max_upload_bytes) for upload in files]
        try:
            result = await analyze_statement_images_async(
                paths, timeout=settings.statement_timeout_seconds
            )
        except StatementAgentError as exc:
            logger.exception("statement analysis failed")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    summaries = result["summaries"]
    # Resolve every statement before writing any, so a partial upload does not
    # leave half the months stored and the rest rejected.
    targets = [explicit or _resolve_card(summary, user_cards) for summary in summaries]
    months = [_analysis_month(summary) for summary in summaries]

    rows = []
    for index, (summary, target, month) in enumerate(zip(summaries, targets, months, strict=True)):
        rows.append(
            await analyses_repo.upsert_analysis(
                session,
                user_card_id=target.id,
                analysis_month=month,
                report=_report(summary, result["narrative"], newest=index == len(summaries) - 1),
                analysis_data={"summary": summary, "trend": result.get("trend")},
            )
        )
    await refresh_latest_spend_report(session, user.id)
    return [AnalysisRead.model_validate(r) for r in rows]


@router.get(
    "/me/statements",
    response_model=SpendReportRead,
    summary="Read the cached three-month spending summary",
)
async def read_spend_report(user: CurrentUser, session: SessionDep) -> SpendReportRead:
    """Return the merged summary cached on the user record.

    This is a plain read: the report is rebuilt whenever an analysis is written,
    so there is nothing to recompute here, and regenerating on GET would put an
    LLM call behind a request that callers reasonably expect to be cheap and
    repeatable. Re-upload a statement, or write an analysis, to refresh it.

    The counts come from the same window the report was built from, so a client
    can say "based on 3 months across 2 cards" without parsing the Markdown.
    """
    rows = await analyses_repo.latest_months_for_user(session, user.id, months=3)
    return SpendReportRead(
        report=user.latest_spend_report,
        months_covered=len({row.analysis_month for row in rows}),
        cards_covered=len({row.user_card_id for row in rows}),
    )
