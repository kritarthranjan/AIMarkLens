"""Cancellation and progress plumbing shared by engines and the UI.

Engines never import Qt. They receive a TaskContext, call ``progress`` to
report where they are and ``check`` to honour cancellation.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


class Cancelled(Exception):
    """Raised inside a task when the user has cancelled it."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled()


ProgressCallback = Callable[[float, str], None]


class TaskContext:
    """Reports progress within a sub-range of the overall task and checks for cancellation."""

    def __init__(self, token: Optional[CancelToken] = None,
                 on_progress: Optional[ProgressCallback] = None,
                 start: float = 0.0, end: float = 1.0) -> None:
        self.token = token or CancelToken()
        self._on_progress = on_progress
        self._start = start
        self._end = end

    @property
    def cancelled(self) -> bool:
        return self.token.cancelled

    def check(self) -> None:
        self.token.raise_if_cancelled()

    def progress(self, fraction: float, message: str = "") -> None:
        self.check()
        if self._on_progress is not None:
            fraction = max(0.0, min(1.0, fraction))
            self._on_progress(self._start + (self._end - self._start) * fraction, message)

    def sub(self, start: float, end: float) -> "TaskContext":
        """A child context that maps its own 0..1 range onto [start, end] of this one."""
        span = self._end - self._start
        return TaskContext(self.token, self._on_progress,
                           self._start + span * start, self._start + span * end)
