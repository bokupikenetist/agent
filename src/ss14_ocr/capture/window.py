"""Поиск окна SS14, DPI-awareness, клиентская область (FR-1, FR-2, NFR-5).

Работает только на Windows (pywin32 + ctypes); на других ОС импорт не падает,
но функции raise RuntimeError — это позволяет тестировать пакет целиком.
"""
from __future__ import annotations

import ctypes
import logging
import re
import sys
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import win32con
    import win32gui
    import win32process
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    shcore = ctypes.windll.shcore


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process_id: int | None
    client_rect: tuple[int, int, int, int]   # x, y, w, h экранные физические пиксели
    minimized: bool


def make_dpi_aware() -> None:
    """Per-Monitor V2 DPI awareness, иначе координаты «плывут» при 125–200%."""
    if not IS_WINDOWS:
        return
    try:
        PROCESS_PER_MONITOR_DPI_AWARE = 2
        shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        log.debug("DPI awareness: Per-Monitor V2")
    except Exception:
        try:
            user32.SetProcessDPIAware()
            log.debug("DPI awareness: System")
        except Exception:
            log.warning("не удалось включить DPI awareness", exc_info=True)


def _client_rect_screen(hwnd: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = win32gui.ClientToScreen(hwnd, (0, 0)) + win32gui.ClientToScreen(
        hwnd, win32gui.GetClientRect(hwnd)[2:])
    return left, top, right - left, bottom - top


def find_window(title_regex: str | None, process_name: str | None = None) -> WindowInfo | None:
    """Первое видимое окно, чей заголовок подходит под regex, либо по имени процесса."""
    if not IS_WINDOWS:
        raise RuntimeError("поиск окон доступен только на Windows")
    make_dpi_aware()
    rx = re.compile(title_regex, re.IGNORECASE) if title_regex else None
    proc = process_name.lower().removesuffix(".exe") if process_name else None
    found: list[WindowInfo] = []

    def cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd) or ""
            match_title = bool(rx and rx.search(title))
            match_proc = False
            pid = None
            if proc:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                match_proc = _process_name(pid, proc)
            if match_title or match_proc:
                minimized = bool(win32gui.IsIconic(hwnd))
                cr = _client_rect_screen(hwnd) if not minimized else (0, 0, 0, 0)
                found.append(WindowInfo(hwnd, title, pid, cr, minimized))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def _process_name(pid: int, wanted: str) -> bool:
    import win32api
    import win32con as _c
    import win32security
    try:
        h = win32api.OpenProcess(_c.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        exe = win32api.GetModuleFileNameExW(h, None)
        return exe.lower().removesuffix(".exe").rsplit("\\", 1)[-1] == wanted
    except Exception:
        return False


def refresh(info: WindowInfo) -> WindowInfo | None:
    """Актуальные состояние и клиентская область окна; None, если окно закрыто."""
    if not IS_WINDOWS:
        raise RuntimeError("окна доступны только на Windows")
    hwnd = info.hwnd
    if not win32gui.IsWindow(hwnd):
        return None
    minimized = bool(win32gui.IsIconic(hwnd))
    cr = (0, 0, 0, 0) if minimized else _client_rect_screen(hwnd)
    return WindowInfo(hwnd, win32gui.GetWindowText(hwnd) or "", info.process_id, cr, minimized)


def wait_for_window(title_regex: str, retry_seconds: float = 2.0,
                    process_name: str | None = None,
                    should_stop=lambda: False) -> WindowInfo:
    """FR-1: при отсутствии окна ждать и повторять поиск каждые retry_seconds."""
    while not should_stop():
        info = find_window(title_regex, process_name)
        if info:
            log.info("окно найдено: hwnd=%s title=%r rect=%s", info.hwnd, info.title, info.client_rect)
            return info
        log.info("окно '%s' не найдено, повтор через %.1f с", title_regex, retry_seconds)
        time.sleep(retry_seconds)
    raise TimeoutError("ожидание окна прервано остановкой")
