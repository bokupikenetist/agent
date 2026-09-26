"""Частота опроса и детектор изменений кропа (FR-6, часть NFR-2).

FrameScheduler хранит последний обработанный кроп и пропускает OCR, если
изменение ниже порога change_threshold (доля изменившихся пикселей после
уменьшения — быстро и устойчиво к шуму сжатия).
"""
from __future__ import annotations

import logging
import time

import cv2
import numpy as np

log = logging.getLogger(__name__)


class ChangeDetector:
    def __init__(self, threshold: float = 0.01):
        self.threshold = threshold
        self._ref: np.ndarray | None = None

    def reset(self) -> None:
        self._ref = None

    def changed(self, crop: np.ndarray) -> bool:
        """True, если кроп отличается от прошлого больше чем на threshold."""
        small = cv2.resize(crop, (160, 90), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if self._ref is None or self._ref.shape != gray.shape:
            self._ref = gray
            return True
        diff = cv2.absdiff(gray, self._ref)
        ratio = float((diff > 12).mean())
        self._ref = gray
        return ratio >= self.threshold


class FrameScheduler:
    """Ограничивает частоту OCR до poll_hz области чата."""

    def __init__(self, poll_hz_getter, threshold_getter):
        # getter'ы читают актуальные значения из ConfigStore на каждый кадр
        self._poll_hz = poll_hz_getter
        self._threshold = threshold_getter
        self._detector = ChangeDetector(self._threshold())
        self._last_pass = 0.0

    def should_process(self, crop: np.ndarray, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if now - self._last_pass < 1.0 / max(0.1, self._poll_hz()):
            return False
        self._detector.threshold = self._threshold()
        if not self._detector.changed(crop):
            return False
        self._last_pass = now
        return True
