"""Request body size limit: 413 payload_too_large before the body reaches any parser.

A declared Content-Length above the limit is refused without reading the body.
A body without Content-Length (chunked) is read up to the limit and refused as
soon as it goes over; otherwise it is replayed unchanged to the application.
"""
from __future__ import annotations

import uuid

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from osm.errors import _REQUEST_ID_RE, REQUEST_ID_HEADER

PAYLOAD_TOO_LARGE = "payload_too_large"


class BodySizeLimit:
    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared is not None:
            if not declared.isdigit():
                await self._refuse(scope, receive, send, headers, 400, "invalid_request",
                                   "Header Content-Length tidak valid.")
                return
            if int(declared) > self.max_bytes:
                await self._too_large(scope, receive, send, headers)
                return
            await self.app(scope, receive, send)
            return
        if headers.get(b"transfer-encoding") is None:
            await self.app(scope, receive, send)  # no body
            return

        # Chunked: buffer up to the limit, then hand the same messages to the app.
        buffered: list[Message] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                buffered.append(message)  # disconnect
                break
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                await self._too_large(scope, receive, send, headers)
                return
            buffered.append(message)
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            return buffered.pop(0) if buffered else await receive()

        await self.app(scope, replay, send)

    async def _too_large(self, scope, receive, send, headers) -> None:
        await self._refuse(scope, receive, send, headers, 413, PAYLOAD_TOO_LARGE,
                           f"Body permintaan melebihi batas {self.max_bytes} byte.")

    @staticmethod
    async def _refuse(scope, receive, send, headers, status: int, code: str, message: str) -> None:
        incoming = headers.get(REQUEST_ID_HEADER.lower().encode(), b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else str(uuid.uuid4())
        response = JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": message, "request_id": request_id}},
            headers={REQUEST_ID_HEADER: request_id, "Connection": "close"},
        )
        await response(scope, receive, send)
