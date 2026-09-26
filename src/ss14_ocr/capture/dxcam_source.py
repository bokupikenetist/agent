"""DXGI-захват через dxcam (альтернативный backend, без жёлтой рамки).

Захватывает экран (monitor) и кроет по клиентской области окна. Окно должно
быть не перекрыто — отличие от WGC. Включение: capture.backend: dxcam.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from .base import Frame

log = logging.getLogger(__name__)


class DxcamSource:
    name = "dxcam"

    def __init__(self, window_provider, fps: float = 15.0):
        self._provider = window_provider
        self._fps = fps
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_id = 0

    def start(self, on_frame: Callable[[Frame], None]) -> None:
        import dxcam  # lazy import

        info = self._provider()
        if info is None:
            raise RuntimeError("окно недоступно")
        camera = dxcam.create(output_color="BGR")
        region = None  # полный экран; кроп клиента делаем сами — проще при перемещении окна

        def loop():
            last_min_log = 0.0
            while not self._stop.is_set():
                cur = self._provider()
                if cur is None:
                    log.warning("окно закрыто — dxcam останавливается")
                    break
                if cur.minimized:
                    if time.monotonic() - last_min_log > 5:
                        log.info("окно свёрнуто — захват на паузе")
                        last_min_log = time.monotonic()
                    time.sleep(0.2)
                    continue
                img = camera.grab(region=region)
                if img is None:
                    time.sleep(1.0 / self._fps)
                    continue
                x, y, w, h = cur.client_rect
                # координаты клиента на экране -> индексы в кадре монитора
                ox, oy = max(0, x), max(0, y)
                crop = img[oy: oy + h, ox: ox + w]
                if crop.size == 0:
                    time.sleep(1.0 / self._fps)
                    continue
                self._frame_id += 1
                try:
                    on_frame(Frame(self._frame_id, np.ascontiguousarray(crop),
                                   time.monotonic(), cur.client_rect))
                except Exception:
                    log.exception("ошибка обработки кадра")
                time.sleep(1.0 / self._fps)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        log.info("dxcam-захват запущен")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
