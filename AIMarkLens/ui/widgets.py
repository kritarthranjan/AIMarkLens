"""Reusable widgets: cards, chips, spinner, animated status icons, overlays, banners and drop zones."""

from __future__ import annotations

import os
from typing import Callable, Iterable, Optional

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
                            QTimer, QVariantAnimation, Property, Signal)
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from AIMarkLens.ui import icons
from AIMarkLens.ui.motion import Motion, ms
from AIMarkLens.ui.theme import C, TONE, tone_colour


def label(text: str = "", role: str = "", wrap: bool = False, selectable: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def button(text: str, variant: str = "", icon_name: str = "", tooltip: str = "",
           on_click: Optional[Callable] = None) -> QPushButton:
    btn = QPushButton(text)
    if variant:
        btn.setProperty("variant", variant)
    if icon_name:
        colour = "#FFFFFF" if variant == "primary" else C["muted"]
        btn.setIcon(icons.icon(icon_name, colour, 18, "#FFFFFF" if variant == "primary" else C["text"]))
        btn.setIconSize(QSize(18, 18))
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setAccessibleName(text.replace("…", "").replace("&", "") or tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    if on_click is not None:
        btn.clicked.connect(lambda _checked=False: on_click())
    return btn


class Card(QFrame):
    def __init__(self, parent: Optional[QWidget] = None, padding: int = 16, spacing: int = 10) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)


class Chip(QLabel):
    """Small pill label coloured by tone. Emits clicked when pressed (used by the status bar)."""

    clicked = Signal()

    def __init__(self, text: str = "", tone: str = "neutral", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.set(text, tone)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def set(self, text: str, tone: str = "neutral") -> None:
        colour = TONE.get(tone, C["neutral"])
        bg = tone_colour(tone, 34).name(QColor.NameFormat.HexArgb)
        self.setText(text)
        self.setStyleSheet(f"QLabel {{ color: {colour}; background: {bg}; border: 1px solid {colour}; "
                           f"border-radius: 9px; padding: 1px 8px; font-size: 8.8pt; font-weight: 600; }}")
        self.setVisible(bool(text))


class Spinner(QWidget):
    def __init__(self, size: int = 40, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(360.0)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)
        self.setAccessibleName("Working")

    def _tick(self, value: float) -> None:
        self._angle = value
        self.update()

    def start(self) -> None:
        self._anim.setDuration(1600 if Motion.reduced else 850)
        self._anim.start()

    def stop(self) -> None:
        self._anim.stop()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
        painter.setPen(QPen(QColor(C["border_strong"]), 3.5))
        painter.drawEllipse(rect)
        pen = QPen(QColor(C["accent"]), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, int(-self._angle * 16), int(110 * 16))


class SmoothProgressBar(QProgressBar):
    """Progress bar whose value glides to each new target."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setRange(0, 1000)
        self.setTextVisible(False)
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_fraction(self, fraction: float) -> None:
        if self.maximum() == 0:
            self.setRange(0, 1000)
        target = int(max(0.0, min(1.0, fraction)) * 1000)
        if target < self.value():
            self._anim.stop()
            self.setValue(target)
            return
        self._anim.stop()
        self._anim.setDuration(ms(240))
        self._anim.setStartValue(self.value())
        self._anim.setEndValue(target)
        self._anim.start()

    def set_indeterminate(self) -> None:
        self._anim.stop()
        self.setRange(0, 0)

    def reset_value(self) -> None:
        self._anim.stop()
        self.setRange(0, 1000)
        self.setValue(0)


class StatusIcon(QWidget):
    """Circular status mark that draws itself in (check for success, cross for failure)."""

    def __init__(self, tone: str = "ok", size: int = 28, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._tone = tone
        self._progress = 1.0
        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        self._progress = value
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    def set_tone(self, tone: str, animate: bool = True) -> None:
        self._tone = tone
        if animate and not Motion.reduced:
            self._anim.stop()
            self._anim.setDuration(560)
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()
        else:
            self._progress = 1.0
            self.update()

    def _glyph(self, s: float) -> QPainterPath:
        path = QPainterPath()
        if self._tone == "ok":
            path.moveTo(7.5 * s, 12.5 * s)
            path.lineTo(10.5 * s, 15.5 * s)
            path.lineTo(16.5 * s, 9 * s)
        elif self._tone == "error":
            path.moveTo(8.5 * s, 8.5 * s)
            path.lineTo(15.5 * s, 15.5 * s)
            path.moveTo(15.5 * s, 8.5 * s)
            path.lineTo(8.5 * s, 15.5 * s)
        elif self._tone in ("warning", "signal"):
            path.moveTo(12 * s, 7 * s)
            path.lineTo(12 * s, 13 * s)
            path.moveTo(12 * s, 16.6 * s)
            path.lineTo(12 * s, 16.7 * s)
        else:
            path.moveTo(12 * s, 7.4 * s)
            path.lineTo(12 * s, 7.5 * s)
            path.moveTo(12 * s, 10.5 * s)
            path.lineTo(12 * s, 16.5 * s)
        return path

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width() / 24.0
        colour = tone_colour(self._tone)
        ease = min(1.0, self._progress * 1.6)
        radius = 10.5 * s * (0.7 + 0.3 * ease)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tone_colour(self._tone, int(48 * ease)))
        painter.drawEllipse(QPointF(12 * s, 12 * s), radius, radius)
        painter.setPen(QPen(colour, 1.6 * s))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(12 * s, 12 * s), radius, radius)
        stroke = max(0.0, (self._progress - 0.25) / 0.75)
        if stroke <= 0:
            return
        pen = QPen(colour, 2.2 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        glyph = self._glyph(s)
        if stroke >= 1.0:
            painter.drawPath(glyph)
            return
        # Draw each sub-path up to the animated fraction of its length.
        for poly in glyph.toSubpathPolygons():
            sub = QPainterPath()
            sub.addPolygon(poly)
            points = [sub.pointAtPercent(t / 20 * stroke) for t in range(21)]
            for a, b in zip(points, points[1:]):
                painter.drawLine(a, b)


class TaskOverlay(QWidget):
    """Covers a results area while a task runs: spinner, stage, smooth progress and a Cancel button."""

    cancelRequested = Signal()

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.hide()
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = Card(padding=24, spacing=12)
        card.setFixedWidth(380)
        outer.addWidget(card)
        top = QHBoxLayout()
        top.setSpacing(14)
        self.spinner = Spinner(40)
        top.addWidget(self.spinner)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = label("Working…", "h3")
        self.stage = label("", "muted", wrap=True)
        texts.addWidget(self.title)
        texts.addWidget(self.stage)
        top.addLayout(texts, 1)
        card.body.addLayout(top)
        self.bar = SmoothProgressBar()
        card.body.addWidget(self.bar)
        row = QHBoxLayout()
        self.percent = label("", "caption")
        row.addWidget(self.percent)
        row.addStretch(1)
        self.cancel_button = button("Cancel", icon_name="cancel", tooltip="Cancel this task (Esc)",
                                    on_click=self._cancel)
        row.addWidget(self.cancel_button)
        card.body.addLayout(row)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.finished.connect(self._fade_done)
        self._hiding = False
        host.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.parent() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.setGeometry(self.parentWidget().rect())
        return False

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(14, 17, 22, 215))

    def start(self, title: str) -> None:
        self.title.setText(title)
        self.stage.setText("Starting…")
        self.percent.setText("")
        self.bar.reset_value()
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Cancel")
        self.setGeometry(self.parentWidget().rect())
        self._hiding = False
        self._fade.stop()
        self._effect.setOpacity(0.0 if not Motion.reduced else 1.0)
        self.show()
        self.raise_()
        self.spinner.start()
        if not Motion.reduced:
            self._fade.setDuration(160)
            self._fade.setStartValue(self._effect.opacity())
            self._fade.setEndValue(1.0)
            self._fade.start()
        self.cancel_button.setFocus()

    def set_progress(self, fraction: float, message: str) -> None:
        if message:
            self.stage.setText(message)
        if fraction < 0:
            self.bar.set_indeterminate()
            self.percent.setText("")
        else:
            self.bar.set_fraction(fraction)
            self.percent.setText(f"{fraction * 100:.0f}%")

    def set_cancelling(self) -> None:
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancelling…")
        self.stage.setText("Stopping safely…")

    def finish(self) -> None:
        if not self.isVisible():
            return
        if Motion.reduced:
            self.spinner.stop()
            self.hide()
            return
        self._hiding = True
        self._fade.stop()
        self._fade.setDuration(180)
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _fade_done(self) -> None:
        if self._hiding:
            self.spinner.stop()
            self.hide()
            self._hiding = False

    def _cancel(self) -> None:
        self.set_cancelling()
        self.cancelRequested.emit()


class Banner(QFrame):
    """Inline message with an icon, optional technical details and actions (used for errors with Retry)."""

    dismissed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        self.hide()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        self.icon = StatusIcon("error", 28)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.title = label("", "h3", wrap=True, selectable=True)
        self.message = label("", "muted", wrap=True, selectable=True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(160)
        self.details.hide()
        texts.addWidget(self.title)
        texts.addWidget(self.message)
        texts.addWidget(self.details)
        layout.addLayout(texts, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)
        self._tone = "error"

    def show_message(self, tone: str, title: str, message: str = "", details: str = "",
                     actions: Iterable[tuple[str, Callable]] = ()) -> None:
        self._tone = tone
        colour = TONE.get(tone, C["neutral"])
        bg = tone_colour(tone, 26).name(QColor.NameFormat.HexArgb)
        self.setStyleSheet(f"QFrame#Banner {{ background: {bg}; border: 1px solid {colour}; border-radius: 10px; }}")
        self.title.setText(title)
        self.message.setText(message)
        self.message.setVisible(bool(message))
        self.details.setPlainText(details)
        self.details.hide()
        while self.actions.count():
            item = self.actions.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        first: Optional[QPushButton] = None
        for text, callback in actions:
            btn = button(text, "primary" if first is None else "", on_click=callback)
            first = first or btn
            self.actions.addWidget(btn)
        if details:
            toggle = button("Details", "ghost", tooltip="Show the technical details")
            toggle.clicked.connect(lambda: self.details.setVisible(not self.details.isVisible()))
            self.actions.addWidget(toggle)
        close = button("", "ghost", "close", "Dismiss", on_click=self.dismiss)
        close.setAccessibleName("Dismiss message")
        self.actions.addWidget(close)
        self.setAccessibleName(f"{title}. {message}")
        self.show()
        self.icon.set_tone(tone)
        if tone == "error":
            self._shake()
        if first is not None:
            first.setFocus()

    def _shake(self) -> None:
        if Motion.reduced:
            return
        anim = QVariantAnimation(self)
        anim.setDuration(360)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        margins = self.layout().contentsMargins()
        base_left, base_right = margins.left(), margins.right()

        def step(t: float) -> None:
            import math

            offset = int(round(7 * math.sin(t * math.pi * 6) * (1 - t)))
            self.layout().setContentsMargins(base_left + offset, margins.top(), base_right - offset, margins.bottom())

        anim.valueChanged.connect(step)
        anim.finished.connect(lambda: self.layout().setContentsMargins(base_left, margins.top(), base_right, margins.bottom()))
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()


class DropZone(QFrame):
    """Dashed drop target with a Choose button; keyboard users can press Enter or Space to browse."""

    fileChosen = Signal(str)

    def __init__(self, title: str, subtitle: str, extensions: Iterable[str], dialog_filter: str,
                 icon_name: str = "image", compact: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.extensions = {e.lower() for e in extensions}
        self.dialog_filter = dialog_filter
        self.start_dir = ""
        self._hover = False
        self._rejecting = False
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{title}. Press Enter to choose a file.")
        self.setMinimumHeight(120 if compact else 220)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(8 if compact else 10)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap(icon_name, C["accent_hover"], 30 if compact else 40))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(glyph)
        self.title = label(title, "h3" if compact else "h2")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        self.subtitle = label(subtitle, "muted", wrap=True)
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.subtitle)
        self.choose = button("Choose file…", "primary" if not compact else "", "open", on_click=self.browse)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.choose)
        row.addStretch(1)
        layout.addLayout(row)

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file", self.start_dir, self.dialog_filter)
        if path:
            self.fileChosen.emit(path)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.browse()
            return
        super().keyPressEvent(event)

    def _paths(self, event) -> list[str]:
        if not event.mimeData().hasUrls():
            return []
        return [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]

    def accepts(self, path: str) -> bool:
        return not self.extensions or os.path.splitext(path)[1].lower() in self.extensions

    def dragEnterEvent(self, event) -> None:
        paths = self._paths(event)
        if paths:
            self._hover = True
            self._rejecting = not self.accepts(paths[0])
            event.acceptProposedAction()
            self.update()

    def dragLeaveEvent(self, _event) -> None:
        self._hover = self._rejecting = False
        self.update()

    def dropEvent(self, event) -> None:
        paths = self._paths(event)
        rejecting = self._rejecting
        self._hover = self._rejecting = False
        self.update()
        if paths:
            event.acceptProposedAction()
            if rejecting:
                self.subtitle.setText("That file type is not supported here. " + self.subtitle.text().split(". ", 1)[-1])
            self.fileChosen.emit(paths[0])

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        if self._hover:
            colour = QColor(C["error"] if self._rejecting else C["accent"])
            fill = QColor(colour)
            fill.setAlpha(24)
            painter.setBrush(fill)
        else:
            colour = QColor(C["focus"] if self.hasFocus() else C["border_strong"])
            painter.setBrush(QColor(C["surface"]))
        pen = QPen(colour, 2 if (self._hover or self.hasFocus()) else 1.4, Qt.PenStyle.DashLine)
        pen.setDashPattern([5, 4])
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 12, 12)

    def focusInEvent(self, event) -> None:
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self.update()
        super().focusOutEvent(event)


class ImagePreview(QWidget):
    """Aspect-correct image display over a subtle checkerboard (so transparency is visible)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._image: Optional[QImage] = None
        self._scaled: Optional[QPixmap] = None
        self._placeholder = "No image"
        self.setMinimumSize(200, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, image: Optional[QImage], placeholder: str = "No image") -> None:
        self._image = image
        self._scaled = None
        self._placeholder = placeholder
        self.setAccessibleDescription("Image preview" if image is not None else placeholder)
        self.update()

    def resizeEvent(self, event) -> None:
        self._scaled = None
        super().resizeEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._image is None or self._image.isNull():
            painter.setPen(QColor(C["faint"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            return
        ratio = self.devicePixelRatioF()
        if self._scaled is None:
            target = QSize(int(self.width() * ratio), int(self.height() * ratio))
            self._scaled = QPixmap.fromImage(self._image.scaled(target, Qt.AspectRatioMode.KeepAspectRatio,
                                                                Qt.TransformationMode.SmoothTransformation))
            self._scaled.setDevicePixelRatio(ratio)
        w, h = self._scaled.width() / ratio, self._scaled.height() / ratio
        x, y = (self.width() - w) / 2, (self.height() - h) / 2
        frame = QRectF(x, y, w, h)
        clip = QPainterPath()
        clip.addRoundedRect(frame, 8, 8)
        painter.setClipPath(clip)
        if self._image.hasAlphaChannel():
            cell = 8
            dark, light = QColor("#1C222B"), QColor("#252C37")
            for row in range(int(h // cell) + 1):
                for col in range(int(w // cell) + 1):
                    painter.fillRect(QRectF(x + col * cell, y + row * cell, cell, cell),
                                     dark if (row + col) % 2 else light)
        painter.drawPixmap(QPointF(x, y), self._scaled)


class SegmentedControl(QFrame):
    currentChanged = Signal(int)

    def __init__(self, labels: list[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SegmentedControl")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        for index, text in enumerate(labels):
            btn = QPushButton(text)
            btn.setObjectName("Segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAccessibleName(text)
            self.group.addButton(btn, index)
            layout.addWidget(btn)
            self.buttons.append(btn)
        self.buttons[0].setChecked(True)
        self.group.idToggled.connect(lambda i, on: on and self.currentChanged.emit(i))

    def current(self) -> int:
        return self.group.checkedId()

    def set_current(self, index: int) -> None:
        self.buttons[index].setChecked(True)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = -1 if event.key() == Qt.Key.Key_Left else 1
            index = (self.current() + step) % len(self.buttons)
            self.set_current(index)
            self.buttons[index].setFocus()
            return
        super().keyPressEvent(event)


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap(icon_name, C["border_strong"], 56))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(glyph)
        head = label(title, "h2")
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(head)
        body = label(text, "muted", wrap=True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setMaximumWidth(520)
        layout.addWidget(body, 0, Qt.AlignmentFlag.AlignHCenter)
