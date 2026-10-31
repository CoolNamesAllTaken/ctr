# Vendored from firmware/caltreat_lighting_controller/tools/src/lcctl/regmap.py (deploy.sh copies only pi/).
# Don't edit: change the original, then run `python pi/tests/test_lcfw.py --sync`.
"""Modbus register map of the lighting controller firmware.

Mirrors src/regmap.h and src/app.h; keep them in step.
Addresses are 0-based. 32-bit values take two registers, high word first.
"""

REGMAP_VERSION = 1
GPIO_COUNT = 30
MODBUS_PORT = 502

# ---- input registers --------------------------------------------------------
IR_MAP_VERSION = 0
IR_FW_VERSION = 1  # u32 0x00MMmmpp
IR_UID = 3  # 8 bytes
IR_BOOT_PARTITION = 7
IR_BOOT_TYPE = 8
IR_TBYB_INFO = 9
IR_FLAGS = 10
IR_FAILSAFE_REASON = 11
IR_UPTIME_MS = 12  # u32
IR_IO_MASK = 14  # u32
IR_MAC = 16  # 6 bytes
IR_RESET_REASON = 19
IR_IP = 20
IR_NETMASK = 22
IR_GATEWAY = 24
IR_BAD_FRAMES = 26
IR_INFO_COUNT = 32

IR_FLAG_TRIAL = 0x0001
IR_FLAG_FAILSAFE = 0x0002
IR_FLAG_OTA_BUSY = 0x0004

IR_ADC_BASE = 100  # raw, mV for GP26, GP27, GP28, temperature
IR_ADC_TEMP_CENTI_C = 108
IR_ADC_COUNT = 9
ADC_INPUTS = (26, 27, 28, "temp")

IR_EDGE_BASE = 200
IR_EDGE_STRIDE = 4

IR_OTA_BASE = 400
IR_OTA_COUNT = 5

# ---- holding registers --------------------------------------------------------
HR_HEARTBEAT_MS = 0
HR_CMD_KEY = 1
HR_CMD = 2
CMD_KEY = 0x5AA5
CMD_REBOOT = 1
CMD_SAVE = 2
CMD_BOOTSEL = 3

HR_PIN_BASE = 1000
HR_PIN_STRIDE = 16
PIN_REG_FUNC = 0
PIN_REG_PULL = 1
PIN_REG_DRIVE = 2
PIN_REG_FLAGS = 3
PIN_REG_DUTY = 4
PIN_REG_FREQ = 5  # u32
PIN_REG_FADE_MS = 7  # u32
PIN_REG_PULSE_LEVEL = 9
PIN_REG_PULSE_MS = 10  # u32
PIN_REG_EDGES = 12
PIN_REG_DEBOUNCE_MS = 13
PIN_REG_COUNT = 14

HR_DEFAULT_BASE = 2000
HR_DEFAULT_STRIDE = 8
DEF_REG_COUNT = 7

HR_OTA_BASE = 3000
HR_OTA_COUNT = 21

# ---- pin configuration values (app.h) --------------------------------------------
FUNC_OFF, FUNC_INPUT, FUNC_OUTPUT, FUNC_PWM, FUNC_ADC = range(5)
FUNC_NAMES = {"off": FUNC_OFF, "input": FUNC_INPUT, "output": FUNC_OUTPUT, "pwm": FUNC_PWM, "adc": FUNC_ADC}
PULL_NONE, PULL_UP, PULL_DOWN, PULL_BUS_KEEP = range(4)
PULL_NAMES = {"none": PULL_NONE, "up": PULL_UP, "down": PULL_DOWN, "keep": PULL_BUS_KEEP}
DRIVE_2MA, DRIVE_4MA, DRIVE_8MA, DRIVE_12MA = range(4)

FLAG_SLEW_FAST = 0x01
FLAG_SCHMITT = 0x02
FLAG_INVERT_IN = 0x04
FLAG_INVERT_OUT = 0x08
FLAG_LEVEL_HIGH = 0x10
FLAG_FS_HOLD = 0x20

EDGE_RISE = 0x01
EDGE_FALL = 0x02

FAILSAFE_REASONS = {0: "heartbeat", 1: "disconnect", 2: "boot"}
RESET_REASONS = {0: "power-on", 1: "watchdog", 2: "requested"}
BOOT_TYPES = {0: "normal", 2: "bootsel", 3: "ram", 4: "flash-update", 0xD: "pc/sp"}

# Header pins on the Waveshare RP2350-ETH
HEADER_PINS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 22, 26, 27, 28)
ADC_PINS = (26, 27, 28)


def pin_block(gpio: int) -> int:
    return HR_PIN_BASE + HR_PIN_STRIDE * gpio


def default_block(gpio: int) -> int:
    return HR_DEFAULT_BASE + HR_DEFAULT_STRIDE * gpio


def edge_block(gpio: int) -> int:
    return IR_EDGE_BASE + IR_EDGE_STRIDE * gpio


def u32(regs, i: int = 0) -> int:
    return (regs[i] << 16) | regs[i + 1]


def split_u32(v: int) -> list[int]:
    return [(v >> 16) & 0xFFFF, v & 0xFFFF]


def reg_bytes(regs) -> bytes:
    return b"".join(r.to_bytes(2, "big") for r in regs)


def version_str(v: int) -> str:
    return f"{(v >> 16) & 0xFF}.{(v >> 8) & 0xFF}.{v & 0xFF}"


def parse_version(s: str) -> int:
    parts = [int(p) for p in s.strip().split(".")]
    if len(parts) != 3 or any(not 0 <= p <= 255 for p in parts):
        raise ValueError(f"version must be X.Y.Z with fields 0-255: {s!r}")
    return (parts[0] << 16) | (parts[1] << 8) | parts[2]
