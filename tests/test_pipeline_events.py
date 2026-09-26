"""Регрессии по итогам code review: единственный маршрут событий через шину,
crop до sinks из OCR-потока, честный hot reload, валидации конфига."""
import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ss14_ocr.bus import EventBus                     # noqa: E402
from ss14_ocr.config import RegionCfg                 # noqa: E402
from ss14_ocr.events import TextEvent                 # noqa: E402
from ss14_ocr.sinks.journal import SessionJournal     # noqa: E402


def _ev(text="привет", fid=1):
    return TextEvent(region="chat", kind="chat_line", text=text, ts="t",
                     frame_id=fid, engine="tess", confidence=0.9)


@pytest.mark.asyncio
async def test_journal_receives_event_once_via_bus(tmp_path):
    """Вариант A: событие публикуется один раз и попадает в журнал один раз."""
    bus = EventBus()
    j = SessionJournal(tmp_path, save_crops=False)
    bus.subscribe(j)
    bus.attach_loop(asyncio.get_running_loop())
    await bus.start()
    await bus.publish_threadsafe(_ev())
    await asyncio.sleep(0.5)
    await bus.stop()
    lines = j.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, f"ожидается 1 запись, получено {len(lines)} (дубль?)"
    assert json.loads(lines[0])["text"] == "привет"


@pytest.mark.asyncio
async def test_crop_travels(tmp_path):
    """OCR-поток передаёт кроп через шину; journal сохраняет PNG + crop_path."""
    bus = EventBus()
    j = SessionJournal(tmp_path, save_crops=True)
    got = []

    class PlainSink:
        async def publish(self, event):        # sink без crop — не должен падать
            got.append(event)
        async def close(self):
            pass

    bus.subscribe(j)
    bus.subscribe(PlainSink())
    bus.attach_loop(asyncio.get_running_loop())
    await bus.start()
    crop = np.full((20, 60, 3), 7, np.uint8)
    bus.publish_from_thread(_ev("x"), crop=crop)   # fire-and-forget из потока
    await asyncio.sleep(0.5)
    await bus.stop()
    d = json.loads(j.path.read_text(encoding="utf-8").strip())
    assert "crop_path" in d and (j.dir / d["crop_path"]).exists()
    assert [e["text"] for e in got] == ["x"]      # совместимость Sink без crop


def test_event_carries_detection_quality():
    from ss14_ocr.chat_tracker import ChatTracker
    from ss14_ocr.ocr.base import OcrLine

    def L(*ts):
        return [OcrLine(text=t, bbox=(0, i * 20, 100, 20), confidence=0.9)
                for i, t in enumerate(ts)]

    tr = ChatTracker(engine_name="tess")
    tr.on_lines(L("A1", "A2"), 1)
    ev = tr.on_lines(L("A1", "A2", "новое"), 2)
    assert ev[0].detection_quality == "anchored"
    tr.reset()
    tr.on_lines(L("что-то совсем другое длинное"), 1)
    ev2 = tr.on_lines(L("совершенно иные строки экрана"), 2)
    assert ev2 and ev2[0].detection_quality == "unanchored"


def test_rect_must_fit_client_area():
    with pytest.raises(ValidationError):
        RegionCfg(rect=(0.9, 0.9, 0.5, 0.5))       # x+w=1.4 — за пределами
    RegionCfg(rect=(0.0, 0.6, 0.35, 0.40))         # ровно до границы — ок


def test_unknown_preprocess_step_rejected_at_validation():
    with pytest.raises(ValidationError):
        RegionCfg(rect=(0.0, 0.6, 0.35, 0.38), preprocess=["grayscalle"])
    RegionCfg(rect=(0.0, 0.6, 0.35, 0.38), preprocess=["grayscale"])


def test_setup_logging_is_idempotent(tmp_path):
    import logging
    from ss14_ocr.app import setup_logging
    from ss14_ocr.config import AppConfig

    cfg = AppConfig(logging=AppConfig().logging.model_copy(
        update={"file": str(tmp_path / "logs" / "t.log")}))
    root = logging.getLogger()
    before = len(root.handlers)
    setup_logging(cfg, tmp_path / "config")
    mid = len(root.handlers)
    setup_logging(cfg, tmp_path / "config")          # hot reload logging.*
    after = len(root.handlers)
    try:
        assert mid == before + 2 or mid == before + 1  # stdout (+ файл)
        assert after == mid, "повторный вызов добавил дубли handlers"
    finally:
        for h in list(root.handlers):
            if getattr(h, "_ss14_ocr_handler", False):
                root.removeHandler(h)
                h.close()
