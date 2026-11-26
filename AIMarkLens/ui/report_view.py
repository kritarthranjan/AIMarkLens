"""Renders a ReportDocument as tabs of cards, so the screen always matches the exported report."""

from __future__ import annotations

import re
from typing import Any, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, QRectF, QSize, QSortFilterProxyModel, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QGuiApplication, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLayout,
                               QMenu, QPlainTextEdit, QScrollArea, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QTableView, QTabWidget, QVBoxLayout, QWidget, QWidgetItem)

from AIMarkLens.core.report import (Cell, Findings, KeyValues, Links, Paragraph, Picture, Preformatted, ReportDocument,
                                   Section, Table, Verdict, cell_text, cell_tone)
from AIMarkLens.ui import icons
from AIMarkLens.ui.motion import fade_in
from AIMarkLens.ui.theme import C, TONE, TONE_LABEL, mono_font, tone_colour
from AIMarkLens.ui.widgets import Card, Chip, StatusIcon, button, label

_TONE_ROLE = Qt.ItemDataRole.UserRole + 1
_SORT_ROLE = Qt.ItemDataRole.UserRole + 2
_NUMBER = re.compile(r"^[+-]?[\d,]*\.?\d+")


class RowsModel(QAbstractTableModel):
    def __init__(self, headers: list[str], rows: list[list[Any]]) -> None:
        super().__init__()
        self.headers = headers
        self.rows = rows

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        value = row[index.column()] if index.column() < len(row) else ""
        text = cell_text(value)
        if role == Qt.ItemDataRole.DisplayRole:
            return text if len(text) <= 400 else text[:400] + "…"
        if role == Qt.ItemDataRole.ToolTipRole:
            return text if len(text) > 40 else None
        if role == _TONE_ROLE:
            return cell_tone(value)
        if role == _SORT_ROLE:
            match = _NUMBER.match(text.strip())
            if match:
                try:
                    return (0, float(match.group(0).replace(",", "")), text.lower())
                except ValueError:
                    pass
            return (1, 0.0, text.lower())
        if role == Qt.ItemDataRole.UserRole:
            return text
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None


class _SortProxy(QSortFilterProxyModel):
    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        return left.data(_SORT_ROLE) < right.data(_SORT_ROLE)


