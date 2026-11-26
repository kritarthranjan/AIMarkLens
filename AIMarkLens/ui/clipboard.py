"""Exact clipboard capture.

Qt normalises Windows clipboard text (for example CRLF becomes LF). For
forensic work the sample must be captured exactly, so on Windows the
CF_UNICODETEXT data is read directly through the Win32 API.
"""

from __future__ import annotations

import sys
import time
from typing import Optional

from PySide6.QtGui import QGuiApplication


def _read_windows() -> Optional[str]:
    import ctypes
    from ctypes import wintypes

    cf_unicodetext = 13
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t

    for _ in range(5):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)  # another application may briefly hold the clipboard
    else:
        return None
    try:
        handle = user32.GetClipboardData(cf_unicodetext)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            raw = ctypes.string_at(pointer, kernel32.GlobalSize(handle))
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
    text = raw.decode("utf-16-le", "surrogatepass")
    end = text.find("\x00")
    return text[:end] if end >= 0 else text


def read_clipboard_exact() -> tuple[str, bool]:
    """Return (text, exact). exact is False when only Qt's normalised text was available."""
    if sys.platform == "win32":
        try:
            text = _read_windows()
            if text is not None:
                return text, True
        except OSError:
            pass
    return QGuiApplication.clipboard().text(), False
