"""FolderSource: воспроизведение папки PNG/JPG с заданной частотой (FR-12).

Режим `--source folder` читает отсортированные по имени кадры и отдаёт их
в конвейер как живые; цикл по папке зациклен, пока не вызовут stop().
client_rect синтетический (0,0,w,h) — регион считается от размера кадра.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .base import Frame

log = logging.getLogger(__name__)

IMG_EXTS = {".png", ".jpg", ".jpeg"}


def list_frames(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).iterdir()
                  if p.suffix.lower() in IMG_EXTS and p.is_file())


class FolderSource:
    name = "folder"

    def __init__(self, folder: str | Path, fps: float = 2.0, loop: bool = True):
        self.folder = Path(folder)
        self.fps = max(0.1, fps)
        self.loop = loop
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, on_frame: Callable[[Frame], None]) -> None:
        files = list_frames(self.folder)
        if not files:
            raise FileNotFoundError(f"в папке {self.folder} нет PNG/JPG кадров")
        log.info("FolderSource: %d кадров из %s при %.1f к/с", len(files), self.folder, self.fps)

        def loop():
            fid = 0
            interval = 1.0 / self.fps
            while not self._stop.is_set() and True:
                for p in files:
                    if self._stop.is_set():
                        return
                    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
                    if img is None:
                        log.warning("не читается %s — пропуск", p)
                        continue
                    h, w = img.shape[:2]
                    fid += 1
                    try:
                        on_frame(Frame(fid, img, time.monotonic(), (0, 0, w, h)))
                    except Exception:
                        log.exception("ошибка обработки кадра")
                    if self._stop.wait(interval):
                        return
                if not self.loop:
                    return

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        log.info("FolderSource остановлен")
