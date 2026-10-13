"""Uniform error schema {"error": {"code", "message", "request_id"}} (Tabel 3.13, 3.16)."""
from __future__ import annotations

import logging
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("osm")

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class OsmApiError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidRequest(OsmApiError):
    status_code = 400
    code = "invalid_request"


class NotFound(OsmApiError):
    status_code = 404
    code = "not_found"


class OutsideStudyArea(OsmApiError):
    status_code = 422
    code = "outside_study_area"


class PointNotOnNetwork(OsmApiError):
    status_code = 422
    code = "point_not_on_network"


class NoRouteFound(OsmApiError):
    status_code = 422
    code = "no_route_found"


class CacheUnavailable(OsmApiError):
    """503 cache_unavailable. `reason` tells the cases apart (TI-SE-04):

    cache_empty           the cache has no usable data yet (no active graph_version,
                          no complete time window)
    database_unavailable  the database cannot be reached
    query_timeout         a query exceeded statement_timeout or the client timeout
    """

    status_code = 503
    code = "cache_unavailable"
    CACHE_EMPTY = "cache_empty"
    DATABASE_UNAVAILABLE = "database_unavailable"
    QUERY_TIMEOUT = "query_timeout"

    def __init__(self, message: str, reason: str = DATABASE_UNAVAILABLE):
        super().__init__(message)
        self.reason = reason


def get_request_id(request: Request) -> str:
    """X-Request-ID from the API Gateway, or a fresh UUID v4 when absent or malformed."""
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else str(uuid.uuid4())
        request.state.request_id = request_id
    return request_id


def error_response(request: Request, status_code: int, code: str, message: str,
                   reason: str | None = None) -> JSONResponse:
    request_id = get_request_id(request)
    error = {"code": code, "message": message, "request_id": request_id}
    if reason is not None:
        error["reason"] = reason
    return JSONResponse(status_code=status_code, content={"error": error}, headers={REQUEST_ID_HEADER: request_id})


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def install_error_handling(app: FastAPI) -> None:
    """Register the request-id middleware and the uniform error handlers on app."""

    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        request_id = get_request_id(request)
        response = await call_next(request)
        response.headers.setdefault(REQUEST_ID_HEADER, request_id)
        return response

    @app.exception_handler(OsmApiError)
    async def _osm_error(request: Request, exc: OsmApiError):
        if exc.status_code >= 500:
            logger.warning("request_id=%s %s: %s", get_request_id(request), exc.code, exc.message)
        return error_response(request, exc.status_code, exc.code, exc.message, getattr(exc, "reason", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        problems = []
        for err in exc.errors()[:5]:
            loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
            problems.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
        return error_response(request, 400, "invalid_request", "; ".join(problems) or "Permintaan tidak valid.")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return error_response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        logger.exception("request_id=%s unhandled error", get_request_id(request))
        return error_response(request, 500, "internal_error", "Terjadi galat internal pada server.")
