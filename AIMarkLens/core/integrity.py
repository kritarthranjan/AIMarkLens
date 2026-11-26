"""File integrity: read-only access, SHA-256 fingerprints and safe output writing.

Originals are opened with mode "rb" only. Every analysis fingerprints the file
before reading and again afterwards, so the report can prove it was untouched.
Exports always go to a different path, written atomically.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.errors import LabError

MAX_FILE_BYTES = 512 * 1024 * 1024
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Fingerprint:
    path: str
    size: int
    modified_ns: int
    sha256: str

    @property
    def modified_text(self) -> str:
        if not self.modified_ns:
            return "n/a"
        return datetime.fromtimestamp(self.modified_ns / 1e9).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class IntegrityResult:
    before: Fingerprint
    after: Fingerprint

    @property
    def unchanged(self) -> bool:
        return (self.before.sha256 == self.after.sha256
                and self.before.size == self.after.size
                and self.before.modified_ns == self.after.modified_ns)

    def rows(self) -> list[tuple[str, str]]:
        return [
            ("Original unchanged", "Yes, verified" if self.unchanged else "NO: the file changed during analysis"),
            ("SHA-256 before", self.before.sha256),
            ("SHA-256 after", self.after.sha256),
            ("Size", f"{self.before.size:,} bytes"),
            ("Last modified", self.before.modified_text),
        ]


def _stat(path: str) -> os.stat_result:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise LabError("The file could not be found.", "It may have been moved, renamed or deleted.")
    except PermissionError:
        raise LabError("Access to the file was denied.", "Check that you have permission to read it.")
    except OSError as exc:
        raise LabError(f"The file could not be accessed: {exc.strerror or exc}")
    if not os.path.isfile(path):
        raise LabError("This is not a regular file.", "Choose an image or text file.")
    return st


def read_original(path: str, ctx: Optional[TaskContext] = None,
                  max_bytes: int = MAX_FILE_BYTES) -> tuple[bytes, Fingerprint]:
    """Read a file once, read-only, returning its bytes and fingerprint."""
    st = _stat(path)
    if st.st_size > max_bytes:
        raise LabError(f"The file is too large ({st.st_size / 1e6:,.0f} MB).",
                       f"The limit is {max_bytes / 1e6:,.0f} MB.")
    digest = hashlib.sha256()
    parts: list[bytes] = []
    with open(path, "rb") as fh:
        while True:
            if ctx is not None:
                ctx.check()
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            parts.append(chunk)
    data = b"".join(parts)
    st_after = _stat(path)
    if st_after.st_size != len(data) or st_after.st_mtime_ns != st.st_mtime_ns:
        raise LabError("The file changed while it was being read.",
                       "Wait for other programs to finish writing it, then retry.")
    return data, Fingerprint(os.path.abspath(path), len(data), st.st_mtime_ns, digest.hexdigest())


def fingerprint_file(path: str, ctx: Optional[TaskContext] = None) -> Fingerprint:
    st = _stat(path)
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            if ctx is not None:
                ctx.check()
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return Fingerprint(os.path.abspath(path), st.st_size, st.st_mtime_ns, digest.hexdigest())


def verify_unchanged(before: Fingerprint, ctx: Optional[TaskContext] = None) -> IntegrityResult:
    return IntegrityResult(before, fingerprint_file(before.path, ctx))


def text_bytes(text: str) -> bytes:
    return text.encode("utf-8", "surrogatepass")


def fingerprint_text(text: str, label: str) -> Fingerprint:
    data = text_bytes(text)
    return Fingerprint(label, len(data), 0, hashlib.sha256(data).hexdigest())


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def guard_output_path(out_path: str, protected: Iterable[str]) -> None:
    """Refuse to write over any protected (original) file."""
    target = _norm(out_path)
    for original in protected:
        if not original:
            continue
        if target == _norm(original):
            raise LabError("The output would overwrite the original file.",
                           "Choose a different file name or folder. Originals are never modified.")
        try:
            if os.path.exists(out_path) and os.path.samefile(out_path, original):
                raise LabError("The output path points at the original file.",
                               "Choose a different file name or folder.")
        except OSError:
            pass


def atomic_write_bytes(path: str, data: bytes, protected: Iterable[str] = ()) -> None:
    """Write via a temporary file in the same folder, then replace atomically."""
    guard_output_path(path, protected)
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".AIMarkLens-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: str, text: str, protected: Iterable[str] = ()) -> None:
    atomic_write_bytes(path, text_bytes(text), protected)
