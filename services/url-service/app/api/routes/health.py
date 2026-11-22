"""Liveness and readiness probes (service-health)."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", summary="Liveness: the process is running")
async def live() -> dict:
    return {"status": "UP"}


@router.get("/ready", summary="Readiness: the instance can serve traffic")
async def ready(request: Request) -> JSONResponse:
    readiness = await request.app.state.health_service.readiness()
    return JSONResponse(readiness.body(), status_code=200 if readiness.ready else 503)
