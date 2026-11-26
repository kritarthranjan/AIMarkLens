"""Crisp vector icons painted at runtime (no image assets to ship or load)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap

from AIMarkLens.ui.theme import C


def _path_for(name: str) -> tuple[QPainterPath, bool]:
    """Return a path on a 24×24 grid and whether it should be filled."""
    p = QPainterPath()
    fill = False
    if name == "image":
        p.addRoundedRect(QRectF(3, 4, 18, 16), 3, 3)
        p.moveTo(3.5, 17)
        p.lineTo(9, 11.5)
        p.lineTo(13, 15.5)
        p.lineTo(15.5, 13)
        p.lineTo(20.5, 18)
        p.addEllipse(QPointF(15.5, 8.5), 1.6, 1.6)
    elif name == "text":
        for y, x2 in ((6, 20), (10, 17), (14, 20), (18, 14)):
            p.moveTo(4, y)
            p.lineTo(x2, y)
    elif name == "compare":
        p.addRoundedRect(QRectF(3, 5, 10, 14), 2, 2)
        p.addRoundedRect(QRectF(11, 5, 10, 14), 2, 2)
    elif name == "flask":
        p.moveTo(9.5, 3)
        p.lineTo(14.5, 3)
        p.moveTo(10.5, 3)
        p.lineTo(10.5, 9)
        p.lineTo(5, 18.5)
        p.quadTo(4.5, 21, 7, 21)
        p.lineTo(17, 21)
        p.quadTo(19.5, 21, 19, 18.5)
        p.lineTo(13.5, 9)
        p.lineTo(13.5, 3)
        p.moveTo(7.2, 15)
        p.lineTo(16.8, 15)
    elif name == "settings":
        import math

        p.addEllipse(QPointF(12, 12), 3, 3)
        outer = QPainterPath()
        for i in range(16):
            angle = i * math.pi / 8
            r = 8.5 if i % 2 == 0 else 6.6
            pt = QPointF(12 + r * math.cos(angle), 12 + r * math.sin(angle))
            outer.moveTo(pt) if i == 0 else outer.lineTo(pt)
        outer.closeSubpath()
        p.addPath(outer)
    elif name == "open":
        p.moveTo(3, 7)
        p.lineTo(3, 18)
        p.quadTo(3, 20, 5, 20)
        p.lineTo(18, 20)
        p.lineTo(21, 11)
        p.lineTo(7, 11)
        p.lineTo(4.5, 18)
        p.moveTo(3, 7)
        p.quadTo(3, 5, 5, 5)
        p.lineTo(9, 5)
        p.lineTo(11, 7)
        p.lineTo(17, 7)
        p.quadTo(18.5, 7, 18.5, 8.5)
        p.lineTo(18.5, 11)
    elif name == "paste":
        p.addRoundedRect(QRectF(5, 5, 14, 16), 2, 2)
        p.addRoundedRect(QRectF(9, 3, 6, 4), 1.5, 1.5)
        p.moveTo(8.5, 12)
        p.lineTo(15.5, 12)
        p.moveTo(8.5, 16)
        p.lineTo(13.5, 16)
    elif name == "analyse":
        p.addEllipse(QPointF(10.5, 10.5), 6, 6)
        p.moveTo(15, 15)
        p.lineTo(20.5, 20.5)
    elif name == "cancel":
        p.addEllipse(QPointF(12, 12), 8.5, 8.5)
        p.moveTo(9, 9)
        p.lineTo(15, 15)
        p.moveTo(15, 9)
        p.lineTo(9, 15)
    elif name == "close":
        p.moveTo(7, 7)
        p.lineTo(17, 17)
        p.moveTo(17, 7)
        p.lineTo(7, 17)
    elif name == "export":
        p.moveTo(12, 15)
        p.lineTo(12, 3.5)
        p.moveTo(7.5, 8)
        p.lineTo(12, 3.5)
        p.lineTo(16.5, 8)
        p.moveTo(4, 13)
        p.lineTo(4, 19)
        p.quadTo(4, 20.5, 5.5, 20.5)
        p.lineTo(18.5, 20.5)
        p.quadTo(20, 20.5, 20, 19)
        p.lineTo(20, 13)
    elif name == "clean":
        p.moveTo(12, 3)
        p.lineTo(13.6, 8.4)
        p.lineTo(19, 10)
        p.lineTo(13.6, 11.6)
        p.lineTo(12, 17)
        p.lineTo(10.4, 11.6)
        p.lineTo(5, 10)
        p.lineTo(10.4, 8.4)
        p.closeSubpath()
        p.moveTo(18, 16)
        p.lineTo(18, 21)
        p.moveTo(15.5, 18.5)
        p.lineTo(20.5, 18.5)
    elif name == "shield":
        p.moveTo(12, 3)
        p.lineTo(19.5, 6)
        p.lineTo(19.5, 11.5)
        p.quadTo(19.5, 18, 12, 21)
        p.quadTo(4.5, 18, 4.5, 11.5)
        p.lineTo(4.5, 6)
        p.closeSubpath()
        p.moveTo(8.5, 12)
        p.lineTo(11, 14.5)
        p.lineTo(15.5, 9.5)
    elif name == "swap":
        p.moveTo(4, 8)
        p.lineTo(19, 8)
        p.moveTo(15.5, 4.5)
        p.lineTo(19, 8)
        p.lineTo(15.5, 11.5)
        p.moveTo(20, 16)
        p.lineTo(5, 16)
        p.moveTo(8.5, 12.5)
        p.lineTo(5, 16)
        p.lineTo(8.5, 19.5)
    elif name == "link":
        p.moveTo(14, 4)
        p.lineTo(20, 4)
        p.lineTo(20, 10)
        p.moveTo(20, 4)
        p.lineTo(11, 13)
        p.moveTo(17, 14)
        p.lineTo(17, 19)
        p.quadTo(17, 20, 16, 20)
        p.lineTo(5, 20)
        p.quadTo(4, 20, 4, 19)
        p.lineTo(4, 8)
        p.quadTo(4, 7, 5, 7)
        p.lineTo(10, 7)
    elif name == "folder":
        p.moveTo(3, 7)
        p.quadTo(3, 5, 5, 5)
        p.lineTo(9.5, 5)
        p.lineTo(11.5, 7.5)
        p.lineTo(19, 7.5)
        p.quadTo(21, 7.5, 21, 9.5)
        p.lineTo(21, 18)
        p.quadTo(21, 20, 19, 20)
        p.lineTo(5, 20)
        p.quadTo(3, 20, 3, 18)
        p.closeSubpath()
    elif name == "eye":
        p.moveTo(2.5, 12)
        p.quadTo(12, 2, 21.5, 12)
        p.quadTo(12, 22, 2.5, 12)
        p.addEllipse(QPointF(12, 12), 3, 3)
    elif name == "keyboard":
        p.addRoundedRect(QRectF(2.5, 6, 19, 12), 2.5, 2.5)
        for x in (6, 9.5, 13, 16.5):
            p.moveTo(x, 10)
            p.lineTo(x + 1, 10)
        p.moveTo(7.5, 14.5)
        p.lineTo(16.5, 14.5)
    elif name == "watermark":
        p.moveTo(12, 3)
        p.quadTo(19, 11, 19, 14.5)
        p.quadTo(19, 21, 12, 21)
        p.quadTo(5, 21, 5, 14.5)
        p.quadTo(5, 11, 12, 3)
        p.closeSubpath()
        p.moveTo(9, 15)
        p.quadTo(9.5, 17.5, 12, 18)
    elif name == "download":
        p.moveTo(12, 3.5)
        p.lineTo(12, 15)
        p.moveTo(7.5, 10.5)
        p.lineTo(12, 15)
        p.lineTo(16.5, 10.5)
        p.moveTo(4, 14)
        p.lineTo(4, 19)
        p.quadTo(4, 20.5, 5.5, 20.5)
        p.lineTo(18.5, 20.5)
        p.quadTo(20, 20.5, 20, 19)
        p.lineTo(20, 14)
    elif name == "cloud":
        p.moveTo(7, 18.5)
        p.quadTo(3, 18.5, 3, 14.5)
        p.quadTo(3, 11, 6.5, 10.6)
        p.quadTo(7.5, 5.5, 12.5, 5.5)
        p.quadTo(17, 5.5, 18, 10)
        p.quadTo(21, 10.5, 21, 14.2)
        p.quadTo(21, 18.5, 17, 18.5)
        p.closeSubpath()
        p.moveTo(9.5, 13.5)
        p.lineTo(11.5, 15.5)
        p.lineTo(15, 11.5)
    elif name == "check":
        p.moveTo(5.5, 12.5)
        p.lineTo(10, 17)
        p.lineTo(18.5, 7.5)
    elif name == "clear":
        p.moveTo(5, 7)
        p.lineTo(19, 7)
        p.moveTo(9.5, 7)
        p.lineTo(9.5, 4.5)
        p.lineTo(14.5, 4.5)
        p.lineTo(14.5, 7)
        p.moveTo(6.5, 7)
        p.lineTo(7.5, 20)
        p.lineTo(16.5, 20)
        p.lineTo(17.5, 7)
    else:  # fallback dot
        p.addEllipse(QPointF(12, 12), 3, 3)
        fill = True
    return p, fill


@lru_cache(maxsize=256)
def pixmap(name: str, colour: str = C["muted"], size: int = 20, ratio: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size * ratio / 24.0, size * ratio / 24.0)
    path, fill = _path_for(name)
    pen = QPen(QColor(colour), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QBrush(QColor(colour)) if fill else Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icon(name: str, colour: str = C["muted"], size: int = 20, active: str = C["text"]) -> QIcon:
    result = QIcon()
    result.addPixmap(pixmap(name, colour, size), QIcon.Mode.Normal, QIcon.State.Off)
    result.addPixmap(pixmap(name, active, size), QIcon.Mode.Active, QIcon.State.Off)
    result.addPixmap(pixmap(name, active, size), QIcon.Mode.Normal, QIcon.State.On)
    result.addPixmap(pixmap(name, C["faint"], size), QIcon.Mode.Disabled, QIcon.State.Off)
    return result


RESOURCES = Path(__file__).resolve().parent.parent / "resources"


def logo_image(size: int) -> QImage:
    """The AIMarkLens app icon artwork, scaled to size × size."""
    image = QImage(str(RESOURCES / "app-icon-256.png"))
    if image.isNull():
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(C["accent"]))
        return image
    return image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def app_icon() -> QIcon:
    icon_file = RESOURCES / "app-icon.ico"
    result = QIcon(str(icon_file)) if icon_file.is_file() else QIcon()
    if result.isNull():
        for size in (16, 24, 32, 48, 64, 128, 256):
            result.addPixmap(QPixmap.fromImage(logo_image(size)))
    return result
