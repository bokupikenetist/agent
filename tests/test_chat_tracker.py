"""Тесты ChatTracker на синтетических последовательностях (FR-9):
прокрутка, переносы, одинаковые сообщения подряд, старт с истории.
"""
from ss14_ocr.chat_tracker import ChatTracker
from ss14_ocr.ocr.base import OcrLine


def L(*texts):
    return [OcrLine(text=t, bbox=(0, i * 20, 300, 18), confidence=0.9)
            for i, t in enumerate(texts)]


def texts(events):
    return [e.text for e in events]


def test_first_frame_not_published():
    tr = ChatTracker(engine_name="tess")
    ev = tr.on_lines(L("[общий].alpha говорит: привет", "[общий] Боб: ок"), frame_id=1)
    assert ev == []                            # история при старте — не события


def test_new_line_scrolled_in():
    tr = ChatTracker(engine_name="tess")
    screen = ["A1", "A2", "A3"]
    tr.on_lines(L(*screen), 1)
    ev = tr.on_lines(L("A1", "A2", "A3", "A4 новый"), 2)
    assert texts(ev) == ["A4 новый"]


def test_scroll_off_top_only():
    tr = ChatTracker(engine_name="tess")
    tr.on_lines(L("A1", "A2", "A3", "A4"), 1)
    ev = tr.on_lines(L("A2", "A3", "A4"), 2)   # просто прокрутка вверх/исчезновение
    assert ev == []


def test_scroll_with_new_at_bottom():
    tr = ChatTracker(engine_name="tess")
    tr.on_lines(L("B1", "B2", "B3"), 1)
    ev = tr.on_lines(L("B2", "B3", "B4", "B5"), 2)
    assert texts(ev) == ["B4", "B5"]


def test_wrapped_message_two_lines():
    tr = ChatTracker(engine_name="tess")
    tr.on_lines(L("X1", "X2"), 1)
    ev = tr.on_lines(L("X1", "X2", "Длинное сообщение начало", "продолжение"), 2)
    assert texts(ev) == ["Длинное сообщение начало", "продолжение"]


def test_identical_messages_repeated_publish_once_each():
    tr = ChatTracker(engine_name="tess")
    tr.on_lines(L("C1"), 1)
    ev1 = tr.on_lines(L("C1", "Лол"), 2)
    ev2 = tr.on_lines(L("C1", "Лол", "Лол"), 3)
    assert texts(ev1) == ["Лол"]
    # второе «Лол» на экране — новое событие? Нет: на прошлом экране было 1 «Лол»,
    # стало 2 — публикуем одно.
    assert texts(ev2) == ["Лол"]


def test_same_screen_twice_no_events():
    tr = ChatTracker(engine_name="tess")
    tr.on_lines(L("D1", "D2"), 1)
    assert tr.on_lines(L("D1", "D2"), 2) == []
    assert tr.on_lines(L("D1", "D2"), 3) == []


def test_ocr_noise_tolerated():
    tr = ChatTracker(engine_name="tess")
    tr.on_lines(L("[общий] Джон говорит: в двигателе утечка"), 1)
    # лёгкий шум распознавания той же строки — не считаем новой
    ev = tr.on_lines(L("[общий] Дон говорит: в двигателе утечка"), 2)
    assert ev == []


def test_reset_clears_state():
    tr = ChatTracker(engine_name="tess")
    tr.on_lines(L("E1"), 1)
    tr.reset()
    assert tr.on_lines(L("E1"), 2) == []       # снова первый кадр — молчим
