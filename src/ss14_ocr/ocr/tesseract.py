"""Tesseract-движок (pytesseract), язык rus, psm/oem/min_word_conf из конфига (FR-8).

Слова ниже min_word_conf отбрасываются; строки собираются по блокам/строкам
из TSV, уверенность строки — средневзвешенная по словам (0..1).
"""
from __future__ import annotations

import logging

import numpy as np

from .base import OcrLine, register_engine

log = logging.getLogger(__name__)


@register_engine("tesseract")
class TesseractEngine:
    name = "tesseract"

    def __init__(self, ocr_cfg):
        self.cfg = ocr_cfg                 # OcrCfg
        self.t = ocr_cfg.tesseract         # TesseractCfg

    def get_cmd(self) -> str | None:
        if not self.t.cmd:
            return None                    # пусть pytesseract ищет tesseract в PATH
        return self.t.cmd                  # явный путь из app.yaml (FR-8)

    def recognize(self, image: np.ndarray, language: str) -> list[OcrLine]:
        import os
        import pytesseract

        cfg_args = f"--oem {self.t.oem} --psm {self.t.psm}"
        # у image_to_data нет параметров cmd/env (это аргументы run_tesseract) —
        # путь к бинарю задаём через pytesseract.tesseract_cmd, tessdata — через
        # переменную окружения TESSDATA_PREFIX
        cmd = self.get_cmd()
        if cmd is not None:
            pytesseract.tesseract_cmd = cmd
        prev_prefix = os.environ.get("TESSDATA_PREFIX")
        if self.t.tessdata_dir:
            os.environ["TESSDATA_PREFIX"] = self.t.tessdata_dir
        try:
            data = pytesseract.image_to_data(
                image,
                lang=language,
                config=cfg_args,
                output_type=pytesseract.Output.DICT,
            )
        except pytesseract.TesseractNotFoundError as e:
            raise RuntimeError(
                "tesseract не найден; укажите ocr.tesseract.cmd в app.yaml"
            ) from e
        finally:
            if self.t.tessdata_dir:
                if prev_prefix is None:
                    os.environ.pop("TESSDATA_PREFIX", None)
                else:
                    os.environ["TESSDATA_PREFIX"] = prev_prefix
        return self._lines_from_data(data)

    def _lines_from_data(self, data: dict) -> list[OcrLine]:
        min_conf = self.t.min_word_conf
        lines: dict[tuple[int, int, int], list] = {}   # (block,line_num) -> words
        n = len(data["text"])
        for i in range(n):
            txt = (data["text"][i] or "").strip()
            if not txt:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            key = (int(data["page_num"][i]), int(data["block_num"][i]), int(data["par_num"][i]))
            lines.setdefault(key, []).append((txt, conf,
                                              int(data["left"][i]), int(data["top"][i]),
                                              int(data["width"][i]), int(data["height"][i])))
        out: list[OcrLine] = []
        for key in sorted(lines):
            words = [w for w in lines[key] if w[1] >= min_conf]
            if not words:
                continue
            text = " ".join(w[0] for w in words)
            x0 = min(w[2] for w in words)
            y0 = min(w[3] for w in words)
            x1 = max(w[2] + w[4] for w in words)
            y1 = max(w[3] + w[5] for w in words)
            confs = [w[1] for w in words]
            avg = sum(confs) / len(confs) / 100.0
            out.append(OcrLine(text=text, bbox=(x0, y0, x1 - x0, y1 - y0), confidence=round(avg, 3)))
        return out
