"""Реестр шагов предобработки (FR-7) и применение цепочки из конфига.

Каждый шаг: np.ndarray -> np.ndarray, BGR на входе и выходе (grayscale
возвращает 1-канал — это допустимо для Tesseract). Новый шаг — одна строка
@register + имя в списке preprocess раскладки.
"""
from __future__ import annotations

import logging
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger(__name__)

Step = Callable[[np.ndarray], np.ndarray]
REGISTRY: dict[str, Step] = {}


def register(name: str):
    def deco(fn: Step) -> Step:
        REGISTRY[name] = fn
        return fn
    return deco


def _resize(img: np.ndarray, scale: float) -> np.ndarray:
    interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)


@register("upscale_2x")
def upscale_2x(img): return _resize(img, 2.0)


@register("upscale_3x")
def upscale_3x(img): return _resize(img, 3.0)


@register("grayscale")
def grayscale(img):
    if img.ndim == 3 and img.shape[2] == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


@register("autocontrast")
def autocontrast(img):
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lo, hi = np.percentile(gray, 1), np.percentile(gray, 99)
    if hi - lo < 10:            # почти однотонно — не трогаем
        return img
    out = np.clip((gray.astype(np.int32) - lo) * 255 // max(1, int(hi - lo)), 0, 255)
    out = out.astype(np.uint8)
    if img.ndim == 3:
        return cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
    return out


@register("threshold")
def threshold(img):
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, out = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return out


@register("invert")
def invert(img):
    # тёмный текст на светлом: если среднее яркое — инвертируем к белому фону?
    # По ТЗ кроп инвертируется в тёмный текст на светлом; SS14-чат светлый текст
    # на тёмном фоне -> invert даёт то, что нужно. Если уже светлый фон — не трогаем.
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if float(gray.mean()) < 127:
        return cv2.bitwise_not(img)
    return img


def apply_chain(img: np.ndarray, steps: list[str]) -> np.ndarray:
    """Прогоняет изображение через цепочку шагов; неизвестный шаг логируется и пропускается."""
    for name in steps:
        step = REGISTRY.get(name)
        if step is None:
            log.warning("неизвестный шаг предобработки %r — пропущен", name)
            continue
        try:
            img = step(img)
        except Exception:
            log.exception("шаг %r упал — пропущен", name)
    return img
