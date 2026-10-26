"""Cancellable CPU work for in-process solvers (mock, baseline).

A route search is synchronous CPU work. Run directly inside an async method it
blocks the event loop, so asyncio.wait_for in call_route_solve cannot fire until
the search ends. Run in a plain worker thread, the timeout fires but the thread
keeps computing a route nobody will read.

run_cancellable runs the work in a worker thread and hands it a CancelToken.
When the awaiting task is cancelled (the call_route_solve timeout, or the client
going away), the token is set and the work stops at its next token.check(),
which raises Cancelled. Threads cannot be stopped from outside, so the work has
to call check() regularly: once per node expansion in both searches, and every
CHECK_EVERY items while preparing their input.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


# Loops that prepare a large input before the search (adjacency, edge lists) take
# a noticeable fraction of a second, so they check the token every CHECK_EVERY items.
CHECK_EVERY = 1024


class Cancelled(Exception):
    """Raised inside the worker thread by CancelToken.check() after cancellation."""


class CancelToken:
    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self.finished = threading.Event()  # set when the worker thread has returned or raised

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self) -> None:
        if self._cancelled.is_set():
            raise Cancelled()


async def run_cancellable(fn: Callable[..., T], *args) -> T:
    """fn(token, *args) in a worker thread; cancelling the caller cancels the token."""
    token = CancelToken()

    def work() -> T:
        try:
            return fn(token, *args)
        finally:
            token.finished.set()

    try:
        return await asyncio.to_thread(work)
    finally:
        # On success this is a no-op; on cancellation or timeout it stops the thread.
        token.cancel()
