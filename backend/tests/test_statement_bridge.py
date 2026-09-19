"""POST /me/cards/{id}/statements: run the skill, store the result."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.deps import get_current_user
from app.api.v1.routes import statements as route
from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.models import Card, UserAnalysis
from app.repositories import cards as cards_repo
from app.services.users import upsert_user

P = get_settings().api_v1_prefix
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _summary(month: str, net: int, issuer: str | None = "玉山銀行") -> dict:
    return {
        "currency": "TWD",
        "period": {"start": f"{month}-01", "end": f"{month}-28"},
        "card": {"issuer": issuer, "last4": "8223"},
        "totals": {"net_spend": net, "txn_count": 8},
        "insights": [f"{month}: spent NT${net}."],
    }


@pytest_asyncio.fixture
async def client(session, monkeypatch):
    app = create_app()
    state: dict = {"summaries": [_summary("2026-08", 5728)], "narrative": "八月花了 NT$5,728。"}

    async def _fake(image_paths, **kwargs):
        state["paths"] = [str(p) for p in image_paths]
        return {
            "summary": state["summaries"][0],
            "summaries": state["summaries"],
            "trend": state.get("trend"),
            "narrative": state["narrative"],
            "images": state["paths"],
        }

    monkeypatch.setattr(route, "analyze_statement_images_async", _fake)

    async def _session():
        yield session

    async def _user():
        return await upsert_user(session, google_uid="g", email="e@x.com")

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.state = state
        yield c


async def _my_card(session, bank: str = "玉山銀行", name: str = "Pi 信用卡") -> str:
    user = await upsert_user(session, google_uid="g", email="e@x.com")
    card = Card(bank_name=bank, name=name)
    session.add(card)
    await session.flush()
    row, _ = await cards_repo.add_user_card(session, user.id, card.id)
    return str(row.id)


async def test_stores_an_analysis_for_the_statement_month(client, session) -> None:
    await _my_card(session)  # only card -> resolved with no hint needed
    r = await client.post(f"{P}/me/statements",
                          files={"files": ("aug.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 201, r.text
    [row] = r.json()
    assert row["analysis_month"] == "2026-08-01"
    assert row["report"] == "2026-08: spent NT$5728."
    assert row["analysis_data"]["summary"]["totals"]["net_spend"] == 5728


async def test_refreshes_the_users_spend_report(client, session) -> None:
    await _my_card(session)
    await client.post(f"{P}/me/statements",
                      files={"files": ("aug.jpg", JPEG, "image/jpeg")})
    assert (await client.get(f"{P}/me")).json()["latest_spend_report"]


async def test_each_month_becomes_its_own_row(client, session) -> None:
    await _my_card(session)
    client.state["summaries"] = [
        _summary("2026-06", 5420), _summary("2026-07", 3360), _summary("2026-08", 5728)
    ]
    client.state["trend"] = {"periods": [{"label": "2026-06"}]}
    rows = (await client.post(f"{P}/me/statements", files=[
        ("files", ("a.jpg", JPEG, "image/jpeg")),
        ("files", ("b.jpg", JPEG, "image/jpeg")),
        ("files", ("c.jpg", JPEG, "image/jpeg")),
    ])).json()
    assert [r["analysis_month"] for r in rows] == ["2026-06-01", "2026-07-01", "2026-08-01"]
    assert all(r["analysis_data"]["trend"] for r in rows)


async def test_reuploading_the_same_month_updates_rather_than_duplicates(client, session) -> None:
    await _my_card(session)
    await client.post(f"{P}/me/statements",
                      files={"files": ("aug.jpg", JPEG, "image/jpeg")})
    client.state["summaries"] = [_summary("2026-08", 9999)]
    await client.post(f"{P}/me/statements",
                      files={"files": ("aug.jpg", JPEG, "image/jpeg")})
    rows = (await session.scalars(select(UserAnalysis))).all()
    assert len(rows) == 1 and rows[0].analysis_data["summary"]["totals"]["net_spend"] == 9999


async def test_statement_without_a_detectable_month_is_422(client, session) -> None:
    await _my_card(session)
    client.state["summaries"] = [{"currency": "TWD", "totals": {"net_spend": 1}}]
    r = await client.post(f"{P}/me/statements",
                          files={"files": ("x.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 422 and "which month" in r.json()["detail"]


async def test_explicit_card_must_be_one_you_hold(client, session) -> None:
    import uuid

    await _my_card(session)
    r = await client.post(
        f"{P}/me/statements",
        files={"files": ("x.jpg", JPEG, "image/jpeg")},
        data={"user_card_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404


async def test_no_cards_at_all_is_a_clear_409(client) -> None:
    r = await client.post(f"{P}/me/statements",
                          files={"files": ("x.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 409 and "POST /me/cards" in r.json()["detail"]


async def test_issuer_on_the_statement_picks_the_right_card(client, session) -> None:
    esun = await _my_card(session, "玉山銀行", "Pi 信用卡")
    await _my_card(session, "中國信託商業銀行", "LINE Pay 聯名卡")
    client.state["summaries"] = [_summary("2026-08", 5728, issuer="玉山商業銀行")]

    [row] = (await client.post(f"{P}/me/statements",
                               files={"files": ("x.jpg", JPEG, "image/jpeg")})).json()
    assert row["user_card_id"] == esun, "must match 玉山 despite the 商業 spelling difference"


async def test_ambiguous_issuer_asks_instead_of_guessing(client, session) -> None:
    await _my_card(session, "玉山銀行", "Pi 信用卡")
    await _my_card(session, "玉山銀行", "Unicard")
    client.state["summaries"] = [_summary("2026-08", 5728, issuer="玉山銀行")]

    r = await client.post(f"{P}/me/statements",
                          files={"files": ("x.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert len(detail["candidates"]) == 2
    assert detail["detected"] == {"issuer": "玉山銀行", "last4": "8223"}
    assert (await session.scalars(select(UserAnalysis))).all() == [], "must store nothing"


async def test_explicit_card_overrides_detection(client, session) -> None:
    await _my_card(session, "玉山銀行", "Pi 信用卡")
    other = await _my_card(session, "玉山銀行", "Unicard")
    [row] = (await client.post(
        f"{P}/me/statements",
        files={"files": ("x.jpg", JPEG, "image/jpeg")},
        data={"user_card_id": other},
    )).json()
    assert row["user_card_id"] == other


async def test_two_cards_in_one_upload_are_split(client, session) -> None:
    esun = await _my_card(session, "玉山銀行", "Pi 信用卡")
    ctbc = await _my_card(session, "中國信託商業銀行", "LINE Pay 聯名卡")
    client.state["summaries"] = [
        _summary("2026-08", 5728, issuer="玉山銀行"),
        _summary("2026-08", 1200, issuer="中國信託"),
    ]
    rows = (await client.post(f"{P}/me/statements", files=[
        ("files", ("a.jpg", JPEG, "image/jpeg")),
        ("files", ("b.jpg", JPEG, "image/jpeg")),
    ])).json()
    assert {r["user_card_id"] for r in rows} == {esun, ctbc}


async def test_falls_back_to_the_daily_series_for_the_month(client, session) -> None:
    await _my_card(session)
    client.state["summaries"] = [{
        "currency": "TWD", "totals": {"net_spend": 100, "txn_count": 2},
        "daily": [{"date": "2026-05-04", "amount": 100}],
    }]
    client.state["narrative"] = "五月摘要。"
    [row] = (await client.post(f"{P}/me/statements",
                               files={"files": ("x.jpg", JPEG, "image/jpeg")})).json()
    assert row["analysis_month"] == "2026-05-01"
    assert row["report"] == "五月摘要。"  # no insights -> newest month gets the narrative


# --- reading the cached report ------------------------------------------


async def test_report_is_null_before_any_analysis(client, session) -> None:
    await _my_card(session)
    body = (await client.get(f"{P}/me/statements")).json()
    assert body == {"report": None, "months_covered": 0, "cards_covered": 0}


async def test_report_appears_after_an_upload(client, session) -> None:
    await _my_card(session)
    await client.post(f"{P}/me/statements", files={"files": ("aug.jpg", JPEG, "image/jpeg")})
    body = (await client.get(f"{P}/me/statements")).json()
    assert "## 近三個月消費總結" in body["report"]
    assert body["months_covered"] == 1 and body["cards_covered"] == 1


async def test_counts_track_months_and_cards(client, session) -> None:
    await _my_card(session, "玉山銀行", "Pi 信用卡")
    await _my_card(session, "中國信託", "LINE Pay 聯名卡")
    client.state["summaries"] = [
        _summary("2026-06", 5420, issuer="玉山銀行"),
        _summary("2026-07", 3360, issuer="玉山銀行"),
        _summary("2026-08", 1200, issuer="中國信託"),
    ]
    await client.post(f"{P}/me/statements", files=[
        ("files", (f"{i}.jpg", JPEG, "image/jpeg")) for i in range(3)
    ])
    body = (await client.get(f"{P}/me/statements")).json()
    assert body["months_covered"] == 3 and body["cards_covered"] == 2


async def test_reading_the_report_does_not_call_the_model(client, session, monkeypatch) -> None:
    """GET must stay cheap and repeatable -- no LLM call behind a plain read."""
    from app.services import spend_report

    await _my_card(session)
    await client.post(f"{P}/me/statements", files={"files": ("aug.jpg", JPEG, "image/jpeg")})

    async def _explode(*args, **kwargs):
        raise AssertionError("GET /me/statements must not regenerate the report")

    monkeypatch.setattr(spend_report, "write_report", _explode)
    assert (await client.get(f"{P}/me/statements")).status_code == 200


async def test_matches_what_me_returns(client, session) -> None:
    await _my_card(session)
    await client.post(f"{P}/me/statements", files={"files": ("aug.jpg", JPEG, "image/jpeg")})
    assert (await client.get(f"{P}/me/statements")).json()["report"] == (
        await client.get(f"{P}/me")
    ).json()["latest_spend_report"]
