"""WebSocketSink: локальный WS-сервер для агента (FR-11, NFR-8).

Слушает только 127.0.0.1; каждое событие — одна JSON-строка всем подписчикам.
Отвалившихся клиентов вычищаем; ошибки отправки логируются, конвейер не роняем.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)


class WebSocketSink:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self._clients: set = set()
        self._server = None

    async def start(self) -> None:
        import websockets

        async def handler(ws):
            self._clients.add(ws)
            log.info("WS-клиент подключился: %s (всего %d)", ws.remote_address, len(self._clients))
            try:
                async for _ in ws:      # входящие сообщения не ожидаются
                    pass
            finally:
                self._clients.discard(ws)

        self._server = await websockets.serve(handler, self.host, self.port)
        log.info("WebSocket-сервер слушает ws://%s:%d", self.host, self.port)

    async def publish(self, event: dict) -> None:
        if not self._clients:
            return
        msg = json.dumps(event, ensure_ascii=False)
        dead = set()
        for ws in list(self._clients):
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def close(self) -> None:
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()
        log.info("WebSocket-сервер остановлен")
