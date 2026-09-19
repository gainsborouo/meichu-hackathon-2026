from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe used by the container healthcheck and by the frontend."""
    return HealthResponse(status="ok")
