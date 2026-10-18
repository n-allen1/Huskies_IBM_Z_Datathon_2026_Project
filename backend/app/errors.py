import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .models import ErrorCode

log = logging.getLogger("dispatchz")

_STATUS = {
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: 409,
    ErrorCode.UNIT_UNAVAILABLE: 409,
    ErrorCode.INVALID_STATE_TRANSITION: 409,
    ErrorCode.RECOMMENDATION_OUTDATED: 409,
    ErrorCode.STALE_DATA: 409,
    ErrorCode.INFERENCE_UNAVAILABLE: 503,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
}


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: list[tuple[str | None, str]] | None = None,
        status: int | None = None,
    ):
        self.code = code
        self.message = message
        self.details = details or []
        self.status = status or _STATUS[code]


def _body(code: ErrorCode, message: str, details=None) -> dict:
    body = {
        "code": code.value,
        "message": message,
        "requestId": f"req_{uuid.uuid4().hex[:16]}",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if details:
        body["details"] = [{**({"field": f} if f else {}), "issue": i} for f, i in details]
    return body


def _respond(code: ErrorCode, message: str, details=None, status: int | None = None) -> JSONResponse:
    return JSONResponse(_body(code, message, details), status_code=status or _STATUS[code])


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return _respond(exc.code, exc.message, exc.details, exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [
            (".".join(str(p) for p in e["loc"] if p not in ("body", "query", "path", "header")) or None, e["msg"])
            for e in exc.errors()
        ]
        return _respond(ErrorCode.VALIDATION_ERROR, "Request validation failed.", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = ErrorCode.NOT_FOUND if exc.status_code in (404, 405) else ErrorCode.INTERNAL_ERROR
        return _respond(code, "Resource not found." if code is ErrorCode.NOT_FOUND else str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("Unhandled error")
        return _respond(ErrorCode.INTERNAL_ERROR, "Internal error.")
