"""Сборка и запуск конвейера (ТЗ §4): capture → scheduler → cropper → preprocess →
OCR (ThreadPoolExecutor) → ChatTracker → EventBus → sinks.

Захват в своём потоке (callback WGC), OCR в пуле потоков, шина и sinks в
asyncio-цикле. При переполнении очереди кадров старый кадр отбрасывается —
OCR не блокирует захват (NFR-1/NFR-2).
"""
from __future__ import annotations

import asyncio
import concurrent.futures as cf
import logging
import logging.handlers
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import preprocess
from .bus import EventBus
from .capture.base import Frame
from .chat_tracker import ChatTracker
from .config import AppConfig, LayoutCfg, load_app_config, load_layout_config
from .config_watch import ConfigStore, ConfigWatcher
from .events import TextEvent
from .ocr import base as ocr_base
from .ocr import tesseract  # noqa: F401 — регистрация движка
from .regions import RegionCropper
from .scheduler import FrameScheduler
from .sinks.console import ConsoleSink
from .sinks.journal import SessionJournal
from .sinks.websocket import WebSocketSink

log = logging.getLogger(__name__)


def setup_logging(cfg: AppConfig, config_dir: Path) -> None:
    level = getattr(logging, cfg.logging.level, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if cfg.logging.file:
        logfile = (config_dir.parent / cfg.logging.file) if not Path(cfg.logging.file).is_absolute() \
            else Path(cfg.logging.file)
        logfile.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            logfile, maxBytes=cfg.logging.max_bytes, backupCount=cfg.logging.backup_count,
            encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def make_source(cfg: AppConfig, window_provider, poll_hz: float = 2.0):
    b = cfg.capture.backend
    if b == "folder":
        from .capture.folder import FolderSource
        folder = cfg.capture.folder
        p = Path(folder)
        if not p.is_absolute():
            p = Path.cwd() / folder
        return FolderSource(p, fps=poll_hz)
    if b == "wgc":
        from .capture.wgc import WgcSource
        return WgcSource(window_provider, fps=cfg.capture.fps)
    if b == "dxcam":
        from .capture.dxcam_source import DxcamSource
        return DxcamSource(window_provider, fps=cfg.capture.fps)
    if b == "mss":
        from .capture.mss_source import MssSource
        return MssSource(window_provider, fps=cfg.capture.fps)
    raise ValueError(f"неизвестный backend захвата {b!r}")


class Pipeline:
    def __init__(self, store: ConfigStore, config_dir: Path, source_override: str | None = None,
                 debug_preview: bool = False, on_event: Callable[[TextEvent], None] | None = None):
        self.store = store
        self.config_dir = config_dir
        app, layout = store.snapshot()
        if source_override:
            app = app.model_copy(update={"capture": app.capture.model_copy(
                update={"backend": source_override})})
            store.apply_app(app)
        if debug_preview:
            app = app.model_copy(update={"debug": app.debug.model_copy(update={"preview": True})})
            store.apply_app(app)
        self.app = app

        self.cropper = RegionCropper(store)
        self.scheduler = FrameScheduler(
            poll_hz_getter=lambda: store.snapshot()[1].chat.poll_hz,
            threshold_getter=lambda: store.snapshot()[1].chat.change_threshold,
        )
        engine_name = app.ocr.engine
        self.engine = ocr_base.create_engine(engine_name, app.ocr)
        self.tracker = ChatTracker(region="chat", engine_name=engine_name)

        self.bus = EventBus()
        self.journal = SessionJournal(Path(app.journal.dir), save_crops=app.journal.save_crops)
        self.console = ConsoleSink() if app.sinks.console else None
        self.ws = WebSocketSink(app.sinks.websocket.host, app.sinks.websocket.port) \
            if app.sinks.websocket.enabled else None
        self.preview = None
        if store.snapshot()[0].debug.preview:
            from .debug.preview import PreviewWindow
            self.preview = PreviewWindow()

        self._executor = cf.ThreadPoolExecutor(max_workers=max(1, app.ocr.workers),
                                               thread_name_prefix="ocr")
        self._latest_frame: Frame | None = None
        self._frame_lock = threading.Lock()
        self._ocr_busy = threading.Event()
        self._stop = threading.Event()
        self._source = None
        self._on_event_extra = on_event
        store.subscribe(self._on_config_change)

    # ---------- конфиг на лету (FR-5) ----------
    def _on_config_change(self, what: str) -> None:
        app, layout = self.store.snapshot()
        if what == "app":
            new_engine = ocr_base.create_engine(app.ocr.engine, app.ocr)
            if new_engine.name != self.engine.name:
                log.info("смена OCR-движка: %s -> %s", self.engine.name, new_engine.name)
                self.engine = new_engine
                self.tracker.engine = new_engine.name
            self.journal.update_opts(app.journal.save_crops)
        if what == "layout":
            self.scheduler._detector.reset()   # регион поехал — сравниваем заново

    # ---------- поток захвата ----------
    def _on_frame(self, frame: Frame) -> None:
        """Из потока захвата: держим только последний кадр (drop-old)."""
        with self._frame_lock:
            self._latest_frame = frame
        if not self._ocr_busy.is_set():
            self._ocr_busy.set()
            self._executor.submit(self._ocr_worker)

    def _ocr_worker(self) -> None:
        try:
            while not self._stop.is_set():
                with self._frame_lock:
                    frame, self._latest_frame = self._latest_frame, None
                if frame is None:
                    break
                self._process_frame(frame)
        except Exception:
            log.exception("сбой OCR-воркера на кадре — конвейер продолжает (NFR-7)")
        finally:
            self._ocr_busy.clear()

    def _process_frame(self, frame: Frame) -> None:
        _, layout = self.store.snapshot()
        crop, rect = self.cropper.crop_chat(frame)
        if self.preview:
            self.preview.show_raw(crop, rect)
        if not self.scheduler.should_process(crop):
            return                                   # FR-6
        img = preprocess.apply_chain(crop, layout.chat.preprocess)
        if self.preview:
            self.preview.show_processed(img)
        try:
            lines = self.engine.recognize(img, self.store.snapshot()[0].ocr.language)
        except Exception:
            log.exception("OCR упал на кадре %d — пропуск (NFR-7)", frame.frame_id)
            return
        events = self.tracker.on_lines(lines, frame.frame_id)
        for ev in events:
            log.debug("событие: %s", ev)
            if self._on_event_extra:
                try:
                    self._on_event_extra(ev)
                except Exception:
                    log.exception("on_event callback упал")
            # журнал пишем синхронно из этого же потока с кропом (save_crops)
            self.journal.publish_sync(ev, crop=crop if self.journal.save_crops else None)
            fut = asyncio.run_coroutine_threadsafe(
                self.bus.publish_threadsafe(_as_dict_without_crop(ev)), self._loop)
            fut.result(timeout=2)
        if self.preview:
            self.preview.show_lines(lines)

    # ---------- главный цикл ----------
    async def run_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.bus.subscribe(self.console) if self.console else None
        # journal уже пишется синхронно из OCR-потока (FR-10 «сразу»),
        # но подписываем и на шину события других типов (MomentEvent в будущем)
        self.bus.subscribe(self.journal)
        if self.ws:
            await self.ws.start()
            self.bus.subscribe(self.ws)
        await self.bus.start()

        provider = self._make_window_provider()
        self._source = make_source(self.store.snapshot()[0], provider,
                                   poll_hz=self.store.snapshot()[1].chat.poll_hz)
        stop_evt = self._stop

        def should_stop():
            return stop_evt.is_set()

        loop = asyncio.get_running_loop()
        try:
            sigint_handled = False
            if hasattr(signal, "SIGINT"):
                try:
                    loop.add_signal_handler(signal.SIGINT, self.request_stop)
                    sigint_handled = True
                except NotImplementedError:   # Windows
                    pass
            await asyncio.to_thread(self._start_capture_blocking, should_stop)
        except TimeoutError:
            log.warning("ожидание окна прервано")
        finally:
            await self.shutdown()
            if sigint_handled:
                loop.remove_signal_handler(signal.SIGINT)

    def _make_window_provider(self):
        state = {}

        def provider():
            app, _ = self.store.snapshot()
            from .capture import window as wmod
            if "info" not in state or state["info"] is None:
                try:
                    state["info"] = wmod.find_window(app.window.title_regex,
                                                     app.window.process_name)
                except RuntimeError:
                    return None
            else:
                try:
                    state["info"] = wmod.refresh(state["info"])
                except RuntimeError:
                    return None
            return state["info"]

        return provider

    def _start_capture_blocking(self, should_stop) -> None:
        app, _ = self.store.snapshot()
        if app.capture.backend != "folder":
            from .capture import window as wmod
            info = None
            while info is None and not should_stop():
                info = wmod.find_window(app.window.title_regex, app.window.process_name) \
                    if wmod.IS_WINDOWS else None
                if info is None and not should_stop():
                    log.info("окно '%s' не найдено, повтор через %.1f с",
                             app.window.title_regex, app.window.retry_seconds)  # FR-1
                    time.sleep(app.window.retry_seconds)
            if info is None:
                return
        self._source.start(self._on_frame)
        while not should_stop():
            time.sleep(0.2)
        self._source.stop()

    def request_stop(self) -> None:
        log.info("остановка запрошена")
        self._stop.set()

    async def shutdown(self) -> None:
        """FR-15: остановка захвата, сброс журнала и буферов sinks."""
        if self._source:
            try:
                self._source.stop()
            except Exception:
                log.exception("ошибка остановки захвата")
        self._executor.shutdown(wait=True)
        await self.bus.stop()          # закрывает sinks (console/ws)
        await self.journal.close()
        if self.preview:
            self.preview.destroy()
        log.info("конвейер остановлен")

    def run(self) -> None:
        asyncio.run(self.run_async())


def _as_dict_without_crop(ev: TextEvent) -> dict:
    import dataclasses
    d = dataclasses.asdict(ev)
    return d
