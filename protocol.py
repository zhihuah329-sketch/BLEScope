"""Bounded stream framing shared by BLE, replay and tests."""
from __future__ import annotations

import math
import re

KEY = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.]{0,31}$")


class FrameParser:
    def __init__(self, max_size=2048):
        self.buffer = bytearray()
        self.max_size = max_size
        self.discarded = 0

    def feed(self, data: bytes) -> list[str]:
        frames = []
        for byte in data:
            if byte == 91:  # a fresh '[' resynchronizes a damaged frame
                if self.buffer:
                    self.discarded += 1
                self.buffer = bytearray(b"[")
            elif self.buffer:
                self.buffer.append(byte)
                if len(self.buffer) > self.max_size:
                    self.buffer.clear()
                    self.discarded += 1
                elif byte == 93:
                    try:
                        frames.append(self.buffer.decode("utf-8", errors="strict"))
                    except UnicodeDecodeError:
                        self.discarded += 1
                    self.buffer.clear()
        return frames


def number(value):
    result = float(value)
    if not math.isfinite(result) or abs(result) > 1e15:
        raise ValueError("non-finite or excessive numeric value")
    return result


def decode(frame: str, plot_names=None) -> dict:
    if not (frame.startswith("[") and frame.endswith("]")):
        raise ValueError("invalid envelope")
    parts = [part.strip() for part in frame[1:-1].split(",")]
    kind = parts[0]
    if kind == "plot":
        if not 2 <= len(parts) <= 33:
            raise ValueError("plot supports 1..32 channels")
        names = plot_names or ["roll_acc", "roll_est"]
        fields = {}
        for i, value in enumerate(parts[1:]):
            key = names[i] if i < len(names) else f"ch{i + 1}"
            if not KEY.fullmatch(key) or key in fields:
                raise ValueError("invalid or duplicate channel name")
            fields[key] = number(value)
        return {"type": "sample", "device_ms": None, "values": fields}
    if kind == "data":
        if not 3 <= len(parts) <= 34:
            raise ValueError("invalid named telemetry")
        fields = {}
        for field in parts[2:]:
            key, value = field.split("=", 1)
            if not KEY.fullmatch(key) or key in fields:
                raise ValueError("invalid or duplicate channel name")
            fields[key] = number(value)
        return {"type": "sample", "device_ms": number(parts[1]), "values": fields}
    if kind in ("ack", "param") and len(parts) == 4:
        if not parts[1].isdigit() or not KEY.fullmatch(parts[2]):
            raise ValueError("invalid parameter response")
        return {"type": kind, "seq": int(parts[1]), "key": parts[2], "value": number(parts[3])}
    if kind == "nack" and len(parts) >= 3 and parts[1].isdigit():
        return {"type": kind, "seq": int(parts[1]), "reason": ",".join(parts[2:])}
    return {"type": "message", "text": frame}


def set_command(seq: int, key: str, value: float) -> bytes:
    if not KEY.fullmatch(key):
        raise ValueError("parameter name must be 1..32 ASCII letters/digits/._")
    return f"[set,{seq},{key},{number(value):.8g}]".encode("ascii")
