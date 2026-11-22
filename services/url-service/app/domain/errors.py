"""Domain error hierarchy (LLD §7.2). Rendered as problem+json by app.api.errors."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


class AppException(Exception):
    """Base class for every error the API reports deliberately."""

    status: int = 500
    code: str = "INTERNAL_ERROR"
    title: str = "Internal server error"

    def __init__(self, detail: str | None = None, field_errors: list[FieldError] | None = None):
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.field_errors = field_errors or []


class ValidationException(AppException):
    status, code, title = 400, "VALIDATION_ERROR", "Request validation failed"


class AuthenticationException(AppException):
    status, code, title = 401, "UNAUTHENTICATED", "Authentication required"


class AuthorizationException(AppException):
    status, code, title = 403, "FORBIDDEN", "Not allowed"


class NotFoundException(AppException):
    status, code, title = 404, "NOT_FOUND", "Resource not found"


class ConflictException(AppException):
    status, code, title = 409, "CONFLICT", "Conflict"


class GoneException(AppException):
    status, code, title = 410, "GONE", "Resource no longer available"


class PayloadTooLargeException(AppException):
    status, code, title = 413, "PAYLOAD_TOO_LARGE", "Request body too large"


class RateLimitedException(AppException):
    status, code, title = 429, "RATE_LIMITED", "Too many requests"

    def __init__(self, retry_after_seconds: int, detail: str | None = None):
        super().__init__(detail)
        self.retry_after_seconds = retry_after_seconds


class DependencyUnavailableException(AppException):
    status, code, title = 503, "SERVICE_UNAVAILABLE", "Service temporarily unavailable"

    def __init__(self, dependency: str, detail: str | None = None):
        super().__init__(detail)
        self.dependency = dependency
