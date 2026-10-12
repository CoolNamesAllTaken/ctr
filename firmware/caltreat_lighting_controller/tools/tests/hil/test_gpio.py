import time

import pytest

from lcctl import regmap as rm
from lcctl.client import ILLEGAL_DATA_ADDRESS, ILLEGAL_DATA_VALUE, ModbusError, PinConfig

OUT = PinConfig(func=rm.FUNC_OUTPUT)


def test_output_follows_coil(board):
    board.configure(2, OUT)
    for level in (True, False, True, False):
        board.write_output(2, level)
        assert board.read_coils(2, 1) == [level]
        assert board.level(2) == level   # pad input reads the driven level


def test_initial_level_from_config(board):
    board.configure(3, PinConfig(func=rm.FUNC_OUTPUT, flags=rm.FLAG_SCHMITT | rm.FLAG_LEVEL_HIGH))
    assert board.level(3) is True
    assert board.pin(3)["config"].flags & rm.FLAG_LEVEL_HIGH


def test_multiple_coils(board):
    for gpio in range(2, 6):
        board.configure(gpio, OUT)
    board.write_outputs(2, [1, 0, 1, 1])
    assert board.read_inputs(2, 4) == [True, False, True, True]
    board.write_outputs(2, [0, 1, 0, 0])
    assert board.read_inputs(2, 4) == [False, True, False, False]


def test_coil_errors(board):
    board.configure(2, PinConfig(func=rm.FUNC_INPUT))
    with pytest.raises(ModbusError) as e:
        board.write_output(2, True)          # not an output
    assert e.value.code == ILLEGAL_DATA_VALUE
    with pytest.raises(ModbusError) as e:
        board.write_output(12, True)         # not on the headers
    assert e.value.code == ILLEGAL_DATA_ADDRESS
    with pytest.raises(ModbusError) as e:
        board.read_coils(25, 10)             # past GPIO 29
    assert e.value.code == ILLEGAL_DATA_ADDRESS


def test_config_readback_and_validation(board):
    cfg = PinConfig(func=rm.FUNC_INPUT, pull=rm.PULL_UP, drive=rm.DRIVE_8MA, flags=rm.FLAG_SCHMITT | rm.FLAG_SLEW_FAST)
    board.configure(4, cfg)
    got = board.pin(4)["config"]
    assert (got.func, got.pull, got.drive, got.flags) == (cfg.func, cfg.pull, cfg.drive, cfg.flags)
    for bad in (PinConfig(func=9), PinConfig(func=rm.FUNC_ADC), PinConfig(flags=0x80)):
        with pytest.raises(ModbusError) as e:
            board.configure(4, bad)          # bad value / ADC on a non-ADC pin / unknown flag
        assert e.value.code == ILLEGAL_DATA_VALUE


def test_input_pulls(board):
    board.configure(5, PinConfig(func=rm.FUNC_INPUT, pull=rm.PULL_UP))
    time.sleep(0.01)
    assert board.level(5) is True
    board.configure(5, PinConfig(func=rm.FUNC_INPUT, pull=rm.PULL_DOWN))
    time.sleep(0.01)
    assert board.level(5) is False


def test_input_inversion(board):
    board.configure(5, PinConfig(func=rm.FUNC_INPUT, pull=rm.PULL_UP, flags=rm.FLAG_SCHMITT | rm.FLAG_INVERT_IN))
    time.sleep(0.01)
    assert board.level(5) is False


def test_pulse(board):
    board.configure(6, OUT)
    board.pulse(6, True, 300)
    assert board.level(6) is True
    time.sleep(0.45)
    assert board.level(6) is False
    p = board.pin(6)
    assert (p["pulse_level"], p["pulse_ms"]) == (1, 300)


def test_pulse_needs_output(board):
    board.configure(6, PinConfig(func=rm.FUNC_INPUT))
    with pytest.raises(ModbusError) as e:
        board.pulse(6, True, 100)
    assert e.value.code == ILLEGAL_DATA_VALUE
