"""Sealed firmware images (.pio/build/app/firmware.bin)."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

from . import regmap as rm

FW_INFO_MAGIC = struct.pack("<II", 0x45325052, 0x4F464E49)   # "RP2E", "INFO" (fw_info in src/main.c)
APP_PARTITION_SIZE = 1920 * 1024


@dataclass
class Image:
    data: bytes
    version: int   # 0x00MMmmpp

    @property
    def version_str(self) -> str:
        return rm.version_str(self.version)

    @property
    def major(self) -> int:     # picobin version, as sealed by scripts/build_image.py
        return (self.version >> 16) & 0xFF

    @property
    def minor(self) -> int:
        return self.version & 0xFFFF

    @property
    def sha256(self) -> bytes:
        return hashlib.sha256(self.data).digest()


def load(path: str | Path) -> Image:
    data = Path(path).read_bytes()
    if not 0 < len(data) <= APP_PARTITION_SIZE:
        raise ValueError(f"{path}: {len(data)} bytes does not fit an app partition")
    i = data.find(FW_INFO_MAGIC)
    if i < 0:
        raise ValueError(f"{path}: no firmware info block; not a lighting controller image?")
    (version,) = struct.unpack_from("<I", data, i + 8)
    return Image(data, version)
