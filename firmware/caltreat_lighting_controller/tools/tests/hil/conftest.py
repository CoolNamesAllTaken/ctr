"""Hardware-in-the-loop fixtures: tests run against a real board.

    uv run pytest tests/hil                 # board found through DHCP leases
    uv run pytest tests/hil --ip 192.168.2.3

No wiring is needed. Header pins GP0-GP9 must be left unconnected: the
tests drive them and use the internal pulls to create input edges.
"""

import time

import pytest

from lcctl import regmap as rm
from lcctl.client import Board, PinConfig
from lcctl.discover import discover

TEST_PINS = tuple(range(10))   # GP0-GP9


def pytest_addoption(parser):
    parser.addoption("--ip", default=None, help="board IP (default: discover through DHCP leases)")


@pytest.fixture(scope="session")
def board_ip(request) -> str:
    ip = request.config.getoption("--ip")
    if ip:
        return ip
    found = discover()
    if len(found) != 1:
        pytest.exit(f"expected exactly one board, discovered {len(found)}; pass --ip", returncode=2)
    return found[0]["ip"]


def connect(ip: str, attempts: int = 1, delay: float = 0.5) -> Board:
    for i in range(attempts):
        try:
            # No retries: a dropped request should fail the test, not be hidden
            return Board(ip, retries=0).connect()
        except ConnectionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def wait_for_board(ip: str, timeout: float = 20.0) -> Board:
    """Reconnect after a reboot."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            b = connect(ip)
            b.info()
            return b
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def reset_pins(b: Board) -> None:
    for gpio in TEST_PINS:
        b.set_edges(gpio, 0, 0)
        b.configure(gpio, PinConfig())
        b.set_default(gpio, PinConfig())
        b.write_holding(rm.pin_block(gpio) + rm.PIN_REG_FADE_MS, [0, 0])


@pytest.fixture
def board(board_ip):
    """A connected board with heartbeat off and the test pins switched off."""
    b = connect(board_ip, attempts=10)
    saved_heartbeat = b.heartbeat_ms
    b.heartbeat_ms = 0
    reset_pins(b)
    yield b
    try:
        b.connect()   # a test may have closed or lost the connection
        reset_pins(b)
        b.heartbeat_ms = saved_heartbeat
    finally:
        b.close()
