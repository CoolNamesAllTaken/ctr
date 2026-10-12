"""Edge counting, driven by switching an input's internal pull (no wiring)."""

import time

from lcctl import regmap as rm
from lcctl.client import PinConfig

GPIO = 6


def set_pull(board, pull: int) -> None:
    board.write_holding(rm.pin_block(GPIO) + rm.PIN_REG_PULL, [pull])


def setup_input(board, edges: int, debounce_ms: int = 0) -> None:
    board.configure(GPIO, PinConfig(func=rm.FUNC_INPUT, pull=rm.PULL_DOWN))
    time.sleep(0.02)
    board.set_edges(GPIO, edges, debounce_ms)


def test_counts_both_edges(board):
    setup_input(board, rm.EDGE_RISE | rm.EDGE_FALL)
    c0, _ = board.edge_count(GPIO)
    set_pull(board, rm.PULL_UP)
    time.sleep(0.05)
    c1, t1 = board.edge_count(GPIO)
    set_pull(board, rm.PULL_DOWN)
    time.sleep(0.05)
    c2, t2 = board.edge_count(GPIO)
    assert (c1 - c0, c2 - c1) == (1, 1)
    assert 0 < t2 - t1 < 1000
    assert abs(board.info()["uptime_ms"] - t2) < 2000
    p = board.pin(GPIO)
    assert (p["edges"], p["debounce_ms"]) == (rm.EDGE_RISE | rm.EDGE_FALL, 0)


def test_rising_only(board):
    setup_input(board, rm.EDGE_RISE)
    c0, _ = board.edge_count(GPIO)
    for _ in range(3):
        set_pull(board, rm.PULL_UP)
        time.sleep(0.03)
        set_pull(board, rm.PULL_DOWN)
        time.sleep(0.03)
    c1, _ = board.edge_count(GPIO)
    assert c1 - c0 == 3


def test_debounce(board):
    setup_input(board, rm.EDGE_RISE | rm.EDGE_FALL, debounce_ms=300)
    c0, _ = board.edge_count(GPIO)
    # Glitches shorter than the debounce time are not counted
    for _ in range(3):
        set_pull(board, rm.PULL_UP)
        set_pull(board, rm.PULL_DOWN)
        time.sleep(0.05)
    time.sleep(0.4)
    assert board.edge_count(GPIO)[0] == c0
    # A change that lasts is counted once
    set_pull(board, rm.PULL_UP)
    time.sleep(0.5)
    assert board.edge_count(GPIO)[0] == c0 + 1


def test_disabled_counts_nothing(board):
    setup_input(board, 0)
    c0, _ = board.edge_count(GPIO)
    set_pull(board, rm.PULL_UP)
    time.sleep(0.05)
    set_pull(board, rm.PULL_DOWN)
    time.sleep(0.05)
    assert board.edge_count(GPIO)[0] == c0
