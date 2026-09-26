"""Bench (FR-14): CER, p50/p95 задержки распознавания и память по вариантам.

Прогоняет golden-набор (пары PNG + .txt эталон в tests/fixtures/chat) через
матрицу: цепочки предобработки × psm Tesseract. Точность — нормализованный
CER по Левенштейну (rapidfuzz.distance.Levenshtein). Память — RSS процесса
после прогона (Windows: psutil не в зависимостях, берём GetProcessMemoryInfo
через ctypes; на Linux — /proc/self/status).

Отчёт печатается таблицей в консоль и сохраняется в logs/bench_<ts>.txt.
"""
from __future__ import annotations

import logging
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from rapidfuzz.distance import Levenshtein

from . import preprocess
from .config import load_app_config
from .ocr.base import OcrLine
from .ocr.tesseract import TesseractEngine  # регистрация движка как побочный эффект импорта

log = logging.getLogger(__name__)

DEFAULT_CHAINS = [
    ["upscale_3x", "grayscale", "autocontrast", "invert"],   # базовая из раскладки
    ["upscale_2x", "grayscale", "autocontrast", "invert"],
    ["upscale_3x", "grayscale", "threshold", "invert"],
    ["grayscale", "invert"],
]
DEFAULT_PSMS = [6, 4]


@dataclass
class BenchResult:
    chain: tuple[str, ...]
    psm: int
    cer: float | None
    n_files: int
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def p50(self): return statistics.median(self.latencies_ms) if self.latencies_ms else float("nan")

    @property
    def p95(self):
        if not self.latencies_ms:
            return float("nan")
        xs = sorted(self.latencies_ms)
        i = max(0, min(len(xs) - 1, int(round(0.95 * (len(xs) - 1)))))
        return xs[i]


def normalize_text(s: str) -> str:
    return "".join(s.lower().split())


def cer(hyp: str, ref: str) -> float:
    ref_n, hyp_n = normalize_text(ref), normalize_text(hyp)
    if not ref_n:
        return 0.0 if not hyp_n else 1.0
    return Levenshtein.distance(hyp_n, ref_n) / len(ref_n)


def process_rss_mb() -> float:
    if sys.platform == "win32":
        try:
            import ctypes
            h = ctypes.windll.kernel32.GetCurrentProcess()
            class PMC(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_uint32),
                            ("PageFaultCount", ctypes.c_uint32),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
            return pmc.WorkingSetSize / 1024 / 1024
        except Exception:
            return float("nan")
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"):
                return float(line.split()[1]) / 1024
    except Exception:
        pass
    return float("nan")


def load_cases(folder: Path) -> list[tuple[Path, str]]:
    cases = []
    for png in sorted(folder.glob("*.png")):
        txt = png.with_suffix(".txt")
        if txt.exists():
            cases.append((png, txt.read_text(encoding="utf-8")))
    return cases


def run_bench(config_dir: Path, fixtures: Path,
              chains: list[str] | None = None) -> int:
    app = load_app_config(config_dir / "app.yaml")
    cases = load_cases(fixtures)
    if not cases:
        print(f"golden-набор пуст: {fixtures} (нужны пары image.png + image.txt)", file=sys.stderr)
        return 2
    chain_list: list[list[str]] = DEFAULT_CHAINS
    if chains:
        chain_list = [[s.strip() for s in c.split(",") if s.strip()] for c in chains]

    results: list[BenchResult] = []
    for chain in chain_list:
        for psm in DEFAULT_PSMS:
            tcfg = app.ocr.tesseract.model_copy(update={"psm": psm, "min_word_conf": 0})
            ocr_cfg = app.ocr.model_copy(update={"tesseract": tcfg})
            engine = TesseractEngine(ocr_cfg)
            ces: list[float] = []
            lats: list[float] = []
            ok = 0
            for png, ref in cases:
                img = cv2.imread(str(png), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                prep = preprocess.apply_chain(img, chain)
                t0 = time.perf_counter()
                try:
                    lines = engine.recognize(prep, app.ocr.language)
                except Exception as e:
                    print(f"движок упал ({'->'.join(chain)}, psm={psm}): {e}", file=sys.stderr)
                    return 3
                dt = (time.perf_counter() - t0) * 1000
                hyp = "\n".join(l.text for l in lines)
                ces.append(cer(hyp, ref))
                lats.append(dt)
                ok += 1
            results.append(BenchResult(tuple(chain), psm,
                                       sum(ces) / len(ces) if ces else None, ok, lats))

    rss = process_rss_mb()
    lines_out = []
    lines_out.append(f"Bench: {len(cases)} кейсов из {fixtures}; язык={app.ocr.language}; "
                     f"движок=tesseract; RSS после прогона: {rss:.0f} МБ")
    lines_out.append(f"{'цепочка предобработки':46} {'psm':>3} {'CER%':>7} {'p50 мс':>8} "
                     f"{'p95 мс':>8} {'N':>3}")
    best = min((r for r in results if r.cer is not None), key=lambda r: r.cer, default=None)
    for r in sorted(results, key=lambda r: (r.cer is None, r.cer)):
        mark = "  <= лучшая" if r is best else ""
        cer_s = f"{100*r.cer:7.2f}" if r.cer is not None else "    n/a"
        lines_out.append(f"{'->'.join(r.chain):46} {r.psm:>3} {cer_s} {r.p50:8.0f} "
                         f"{r.p95:8.0f} {r.n_files:>3}{mark}")
    report = "\n".join(lines_out)
    print(report)
    outdir = Path("logs"); outdir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    (outdir / f"bench_{stamp}.txt").write_text(report + "\n", encoding="utf-8")
    if best and best.cer is not None:
        print(f"\nКритерий этапа 3 (CER ≤ 5%): {'PASS' if best.cer <= 0.05 else 'FAIL'} "
              f"(лучший CER {100*best.cer:.2f}% при {'->'.join(best.chain)}, psm={best.psm})")
    return 0
