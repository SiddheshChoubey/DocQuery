"""Error handling shared by every route.

All errors reach the client as JSON shaped {"message": "..."}, matching the Node
API. Raw provider errors can contain request details or keys, so they are never
sent to the client or logged.
"""

import logging
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
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
    """The HTTP status a provider error carries, if any.

    Libraries often wrap the original error, so the whole cause chain is checked,
    and Gemini's RESOURCE_EXHAUSTED code counts as 429.
    """
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        for name in ("status", "status_code", "code"):
            value = getattr(error, name, None)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        if re.search(r"\b429\b|RESOURCE_EXHAUSTED", str(error)):
            return 429
        error = error.__cause__ or error.__context__
    return None


def message_response(status: int, message: str) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status)


def unexpected_error_response(error: Exception) -> JSONResponse:
    status = 429 if error_status(error) == 429 else 500
    logger.error("Request failed (status %s). Check Gemini access/quota and local services.", status)
    return message_response(status, QUOTA_MESSAGE if status == 429 else GENERIC_MESSAGE)


class SafeErrorsMiddleware:
    """Turns unexpected exceptions into the JSON error format.

    Runs inside the CORS middleware so error responses still carry CORS headers
    and the frontend can read their message.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def track_start(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, track_start)
        except Exception as error:
            if started:  # Part of the response was already sent; nothing safe to add.
                raise
            await unexpected_error_response(error)(scope, receive, send)


class NoStoreMiddleware:
    """Adds Cache-Control: no-store to every response except the health check."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/":
            return await self.app(scope, receive, send)

        async def add_header(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, add_header)


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
