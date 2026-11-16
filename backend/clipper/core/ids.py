"""Time-ordered unique IDs (UUIDv7 layout) — sortable, safe to use as path components."""

from __future__ import annotations

import os
import re
import time
import uuid

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def new_id() -> str:
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80 | (0x7 << 76) | ((rand >> 64) & 0xFFF) << 64 | (0b10 << 62)
    value |= rand & ((1 << 62) - 1)
    return uuid.UUID(int=value).hex


def is_valid_id(value: str) -> bool:
    return bool(_ID_RE.fullmatch(value))
