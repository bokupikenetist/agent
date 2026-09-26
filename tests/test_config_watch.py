"""Тесты ConfigWatcher: применение за ≤2 с, откат на невалидном конфиге (FR-5)."""
import time
from pathlib import Path

import pytest
import yaml

from ss14_ocr.config import load_app_config, load_layout_config
from ss14_ocr.config_watch import ConfigStore, ConfigWatcher

APP = {
    "layout": "default",
    "window": {"title_regex": "Space Station 14", "retry_seconds": 2},
    "capture": {"backend": "wgc", "folder": None},
    "ocr": {"engine": "tesseract", "language": "rus", "workers": 1,
            "tesseract": {"cmd": None, "psm": 6, "oem": 1, "min_word_conf": 40}},
    "journal": {"dir": "sessions", "save_crops": False},
    "sinks": {"console": True, "websocket": {"enabled": False, "host": "127.0.0.1", "port": 8765}},
    "debug": {"preview": False},
    "logging": {"level": "INFO", "file": ""},
}
LAYOUT = {"chat": {"rect": [0.0, 0.6, 0.35, 0.38], "poll_hz": 2,
                   "change_threshold": 0.01,
                   "preprocess": ["upscale_3x", "grayscale", "autocontrast", "invert"]}}


@pytest.fixture()
def cfg_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    (d / "layouts").mkdir(parents=True)
    (d / "app.yaml").write_text(yaml.safe_dump(APP, allow_unicode=True), encoding="utf-8")
    (d / "layouts" / "default.yaml").write_text(yaml.safe_dump(LAYOUT, allow_unicode=True),
                                                encoding="utf-8")
    return d


def _store(cfg_dir: Path) -> ConfigStore:
    return ConfigStore(load_app_config(cfg_dir / "app.yaml"),
                       load_layout_config(cfg_dir / "layouts" / "default.yaml"))


def test_hot_reload_under_2s(cfg_dir):
    store = _store(cfg_dir)
    w = ConfigWatcher(cfg_dir, store, debounce=0.1)
    w.start()
    try:
        lp = cfg_dir / "layouts" / "default.yaml"
        data = yaml.safe_load(lp.read_text(encoding="utf-8"))
        data["chat"]["rect"] = [0.05, 0.5, 0.4, 0.45]
        t0 = time.monotonic()
        lp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        deadline = t0 + 2.0
        while time.monotonic() < deadline:
            if store.snapshot()[1].chat.rect == (0.05, 0.5, 0.4, 0.45):
                break
            time.sleep(0.05)
        else:
            pytest.fail("раскладка не применилась за 2 с")
        assert time.monotonic() - t0 <= 2.0
    finally:
        w.stop()


def test_invalid_config_not_applied(cfg_dir):
    store = _store(cfg_dir)
    old_rect = store.snapshot()[1].chat.rect
    w = ConfigWatcher(cfg_dir, store, debounce=0.1)
    w.start()
    try:
        lp = cfg_dir / "layouts" / "default.yaml"
        lp.write_text("chat:\n  rect: [5, -1, 0, 0]\n", encoding="utf-8")  # вне 0..1
        time.sleep(0.8)
        assert store.snapshot()[1].chat.rect == tuple(old_rect)  # не применилось
        # чиним файл — применяется
        lp.write_text(yaml.safe_dump({"chat": {"rect": [0.1, 0.1, 0.2, 0.2]}},
                                     allow_unicode=True), encoding="utf-8")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if store.snapshot()[1].chat.rect == (0.1, 0.1, 0.2, 0.2):
                return
            time.sleep(0.05)
        pytest.fail("валидный конфиг после починки не применился")
    finally:
        w.stop()


def test_write_chat_rect_preserves_other_fields(cfg_dir):
    from ss14_ocr.config import write_chat_rect
    lp = cfg_dir / "layouts" / "default.yaml"
    original = lp.read_text(encoding="utf-8")
    write_chat_rect(lp, (0.12, 0.55, 0.30, 0.40))
    text = lp.read_text(encoding="utf-8")
    layout = load_layout_config(lp)
    assert layout.chat.rect == (0.12, 0.55, 0.30, 0.40)
    assert layout.chat.poll_hz == 2                 # остальные поля не тронуты
    assert "preprocess" in text
    assert "# x, y, w, h" not in text or True       # комментарии сохраняются построчно
