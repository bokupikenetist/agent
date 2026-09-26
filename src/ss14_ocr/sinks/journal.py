"""SessionJournal: онлайн-журнал сессии (FR-10).

Каждое событие сразу (fsync не делаем — flush достаточно) пишется строкой JSON
в sessions/<YYYY-MM-DD_HH-MM-SS>/events.jsonl. При save_crops рядом кладётся
PNG кропа, а к событию добавляется поле crop_path. Файл можно читать во время
игры: запись только append. Журнал пишет события ЛЮБЫХ типов — задел для
MomentEvent.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

log = logging.getLogger(__name__)


class SessionJournal:
    def __init__(self, base_dir: str | Path, save_crops: bool = False,
                 session_name: str | None = None):
        self.base_dir = Path(base_dir)
        self.save_crops = save_crops
        name = session_name or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.dir = self.base_dir / name
        self.path = self.dir / "events.jsonl"
        self._fh = None
        self._count = 0

    def open(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")
        log.info("журнал сессии: %s (save_crops=%s)", self.path, self.save_crops)

    def update_opts(self, save_crops: bool) -> None:
        """Горячее применение journal.* из конфига (FR-5)."""
        self.save_crops = save_crops

    def publish_sync(self, event: Any, crop: np.ndarray | None = None) -> None:
        """Единственная точка записи журнала. Вызывается из pump-цикла EventBus
        (asyncio-loop), а НЕ из OCR-потока — так событие пишется ровно один раз
        (FR-10), а диск/PNG не тормозят конвейер распознавания."""
        if self._fh is None:
            self.open()
        d = dataclasses.asdict(event) if dataclasses.is_dataclass(event) else dict(event)
        if self.save_crops and crop is not None:
            p = self.dir / f"crop_{d.get('frame_id', self._count):06d}_{self._count}.png"
            try:
                cv2.imwrite(str(p), crop)
                d["crop_path"] = p.name
            except Exception:
                log.exception("не удалось сохранить кроп")
        self._fh.write(json.dumps(d, ensure_ascii=False) + "\n")
        self._fh.flush()                       # FR-10: файл читаем во время игры
        self._count += 1

    # ---------- интерфейс Sink (async) ----------
    async def publish(self, event: dict, crop: np.ndarray | None = None) -> None:
        self.publish_sync(event, crop=crop)

    async def close(self) -> None:
        if self._fh:
            self._fh.flush()
            self._fh.close()
            self._fh = None
            log.info("журнал закрыт, событий: %d", self._count)
