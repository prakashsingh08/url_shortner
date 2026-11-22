"""Test-only routes, mounted only when APP_ENV=test. Used by API tests for error,
JSON-parsing and graceful-shutdown scenarios that no real endpoint exercises yet."""

import asyncio

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/__test", tags=["test-only"], include_in_schema=False)


class EchoBody(BaseModel):
    message: str


@router.post("/echo")
async def echo(body: EchoBody) -> dict:
    return {"message": body.message}


@router.get("/boom")
async def boom() -> dict:
    raise RuntimeError("deliberate failure: secret-internal-detail")


@router.get("/slow")
async def slow(seconds: float = Query(default=2.0, ge=0, le=30)) -> dict:
    await asyncio.sleep(seconds)
    return {"slept": seconds}
