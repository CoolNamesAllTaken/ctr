"""ADC. Without wiring the pin voltage is unknown, so only consistency is checked."""

from lcctl import regmap as rm
from lcctl.client import PinConfig


def test_temperature_plausible(board):
    t = board.adc()["temp_c"]
    assert 5 < t < 70, t


def test_adc_pin(board):
    board.configure(26, PinConfig(func=rm.FUNC_ADC))
    try:
        a = board.adc()
        s = a[26]
        assert s is not None and 0 <= s["raw"] <= 4095
        assert abs(s["mv"] - s["raw"] * 3300 / 4095) <= 1
        assert a[27] is None or board.pin(27)["config"].func == rm.FUNC_ADC
    finally:
        board.configure(26, PinConfig())


def test_non_adc_pin_reads_none(board):
    board.configure(26, PinConfig(func=rm.FUNC_OFF))
    assert board.adc()[26] is None
