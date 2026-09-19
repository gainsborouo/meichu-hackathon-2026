"""Endpoint tests for statement analysis.

The agent itself is stubbed: these cover upload handling, validation and
error mapping, none of which should need a live model gateway to verify.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1.routes import statements as route
from app.core.config import get_settings
from app.main import app
from app.services.statement_agent import StatementAgentError

client = TestClient(app)
URL = f"{get_settings().api_v1_prefix}/statements/analyze"

GROUND_TRUTH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "credit-card-statement-analysis"
    / "evals"
    / "ground_truth"
)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def fake_agent(monkeypatch):
    """Replace the agent with one that reports what it was handed."""
    seen: dict = {}

    async def _fake(image_paths, **kwargs):
        paths = [Path(p) for p in image_paths]
        seen["paths"] = paths
        seen["kwargs"] = kwargs
        seen["existed"] = [p.is_file() for p in paths]
        return {
            "summary": {"totals": {"net_spend": 5728}},
            "summaries": [{"totals": {"net_spend": 5728}}] * len(paths),
            "trend": {"periods": []} if len(paths) > 1 else None,
            "narrative": "August came to NT$5,728.",
            "images": [str(p) for p in paths],
        }

    monkeypatch.setattr(route, "analyze_statement_images_async", _fake)
    return seen


def test_single_image_returns_summary_without_trend(fake_agent):
    r = client.post(URL, files={"files": ("aug.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["totals"]["net_spend"] == 5728
    assert body["trend"] is None
    assert body["narrative"]
    assert len(fake_agent["paths"]) == 1


def test_multiple_images_produce_a_trend(fake_agent):
    r = client.post(
        URL,
        files=[
            ("files", ("jun.jpg", JPEG, "image/jpeg")),
            ("files", ("jul.png", PNG, "image/png")),
            ("files", ("aug.jpg", JPEG, "image/jpeg")),
        ],
    )
    assert r.status_code == 200, r.text
    assert r.json()["trend"] is not None
    assert len(fake_agent["paths"]) == 3
    # Extensions follow the sniffed type, not the client's say-so.
    assert [p.suffix for p in fake_agent["paths"]] == [".jpg", ".png", ".jpg"]


def test_uploads_reach_the_agent_then_are_cleaned_up(fake_agent):
    client.post(URL, files={"files": ("aug.jpg", JPEG, "image/jpeg")})
    assert fake_agent["existed"] == [True], "file must exist while the agent runs"
    assert not fake_agent["paths"][0].exists(), "temp file must be gone afterwards"
    assert not fake_agent["paths"][0].parent.exists()


def test_non_image_is_rejected(fake_agent):
    r = client.post(URL, files={"files": ("notes.pdf", b"%PDF-1.7 not an image", "image/jpeg")})
    assert r.status_code == 415
    assert "not a JPEG" in r.json()["detail"]
    assert "paths" not in fake_agent, "agent must not run on a rejected upload"


def test_oversized_upload_is_rejected(fake_agent, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1024)
    r = client.post(URL, files={"files": ("big.jpg", JPEG + b"\x00" * 4096, "image/jpeg")})
    assert r.status_code == 413
    assert "limit" in r.json()["detail"]


def test_too_many_files_is_rejected(fake_agent, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_statement_files", 2)
    r = client.post(
        URL, files=[("files", (f"{i}.jpg", JPEG, "image/jpeg")) for i in range(3)]
    )
    assert r.status_code == 400
    assert "at most 2" in r.json()["detail"]


def test_agent_failure_becomes_502(monkeypatch):
    async def _boom(image_paths, **kwargs):
        raise StatementAgentError("Missing LLM_BASE_URL.")

    monkeypatch.setattr(route, "analyze_statement_images_async", _boom)
    r = client.post(URL, files={"files": ("aug.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 502
    assert "LLM_BASE_URL" in r.json()["detail"]


def test_missing_files_is_a_validation_error():
    assert client.post(URL).status_code == 422


def test_response_shape_matches_a_real_summary(fake_agent):
    """The schema must accept the actual output of the analysis scripts."""
    import sys

    sys.path.insert(0, str(GROUND_TRUTH.parents[1] / "scripts"))
    from analyze_transactions import aggregate

    real = aggregate(json.loads((GROUND_TRUTH / "2026-08.json").read_text(encoding="utf-8")))
    from app.schemas.statements import StatementAnalysisResponse

    model = StatementAnalysisResponse(
        summary=real, summaries=[real], trend=None, narrative="x"
    )
    assert model.summary["totals"]["net_spend"] == 5728
    assert model.summary["reconciliation"]["matches"] is True
