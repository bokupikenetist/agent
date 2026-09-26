"""Тесты реестра и шагов предобработки (FR-7)."""
import numpy as np
import pytest

from ss14_ocr import preprocess


@pytest.fixture()
def dark_bg_light_text():
    """Синтетический «чат SS14»: тёмный фон, светлый текст."""
    img = np.full((60, 300, 3), 40, np.uint8)
    # псевдотекст: случайные белые прямоугольные «глифы»
    rng = np.random.default_rng(7)
    for x in range(5, 290, 12):
        h = int(rng.integers(8, 20))
        img[20:20 + h, x:x + 8] = 230
    return img


@pytest.mark.parametrize("name", ["upscale_2x", "upscale_3x", "grayscale",
                                  "autocontrast", "threshold", "invert"])
def test_registry_has_all_steps(name):
    assert name in preprocess.REGISTRY


def test_upscales(dark_bg_light_text):
    a = preprocess.REGISTRY["upscale_2x"](dark_bg_light_text)
    b = preprocess.REGISTRY["upscale_3x"](dark_bg_light_text)
    assert a.shape[:2] == (120, 600)
    assert b.shape[:2] == (180, 900)


def test_grayscale_shape(dark_bg_light_text):
    g = preprocess.REGISTRY["grayscale"](dark_bg_light_text)
    assert g.ndim == 2 and g.shape == (60, 300)


def test_threshold_binary(dark_bg_light_text):
    t = preprocess.REGISTRY["threshold"](preprocess.REGISTRY["grayscale"](dark_bg_light_text))
    assert set(np.unique(t)) <= {0, 255}


def test_invert_dark_to_light(dark_bg_light_text):
    inv = preprocess.REGISTRY["invert"](dark_bg_light_text)
    gray = preprocess.REGISTRY["grayscale"](inv)
    assert float(gray.mean()) > 127          # стал светлым фоном — как требует Tesseract


def test_invert_noop_on_light():
    light = np.full((40, 40, 3), 240, np.uint8)
    out = preprocess.REGISTRY["invert"](light)
    assert np.array_equal(out, light)         # уже светлый — не инвертируем


def test_apply_chain_full(dark_bg_light_text):
    out = preprocess.apply_chain(dark_bg_light_text,
                                 ["upscale_3x", "grayscale", "autocontrast", "invert"])
    assert out.ndim == 2
    assert out.shape[0] >= 30                 # высота строки ~30px после апскейла (ТЗ §3)


def test_unknown_step_skipped(dark_bg_light_text, caplog):
    out = preprocess.apply_chain(dark_bg_light_text, ["no_such_step", "grayscale"])
    assert out.ndim == 2                      # цепочка продолжилась
