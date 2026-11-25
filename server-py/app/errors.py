"""Error handling shared by every route.

All errors reach the client as JSON shaped {"message": "..."}, matching the Node
API. Raw provider errors can contain request details or keys, so they are never
sent to the client or logged.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("docquery")

QUOTA_MESSAGE = "Gemini quota reached. Wait and retry, or check your free-tier limits in AI Studio."
GENERIC_MESSAGE = "Request failed. Check the API key, model access, Qdrant, and Valkey."
INVALID_JSON_MESSAGE = "Send valid JSON in the request body."


class ApiError(Exception):
    """An error whose message is safe to show to the user."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def error_status(error: Exception):
    """Return the HTTP status a provider error carries, if any."""
    for name in ("status", "status_code", "code"):
        value = getattr(error, name, None)
        if isinstance(value, int):
            return value
    return None


def message_response(status: int, message: str) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_request: Request, error: ApiError):
        return message_response(error.status, error.message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, error: StarletteHTTPException):
        message = error.detail if isinstance(error.detail, str) else GENERIC_MESSAGE
        return message_response(error.status_code, message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError):
        # Routes validate their own inputs to return the contract's messages.
        # Anything FastAPI rejects first is a malformed request body.
        return message_response(400, INVALID_JSON_MESSAGE)

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, error: Exception):
        status = 429 if error_status(error) == 429 else 500
        logger.error("Request failed (status %s). Check Gemini access/quota and local services.", status)
        return message_response(status, QUOTA_MESSAGE if status == 429 else GENERIC_MESSAGE)
