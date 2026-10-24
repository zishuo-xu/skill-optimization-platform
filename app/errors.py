"""Typed application errors mapped to HTTP responses by the API layer."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None,
                 status_code: int | None = None, details: Any = None,
                 field_path: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details
        self.field_path = field_path

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.field_path:
            body["error"]["field_path"] = self.field_path
        if self.details is not None:
            body["error"]["details"] = self.details
        return body


class ValidationError(AppError):
    status_code = 422
    code = "validation_failed"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"


class CancelledError(AppError):
    status_code = 409
    code = "cancelled"
