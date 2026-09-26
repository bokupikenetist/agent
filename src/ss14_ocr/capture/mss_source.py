"""MSS-захват (mss, экран+кроп клиента) — запасной backend, без зависимостей от WinRT."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from .base import Frame

log = logging.getLogger(__name__)


class MssSource:
    name = "mss"

    def __init__(self, window_provider, fps: float = 15.0):
        self._provider = window_provider
        self._fps = fps
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_id = 0

    def start(self, on_frame: Callable[[Frame], None]) -> None:
        import mss  # lazy import

        def loop():
            with mss.mss() as sct:
                last_min_log = 0.0
                while not self._stop.is_set():
                    cur = self._provider()
                    if cur is None:
                        log.warning("окно закрыто — mss останавливается")
                        break
                    x, y, w, h = cur.client_rect
                    if cur.minimized or w <= 0 or h <= 0:
                        if time.monotonic() - last_min_log > 5:
                            log.info("окно свёрнуто/нулевое — захват на паузе")
                            last_min_log = time.monotonic()
                        time.sleep(0.2)
                        continue
                    shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
                    arr = np.asarray(shot, dtype=np.uint8)[:, :, :3]  # BGRA->BGR
                    self._frame_id += 1
                    try:
                        on_frame(Frame(self._frame_id, np.ascontiguousarray(arr),
                                       time.monotonic(), cur.client_rect))
                    except Exception:
                        log.exception("ошибка обработки кадра")
                    time.sleep(1.0 / self._fps)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        log.info("mss-захват запущен")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
