"""Synchronous client for one lighting controller board (Modbus TCP)."""

from __future__ import annotations

from dataclasses import dataclass

from pymodbus.client import ModbusTcpClient

from . import regmap as rm


class ModbusError(Exception):
    """The board answered with a Modbus exception (or not at all)."""

    def __init__(self, what: str, code: int | None = None):
        super().__init__(f"{what}: " + (f"exception {code}" if code is not None else "no valid response"))
        self.code = code


ILLEGAL_DATA_ADDRESS = 2
ILLEGAL_DATA_VALUE = 3
SERVER_DEVICE_FAILURE = 4


@dataclass
class PinConfig:
    func: int = rm.FUNC_OFF
    pull: int = rm.PULL_NONE
    drive: int = rm.DRIVE_4MA
    flags: int = rm.FLAG_SCHMITT
    duty: int = 0
    freq_hz: int = 200

    def regs(self) -> list[int]:
        return [self.func, self.pull, self.drive, self.flags, self.duty, *rm.split_u32(self.freq_hz)]

    @classmethod
    def from_regs(cls, r) -> "PinConfig":
        return cls(r[0], r[1], r[2], r[3], r[4], rm.u32(r, 5))


class Board:
    def __init__(self, host: str, port: int = rm.MODBUS_PORT, timeout: float = 2.0, retries: int = 1):
        self.host = host
        self.client = ModbusTcpClient(host, port=port, timeout=timeout, retries=retries)

    # ---- connection -------------------------------------------------------
    def connect(self) -> "Board":
        if not self.client.connect():
            raise ConnectionError(f"cannot connect to {self.host}:{self.client.comm_params.port}")
        return self

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "Board":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- raw access ---------------------------------------------------------
    @staticmethod
    def _check(rr, what: str):
        if rr is None or rr.isError():
            raise ModbusError(what, getattr(rr, "exception_code", None))
        return rr

    def read_input(self, address: int, count: int = 1) -> list[int]:
        return self._check(self.client.read_input_registers(address, count=count), f"read IR {address}").registers

    def read_holding(self, address: int, count: int = 1) -> list[int]:
        return self._check(self.client.read_holding_registers(address, count=count), f"read HR {address}").registers

    def write_holding(self, address: int, values) -> None:
        values = list(values)
        if len(values) == 1:
            self._check(self.client.write_register(address, values[0]), f"write HR {address}")
        else:
            self._check(self.client.write_registers(address, values), f"write HR {address}")

    def read_coils(self, address: int = 0, count: int = rm.GPIO_COUNT) -> list[bool]:
        return self._check(self.client.read_coils(address, count=count), "read coils").bits[:count]

    def read_inputs(self, address: int = 0, count: int = rm.GPIO_COUNT) -> list[bool]:
        return self._check(self.client.read_discrete_inputs(address, count=count), "read inputs").bits[:count]

    # ---- identification and status ----------------------------------------------
    def device_id(self) -> dict[int, str]:
        out: dict[int, str] = {}
        for code, first in ((1, 0x00), (2, 0x03), (3, 0x80)):
            rr = self._check(self.client.read_device_information(read_code=code, object_id=first), "device id")
            out.update({k: v.decode(errors="replace") for k, v in rr.information.items()})
        return out

    def info(self) -> dict:
        r = self.read_input(rm.IR_MAP_VERSION, rm.IR_INFO_COUNT)
        flags = r[rm.IR_FLAGS]
        return {
            "regmap": r[rm.IR_MAP_VERSION],
            "fw_version": rm.version_str(rm.u32(r, rm.IR_FW_VERSION)),
            "uid": rm.reg_bytes(r[rm.IR_UID:rm.IR_UID + 4]).hex().upper(),
            "boot_partition": {0: "A", 1: "B"}.get(r[rm.IR_BOOT_PARTITION], "?"),
            "boot_type": rm.BOOT_TYPES.get(r[rm.IR_BOOT_TYPE], str(r[rm.IR_BOOT_TYPE])),
            "tbyb_info": r[rm.IR_TBYB_INFO],
            "trial": bool(flags & rm.IR_FLAG_TRIAL),
            "failsafe": bool(flags & rm.IR_FLAG_FAILSAFE),
            "failsafe_reason": rm.FAILSAFE_REASONS.get(r[rm.IR_FAILSAFE_REASON], "?"),
            "ota_busy": bool(flags & rm.IR_FLAG_OTA_BUSY),
            "uptime_ms": rm.u32(r, rm.IR_UPTIME_MS),
            "io_mask": rm.u32(r, rm.IR_IO_MASK),
            "mac": ":".join(f"{b:02x}" for b in rm.reg_bytes(r[rm.IR_MAC:rm.IR_MAC + 3])),
            "reset_reason": rm.RESET_REASONS.get(r[rm.IR_RESET_REASON], "?"),
            "ip": ".".join(str(b) for b in rm.reg_bytes(r[rm.IR_IP:rm.IR_IP + 2])),
            "bad_frames": r[rm.IR_BAD_FRAMES],
        }

    def flags(self) -> int:
        return self.read_input(rm.IR_FLAGS)[0]

    # ---- system --------------------------------------------------------------------
    @property
    def heartbeat_ms(self) -> int:
        return self.read_holding(rm.HR_HEARTBEAT_MS)[0]

    @heartbeat_ms.setter
    def heartbeat_ms(self, ms: int) -> None:
        self.write_holding(rm.HR_HEARTBEAT_MS, [ms])

    def command(self, cmd: int) -> None:
        self.write_holding(rm.HR_CMD_KEY, [rm.CMD_KEY, cmd])

    def save(self) -> None:
        self.command(rm.CMD_SAVE)

    def reboot(self) -> None:
        """Reboot the board; the connection drops."""
        self.command(rm.CMD_REBOOT)
        self.close()

    def reboot_bootsel(self) -> None:
        self.command(rm.CMD_BOOTSEL)
        self.close()

    # ---- pins ------------------------------------------------------------------------
    def configure(self, gpio: int, cfg: PinConfig) -> None:
        self.write_holding(rm.pin_block(gpio), cfg.regs())

    def pin(self, gpio: int) -> dict:
        r = self.read_holding(rm.pin_block(gpio), rm.PIN_REG_COUNT)
        cfg = PinConfig.from_regs(r)
        return {
            "config": cfg,
            "fade_ms": rm.u32(r, rm.PIN_REG_FADE_MS),
            "pulse_level": r[rm.PIN_REG_PULSE_LEVEL],
            "pulse_ms": rm.u32(r, rm.PIN_REG_PULSE_MS),
            "edges": r[rm.PIN_REG_EDGES],
            "debounce_ms": r[rm.PIN_REG_DEBOUNCE_MS],
        }

    def write_output(self, gpio: int, level: bool) -> None:
        self._check(self.client.write_coil(gpio, bool(level)), f"write coil {gpio}")

    def write_outputs(self, first: int, levels) -> None:
        self._check(self.client.write_coils(first, [bool(v) for v in levels]), f"write coils {first}")

    def level(self, gpio: int) -> bool:
        return self.read_inputs(gpio, 1)[0]

    def set_pwm(self, gpio: int, duty: int, fade_ms: int | None = None, freq_hz: int | None = None) -> None:
        """Set a PWM pin's duty (0-65535), optionally changing fade time / frequency first."""
        if fade_ms is not None:
            self.write_holding(rm.pin_block(gpio) + rm.PIN_REG_FADE_MS, rm.split_u32(fade_ms))
        if freq_hz is not None:
            self.write_holding(rm.pin_block(gpio) + rm.PIN_REG_DUTY, [duty, *rm.split_u32(freq_hz)])
        else:
            self.write_holding(rm.pin_block(gpio) + rm.PIN_REG_DUTY, [duty])

    def pulse(self, gpio: int, level: bool, ms: int) -> None:
        self.write_holding(rm.pin_block(gpio) + rm.PIN_REG_PULSE_LEVEL, [int(bool(level)), *rm.split_u32(ms)])

    def set_edges(self, gpio: int, edges: int, debounce_ms: int = 0) -> None:
        self.write_holding(rm.pin_block(gpio) + rm.PIN_REG_EDGES, [edges, debounce_ms])

    def edge_count(self, gpio: int) -> tuple[int, int]:
        r = self.read_input(rm.edge_block(gpio), rm.IR_EDGE_STRIDE)
        return rm.u32(r, 0), rm.u32(r, 2)

    def adc(self) -> dict:
        r = self.read_input(rm.IR_ADC_BASE, rm.IR_ADC_COUNT)
        out = {}
        for i, name in enumerate(rm.ADC_INPUTS):
            raw, mv = r[2 * i], r[2 * i + 1]
            out[name] = None if raw == 0xFFFF else {"raw": raw, "mv": mv}
        t = r[rm.IR_ADC_TEMP_CENTI_C - rm.IR_ADC_BASE]
        out["temp_c"] = (t - 0x10000 if t & 0x8000 else t) / 100
        return out

    # ---- failsafe defaults -----------------------------------------------------------------
    def set_default(self, gpio: int, cfg: PinConfig) -> None:
        self.write_holding(rm.default_block(gpio), cfg.regs())

    def default(self, gpio: int) -> PinConfig:
        return PinConfig.from_regs(self.read_holding(rm.default_block(gpio), rm.DEF_REG_COUNT))
