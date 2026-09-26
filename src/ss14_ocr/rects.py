"""Пиксельные регионы и перевод относительных координат раскладки в пиксели (FR-3).

Отдельным модулем от regions.py(capture), чтобы unit-тесты DPI работали
на любой ОС без pywin32.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RectPx:
    """Прямоугольник в пикселях внутри кадра (x, y, w, h)."""
    x: int
    y: int
    w: int
    h: int

    def is_empty(self) -> bool:
        return self.w <= 0 or self.h <= 0


def rel_to_px(rect: tuple[float, float, float, float], client_w: int, client_h: int) -> RectPx:
    """Переводит доли (0..1) клиентской области в пиксели кадра.

    Кадр уже соответствует клиентской области окна целиком, поэтому
    масштабирование идёт только по размеру кадра; DPI учтён на этапе
    захвата (DPI-aware процесс => физические пиксели).
    """
    rx, ry, rw, rh = rect
    if client_w <= 0 or client_h <= 0:
        raise ValueError(f"пустой кадр {client_w}x{client_h}")
    x = int(round(rx * client_w))
    y = int(round(ry * client_h))
    w = int(round(rw * client_w))
    h = int(round(rh * client_h))
    # зажимаем в границы кадра
    x = max(0, min(x, client_w - 1))
    y = max(0, min(y, client_h - 1))
    w = max(1, min(w, client_w - x))
    h = max(1, min(h, client_h - y))
    return RectPx(x, y, w, h)
