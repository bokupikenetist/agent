"""Типы событий конвейера (ТЗ §8).

Шина принимает любые dataclass-события: будущий MomentEvent добавляется
новым классом без правок EventBus и SessionJournal (точка расширения).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True)
class TextEvent:
    region: str
    kind: Literal["chat_line", "text_block"]
    text: str
    ts: str                                   # ISO 8601, локальное время с зоной
    frame_id: int
    engine: str
    confidence: float | None
    # качество привязки строки к прошлому экрану (см. ChatTracker):
    #   anchored   — ниже подтверждённого совпадения со старым экраном;
    #   unanchored — совпадений нет вовсе (полная прокрутка/сбой OCR), это
    #                нижняя строка экрана «наугад» — downstream может не доверять.
    # Поле добавлено с концом списка и default — старые читатели и JSONL
    # совместимы (агент игнорирует неизвестные поля, ТЗ §8).
    detection_quality: Literal["anchored", "unanchored"] = "anchored"

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        return d


def now_iso() -> str:
    """ISO 8601 локального времени с указанием зоны (для поля ts)."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


# --- Точка расширения: полезные моменты (не реализуются в v1) -------------
# @dataclass(frozen=True)
# class MomentEvent:
#     ts: str
#     kind: Literal["moment"]
#     screenshot_path: str | None
#     tags: list[str]
#     note: str
#     nearby_lines: list[str]
