"""
EventEmitter — asyncio.Queue 기반 이벤트 발행 시스템

파이프라인 실행 중 이벤트를 발행하면 SSE 스트리밍 루프에서 비동기로 소비.
"""

import asyncio
import logging
from typing import AsyncGenerator, Awaitable, Callable, Optional

from .types import DoneEvent, HarnessEvent

logger = logging.getLogger("harness.events")


class EventEmitter:
    """비동기 큐 기반 이벤트 발행기"""

    def __init__(self, queue_size: int = 1000):
        self._queue: asyncio.Queue[HarnessEvent] = asyncio.Queue(maxsize=queue_size)
        self._subscribers: list[Callable[[HarnessEvent], Awaitable[None]]] = []
        self._closed = False

    async def emit(self, event: HarnessEvent) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event: %s", type(event).__name__)
            return

        for subscriber in self._subscribers:
            try:
                await subscriber(event)
            except Exception:
                logger.exception("Subscriber error")

    def subscribe(self, callback: Callable[[HarnessEvent], Awaitable[None]]) -> None:
        self._subscribers.append(callback)

    async def stream(self) -> AsyncGenerator[HarnessEvent, None]:
        """이벤트를 비동기 제너레이터로 소비. DoneEvent가 오면 종료."""
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                continue
            yield event
            if isinstance(event, DoneEvent):
                break

    async def close(self) -> None:
        self._closed = True
        if not isinstance(await self._peek(), DoneEvent):
            await self.emit(DoneEvent(final_output="", success=False))

    async def _peek(self) -> Optional[HarnessEvent]:
        if self._queue.empty():
            return None
        return self._queue._queue[-1] if self._queue._queue else None
