"""Golden-тест OCR (этап 3): Tesseract rus на фикстурах chat_*.png+txt.

Пропускается, если tesseract или rus.traineddata недоступны (CI без OCR).
Порог CER ≤ 5% — критерий приёмки этапа 3; для синтетических фикстур он же.
"""
import shutil
import sys
from pathlib import Path

import pytest

from ss14_ocr import preprocess
from ss14_ocr.bench import cer
from ss14_ocr.config import OcrCfg, TesseractCfg
from ss14_ocr.ocr.tesseract import TesseractEngine

FIXTURES = Path(__file__).parent / "fixtures" / "chat"


def _tesseract_available() -> bool:
    exe = shutil.which("tesseract") or (
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe").exists())
    if not exe:
        return False
    import subprocess
    try:
        r = subprocess.run([str(exe), "--list-langs"], capture_output=True, text=True, timeout=10)
        return "rus" in (r.stdout + r.stderr)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _tesseract_available(),
                                reason="tesseract/rus недоступен в этой среде")


def _cases():
    for png in sorted(FIXTURES.glob("chat_*.png")):
        txt = png.with_suffix(".txt")
        if txt.exists():
            yield pytest.param(png, txt.read_text(encoding="utf-8"), id=png.stem)


@pytest.mark.parametrize("png,ref", list(_cases()))
def test_golden_cer(png, ref):
    import cv2
    cfg = OcrCfg(tesseract=TesseractCfg(cmd=None, psm=6, oem=1, min_word_conf=0))
    engine = TesseractEngine(cfg)
    img = cv2.imread(str(png), cv2.IMREAD_COLOR)
    assert img is not None
    prep = preprocess.apply_chain(img, ["upscale_2x", "grayscale", "autocontrast", "invert"])
    lines = engine.recognize(prep, "rus")
    hyp = "\n".join(l.text for l in lines)
    score = cer(hyp, ref)
    assert score <= 0.05, f"CER {score:.2%} > 5%: гипотеза={hyp!r}"


def test_engine_returns_lines_with_bbox_and_conf():
    import cv2
    pngs = list(FIXTURES.glob("chat_*.png"))
    if not pngs:
        pytest.skip("нет фикстур")
    engine = TesseractEngine(OcrCfg(tesseract=TesseractCfg(min_word_conf=0)))
    img = cv2.imread(str(pngs[0]), cv2.IMREAD_COLOR)
    lines = engine.recognize(preprocess.apply_chain(img, ["upscale_2x", "grayscale"]), "rus")
    assert lines, "распознано 0 строк"
    for l in lines:
        x, y, w, h = l.bbox
        assert w > 0 and h > 0
        assert l.confidence is None or 0.0 <= l.confidence <= 1.0
