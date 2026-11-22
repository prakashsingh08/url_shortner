"""RFC 9457 problem-details responses (api-conventions: Problem Details Error Format, LLD §3.11)."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import AppException, FieldError, ValidationException
from app.infrastructure.logging import request_id_var

PROBLEM_JSON = "application/problem+json"

_HTTP_CODES = {
    404: ("NOT_FOUND", "Resource not found"),
    405: ("METHOD_NOT_ALLOWED", "Method not allowed"),
    413: ("PAYLOAD_TOO_LARGE", "Request body too large"),
}


def problem_response(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    instance: str,
    field_errors: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {
        "type": f"https://docs.short.local/errors/{code.lower().replace('_', '-')}",
        "title": title,
        "status": status,
        "code": code,
        "detail": detail,
        "instance": instance,
        "requestId": request_id_var.get(),
    }
    if field_errors:
        body["errors"] = [{"field": e.field, "message": e.message} for e in field_errors]
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def from_app_exception(exc: AppException, instance: str) -> JSONResponse:
    headers = None
    retry_after = getattr(exc, "retry_after_seconds", None)
    if retry_after is not None:
        headers = {"Retry-After": str(retry_after)}
    return problem_response(
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        instance=instance,
        field_errors=exc.field_errors,
        headers=headers,
    )


def internal_error(instance: str) -> JSONResponse:
    return problem_response(
        status=500,
        code="INTERNAL_ERROR",
        title="Internal server error",
        detail="An unexpected error occurred. Quote the requestId when reporting it.",
        instance=instance,
    )


def _field_errors(exc: RequestValidationError) -> list[FieldError]:
    result = []
    for err in exc.errors():
        if err.get("type") == "json_invalid":
            result.append(FieldError("body", "invalid JSON"))
            continue
        loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path", "header")]
        result.append(FieldError(".".join(loc) or "body", err.get("msg", "invalid")))
    return result


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def _app_exc(request: Request, exc: AppException):
        return from_app_exception(exc, request.url.path)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # FastAPI defaults to 422; this API uses 400 VALIDATION_ERROR (design D7).
        return from_app_exception(
            ValidationException("The request is invalid.", _field_errors(exc)), request.url.path
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code, title = _HTTP_CODES.get(exc.status_code, (f"HTTP_{exc.status_code}", "HTTP error"))
        detail = exc.detail if isinstance(exc.detail, str) and exc.status_code != 404 else title
        return problem_response(
            status=exc.status_code,
            code=code,
            title=title,
            detail=detail,
            instance=request.url.path,
            headers=getattr(exc, "headers", None),
        )
