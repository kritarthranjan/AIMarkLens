"""Run engine functions on the thread pool and marshal results back to the UI thread.

A TaskRunner runs one task at a time, which is what prevents duplicate actions:
while it is busy, the page disables its action buttons and further requests are
ignored. Results from a cancelled or superseded task are discarded.
"""

from __future__ import annotations

import time
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from AIMarkLens.core.context import CancelToken, Cancelled, TaskContext
from AIMarkLens.core.errors import describe_exception


class _Signals(QObject):
    progress = Signal(int, float, str)
    succeeded = Signal(int, object)
    failed = Signal(int, str, str, str)
    cancelled = Signal(int)


class _Worker(QRunnable):
    def __init__(self, task_id: int, fn: Callable[[TaskContext], Any], token: CancelToken, signals: _Signals) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._id = task_id
        self._fn = fn
        self._token = token
        self._signals = signals
        self._last_emit = 0.0
        self._last_message = ""

    def _report(self, fraction: float, message: str) -> None:
        now = time.monotonic()
        if message == self._last_message and now - self._last_emit < 1 / 30:
            return  # throttle to ~30 updates per second
        self._last_emit, self._last_message = now, message
        try:
            self._signals.progress.emit(self._id, fraction, message)
        except RuntimeError:
            self._token.cancel()  # the window has gone; stop the task at its next check

    def run(self) -> None:
        ctx = TaskContext(self._token, self._report)
        try:
            try:
                result = self._fn(ctx)
            except Cancelled:
                self._signals.cancelled.emit(self._id)
            except BaseException as exc:  # noqa: BLE001 - every failure must reach the UI
                message, hint = describe_exception(exc)
                self._signals.failed.emit(self._id, message, hint, traceback.format_exc())
            else:
                if self._token.cancelled:
                    self._signals.cancelled.emit(self._id)
                else:
                    self._signals.succeeded.emit(self._id, result)
        except RuntimeError:
            pass  # the application closed while this task was finishing; nobody is listening any more


class TaskRunner(QObject):
    started = Signal(str)
    progressed = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str, str, str)
    cancelled = Signal()
    busyChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._signals = _Signals()
        self._signals.progress.connect(self._on_progress)
        self._signals.succeeded.connect(self._on_succeeded)
        self._signals.failed.connect(self._on_failed)
        self._signals.cancelled.connect(self._on_cancelled)
        self._current = 0
        self._token: CancelToken | None = None
        self._busy = False
        self._callbacks: dict[str, Callable] = {}

    @property
    def busy(self) -> bool:
        return self._busy

    def run(self, label: str, fn: Callable[[TaskContext], Any], on_success: Callable[[Any], None] | None = None) -> bool:
        if self._busy:
            return False
        self._current += 1
        self._token = CancelToken()
        self._callbacks = {"success": on_success} if on_success else {}
        self._set_busy(True)
        self.started.emit(label)
        QThreadPool.globalInstance().start(_Worker(self._current, fn, self._token, self._signals))
        return True

    def cancel(self) -> None:
        if self._busy and self._token is not None:
            self._token.cancel()

    def _set_busy(self, busy: bool) -> None:
        if self._busy != busy:
            self._busy = busy
            self.busyChanged.emit(busy)

    @Slot(int, float, str)
    def _on_progress(self, task_id: int, fraction: float, message: str) -> None:
        if task_id == self._current and self._busy:
            self.progressed.emit(fraction, message)

    @Slot(int, object)
    def _on_succeeded(self, task_id: int, result: Any) -> None:
        if task_id != self._current:
            return
        self._set_busy(False)
        callback = self._callbacks.get("success")
        if callback is not None:
            try:
                callback(result)
            except Exception as exc:  # noqa: BLE001 - a UI-side failure must still be visible
                message, hint = describe_exception(exc)
                self.failed.emit(message, hint, traceback.format_exc())
                return
        self.succeeded.emit(result)

    @Slot(int, str, str, str)
    def _on_failed(self, task_id: int, message: str, hint: str, details: str) -> None:
        if task_id != self._current:
            return
        self._set_busy(False)
        self.failed.emit(message, hint, details)

    @Slot(int)
    def _on_cancelled(self, task_id: int) -> None:
        if task_id != self._current:
            return
        self._set_busy(False)
        self.cancelled.emit()
