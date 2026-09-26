"""ChatTracker: чистка текста и выделение новых строк чата без дублей (FR-9).

Хранит нормализованный «экран» чата прошлого кадра. Для нового кадра строки
сопоставляются с прошлыми нечётким сравнением (rapidfuzz, token_sort_ratio) —
это даёт корректное поведение при прокрутке (сверху строки ушли, снизу пришли
новые) и переносах (длинное сообщение занимает 2+ визуальные строки).

Правило публикации: ищем снизу вверх самую нижнюю строку текущего экрана,
совпадающую со строкой прошлого; всё ниже неё — новые сообщения. Первый кадр
не публикуется целиком (исторический чат на экране при старте — не событие).
Одинаковые сообщения подряд различаются по числу вхождений: если «X» было на
прошлом экране k раз, а стало k+1 — одно новое внизу опубликуется.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import deque

from rapidfuzz import fuzz

from .events import TextEvent, now_iso
from .ocr.base import OcrLine

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")
_EDGE_JUNK = "\"'`,.;:|~^<>«»“”‘’…"


def normalize(text: str) -> str:
    """NFKC, нижний регистр, схлопнутые пробелы — канон для сравнения."""
    t = unicodedata.normalize("NFKC", text).strip().lower()
    return _WS.sub(" ", t)


class ChatTracker:
    def __init__(self, region: str = "chat", engine_name: str = "",
                 similarity: float = 85.0, history: int = 60):
        self.region = region
        self.engine = engine_name
        self.similarity = similarity            # порог нечёткого совпадения 0..100
        self._prev: list[str] = []              # нормализованный экран прошлого кадра
        self._first = True                      # первый кадр — не публикуем историю
        self._seen: deque[str] = deque(maxlen=history)   # последние опубликованные
        self._published_raw: set[str] = set()             # их сырой текст (для повторов)
        self._screen_published: list[str] = []            # нормализованный экран, по которому уже всё опубликовано

    def reset(self) -> None:
        self._prev = []
        self._seen.clear()
        self._published_raw.clear()
        self._screen_published = []
        self._first = True

    # ---------- основной вход ----------
    def on_lines(self, lines: list[OcrLine], frame_id: int) -> list[TextEvent]:
        cleaned = [(self._clean(l.text), l.confidence) for l in lines]
        keep = [(t, c) for t, c in cleaned if t]
        cur_raw = [t for t, _ in keep]
        cur_norm = [normalize(t) for t, _ in keep]
        conf_by_norm: dict[str, float | None] = {}
        for (raw, conf), n in zip(keep, cur_norm):
            conf_by_norm.setdefault(n, conf)

        events: list[TextEvent] = []
        if self._first:
            # первый непустой кадр — это история чата на экране при старте,
            # не публикуем ничего (docstring модуля и test_first_frame_not_published)
            self._first = False
        elif cur_norm:
            new_idx = self._first_new_index(cur_norm)
            for i in range(new_idx, len(cur_norm)):
                n = cur_norm[i]
                self._seen.append(n)
                self._published_raw.add(cur_raw[i])
                events.append(TextEvent(
                    region=self.region, kind="chat_line", text=cur_raw[i],
                    ts=now_iso(), frame_id=frame_id, engine=self.engine,
                    confidence=conf_by_norm.get(n),
                ))
        if cur_norm or not self._first:
            self._prev = cur_norm
        # помечаем текущий экран как полностью опубликованный (для проверки
        # «тот же экран повторно» в _first_new_index)
        self._screen_published = list(cur_norm)
        return events

    # ---------- внутренняя логика ----------
    @staticmethod
    def _clean(text: str) -> str:
        return _WS.sub(" ", text.strip()).strip(_EDGE_JUNK)

    def _similar(self, a: str, b: str) -> bool:
        if not a or not b:
            return False
        if a == b:
            return True
        return fuzz.token_sort_ratio(a, b) >= self.similarity

    def _first_new_index(self, cur: list[str]) -> int:
        """Индекс первой новой строки через LCS-выравнивание с прошлым экраном.

        Считаем максимальное общее подмножество строк (LCS) текущего и прошлого
        экрана; всё ниже последнего LCS-совпадения — новые сообщения. LCS
        корректно обрабатывает прокрутку (сверху строки ушли, снизу пришли новые),
        переносы и короткие «столовые» строки вроде «A1», «A2» без ложных склеек.
        """
        if not cur:
            return 0
        if not self._prev:
            # экрана раньше не было (первый непустой кадр или пустой прошлый):
            # считаем опубликованным уже весь экран — дубли истории не шлём
            return len(cur)
        n, m = len(cur), len(self._prev)
        # dp[i][j] — длина LCS от cur[i:] и prev[j:] (таблица с конца экранов)
        dp = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(n - 1, -1, -1):
            for j in range(m - 1, -1, -1):
                if self._similar(cur[i], self._prev[j]):
                    dp[i][j] = dp[i + 1][j + 1] + 1
                else:
                    dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
        # Проходим по выравниванию сверху вниз и запоминаем позицию самого
        # нижнего совпадения в текущем экране.
        i = j = 0
        lowest_match = -1
        while i < n and j < m:
            if self._similar(cur[i], self._prev[j]):
                lowest_match = i
                i += 1
                j += 1
            elif dp[i + 1][j] >= dp[i][j + 1]:
                i += 1                      # cur[i] — новая/лишняя строка
            else:
                j += 1                      # prev[j] ушла с экрана (прокрутка)
        if lowest_match < 0:
            # совпадений нет (смена раскладки/полная прокрутка за историю):
            # считаем новым только последний ряд — защита от шквала дублей
            return n - 1
        return lowest_match + 1

