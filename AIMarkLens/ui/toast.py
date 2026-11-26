"""Toast notifications: success confirmations fade out on their own; errors stay until dismissed."""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QParallelAnimationGroup, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QVBoxLayout, QWidget

from AIMarkLens.ui.motion import Motion
from AIMarkLens.ui.theme import C, TONE
from AIMarkLens.ui.widgets import StatusIcon, button, label

_WIDTH = 400
_MARGIN = 18


class Toast(QFrame):
    def __init__(self, host: "ToastHost", parent: QWidget, tone: str, title: str, message: str,
                 actions: Iterable[tuple[str, Callable]], timeout: Optional[int]) -> None:
        super().__init__(parent)
        self.host = host
        self.setObjectName("Toast")
        colour = TONE.get(tone, C["neutral"])
        self.setStyleSheet(f"QFrame#Toast {{ background: {C['raised']}; border: 1px solid {C['border_strong']}; "
                           f"border-left: 4px solid {colour}; border-radius: 10px; }}")
        self.setFixedWidth(_WIDTH)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 10, 12)
        layout.setSpacing(12)
        self.icon = StatusIcon(tone, 26)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(label(title, "h3", wrap=True))
        if message:
            texts.addWidget(label(message, "muted", wrap=True, selectable=True))
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        for text, callback in actions:
            btn = button(text, "link")
            btn.clicked.connect(lambda _=False, cb=callback: (cb(), self.dismiss()))
            action_row.addWidget(btn)
        action_row.addStretch(1)
        if action_row.count() > 1:
            texts.addLayout(action_row)
        layout.addLayout(texts, 1)
        close = button("", "ghost", "close", "Dismiss notification", on_click=self.dismiss)
        close.setFixedSize(30, 30)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.setAccessibleName(f"{title}. {message}")
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._closing = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        if timeout:
            self._timer.start(timeout)
        self.adjustSize()

    def enterEvent(self, event) -> None:
        self._timer.stop()  # keep it visible while the pointer is over it
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._closing and self._timer.interval() and not self._timer.isActive():
            self._timer.start(2500)
        super().leaveEvent(event)

    def appear(self, end: QPoint) -> None:
        self.show()
        self.raise_()
        self.icon.set_tone(self.icon._tone)
        if Motion.reduced:
            self.move(end)
            self._effect.setOpacity(1.0)
            return
        group = QParallelAnimationGroup(self)
        fade = QPropertyAnimation(self._effect, b"opacity", group)
        fade.setDuration(220)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        move = QPropertyAnimation(self, b"pos", group)
        move.setDuration(260)
        move.setStartValue(end + QPoint(0, 18))
        move.setEndValue(end)
        move.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(fade)
        group.addAnimation(move)
        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        if Motion.reduced:
            self._finish()
            return
        fade = QPropertyAnimation(self._effect, b"opacity", self)
        fade.setDuration(170)
        fade.setStartValue(self._effect.opacity())
        fade.setEndValue(0.0)
        fade.finished.connect(self._finish)
        fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _finish(self) -> None:
        self.hide()
        self.host._remove(self)
        self.deleteLater()


class ToastHost(QObject):
    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.window = window
        self.toasts: list[Toast] = []
        window.installEventFilter(self)

    def show(self, tone: str, title: str, message: str = "", actions: Iterable[tuple[str, Callable]] = (),
             timeout: Optional[int] = -1) -> Toast:
        if timeout == -1:
            timeout = None if tone in ("error", "warning") else 4500
        while len(self.toasts) >= 3:
            self.toasts[0].dismiss()
            self.toasts.pop(0)
        toast = Toast(self, self.window, tone, title, message, list(actions), timeout)
        self.toasts.append(toast)
        positions = self._positions()
        toast.appear(positions[toast])
        for other in self.toasts[:-1]:
            other.move(positions[other])
        self._announce(f"{title}. {message}")
        return toast

    def _announce(self, text: str) -> None:
        try:
            from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent

            QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(self.window, text))
        except (ImportError, AttributeError, TypeError):
            pass

    def _positions(self) -> dict[Toast, QPoint]:
        out = {}
        bottom = self.window.height() - _MARGIN - 28  # keep clear of the status bar
        for toast in reversed(self.toasts):
            toast.adjustSize()
            bottom -= toast.height()
            out[toast] = QPoint(self.window.width() - _WIDTH - _MARGIN, bottom)
            bottom -= 10
        return out

    def _remove(self, toast: Toast) -> None:
        if toast in self.toasts:
            self.toasts.remove(toast)
        for other, pos in self._positions().items():
            other.move(pos)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.window and event.type() == QEvent.Type.Resize and self.toasts:
            for toast, pos in self._positions().items():
                toast.move(pos)
        return False
