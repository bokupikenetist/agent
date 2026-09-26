"""Генерация golden-набора: PNG кропов русского чата + .txt эталоны.

Реальные скриншоты добавляет человек (см. ТЗ §10); этот скрипт создаёт
синтетические кейсы, чтобы bench и тесты работали из коробки. Шрифт — любой
Cyrillic-шрифт matplotlib/дефолт cv2 не годится для кириллицы, поэтому рисуем
текст через PIL с шрифтом из системы; если PIL/шрифта нет — fallback на
консольный bitmap-рендер не делается, набор просто не генерируется.

Запуск: python tests/fixtures/make_fixtures.py [количество]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

LINES = [
    "[общий] Джон Доу говорит: в двигателе утечка",
    "[Инженерный] Анна кричит: нужен воздух срочно",
    "[Экипаж] Иванов: кто взял мачете со склада",
    "[радио] Кук говорит: обед готов, камин закрыт",
    "Доктор Петров применяет шприц-тюбик к пациенту",
    "[общий] Смит: на станции чужой, закройте двери",
    "Сигнал бедствия: отсек C затоплен плазмой",
    "[Экипаж] Механик: ремонт корпуса завершён успешно",
    "Неизвестный пишет в телефон: встретимся у шатла",
    "[общий] Охрана: подозреваемый задержан в бриге",
]


def render(lines: list[str], font_path: str | None) -> np.ndarray:
    """Тёмный фон, светлый текст — как чат SS14."""
    from PIL import Image, ImageDraw, ImageFont
    pad = 8
    lh = 34
    width = 760
    height = pad * 2 + lh * len(lines)
    img = np.full((height, width, 3), 28, np.uint8)
    im = Image.fromarray(img[:, :, ::-1])  # BGR->RGB
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype(font_path or _default_font(), 22)
    except Exception:
        font = ImageFont.load_default()
    y = pad
    for t in lines:
        dr.text((pad, y), t, fill=(235, 235, 235), font=font)
        y += lh
    return np.asarray(im)[:, :, ::-1].copy()  # RGB->BGR


def _default_font() -> str:
    import glob
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    hits = glob.glob("/usr/share/fonts/**/*Sans*.ttf", recursive=True)
    return hits[0] if hits else ""


def main(n: int = 10) -> int:
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("Pillow не установлен — пропуск генерации golden-набора", file=sys.stderr)
        return 1
    import cv2
    out = Path(__file__).resolve().parent / "chat"
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(11)
    made = 0
    for i in range(n):
        k = int(rng.integers(2, 5))
        idx = rng.choice(len(LINES), size=k, replace=False)
        lines = [LINES[j] for j in idx]
        img = render(lines, None)
        png = out / f"chat_{i:02d}.png"
        cv2.imwrite(str(png), img)
        txt = out / f"chat_{i:02d}.txt"
        txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
        made += 1
    print(f"golden-набор: {made} кейсов в {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 10))
