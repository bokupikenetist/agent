"""Кроппинг областей экрана из кадра (FR-3) + утилиты дампа кадров (этап 1)."""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from .capture.base import Frame
from .rects import RectPx, rel_to_px

log = logging.getLogger(__name__)


class RegionCropper:
    """Переводит относительные координаты области в пиксели и кропает кадр.

    Координаты берутся из layout на каждый кадр (snapshot) — изменения из
    ConfigWatcher применяются сразу (FR-5).
    """

    def __init__(self, store):
        self._store = store

    def chat_rect_px(self, frame: Frame) -> RectPx:
        _, layout = self._store.snapshot()
        w, h = frame.image.shape[1], frame.image.shape[0]
        return rel_to_px(layout.chat.rect, w, h)

    def crop_chat(self, frame: Frame) -> tuple[np.ndarray, RectPx]:
        r = self.chat_rect_px(frame)
        crop = frame.image[r.y: r.y + r.h, r.x: r.x + r.w]
        return crop, r


def crop_client(image: np.ndarray, rect: tuple[float, float, float, float]) -> np.ndarray:
    """Кроп по относительным координатам из BGR-изображения клиентской области."""
    h, w = image.shape[:2]
    r = rel_to_px(rect, w, h)
    return image[r.y: r.y + r.h, r.x: r.x + r.w]


def crop_rect(image: np.ndarray, r: RectPx) -> np.ndarray:
    return image[r.y: r.y + r.h, r.x: r.x + r.w]


def dump_frame(frame: Frame, out_dir: Path, prefix: str = "frame") -> Path:
    """Сохранить кадр PNG в папку (дамп для отладки/записи раунда)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{prefix}_{frame.frame_id:06d}.png"
    cv2.imwrite(str(p), frame.image)
    return p
