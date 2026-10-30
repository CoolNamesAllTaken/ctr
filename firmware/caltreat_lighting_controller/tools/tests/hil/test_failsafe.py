import time

from conftest import reset_pins, wait_for_board

from lcctl import regmap as rm
from lcctl.client import PinConfig

LOW_OUT = PinConfig(func=rm.FUNC_OUTPUT)
HIGH_OUT = PinConfig(func=rm.FUNC_OUTPUT, flags=rm.FLAG_SCHMITT | rm.FLAG_LEVEL_HIGH)


def test_heartbeat_timeout(board):
    board.set_default(7, LOW_OUT)
    board.set_default(8, PinConfig(func=rm.FUNC_OUTPUT, flags=rm.FLAG_SCHMITT | rm.FLAG_FS_HOLD))
    board.configure(7, LOW_OUT)
    board.configure(8, LOW_OUT)
    board.write_outputs(7, [1, 1])                  # a write takes the pins out of failsafe
    info = board.info()
    assert not info["failsafe"]

    board.heartbeat_ms = 1000
    time.sleep(1.6)                                  # host goes quiet
    info = board.info()
    assert info["failsafe"] and info["failsafe_reason"] == "heartbeat"
    assert board.read_inputs(7, 2) == [False, True]  # GP7 back to default, GP8 held

    board.write_output(7, True)                      # a write leaves failsafe again
    assert not board.info()["failsafe"]
    assert board.level(7) is True


def test_reads_keep_heartbeat_alive(board):
    board.configure(7, LOW_OUT)
    board.heartbeat_ms = 500
    end = time.monotonic() + 1.5
    while time.monotonic() < end:
        assert not board.info()["failsafe"]
        time.sleep(0.1)


def test_disconnect(board):
    board.set_default(7, LOW_OUT)
    board.configure(7, LOW_OUT)
    board.write_output(7, True)
    board.close()
    time.sleep(0.3)
    board.connect()
    info = board.info()
    assert info["failsafe"] and info["failsafe_reason"] == "disconnect"
    assert board.level(7) is False


def test_saved_defaults_apply_at_power_on(board, board_ip):
    board.set_default(9, HIGH_OUT)
    board.heartbeat_ms = 2500
    board.save()
    board.reboot()
    b = wait_for_board(board_ip)
    try:
        info = b.info()
        assert info["reset_reason"] == "requested"
        assert info["failsafe"] and info["failsafe_reason"] == "boot"
        assert b.level(9) is True
        assert b.default(9) == HIGH_OUT
        assert b.heartbeat_ms == 2500
    finally:
        # Leave the board with clean saved defaults
        b.heartbeat_ms = 0
        reset_pins(b)
        b.heartbeat_ms = 3000
        b.save()
        b.close()
    board.connect()
