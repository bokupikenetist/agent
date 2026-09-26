"""Контракт OcrEngine + реестр движков (ТЗ §4, §8, FR-8).

Новый движок (Windows OCR, PaddleOCR) — файл в ocr/ и @register_engine("имя"),
без правок конвейера.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class OcrLine:
    text: str
    bbox: tuple[int, int, int, int]           # в пикселях кропа
    confidence: float | None                  # Windows OCR уверенность не отдаёт


@runtime_checkable
class OcrEngine(Protocol):
    name: str
    def recognize(self, image: np.ndarray, language: str) -> list[OcrLine]: ...


ENGINES: dict[str, type] = {}


def register_engine(name: str):
    def deco(cls):
        ENGINES[name] = cls
        return cls
    return deco


def create_engine(name: str, cfg) -> OcrEngine:
    """cfg — OcrCfg целиком; движок сам достаёт свои секции (tesseract и т.п.)."""
    try:
        cls = ENGINES[name]
    except KeyError:
        raise ValueError(f"неизвестный OCR-движок {name!r}; доступны: {sorted(ENGINES)}") from None
    return cls(cfg)
