import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import SessionDep
from app.repositories import cards as cards_repo
from app.repositories import sales as sales_repo
from app.schemas.db import CardRead, SaleRead

router = APIRouter()


@router.get("/cards", response_model=list[CardRead])
async def list_cards(session: SessionDep) -> list[CardRead]:
    return [CardRead.model_validate(c) for c in await cards_repo.list_cards(session)]


@router.get("/sales", response_model=list[SaleRead])
async def list_sales(
    session: SessionDep,
    card_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SaleRead]:
    rows = await sales_repo.list_sales(session, card_id=card_id, limit=limit, offset=offset)
    return [SaleRead.model_validate(r) for r in rows]


@router.get("/sales/{sale_id}", response_model=SaleRead)
async def read_sale(sale_id: str, session: SessionDep) -> SaleRead:
    sale = await sales_repo.get_sale(session, sale_id)
    if sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sale not found")
    return SaleRead.model_validate(sale)
