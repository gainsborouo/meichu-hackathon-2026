"""Which card to pay with for a specific purchase."""

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep
from app.repositories import cards as cards_repo
from app.repositories import sales as sales_repo
from app.schemas.db import Recommendation, SearchRequest, SearchResponse
from app.services.recommend import Query, normalize_category, rank

router = APIRouter()


@router.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest, user: CurrentUser, session: SessionDep) -> SearchResponse:
    """Rank the user's cards by what this purchase would earn.

    POST rather than GET because the query is a structured body, but it is a
    read: nothing is stored and repeating it changes nothing.

    Cards whose campaigns carry no machine-readable rate are still returned,
    with `estimated_reward: null` and ranked last, so the answer never hides a
    card just because its terms were written in prose we could not parse.
    """
    user_cards = await cards_repo.list_user_cards(session, user.id)
    by_card = {uc.card_id: uc for uc in user_cards}

    if body.include_unowned:
        cards = {uc.card_id: uc.card for uc in user_cards}
        cards.update({c.id: c for c in await cards_repo.list_cards_with_sales(session)})
    else:
        cards = {uc.card_id: uc.card for uc in user_cards}

    sales_by_card: dict = {card_id: [] for card_id in cards}
    for sale in await sales_repo.list_sales(session, limit=1000):
        if sale.card_id in sales_by_card:
            sales_by_card[sale.card_id].append(sale)

    ranked = rank(
        [(by_card.get(card_id), card, sales_by_card[card_id]) for card_id, card in cards.items()],
        Query(
            price=body.price,
            platform=(body.platform or "").strip().lower() or None,
            category=body.category,
            currency=body.currency,
        ),
    )
    rows = [Recommendation.model_validate(r, from_attributes=True) for r in ranked]

    return SearchResponse(
        query=body,
        resolved_category=normalize_category(body.category),
        best=rows[0] if rows else None,
        alternatives=rows[1:],
        considered_card_count=len(cards),
    )
