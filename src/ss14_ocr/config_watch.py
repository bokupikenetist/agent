"""ConfigWatcher: watchdog + валидация + атомарное применение (FR-5).

Слежение за config/app.yaml и активной раскладкой config/layouts/<layout>.yaml.
Изменения применяются без перезапуска за ≤ 2 с; невалидный файл не применяется,
ошибка пишется в лог, работа продолжается на прежних настройках.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import AppConfig, ConfigError, LayoutCfg, load_app_config, load_layout_config

log = logging.getLogger(__name__)


class ConfigStore:
    """Текущий конфиг + подписчики. apply_* возвращают True, если применилось."""

    def __init__(self, app: AppConfig, layout: LayoutCfg):
        self.app = app
        self.layout = layout
        self._lock = threading.RLock()
        self._subs: list[Callable[[str], None]] = []   # str: "app" | "layout"

    def subscribe(self, cb: Callable[[str], None]) -> None:
        with self._lock:
            self._subs.append(cb)

    def snapshot(self) -> tuple[AppConfig, LayoutCfg]:
        with self._lock:
            return self.app, self.layout

    def apply_app(self, cfg: AppConfig) -> bool:
        with self._lock:
            self.app = cfg
            subs = list(self._subs)
        log.info("конфиг app.yaml применён")
        for cb in subs:
            _safe_cb(cb, "app")
        return True

    def apply_layout(self, cfg: LayoutCfg) -> bool:
        with self._lock:
            self.layout = cfg
            subs = list(self._subs)
        log.info("раскладка применена: chat.rect=%s", cfg.chat.rect)
        for cb in subs:
            _safe_cb(cb, "layout")
        return True


def _safe_cb(cb, what: str) -> None:
    try:
        cb(what)
    except Exception:
        log.exception("подписчик конфига упал на %s", what)


class ConfigWatcher:
    def __init__(self, config_dir: Path, store: ConfigStore, debounce: float = 0.3):
        self.config_dir = Path(config_dir).resolve()
        self.layouts_dir = self.config_dir / "layouts"
        self.app_path = self.config_dir / "app.yaml"
        self.store = store
        self.debounce = debounce
        self._observer: Observer | None = None
        self._last_event_ts = 0.0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    # ---------- публичный API ----------
    def start(self) -> None:
        self._observer = Observer()
        handler = _Handler(self)
        self._observer.schedule(handler, str(self.app_path.parent))
        if self.layouts_dir.is_dir():
            self._observer.schedule(handler, str(self.layouts_dir))
        self._observer.daemon = True
        self._observer.start()
        log.info("ConfigWatcher запущен: %s", self.config_dir)

    def stop(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None

    # ---------- внутренняя логика ----------
    def on_file_change(self, path: Path) -> None:
        """Дебаунс: переписывание файла генерирует несколько событий."""
        with self._lock:
            self._pending = path
            now = time.monotonic()
            if self._timer and now - self._last_event_ts < self.debounce:
                self._timer.cancel()
            self._last_event_ts = now
            self._timer = threading.Timer(self.debounce, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            path = getattr(self, "_pending", None)
            self._timer = None
        if path is None:
            return
        try:
            self.reload(path)
        except ConfigError as e:
            # FR-5: невалидный файл не применяется, работаем на прежних настройках
            log.error("конфиг не применён: %s", e)
        except Exception:
            log.exception("ошибка перечитывания конфига %s", path)

    def reload(self, changed: Path | None = None) -> None:
        """Перечитывает нужный файл (или оба) и применяет, если он изменился."""
        app_path = self.app_path
        layout_name = self.store.snapshot()[0].layout
        layout_path = self.layouts_dir / f"{layout_name}.yaml"

        if changed is not None and Path(changed).resolve() == layout_path.resolve():
            targets = [layout_path]
        elif changed is not None and Path(changed).resolve() == app_path.resolve():
            targets = [app_path]
        else:
            targets = [app_path, layout_path]

        for p in targets:
            if p == app_path:
                new_app = load_app_config(p)
                old_app, _ = self.store.snapshot()
                if new_app.layout != old_app.layout:
                    # сменилась активная раскладка — перечитать её
                    lp = self.layouts_dir / f"{new_app.layout}.yaml"
                    self.store.apply_layout(load_layout_config(lp))
                if new_app != old_app:
                    self.store.apply_app(new_app)
            elif p.exists() or p == layout_path:
                new_layout = load_layout_config(p)
                _, old_layout = self.store.snapshot()
                if new_layout != old_layout:
                    self.store.apply_layout(new_layout)


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: ConfigWatcher):
        self.w = watcher

    @staticmethod
    def _relevant(path: str) -> bool:
        return path.endswith((".yaml", ".yml"))

    def on_modified(self, event):
        if not event.is_directory and self._relevant(event.src_path):
            self.w.on_file_change(Path(event.src_path))

    def on_created(self, event):
        if not event.is_directory and self._relevant(event.src_path):
            self.w.on_file_change(Path(event.src_path))

    def on_moved(self, event):
        dest = getattr(event, "dest_path", "") or ""
        if self._relevant(dest):
            self.w.on_file_change(Path(dest))
