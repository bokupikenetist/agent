"""EventBus: рассылка событий подписчикам-sinks (ТЗ §4).

Шина принимает любые dataclass-события (TextEvent сегодня, MomentEvent в
будущем) — sinks фильтруют по типу сами. Вызов publish из потоков захвата/OCR
маршутизирует событие в цикл asyncio через call_soon_threadsafe.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)


def _accepts_crop(sink: "Sink") -> bool:
    """True, если publish sink'а принимает именованный аргумент crop."""
    try:
        sig = inspect.signature(sink.publish)
        return "crop" in sig.parameters
    except (TypeError, ValueError):
        return False


@runtime_checkable
class Sink(Protocol):
    async def publish(self, event: Any, crop=None) -> None: ...
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

    async def publish_threadsafe(self, event: Any, crop=None) -> None:
        """Кладёт событие в очередь шины; вызывается только из цикла asyncio.

        crop (опционально) едет вместе с событием до sinks — так SessionJournal
        получает изображение кропа без прямого вызова из OCR-потока.
        """
        try:
            self._queue.put_nowait((event, crop))
        except asyncio.QueueFull:
            try:
                dropped, _ = self._queue.get_nowait()
                log.warning("шина переполнена, выброшено старое событие: %s", dropped)
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait((event, crop))

    def publish_from_thread(self, event: Any, crop=None) -> None:
        """Fire-and-forget из рабочих потоков (OCR): не ждёт цикл asyncio.

        Гарантии доставки/переполнения — ответственность шины (очередь
        bounded, drop-oldest), а не вызывающего потока.
        """
        try:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self.publish_threadsafe(event, crop)))
        except RuntimeError:
            log.warning("шина недоступна (loop остановлен) — событие потеряно: %s", event)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        while self._running or not self._queue.empty():
            try:
                event, crop = await asyncio.wait_for(self._queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            payload = dataclasses.asdict(event) if dataclasses.is_dataclass(event) else event
            for sink in self._sinks:
                try:
                    # crop передаётся только sinks, которые его принимают (journal);
                    # остальные вызываются без него — совместимость со старыми Sink
                    if crop is not None and _accepts_crop(sink):
                        await sink.publish(payload, crop=crop)
                    else:
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
