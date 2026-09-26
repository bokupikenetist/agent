"""EventBus: рассылка событий подписчикам-sinks (ТЗ §4).

Шина принимает любые dataclass-события (TextEvent сегодня, MomentEvent в
будущем) — sinks фильтруют по типу сами. Вызов publish из потоков захвата/OCR
маршутизирует событие в цикл asyncio через call_soon_threadsafe.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class Sink(Protocol):
    async def publish(self, event: Any) -> None: ...
    async def close(self) -> None: ...


class EventBus:
    def __init__(self, queue_max: int = 1000):
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_max)
        self._sinks: list[Sink] = []
        self._task: asyncio.Task | None = None
        self._running = False

    def subscribe(self, sink: Sink) -> None:
        self._sinks.append(sink)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def publish_threadsafe(self, event: Any) -> None:
        """Вызывается из рабочих потоков (OCR). Старые кадры не копятся: при
        переполнении очереди выбрасываем самое старое событие (ТЗ §4 потоки)."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                dropped = self._queue.get_nowait()
                log.warning("шина переполнена, выброшено старое событие: %s", dropped)
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(event)

    def publish_from_thread(self, event: Any) -> None:
        asyncio.run_coroutine_threadsafe(self.publish_threadsafe(event), self._loop)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        while self._running or not self._queue.empty():
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            payload = dataclasses.asdict(event) if dataclasses.is_dataclass(event) else event
            for sink in self._sinks:
                try:
                    await sink.publish(payload)
                except Exception:
                    log.exception("sink %s упал на событии", type(sink).__name__)

    async def stop(self) -> None:
        """FR-15: сброс буферов и закрытие sinks."""
        self._running = False
        if self._task:
            # даём откачать остаток очереди
            try:
                await asyncio.wait_for(self._drain(), timeout=2.0)
            except asyncio.TimeoutError:
                log.warning("дренаж шины прерван по таймауту")
            self._task.cancel()
            self._task = None
        for sink in self._sinks:
            try:
                await sink.close()
            except Exception:
                log.exception("ошибка закрытия sink %s", type(sink).__name__)

    async def _drain(self) -> None:
        while not self._queue.empty():
            await asyncio.sleep(0.05)
