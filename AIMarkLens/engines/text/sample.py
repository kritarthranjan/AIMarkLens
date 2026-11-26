"""Text samples: exact capture from files or the clipboard, never normalised on the way in."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.errors import LabError
from AIMarkLens.core.integrity import Fingerprint, fingerprint_text, read_original

MAX_TEXT_BYTES = 50 * 1024 * 1024

ORIGIN_LABELS = {
    "file": "Text file (read-only)",
    "clipboard": "Exact clipboard capture",
    "typed": "Typed or edited in AIMarkLens",
}


@dataclass
class TextSample:
    text: str
    origin: str
    label: str
    path: str = ""
    encoding: str = "UTF-8"
    bom: bool = False
    fingerprint: Optional[Fingerprint] = None
    warning: str = ""

    def ensure_fingerprint(self) -> Fingerprint:
        if self.fingerprint is None:
            self.fingerprint = fingerprint_text(self.text, self.label)
        return self.fingerprint


_BOMS = ((b"\xff\xfe\x00\x00", "utf-32-le", "UTF-32 LE"), (b"\x00\x00\xfe\xff", "utf-32-be", "UTF-32 BE"),
         (b"\xef\xbb\xbf", "utf-8", "UTF-8"), (b"\xff\xfe", "utf-16-le", "UTF-16 LE"),
         (b"\xfe\xff", "utf-16-be", "UTF-16 BE"))


def decode_bytes(data: bytes) -> tuple[str, str, bool, str]:
    """Return (text, encoding label, had BOM, warning). Line endings are kept exactly."""
    for bom, codec, label in _BOMS:
        if data.startswith(bom):
            return data[len(bom):].decode(codec, "replace"), label, True, ""
    if data and data.count(b"\x00") > len(data) // 4:
        even_zero = data[1::2].count(0) > len(data) // 4
        codec, label = ("utf-16-le", "UTF-16 LE (no BOM)") if even_zero else ("utf-16-be", "UTF-16 BE (no BOM)")
        try:
            return data.decode(codec), label, False, "No byte order mark; the UTF-16 byte order was inferred."
        except UnicodeDecodeError:
            raise LabError("This looks like a binary file, not text.", "Choose a plain-text file.")
    try:
        return data.decode("utf-8"), "UTF-8", False, ""
    except UnicodeDecodeError:
        return (data.decode("cp1252", "replace"), "Windows-1252 (fallback)", False,
                "The file is not valid UTF-8, so it was read as Windows-1252. Some characters may be misread.")


def load_text_file(path: str, ctx: TaskContext) -> TextSample:
    data, fingerprint = read_original(path, ctx, max_bytes=MAX_TEXT_BYTES)
    text, encoding, bom, warning = decode_bytes(data)
    return TextSample(text, "file", os.path.basename(path), path, encoding, bom, fingerprint, warning)
