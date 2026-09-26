"""Unit-тесты перевода относительных регионов в пиксели при разных DPI (FR-3, NFR-5)."""
import pytest

from ss14_ocr.rects import rel_to_px


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_rel_to_px_scales_with_frame(scale):
    # кадр = клиентская область в физических пикселях (DPI-aware захват)
    w, h = int(1920 * scale), int(1080 * scale)
    r = rel_to_px((0.0, 0.60, 0.35, 0.38), w, h)
    assert r.x == 0
    assert r.y == round(0.60 * h)
    assert r.w == round(0.35 * w)
    assert r.h == round(0.38 * h)
    assert r.x + r.w <= w and r.y + r.h <= h


def test_clamps_out_of_bounds():
    r = rel_to_px((0.9, 0.9, 0.5, 0.5), 1000, 1000)
    assert r.x == 900 and r.w == 100
    assert r.y == 900 and r.h == 100


def test_full_rect():
    r = rel_to_px((0.0, 0.0, 1.0, 1.0), 800, 600)
    assert (r.x, r.y, r.w, r.h) == (0, 0, 800, 600)


def test_empty_frame_raises():
    with pytest.raises(ValueError):
        rel_to_px((0, 0, 1, 1), 0, 100)
