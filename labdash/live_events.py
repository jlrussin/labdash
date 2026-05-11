"""Async event bus for fanning out SSE events to live-mode subscribers.

Watchdog runs on its own OS thread; the FastAPI event loop runs on the main
thread. `EventBus.publish` is thread-safe — callers from any thread schedule
queue puts onto the bus's loop via `loop.call_soon_threadsafe`. SSE
endpoints subscribe with `subscribe()` to get a private `asyncio.Queue` and
unsubscribe on disconnect.
"""

from __future__ import annotations

import asyncio
from typing import Any


_SHUTDOWN = object()


class EventBus:
    """Async fan-out of dict events to all current subscribers.

    Subscribers each own their queue (no shared mailbox), so a slow client
    cannot back-pressure others. Bounded queues prevent runaway memory if a
    client stops draining; on overflow the oldest event is dropped.
    """

    def __init__(self, *, queue_size: int = 256):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: set[asyncio.Queue] = set()
        self._queue_size = queue_size

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the event loop that owns the subscribers.

        Must be called from inside the loop (e.g., from a FastAPI lifespan
        startup handler). `publish` then schedules queue puts onto this loop
        from any thread.
        """
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        """Enqueue `event` on every subscriber. Safe from any thread.

        If a subscriber's queue is full, drop its oldest event before
        appending the new one — keeps clients from drifting unboundedly
        behind without forcing a disconnect.
        """
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._deliver, event)

    def _deliver(self, event: dict[str, Any]) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def shutdown(self) -> None:
        """Wake up every subscriber with a sentinel so generators can exit."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._broadcast_sentinel)

    def _broadcast_sentinel(self) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(_SHUTDOWN)
            except asyncio.QueueFull:
                pass


def is_shutdown(item: Any) -> bool:
    return item is _SHUTDOWN
