"""Debug-превью (FR-13): окно с кропом чата, рамкой региона и распознанным текстом.

Показывает кадр клиентской области целиком с нарисованной рамкой chat.rect и
подписями OcrLine, плюс второе окно — изображение после предобработки.
cv2.imshow требует GUI; без него превью автоматически отключается с warning
(конвейер работает дальше). Вызывается из OCR-потока — все операции в try.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

log = logging.getLogger(__name__)


class PreviewWindow:
    WIN_MAIN = "SS14 OCR: debug preview"
    WIN_PREP = "SS14 OCR: preprocessed"

    def __init__(self):
        self._ok = True
        self._last_frame: np.ndarray | None = None
        self._last_rect = None

    # ---------- основной кадр с рамкой региона ----------
    def show_raw(self, crop: np.ndarray, rect) -> None:
        """crop — кроп чата; rect — RectPx внутри кадра (рамка рисуется на canvas).

        Для экономии показываем сам кроп как «кадр» с подписью размеров:
        рамка региона в конвейере уже применена, в превью виден результат.
        """
        if not self._ensure():
            return
        self._last_frame = crop.copy()
        self._last_rect = rect
        try:
            vis = self._draw_rect(crop)
            cv2.imshow(self.WIN_MAIN, vis)
            self._pump()
        except Exception:
            log.debug("preview show_raw failed", exc_info=True)

    def _draw_rect(self, crop: np.ndarray) -> np.ndarray:
        vis = crop.copy()
        h, w = vis.shape[:2]
        cv2.rectangle(vis, (0, 0), (w - 1, h - 1), (0, 255, 0), 2)
        cv2.putText(vis, f"chat region {w}x{h}", (6, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
        return vis

    # ---------- подготовленное изображение ----------
    def show_processed(self, img: np.ndarray) -> None:
        if not self._ok:
            return
        try:
            g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            cv2.imshow(self.WIN_PREP, g)
        except Exception:
            log.debug("preview show_processed failed", exc_info=True)

    # ---------- распознанные строки поверх кадра ----------
    def show_lines(self, lines) -> None:
        """bbox'ы OcrLine в пикселях предобработанного изображения.

        Масштабируем обратно к размеру кропа по высоте (цепочка может менять
        размер только upscale-шагами, поэтому масштаб = h_cropped/h_prepared).
        """
        if not self._ok or not lines or self._last_frame is None:
            return
        try:
            base = self._last_frame
            bh, bw = base.shape[:2]
            max_y = max((l.bbox[1] + l.bbox[3]) for l in lines) or 1
            s = bh / max(max_y, bh)          # если предобработка не меняла размер — 1
            out = base.copy()
            for l in lines:
                x, y, w, h = l.bbox
                X, Y, W, H = int(x * s), int(y * s), max(2, int(w * s)), max(2, int(h * s))
                cv2.rectangle(out, (X, Y), (min(X + W, bw - 1), min(Y + H, bh - 1)),
                              (0, 200, 255), 1)
                conf = f" {l.confidence:.2f}" if l.confidence is not None else ""
                cv2.putText(out, l.text[:70] + conf, (X, max(12, Y - 3)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1, cv2.LINE_AA)
            cv2.imshow(self.WIN_MAIN, out)
            self._pump()
        except Exception:
            log.debug("preview show_lines failed", exc_info=True)

    # ---------- служебное ----------
    def _ensure(self) -> bool:
        if not self._ok:
            return False
        try:
            cv2.namedWindow(self.WIN_MAIN, cv2.WINDOW_NORMAL)
            cv2.namedWindow(self.WIN_PREP, cv2.WINDOW_NORMAL)
            return True
        except Exception:
            log.warning("GUI недоступен — debug-превью отключено")
            self._ok = False
            return False

    def _pump(self) -> None:
        try:
            cv2.waitKey(1)
        except Exception:
            pass

    def destroy(self) -> None:
        if self._ok:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
