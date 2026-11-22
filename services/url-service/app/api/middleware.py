"""Request-ID, body-size limit, access log and last-resort error handling (design D6)."""

import logging
import re
import time
import uuid

from fastapi import FastAPI, Request

from app.api.errors import internal_error, problem_response
from app.infrastructure.logging import request_id_var

log = logging.getLogger("app.access")
err_log = logging.getLogger("app.error")

REQUEST_ID_HEADER = "X-Request-Id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def resolve_request_id(incoming: str | None) -> str:
    """Reuse a well-formed incoming ID (from Nginx); otherwise generate one."""
    if incoming and _VALID_REQUEST_ID.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


def install_middleware(app: FastAPI, max_body_bytes: int) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > max_body_bytes:
                response = problem_response(
                    status=413,
                    code="PAYLOAD_TOO_LARGE",
                    title="Request body too large",
                    detail=f"Request bodies are limited to {max_body_bytes} bytes.",
                    instance=request.url.path,
                )
            else:
                try:
                    response = await call_next(request)
                except Exception:
                    # Full details go to the log only, never to the client.
                    err_log.exception(
                        "unhandled exception",
                        extra={"fields": {"method": request.method, "path": request.url.path}},
                    )
                    response = internal_error(request.url.path)

            response.headers[REQUEST_ID_HEADER] = request_id
            log.info(
                "request completed",
                extra={
                    "fields": {
                        "method": request.method,
                        "route": _route_template(request),
                        "status": response.status_code,
                        "latencyMs": round((time.perf_counter() - started) * 1000, 2),
                    }
                },
            )
            return response
        finally:
            request_id_var.reset(token)
