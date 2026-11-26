"""Subtle transitions that respect the 'Reduce motion' accessibility setting."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QParallelAnimationGroup
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


class Motion:
    reduced = False


def ms(duration: int) -> int:
    return 0 if Motion.reduced else duration


def fade_in(widget: QWidget, duration: int = 170, slide: int = 0) -> None:
    """Fade a widget in (and optionally slide it up); the effect is removed afterwards to keep rendering cheap."""
    if Motion.reduced:
        widget.setGraphicsEffect(None)
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    group = QParallelAnimationGroup(widget)
    fade = QPropertyAnimation(effect, b"opacity", group)
    fade.setDuration(duration)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setEasingCurve(QEasingCurve.Type.OutCubic)
    group.addAnimation(fade)
    if slide and widget.parentWidget() is not None and widget.parentWidget().layout() is None:
        end = widget.pos()
        move = QPropertyAnimation(widget, b"pos", group)
        move.setDuration(duration)
        move.setStartValue(end + QPoint(0, slide))
        move.setEndValue(end)
        move.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(move)

    def done() -> None:
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)

    group.finished.connect(done)
    group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)
