"""Minimal, defensive CBOR decoder (RFC 8949) for reading C2PA claims and assertions."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any


class CBORError(ValueError):
    pass


@dataclass(frozen=True)
class Tagged:
    tag: int
    value: Any


class _Undefined:
    def __repr__(self) -> str:
        return "undefined"


UNDEFINED = _Undefined()
_BREAK = object()


def loads(data: bytes, max_depth: int = 64) -> Any:
    decoder = _Decoder(bytes(data), max_depth)
    value = decoder.decode(0)
    if value is _BREAK:
        raise CBORError("Unexpected break code.")
    return value


class _Decoder:
    def __init__(self, data: bytes, max_depth: int) -> None:
        self.data = data
        self.pos = 0
        self.max_depth = max_depth

    def _read(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise CBORError("Truncated CBOR data.")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def _arg(self, info: int):
        if info < 24:
            return info
        if info == 24:
            return self._read(1)[0]
        if info == 25:
            return struct.unpack(">H", self._read(2))[0]
        if info == 26:
            return struct.unpack(">I", self._read(4))[0]
        if info == 27:
            return struct.unpack(">Q", self._read(8))[0]
        if info == 31:
            return None
        raise CBORError(f"Reserved additional information value {info}.")

    def _remaining(self) -> int:
        return len(self.data) - self.pos

    def decode(self, depth: int) -> Any:
        if depth > self.max_depth:
            raise CBORError("CBOR nesting is too deep.")
        initial = self._read(1)[0]
        major, info = initial >> 5, initial & 0x1F
        if major == 0:
            return self._arg(info)
        if major == 1:
            return -1 - self._arg(info)
        if major in (2, 3):
            length = self._arg(info)
            if length is None:
                chunks = []
                while True:
                    item = self.decode(depth + 1)
                    if item is _BREAK:
                        break
                    chunks.append(item)
                raw = b"".join(c if isinstance(c, bytes) else c.encode("utf-8") for c in chunks)
            else:
                raw = self._read(length)
            return raw if major == 2 else raw.decode("utf-8", "replace")
        if major == 4:
            length = self._arg(info)
            items = []
            if length is None:
                while True:
                    item = self.decode(depth + 1)
                    if item is _BREAK:
                        break
                    items.append(item)
            else:
                if length > self._remaining():
                    raise CBORError("Array length exceeds the available data.")
                items = [self.decode(depth + 1) for _ in range(length)]
            return items
        if major == 5:
            length = self._arg(info)
            result: dict[Any, Any] = {}
            count = 0
            while length is None or count < length:
                if length is not None and length > self._remaining():
                    raise CBORError("Map length exceeds the available data.")
                key = self.decode(depth + 1)
                if key is _BREAK and length is None:
                    break
                value = self.decode(depth + 1)
                try:
                    hash(key)
                except TypeError:
                    key = repr(key)
                result[key] = value
                count += 1
            return result
        if major == 6:
            tag = self._arg(info)
            return Tagged(tag, self.decode(depth + 1))
        # major 7: simple values and floats
        if info == 20:
            return False
        if info == 21:
            return True
        if info == 22:
            return None
        if info == 23:
            return UNDEFINED
        if info == 24:
            return f"simple({self._read(1)[0]})"
        if info == 25:
            return struct.unpack(">e", self._read(2))[0]
        if info == 26:
            return struct.unpack(">f", self._read(4))[0]
        if info == 27:
            return struct.unpack(">d", self._read(8))[0]
        if info == 31:
            return _BREAK
        return f"simple({info})"


def to_plain(value: Any, depth: int = 0) -> Any:
    """Convert decoded CBOR into JSON-friendly values for display."""
    if depth > 32:
        return "…"
    if isinstance(value, Tagged):
        return to_plain(value.value, depth + 1)
    if isinstance(value, bytes):
        return f"<{len(value):,} bytes>" if len(value) > 32 else value.hex()
    if isinstance(value, dict):
        return {str(k): to_plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [to_plain(v, depth + 1) for v in value]
    if value is UNDEFINED:
        return None
    return value
