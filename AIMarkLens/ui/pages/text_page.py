"""Text Lab: capture text exactly, reveal invisible characters and analyse provenance signals."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextFormat, QTextOption
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QSplitter, QStackedWidget, QTextEdit, QVBoxLayout

from AIMarkLens.ui.clipboard import read_clipboard_exact
from AIMarkLens.ui.dialogs import CleanCopyDialog
from AIMarkLens.ui.motion import fade_in
from AIMarkLens.ui.pages.base import PageBase, open_folder
from AIMarkLens.ui.theme import mono_font, tone_colour
from AIMarkLens.ui.widgets import Card, Chip, SegmentedControl, button, label

TEXT_FILTER = "Text files (*.txt *.md *.text *.log *.csv *.json *.html *.xml);;All files (*.*)"
TEXT_EXTENSIONS = (".txt", ".md", ".text", ".log", ".csv", ".json", ".html", ".htm", ".xml")
EDITOR_LIMIT = 2_000_000
HIGHLIGHT_LIMIT = 4000


class SampleEditor(QPlainTextEdit):
    """Editor that reports pastes so the exact clipboard text can be kept as the sample."""

    pasted = Signal(bool)  # True when the paste replaced the whole document

    def insertFromMimeData(self, source) -> None:
        if source.hasText():
            cursor = self.textCursor()
            whole = self.document().isEmpty() or (
                cursor.selectionStart() == 0 and cursor.selectionEnd() >= self.document().characterCount() - 1)
            super().insertFromMimeData(source)
            self.pasted.emit(whole)
            return
        super().insertFromMimeData(source)


class TextPage(PageBase):
    title = "Text Lab"

    def __init__(self, window) -> None:
        super().__init__(window)
        from AIMarkLens.engines.text.sample import TextSample  # light module

        self._TextSample = TextSample
        self.sample: Optional[TextSample] = None
        self._programmatic = False
        self._stale = False
        actions = self.build_header("Text Lab", "Reveal invisible characters, formatting artefacts and provenance "
                                                "signals. Text is captured exactly as copied.")
        self.open_btn = button("Open text file…", icon_name="open", tooltip="Open a text file (Ctrl+O)", on_click=self.open_file)
        self.paste_btn = button("Paste exactly", icon_name="paste", tooltip="Paste the clipboard exactly (Ctrl+Shift+V)",
                                on_click=self.paste)
        self.clean_btn = button("Cleaned copy…", icon_name="clean", tooltip="Save a cleaned copy of the text",
                                on_click=self.export_clean)
        self.export_btn = button("Export report", icon_name="export", tooltip="Export the report (Ctrl+E)",
                                 on_click=self.export_report)
        self.analyse_btn = button("Analyse", "primary", "analyse", "Analyse the text (F5)", on_click=self.analyse)
        for b in (self.open_btn, self.paste_btn, self.clean_btn, self.export_btn, self.analyse_btn):
            actions.addWidget(b)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        left = Card(padding=14, spacing=10)
        left.setMinimumWidth(320)
        top = QHBoxLayout()
        self.source_chip = Chip("No text yet", "neutral")
        top.addWidget(self.source_chip)
        self.stale_chip = Chip()
        top.addWidget(self.stale_chip)
        top.addStretch(1)
        self.view_switch = SegmentedControl(["Text", "Reveal invisible"])
        self.view_switch.setToolTip("Switch between the text and a view with invisible characters shown as tokens")
        self.view_switch.currentChanged.connect(self._switch_view)
        top.addWidget(self.view_switch)
        left.body.addLayout(top)
        self.editor_stack = QStackedWidget()
        self.editor = SampleEditor()
        self.editor.setFont(mono_font(10))
        self.editor.setPlaceholderText("Paste or type text here, or open a text file.\n\n"
                                       "Use Ctrl+Shift+V (Paste exactly) to capture copied AI output with every "
                                       "hidden character and line ending intact.")
        self.editor.setAccessibleName("Text sample editor")
        self.editor.textChanged.connect(self._edited)
        self.editor.pasted.connect(self._editor_pasted)
        self.reveal = QPlainTextEdit()
        self.reveal.setReadOnly(True)
        self.reveal.setFont(mono_font(10))
        self.reveal.setAccessibleName("Reveal view: invisible characters shown as tokens")
        self.reveal.setPlaceholderText("Analyse the text to reveal invisible characters.")
        self.editor_stack.addWidget(self.editor)
        self.editor_stack.addWidget(self.reveal)
        left.body.addWidget(self.editor_stack, 1)
        bottom = QHBoxLayout()
        self.counts = label("", "caption")
        bottom.addWidget(self.counts)
        bottom.addStretch(1)
        self.clear_btn = button("Clear", "ghost", "clear", "Clear the text", on_click=self.clear)
        bottom.addWidget(self.clear_btn)
        left.body.addLayout(bottom)
        splitter.addWidget(left)
        splitter.addWidget(self.build_results(
            "text", "Paste or open text to begin",
            "AIMarkLens finds zero-width, tag and bidirectional characters, decodes known hiding schemes, spots "
            "look-alike letters and assistant artefacts such as citation markup or tracking parameters, and runs "
            "the lab's statistical watermark test."))
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([520, 760])
        self.root.addWidget(splitter, 1)
        self.report_view.rowActivated.connect(self._row_activated)
        self.setAcceptDrops(True)
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.timeout.connect(self._update_counts)
        self.update_actions()

    # ------------------------------------------------------------ input

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            event.acceptProposedAction()
            self.load_path(urls[0])

    def protected_paths(self) -> list[str]:
        return [self.sample.path] if self.sample and self.sample.path else []

    def update_actions(self) -> None:
        busy = self.runner.busy
        has_text = self.sample is not None or not self.editor.document().isEmpty()
        for b in (self.open_btn, self.paste_btn, self.clear_btn):
            b.setEnabled(not busy)
        self.analyse_btn.setEnabled(not busy and has_text)
        self.analyse_btn.setText("Analyse again" if self.document is not None and not self._stale else "Analyse")
        self.export_btn.setEnabled(self.document is not None and not self.export_runner.busy)
        self.clean_btn.setEnabled(not busy and has_text)
        self.editor.setReadOnly(busy or (self.sample is not None and len(self.sample.text) > EDITOR_LIMIT))

    def _set_editor_text(self, text: str) -> None:
        self._programmatic = True
        try:
            shown = text if len(text) <= EDITOR_LIMIT else text[:EDITOR_LIMIT]
            self.editor.setPlainText(shown)
        finally:
            self._programmatic = False
        self._update_counts()

    def _set_sample(self, sample) -> None:
        self.sample = sample
        from AIMarkLens.engines.text.sample import ORIGIN_LABELS

        tone = "info" if sample.origin == "clipboard" else "neutral"
        self.source_chip.set(f"{ORIGIN_LABELS.get(sample.origin, sample.origin)}: {sample.label}", tone)
        self._set_editor_text(sample.text)
        if len(sample.text) > EDITOR_LIMIT:
            self.toast("info", "Large text", f"The editor shows the first {EDITOR_LIMIT:,} characters; "
                                            "the analysis uses the full text.")
        self.view_switch.set_current(0)
        self.clear_document()
        self._stale = False
        self.stale_chip.set("")
        self.update_actions()
        if self.settings.auto_analyse:
            self.analyse()

    def paste(self) -> None:
        if self.runner.busy:
            return
        text, exact = read_clipboard_exact()
        if not text:
            self.toast("info", "The clipboard has no text", "Copy some text first, then paste again.")
            return
        label_text = "Clipboard text" if exact else "Clipboard text (normalised by the system)"
        self._set_sample(self._TextSample(text, "clipboard", label_text))

    def _editor_pasted(self, whole: bool) -> None:
        if not whole:
            return
        text, exact = read_clipboard_exact()
        shown = self.editor.document().toRawText().replace(" ", "\n")  # raw text keeps no-break spaces
        if exact and text.replace("\r\n", "\n").replace("\r", "\n") == shown:
            # The editor shows a normalised view; keep the exact clipboard string as the sample.
            QTimer.singleShot(0, lambda: self._adopt_clipboard(text))

    def _adopt_clipboard(self, text: str) -> None:
        self.sample = self._TextSample(text, "clipboard", "Clipboard text")
        self.source_chip.set("Exact clipboard capture: Clipboard text", "info")
        self.update_actions()
        if self.settings.auto_analyse:
            self.analyse()

    def _edited(self) -> None:
        self._count_timer.start(150)
        if self._programmatic:
            return
        if self.sample is not None:
            self.sample = None
            self.source_chip.set("Typed or edited text", "neutral")
        if self.document is not None:
            self._stale = True
            self.stale_chip.set("Edited since analysis", "warning")
        self.update_actions()

    def _update_counts(self) -> None:
        text = self.sample.text if self.sample else self.editor.toPlainText()
        words = len(text.split())
        self.counts.setText(f"{len(text):,} characters · {words:,} words")

    def open_file(self) -> None:
        if self.runner.busy:
            return
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, "Open a text file", self.window_.store.last_dir("text"), TEXT_FILTER)
        if path:
            self.load_path(path)

    def load_path(self, path: str) -> None:
        if self.runner.busy:
            return
        self.window_.store.set_last_dir("text", path)
        from AIMarkLens.engines.text.sample import load_text_file

        self.start_task("Reading text file", lambda ctx: load_text_file(path, ctx), self._set_sample,
                        retry=lambda: self.load_path(path))

    def clear(self) -> None:
        if self.runner.busy:
            return
        self.sample = None
        self._set_editor_text("")
        self.reveal.setPlainText("")
        self.source_chip.set("No text yet", "neutral")
        self.stale_chip.set("")
        self.view_switch.set_current(0)
        self.clear_document()
        self.editor.setFocus()

    def current_sample(self):
        if self.sample is not None:
            return self.sample
        raw = self.editor.document().toRawText().replace(" ", "\n")
        return self._TextSample(raw, "typed", "Typed text")

    # ------------------------------------------------------------ analysis

    def analyse(self) -> None:
        sample = self.current_sample()
        if not sample.text:
            self.toast("info", "Nothing to analyse", "Paste, type or open some text first.")
            return
        options = self.settings.text_options()
        from AIMarkLens.engines.text.analyser import analyse_text

        self.start_task("Analysing text", lambda ctx: analyse_text(sample, options, ctx), self._analysed,
                        retry=self.analyse)

    def _analysed(self, doc) -> None:
        self._stale = False
        self.stale_chip.set("")
        self.show_document(doc)
        self._fill_reveal(doc)
        self.toast("ok", "Analysis complete", doc.verdict.headline)

    def _fill_reveal(self, doc) -> None:
        text = doc.extra.get("reveal_text", "")
        self.reveal.setPlainText(text if len(text) <= EDITOR_LIMIT * 2 else text[:EDITOR_LIMIT * 2])
        selections = []
        for start, length, tone in doc.extra.get("highlights", [])[:HIGHLIGHT_LIMIT]:
            sel = QTextEdit.ExtraSelection()
            fmt = QTextCharFormat()
            fmt.setBackground(tone_colour(tone if tone != "neutral" else "info", 70 if tone != "neutral" else 30))
            fmt.setForeground(QColor("#FFFFFF") if tone != "neutral" else tone_colour("neutral"))
            sel.format = fmt
            cursor = QTextCursor(self.reveal.document())
            cursor.setPosition(start)
            cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cursor
            selections.append(sel)
        self._base_selections = selections
        self.reveal.setExtraSelections(selections)
        if doc.extra.get("highlights"):
            self.view_switch.set_current(1)

    def _switch_view(self, index: int) -> None:
        self.editor_stack.setCurrentIndex(index)
        fade_in(self.editor_stack.currentWidget(), 140)

    def _row_activated(self, key: str, payload) -> None:
        if key != "occurrences" or self.document is None:
            return
        mapping = self.document.extra.get("occurrence_map", [])
        highlights = self.document.extra.get("highlights", [])
        if not (0 <= payload < len(mapping)) or mapping[payload] < 0:
            return
        start, length, _tone = highlights[mapping[payload]]
        if self.view_switch.current() != 1:
            self.view_switch.set_current(1)
        cursor = QTextCursor(self.reveal.document())
        cursor.setPosition(start)
        cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
        self.reveal.setTextCursor(cursor)
        self.reveal.centerCursor()
        focus = QTextEdit.ExtraSelection()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#FFFFFF"))
        fmt.setForeground(QColor("#0E1116"))
        fmt.setProperty(QTextFormat.Property.FullWidthSelection, False)
        focus.format = fmt
        focus.cursor = cursor
        self.reveal.setExtraSelections(getattr(self, "_base_selections", []) + [focus])

    # ------------------------------------------------------------ cleaned copy

    def export_clean(self) -> None:
        sample = self.current_sample()
        if not sample.text:
            return
        dialog = CleanCopyDialog(self)
        if dialog.exec() != CleanCopyDialog.DialogCode.Accepted:
            return
        options = dialog.selected()
        stem = os.path.splitext(sample.label)[0] if sample.origin == "file" else "text"
        ext = os.path.splitext(sample.label)[1] if sample.origin == "file" else ".txt"
        out, _ = self.choose_save_path("Save cleaned copy", f"{stem}-cleaned{ext or '.txt'}",
                                       "Text files (*.txt *.md);;All files (*.*)")
        if not out:
            return
        protected = self.protected_paths()

        def work(ctx):
            from AIMarkLens.core.integrity import atomic_write_bytes
            from AIMarkLens.engines.text.cleaning import clean_text

            ctx.progress(0.2, "Cleaning a copy of the text")
            cleaned, changes = clean_text(sample.text, options)
            ctx.progress(0.7, "Writing the cleaned copy")
            codec = {"UTF-16 LE": "utf-16-le", "UTF-16 BE": "utf-16-be", "UTF-32 LE": "utf-32-le",
                     "UTF-32 BE": "utf-32-be"}.get(sample.encoding, "utf-8")
            bom = {"utf-8": b"\xef\xbb\xbf", "utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff",
                   "utf-32-le": b"\xff\xfe\x00\x00", "utf-32-be": b"\x00\x00\xfe\xff"}[codec]
            data = (bom if sample.bom else b"") + cleaned.encode(codec, "surrogatepass")
            atomic_write_bytes(out, data, protected)
            return changes

        def done(changes) -> None:
            summary = ", ".join(f"{label_} ({n:,})" for label_, n in changes) if changes else "No changes were needed."
            self.toast("ok", "Cleaned copy saved", summary, [("Show in folder", lambda: open_folder(out))])

        self.start_task("Saving cleaned copy", work, done)
