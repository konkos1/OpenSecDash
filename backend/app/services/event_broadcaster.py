"""App-wide event polling and in-process WebSocket fan-out."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventPollState:
    enabled: bool
    last_event_id: int


class EventBroadcaster:
    """Poll event state once and fan changes out without blocking on clients."""

    def __init__(self, poll_state: Callable[[], tuple[bool, int]], interval_seconds: float = 1.0) -> None:
        self._poll_state = poll_state
        self._interval_seconds = interval_seconds
        self._state = EventPollState(False, 0)
        self._ready = asyncio.Event()
        self._subscribers: set[asyncio.Queue[EventPollState]] = set()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = None
        await self._poll_once()
        self._task = asyncio.create_task(self._run(), name="event-broadcaster")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def current_state(self) -> EventPollState:
        await self._ready.wait()
        return self._state

    def subscribe(self) -> asyncio.Queue[EventPollState]:
        queue: asyncio.Queue[EventPollState] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[EventPollState]) -> None:
        self._subscribers.discard(queue)

    async def _poll_once(self) -> None:
        enabled, last_event_id = await asyncio.to_thread(self._poll_state)
        state = EventPollState(enabled, last_event_id)
        changed = self._ready.is_set() and state != self._state
        self._state = state
        self._ready.set()
        if changed:
            self._broadcast(state)

    def _broadcast(self, state: EventPollState) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(state)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                await self._poll_once()
            except Exception:
                logger.exception("Event broadcaster poll failed")
