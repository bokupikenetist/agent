"""Контракты источников кадров (ТЗ §8): Frame + CaptureSource."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class Frame:
    frame_id: int
    image: np.ndarray                         # BGR, uint8, клиентская область окна
    ts: float                                 # time.monotonic()
    client_rect: tuple[int, int, int, int]    # x, y, w, h на экране, физические пиксели


@runtime_checkable
class CaptureSource(Protocol):
    def start(self, on_frame: Callable[[Frame], None]) -> None: ...
    def stop(self) -> None: ...
