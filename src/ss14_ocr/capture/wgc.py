"""WGC-захват (windows-capture / Windows Graphics Capture), backend по умолчанию.

Снимает конкретное окно, даже перекрытое другими. На Windows 10 рисует жёлтую
рамку — принять или перейти на dxcam (см. риски ТЗ). Кадр приходит в BGRA —
конвертируем в BGR uint8. При сворачивании окна захват на паузе (FR-2).

Импортируется лениво: на Linux/CI пакета windows-capture нет.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from .base import Frame

log = logging.getLogger(__name__)


class WgcSource:
    name = "wgc"

    def __init__(self, window_provider, fps: float = 15.0):
        """window_provider: callable -> capture.window.WindowInfo | None (для отслеживания)."""
        self._provider = window_provider
        self._fps = fps
        self._on_frame: Callable[[Frame], None] | None = None
        self._capture = None
        self._stop = threading.Event()
        self._frame_id = 0
        self._minimized_logged = False

    def start(self, on_frame: Callable[[Frame], None]) -> None:
        from windows_capture import WindowsCapture, Frame  # lazy import
        self._on_frame = on_frame
        info = self._provider()
        if info is None:
            raise RuntimeError("окно недоступно для WGC-захвата")
        # interest_hint=False убирает жёлтую рамку там, где это поддерживается
        self._capture = WindowsCapture(
            cursor_capture=False, draw_border=False, window_name=info.title
        )

        @self._capture.event
        def on_frame_arrived(capture, frame):
            if self._stop.is_set():
                return
            cur = self._provider()
            if cur is None:
                log.warning("окно закрыто — захват остановлен")
                self._stop.set()
                return
            if cur.minimized:
                if not self._minimized_logged:
                    log.info("окно свёрнуто — захват на паузе")   # FR-2
                    self._minimized_logged = True
                return
            self._minimized_logged = False
            w, h = frame.width, frame.height
            buf = np.frombuffer(frame.buffer, dtype=np.uint8).reshape((h, w, 4))
            bgr = np.ascontiguousarray(buf[:, :, :3])
            self._frame_id += 1
            try:
                self._on_frame(Frame(self._frame_id, bgr, time.monotonic(), cur.client_rect))
            except Exception:
                log.exception("ошибка обработки кадра")

        @self._capture.event
        def on_closed(capture, message):
            log.info("WGC: окно закрыто (%s)", message)
            self._stop.set()

        self._thread = threading.Thread(target=self._capture.start_free_threaded, daemon=True)
        self._thread.start()
        log.info("WGC-захват запущен (окно %r)", info.title)

    def stop(self) -> None:
        self._stop.set()
        # windows-capture не предоставляет graceful stop для free-threaded режима;
        # поток демон, завершается вместе с процессом.
        log.info("WGC-захват остановлен")
