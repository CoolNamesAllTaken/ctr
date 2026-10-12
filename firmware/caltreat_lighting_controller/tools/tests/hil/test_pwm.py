"""PWM, checked by sampling the pin's own pad level (no wiring)."""

import time

import pytest

from lcctl import regmap as rm
from lcctl.client import ILLEGAL_DATA_VALUE, ModbusError, PinConfig


def high_fraction(board, gpio: int, seconds: float) -> float:
    highs = n = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        highs += board.level(gpio)
        n += 1
    return highs / n


def pwm(duty: int, freq: int) -> PinConfig:
    return PinConfig(func=rm.FUNC_PWM, duty=duty, freq_hz=freq)


def test_duty_cycle(board):
    board.configure(4, pwm(32768, 10))
    assert high_fraction(board, 4, 2.0) == pytest.approx(0.5, abs=0.12)
    board.set_pwm(4, 16384)
    assert high_fraction(board, 4, 2.0) == pytest.approx(0.25, abs=0.12)


def test_extremes(board):
    board.configure(4, pwm(0, 1000))
    assert high_fraction(board, 4, 0.3) == 0
    board.set_pwm(4, 65535)
    assert high_fraction(board, 4, 0.3) == 1


def test_fade(board):
    board.configure(4, pwm(0, 200))
    board.set_pwm(4, 65535, fade_ms=3000)
    early = high_fraction(board, 4, 0.6)
    time.sleep(1.6)
    late = high_fraction(board, 4, 0.6)
    assert early < 0.35 and late > 0.65, (early, late)
    assert board.pin(4)["fade_ms"] == 3000
    time.sleep(1.0)
    assert high_fraction(board, 4, 0.3) == 1


def test_frequency_change_keeps_running(board):
    board.configure(4, pwm(32768, 10))
    board.set_pwm(4, 32768, freq_hz=20)
    assert board.pin(4)["config"].freq_hz == 20
    assert high_fraction(board, 4, 1.0) == pytest.approx(0.5, abs=0.15)


def test_slice_conflict(board):
    # GP4 and GP5 share PWM slice 2, which has one frequency
    board.configure(4, pwm(1000, 1000))
    with pytest.raises(ModbusError) as e:
        board.configure(5, pwm(1000, 2000))
    assert e.value.code == ILLEGAL_DATA_VALUE
    board.configure(5, pwm(1000, 1000))


def test_duty_on_non_pwm_pin_is_remembered(board):
    board.configure(4, PinConfig(func=rm.FUNC_OFF))
    board.write_holding(rm.pin_block(4) + rm.PIN_REG_DUTY, [65535, *rm.split_u32(500)])
    cfg = board.pin(4)["config"]
    assert (cfg.duty, cfg.freq_hz) == (65535, 500)
    cfg.func = rm.FUNC_PWM
    board.configure(4, cfg)
    assert high_fraction(board, 4, 0.2) == 1


def test_new_fade_starts_from_current_level(board):
    # Fading up, then reversing halfway, must not jump to the old target first
    board.configure(4, pwm(0, 200))
    board.set_pwm(4, 65535, fade_ms=2000)
    time.sleep(1.0)
    board.set_pwm(4, 0, fade_ms=4000)
    now = high_fraction(board, 4, 0.3)
    assert 0.25 < now < 0.65, now
