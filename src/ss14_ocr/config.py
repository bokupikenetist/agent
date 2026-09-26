"""pydantic-модели config/app.yaml и config/layouts/*.yaml (ТЗ §7).

Валидация одна и та же при старте и при горячем применении (FR-5):
невалидный конфиг поднимает ConfigError, старый продолжает действовать.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Конфиг невалиден (YAML битый или не прошёл pydantic)."""


Rect = tuple[float, float, float, float]


class WindowCfg(BaseModel):
    title_regex: str = "Space Station 14"
    process_name: str | None = None
    retry_seconds: float = Field(2.0, gt=0)


class CaptureCfg(BaseModel):
    backend: Literal["wgc", "dxcam", "mss", "folder"] = "wgc"
    folder: str | None = None          # путь к PNG для backend: folder
    fps: float = Field(15.0, gt=0)     # внутренняя частота захвата

    @field_validator("folder")
    @classmethod
    def _folder_required(cls, v, info):
        if info.data.get("backend") == "folder" and not v:
            raise ValueError("capture.folder обязателен при backend: folder")
        return v


class TesseractCfg(BaseModel):
    cmd: str | None = None             # путь к tesseract.exe, если его нет в PATH
    tessdata_dir: str | None = None
    psm: int = Field(6, ge=1, le=13)   # FR-8
    oem: int = Field(1, ge=0, le=3)
    min_word_conf: int = Field(40, ge=0, le=100)


class OcrCfg(BaseModel):
    engine: str = "tesseract"          # имя из реестра движков (FR-8)
    language: str = "rus"              # первая версия — только rus
    workers: int = Field(1, ge=1, le=8)
    tesseract: TesseractCfg = TesseractCfg()


class JournalCfg(BaseModel):
    dir: str = "sessions"
    save_crops: bool = False           # FR-10


class WebsocketSinkCfg(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"            # FR-11/NFR-8: только localhost
    port: int = Field(8765, ge=1, le=65535)


class SinksCfg(BaseModel):
    console: bool = True
    websocket: WebsocketSinkCfg = WebsocketSinkCfg()


class DebugCfg(BaseModel):
    preview: bool = False              # FR-13


class LoggingCfg(BaseModel):
    level: str = "INFO"
    file: str = "logs/ss14_ocr.log"
    max_bytes: int = 10 * 1024 * 1024  # NFR-9: ротация по 10 МБ
    backup_count: int = 3

    @field_validator("level")
    @classmethod
    def _valid_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        u = v.upper()
        if u not in allowed:
            raise ValueError(f"level должен быть одним из {sorted(allowed)}")
        return u


class AppConfig(BaseModel):
    layout: str = "default"
    window: WindowCfg = WindowCfg()
    capture: CaptureCfg = CaptureCfg()
    ocr: OcrCfg = OcrCfg()
    journal: JournalCfg = JournalCfg()
    sinks: SinksCfg = SinksCfg()
    debug: DebugCfg = DebugCfg()
    logging: LoggingCfg = LoggingCfg()


class RegionCfg(BaseModel):
    """Одна область экрана; для будущих областей (PDA, осмотр) тот же формат."""
    rect: Rect
    poll_hz: float = Field(2.0, gt=0)
    change_threshold: float = Field(0.01, ge=0, le=1)   # FR-6
    preprocess: list[str] = ["upscale_3x", "grayscale", "autocontrast", "invert"]

    @field_validator("rect")
    @classmethod
    def _rect_range(cls, v: Rect) -> Rect:
        x, y, w, h = v
        if not all(isinstance(t, (int, float)) for t in v):
            raise ValueError("rect — четыре числа")
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
            raise ValueError(f"rect {v} вне долей 0..1 (x,y,w,h)")
        return (float(x), float(y), float(w), float(h))


class LayoutCfg(BaseModel):
    chat: RegionCfg


def load_yaml(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"файл не найден: {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"битый YAML {path}: {e}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: ожидается mapping верхнего уровня")
    return data


def load_app_config(path: Path) -> AppConfig:
    try:
        return AppConfig(**load_yaml(path))
    except ValidationError as e:
        raise ConfigError(f"{path}: {e}") from e


def load_layout_config(path: Path) -> LayoutCfg:
    try:
        return LayoutCfg(**load_yaml(path))
    except ValidationError as e:
        raise ConfigError(f"{path}: {e}") from e


def write_chat_rect(layout_path: Path, rect: Rect) -> None:
    """Атомарно обновляет только chat.rect, сохраняя остальные поля (FR-4).

    Файл перечитывается через YAML, меняется одно поле `chat.rect`, результат
    переписывается целиком и подменяется через os.replace (атомарно), чтобы
    читатель не увидел наполовину записанный файл. Разметка приводится к
    block-style — пошаговая правка строк ломала файлы с блочными списками.
    """
    import os
    import tempfile

    data = load_yaml(layout_path)
    chat = data.get("chat") if isinstance(data, dict) else None
    if not isinstance(chat, dict) or "rect" not in chat:
        raise ConfigError(f"в {layout_path} не найдено поле chat.rect")

    chat["rect"] = [round(float(v), 4) for v in rect]
    out = "# раскладка; rect меняется edit-region (x, y, w, h в долях клиентской области)\n"
    out += yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    fd, tmp = tempfile.mkstemp(dir=str(layout_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(out)
        os.replace(tmp, layout_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
