import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.models import Card
from app.repositories import analyses as analyses_repo
from app.repositories import cards as cards_repo
from app.repositories import sales as sales_repo
from app.schemas.db import (
    AnalysisRead,
    AnalysisUpsert,
    UserCardCreate,
    UserCardRead,
    UserRead,
    UserSaleRead,
    UserUpdate,
)
from app.services.spend_report import refresh_latest_spend_report

router = APIRouter(prefix="/me")


@router.get("", response_model=UserRead)
async def read_me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch("", response_model=UserRead)
async def update_me(body: UserUpdate, user: CurrentUser, session: SessionDep) -> UserRead:
    user.registration_campaigns_enabled = body.registration_campaigns_enabled
    await session.flush()
    return UserRead.model_validate(user)


@router.get("/cards", response_model=list[UserCardRead])
async def list_my_cards(user: CurrentUser, session: SessionDep) -> list[UserCardRead]:
    rows = await cards_repo.list_user_cards(session, user.id)
    return [UserCardRead.model_validate(r) for r in rows]


@router.post("/cards", response_model=UserCardRead, status_code=status.HTTP_201_CREATED)
async def add_my_card(
    body: UserCardCreate, user: CurrentUser, session: SessionDep, response: Response
) -> UserCardRead:
    if await session.get(Card, body.card_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Card not found")
    row, created = await cards_repo.add_user_card(session, user.id, body.card_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return UserCardRead.model_validate(row)


@router.delete("/cards/{user_card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_my_card(user_card_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> None:
    row = await cards_repo.get_user_card(session, user.id, user_card_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Card not found")
    await cards_repo.delete_user_card(session, row)
    await refresh_latest_spend_report(session, user.id)


@router.get("/cards/{user_card_id}/analyses", response_model=list[AnalysisRead])
async def list_analyses(
    user_card_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=120)] = 3,
) -> list[AnalysisRead]:
    """Newest first; defaults to the latest three months, full history via `limit`."""
    if await cards_repo.get_user_card(session, user.id, user_card_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Card not found")
    rows = await analyses_repo.list_for_user_card(session, user_card_id, limit)
    return [AnalysisRead.model_validate(r) for r in rows]


@router.put("/cards/{user_card_id}/analyses", response_model=AnalysisRead)
async def upsert_analysis(
    user_card_id: uuid.UUID, body: AnalysisUpsert, user: CurrentUser, session: SessionDep
) -> AnalysisRead:
    if await cards_repo.get_user_card(session, user.id, user_card_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Card not found")
    row = await analyses_repo.upsert_analysis(
        session,
        user_card_id=user_card_id,
        analysis_month=body.analysis_month,
        report=body.report,
        analysis_data=body.analysis_data,
    )
    await refresh_latest_spend_report(session, user.id)
    return AnalysisRead.model_validate(row)


@router.delete(
    "/cards/{user_card_id}/analyses/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_analysis(
    user_card_id: uuid.UUID, analysis_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> None:
    if await cards_repo.get_user_card(session, user.id, user_card_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Card not found")
    row = await analyses_repo.get_for_user_card(session, user_card_id, analysis_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis not found")
    await session.delete(row)
    await session.flush()
    await refresh_latest_spend_report(session, user.id)


@router.get("/sales", response_model=list[UserSaleRead])
async def list_my_sales(user: CurrentUser, session: SessionDep) -> list[UserSaleRead]:
    rows = await sales_repo.list_user_sales(session, user.id)
    return [UserSaleRead.model_validate(r) for r in rows]
