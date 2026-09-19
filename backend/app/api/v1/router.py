from fastapi import APIRouter

from app.api.v1.routes import calendar, catalog, health, me, statements

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(statements.router, tags=["statements"])
api_router.include_router(catalog.router, tags=["catalog"])
api_router.include_router(me.router, tags=["me"])
api_router.include_router(calendar.router, tags=["calendar"])
