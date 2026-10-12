import socket
import time

import pytest

from lcctl import regmap as rm
from lcctl.client import ILLEGAL_DATA_ADDRESS, Board, ModbusError
from lcctl.discover import candidate_leases


def test_identification(board):
    info = board.info()
    ident = board.device_id()
    assert info["regmap"] == rm.REGMAP_VERSION
    assert ident[0x00] == "CalTreat"
    assert ident[0x01] == "lighting-controller"
    assert ident[0x02] == info["fw_version"]
    assert ident[0x80] == info["uid"] and len(info["uid"]) == 16
    assert ident[0x81] == info["mac"]
    assert info["boot_partition"] in ("A", "B")
    assert info["io_mask"] == sum(1 << g for g in rm.HEADER_PINS)


def test_mac_matches_dhcp_lease(board):
    leases = {l.ip: l for l in candidate_leases()}
    lease = leases.get(board.host)
    if lease is None:
        pytest.skip("board IP not in a lease file")
    assert lease.mac == board.info()["mac"]


def test_uptime_advances(board):
    a = board.info()["uptime_ms"]
    time.sleep(0.3)
    b = board.info()["uptime_ms"]
    assert 250 <= b - a <= 1000


def test_unmapped_addresses(board):
    with pytest.raises(ModbusError) as e:
        board.read_input(50)
    assert e.value.code == ILLEGAL_DATA_ADDRESS
    with pytest.raises(ModbusError) as e:
        board.read_holding(500)
    assert e.value.code == ILLEGAL_DATA_ADDRESS
    with pytest.raises(ModbusError) as e:
        board.read_holding(rm.pin_block(12))   # GP12 is not on the headers
    assert e.value.code == ILLEGAL_DATA_ADDRESS


def test_first_request_right_after_connect(board_ip):
    for _ in range(5):
        with Board(board_ip, retries=0) as b:
            assert b.info()["regmap"] == rm.REGMAP_VERSION


def test_garbage_is_dropped(board, board_ip):
    before = board.info()["bad_frames"]
    board.close()   # the CH9120 serves one client at a time
    with socket.create_connection((board_ip, rm.MODBUS_PORT), timeout=2) as s:
        s.sendall(b"GET / HTTP/1.0\r\n\r\n")
        time.sleep(0.3)
    board.connect()
    assert board.info()["bad_frames"] > before


def test_response_time(board):
    n = 50
    t0 = time.perf_counter()
    for _ in range(n):
        board.read_inputs()
    per_request_ms = (time.perf_counter() - t0) / n * 1000
    print(f"round trip {per_request_ms:.2f} ms")
    assert per_request_ms < 20
