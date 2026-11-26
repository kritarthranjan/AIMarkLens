"""Protect stored API keys with Windows DPAPI (encrypted for the current Windows user only).

On other platforms values are stored with a plain marker so they still work,
but Windows is the supported platform.
"""

from __future__ import annotations

import base64
import sys

_PREFIX = "dpapi:"
_PLAIN = "plain:"


def _blob(data: bytes):
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    return DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer, DataBlob


def _crypt(data: bytes, protect: bool) -> bytes:
    import ctypes

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_in, _keep, DataBlob = _blob(data)
    blob_out = DataBlob()
    func = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = func(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))  # 0x1: no UI
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI operation failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def protect(value: str) -> str:
    if not value:
        return ""
    if sys.platform == "win32":
        try:
            return _PREFIX + base64.b64encode(_crypt(value.encode("utf-8"), True)).decode("ascii")
        except OSError:
            pass
    return _PLAIN + value


def unprotect(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith(_PREFIX):
        try:
            return _crypt(base64.b64decode(stored[len(_PREFIX):]), False).decode("utf-8")
        except (OSError, ValueError):
            return ""  # written by another Windows user or machine; the user must re-enter it
    if stored.startswith(_PLAIN):
        return stored[len(_PLAIN):]
    return stored
