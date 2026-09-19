"""Statement analysis endpoint.

Uploaded images are never persisted. They are streamed to a temporary
directory, analyzed, and deleted when the request ends -- the analysis needs
the bytes for one call, and a credit-card statement is not something to leave
lying in a bucket.
"""

import logging
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.core.config import get_settings
from app.schemas.statements import StatementAnalysisResponse
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