class ChipDelegate(QStyledItemDelegate):
    """Draws toned cells as coloured pills."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        tone = index.data(_TONE_ROLE) or "neutral"
        if tone == "neutral":
            super().paint(painter, option, index)
            return
        self.initStyleOption(option, index)
        text = option.text
        option.text = ""
        style = option.widget.style() if option.widget else QGuiApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(option.font)
        metrics = QFontMetrics(option.font)
        width = min(option.rect.width() - 12, metrics.horizontalAdvance(text) + 24)
        pill = QRectF(option.rect.x() + 6, option.rect.center().y() - 10, width, 20)
        painter.setBrush(tone_colour(tone, 38))
        painter.setPen(tone_colour(tone, 160))
        painter.drawRoundedRect(pill, 10, 10)
        painter.setPen(QColor(TONE.get(tone, C["neutral"])))
        painter.drawText(pill.adjusted(9, 0, -9, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(pill.width() - 14)))
        painter.restore()


class DataTable(QTableView):
    activatedPayload = Signal(object)

    def __init__(self, block: Table, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.block = block
        self.model_ = RowsModel(block.headers, block.rows)
        self.proxy = _SortProxy(self)
        self.proxy.setSourceModel(self.model_)
        self.setModel(self.proxy)
        self.setItemDelegate(ChipDelegate(self))
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(30)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        if len(block.rows) > 6:
            header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)  # keep the report's own order until clicked
            self.setSortingEnabled(True)
            header.setSortIndicatorShown(True)
        self.setAccessibleName(f"Table: {', '.join(block.headers)}")
        self._size_columns()
        rows = len(block.rows)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        if rows <= 16:
            self.setFixedHeight(32 + 30 * max(1, rows) + 4)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            self.setFixedHeight(min(520, 32 + 30 * rows + 4))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        if block.payloads is not None:
            self.selectionModel().currentRowChanged.connect(self._current_changed)

    def _size_columns(self) -> None:
        metrics = self.fontMetrics()
        sample = self.block.rows[:200]
        count = len(self.block.headers)
        for col in range(count - 1):
            widest = metrics.horizontalAdvance(self.block.headers[col]) + 32
            for row in sample:
                if col < len(row):
                    pill = 40 if cell_tone(row[col]) != "neutral" else 28  # toned cells are drawn as pills
                    widest = max(widest, metrics.horizontalAdvance(cell_text(row[col])[:80]) + pill)
            self.setColumnWidth(col, min(widest, 340))

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if current.isValid():
            source = self.proxy.mapToSource(current)
            self.activatedPayload.emit(self.block.payloads[source.row()])

    def _selected_text(self, whole: bool = False) -> str:
        rows = range(self.model_.rowCount()) if whole else sorted(
            {self.proxy.mapToSource(i).row() for i in self.selectionModel().selectedIndexes()})
        lines = ["\t".join(self.block.headers)] if whole else []
        for r in rows:
            lines.append("\t".join(cell_text(c) for c in self.block.rows[r]))
        return "\n".join(lines)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            QGuiApplication.clipboard().setText(self._selected_text())
            return
        super().keyPressEvent(event)

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        index = self.indexAt(pos)
        if index.isValid():
            menu.addAction("Copy value", lambda: QGuiApplication.clipboard().setText(index.data(Qt.ItemDataRole.UserRole) or ""))
        menu.addAction("Copy selected rows", lambda: QGuiApplication.clipboard().setText(self._selected_text()))
        menu.addAction("Copy whole table", lambda: QGuiApplication.clipboard().setText(self._selected_text(True)))
        menu.exec(self.viewport().mapToGlobal(pos))


class FlowLayout(QLayout):
    """Wraps child widgets onto new lines as space runs out (used for image galleries)."""

    def __init__(self, parent: Optional[QWidget] = None, spacing: int = 12) -> None:
        super().__init__(parent)
        self._items: list = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect: QRect, test: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + self.spacing(), 0
            if not test:
                item.setGeometry(QRect(x, y, hint.width(), hint.height()))
            x += hint.width() + self.spacing()
            line = max(line, hint.height())
        return y + line - rect.y()


class VerdictCard(QFrame):
    def __init__(self, verdict: Verdict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        colour = TONE.get(verdict.tone, C["neutral"])
        bg = tone_colour(verdict.tone, 22).name(QColor.NameFormat.HexArgb)
        self.setObjectName("Verdict")
        self.setStyleSheet(f"QFrame#Verdict {{ background: {bg}; border: 1px solid {C['border']}; "
                           f"border-left: 5px solid {colour}; border-radius: 12px; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)
        mark = StatusIcon(verdict.tone if verdict.tone != "neutral" else "info", 36)
        layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        head = label(verdict.headline, "h2", wrap=True, selectable=True)
        head.setStyleSheet(f"color: {colour if verdict.tone in ('signal', 'warning', 'error') else C['text']};")
        texts.addWidget(head)
        texts.addWidget(label(verdict.detail, wrap=True, selectable=True))
        if verdict.caveat:
            texts.addWidget(label(verdict.caveat, "caption", wrap=True))
        layout.addLayout(texts, 1)
        self.setAccessibleName(f"Verdict: {verdict.headline}. {verdict.detail}")
        mark.set_tone(verdict.tone if verdict.tone != "neutral" else "info")


def _note(text: str, tone: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName("Note")
    colour = TONE.get(tone, C["neutral"])
    frame.setStyleSheet(f"QFrame#Note {{ background: {tone_colour(tone, 20).name(QColor.NameFormat.HexArgb)}; "
                        f"border: none; border-left: 3px solid {colour}; border-radius: 6px; }}")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.addWidget(label(text, wrap=True, selectable=True), 1)
    return frame


def _key_values(block: KeyValues) -> QWidget:
    if len(block.rows) > 40:
        return DataTable(Table(["Field", "Value"], [list(r) for r in block.rows]))
    widget = QWidget()
    grid = QGridLayout(widget)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(7)
    for row, (key, value) in enumerate(block.rows):
        k = label(key, "muted", wrap=True)
        k.setMinimumWidth(150)
        k.setMaximumWidth(260)
        v = label(value, wrap=True, selectable=True)
        if len(value) > 48 and " " not in value:
            v.setFont(mono_font(9))
        grid.addWidget(k, row, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(v, row, 1)
    grid.setColumnStretch(1, 1)
    return widget


def _findings(block: Findings) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    if not block.items:
        layout.addWidget(label("No findings.", "muted"))
    for index, finding in enumerate(block.items):
        row = QFrame()
        row.setObjectName("FindingRow")
        border = f"border-top: 1px solid {C['border']};" if index else ""
        row.setStyleSheet(f"QFrame#FindingRow {{ {border} }}")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 9, 0, 9)
        h.setSpacing(12)
        chip = Chip(TONE_LABEL.get(finding.tone, finding.tone), finding.tone)
        chip.setFixedWidth(74)
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(label(finding.title, "h3", wrap=True, selectable=True))
        detail = finding.detail + (f"  ·  {finding.source}" if finding.source else "")
        if detail.strip():
            texts.addWidget(label(detail, "muted", wrap=True, selectable=True))
        h.addLayout(texts, 1)
        row.setAccessibleName(f"{TONE_LABEL.get(finding.tone, '')}: {finding.title}. {finding.detail}")
        layout.addWidget(row)
    return widget


def _pictures(blocks: list[Picture]) -> QWidget:
    widget = QWidget()
    flow = FlowLayout(widget)
    for block in blocks:
        holder = QWidget()
        v = QVBoxLayout(holder)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        pm = QPixmap()
        pm.loadFromData(block.png, "PNG")
        shown = pm.scaled(min(300, pm.width()), min(300, pm.height()), Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        image = QLabel()
        image.setPixmap(shown)
        image.setToolTip(f"{block.caption} ({block.width}×{block.height})")
        image.setAccessibleName(block.caption)
        image.setStyleSheet(f"border: 1px solid {C['border']}; border-radius: 6px;")
        v.addWidget(image)
        cap = label(block.caption, "caption", wrap=True)
        cap.setMaximumWidth(max(160, shown.width()))
        v.addWidget(cap)
        flow.addWidget(holder)
    return widget


def _preformatted(block: Preformatted) -> QWidget:
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setFont(mono_font(9))
    view.setPlainText(block.text)
    lines = block.text.count("\n") + 1
    view.setFixedHeight(min(380, 24 + lines * 18))
    view.setAccessibleName("Raw output")
    return view


def _links(block: Links) -> QWidget:
    widget = QWidget()
    h = QHBoxLayout(widget)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    for text, url in block.items:
        btn = button(text, "ghost", "link", f"Opens {url} in your browser",
                     on_click=lambda u=url: QDesktopServices.openUrl(QUrl(u)))
        h.addWidget(btn)
    h.addStretch(1)
    return widget


class SectionCard(Card):
    def __init__(self, section: Section, view: "ReportView") -> None:
        super().__init__(padding=16, spacing=10)
        title = label(section.title, "h2", wrap=True)
        self.body.addWidget(title)
        if section.description:
            self.body.addWidget(label(section.description, "muted", wrap=True))
        pictures: list[Picture] = []
        for block in section.blocks:
            if isinstance(block, Picture):
                pictures.append(block)
                continue
            if pictures:
                self.body.addWidget(_pictures(pictures))
                pictures = []
            self.body.addWidget(view.render_block(block))
        if pictures:
            self.body.addWidget(_pictures(pictures))
        self.setAccessibleName(section.title)


class ReportView(QTabWidget):
    rowActivated = Signal(str, object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideNone)
        self.document: Optional[ReportDocument] = None
        self.tables: dict[str, DataTable] = {}

    def render_block(self, block) -> QWidget:
        if isinstance(block, Paragraph):
            if block.tone == "neutral":
                return label(block.text, wrap=True, selectable=True)
            return _note(block.text, block.tone)
        if isinstance(block, KeyValues):
            return _key_values(block)
        if isinstance(block, Table):
            table = DataTable(block)
            if block.key:
                self.tables[block.key] = table
                table.activatedPayload.connect(lambda payload, key=block.key: self.rowActivated.emit(key, payload))
            if block.note:
                holder = QWidget()
                v = QVBoxLayout(holder)
                v.setContentsMargins(0, 0, 0, 0)
                v.setSpacing(6)
                v.addWidget(table)
                v.addWidget(label(block.note, "caption", wrap=True))
                return holder
            return table
        if isinstance(block, Findings):
            return _findings(block)
        if isinstance(block, Preformatted):
            return _preformatted(block)
        if isinstance(block, Links):
            return _links(block)
        return label("")

    def set_document(self, doc: ReportDocument) -> None:
        previous = self.tabText(self.currentIndex()) if self.count() else ""
        self.setUpdatesEnabled(False)
        try:
            while self.count():
                page = self.widget(0)
                self.removeTab(0)
                page.deleteLater()
            self.tables = {}
            self.document = doc
            for tab in doc.tabs():
                self.addTab(self._build_tab(doc, tab), tab)
            names = [self.tabText(i) for i in range(self.count())]
            self.setCurrentIndex(names.index(previous) if previous in names else 0)
        finally:
            self.setUpdatesEnabled(True)
        fade_in(self.currentWidget(), 200)

    def select_tab(self, name: str) -> None:
        for i in range(self.count()):
            if self.tabText(i) == name:
                self.setCurrentIndex(i)
                return

    def _build_tab(self, doc: ReportDocument, tab: str) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 12, 10, 16)
        layout.setSpacing(12)
        if tab == doc.tabs()[0]:
            if doc.verdict is not None:
                layout.addWidget(VerdictCard(doc.verdict))
            if doc.integrity:
                layout.addWidget(self._integrity_card(doc))
        for section in doc.sections:
            if section.tab == tab:
                layout.addWidget(SectionCard(section, self))
        layout.addStretch(1)
        scroll.setWidget(content)
        return scroll

    def _integrity_card(self, doc: ReportDocument) -> QWidget:
        ok = doc.extra.get("integrity_ok", True)
        card = Card(padding=14, spacing=8)
        head = QHBoxLayout()
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap("shield", C["ok"] if ok else C["error"], 22))
        head.addWidget(glyph)
        head.addWidget(label("Original preserved" if ok else "Integrity check failed", "h3"))
        head.addStretch(1)
        head.addWidget(Chip("SHA-256 verified" if ok else "Changed", "ok" if ok else "error"))
        card.body.addLayout(head)
        card.body.addWidget(_key_values(KeyValues(doc.integrity)))
        return card
