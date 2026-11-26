"""Compare: an original against a processed copy, for images or text."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPlainTextEdit, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from AIMarkLens.formats.containers import SUPPORTED_EXTENSIONS
from AIMarkLens.ui.clipboard import read_clipboard_exact
from AIMarkLens.ui.motion import fade_in
from AIMarkLens.ui.pages.base import PageBase
from AIMarkLens.ui.pages.image_page import IMAGE_FILTER, load_preview
from AIMarkLens.ui.pages.text_page import TEXT_FILTER
from AIMarkLens.ui.tasks import TaskRunner
from AIMarkLens.ui.theme import mono_font
from AIMarkLens.ui.widgets import Card, Chip, DropZone, ImagePreview, SegmentedControl, button, label


class Slot(Card):
    """One side of the comparison (A or B)."""

    changed = Signal()

    def __init__(self, tag: str, role: str, page: "ComparePage") -> None:
        super().__init__(padding=12, spacing=8)
        self.page = page
        self.tag = tag
        self.path: Optional[str] = None
        self.sample = None
        self.mode = "image"
        self.preview_runner = TaskRunner(self)
        self.preview_runner.failed.connect(lambda message, _hint, _details: self.info.setText(message))
        head = QHBoxLayout()
        head.addWidget(Chip(f"{tag} · {role}", "info" if tag == "A" else "signal"))
        head.addStretch(1)
        self.paste_btn = button("Paste exactly", "ghost", "paste", f"Paste text into {tag}", on_click=self.paste)
        self.clear_btn = button("", "ghost", "clear", f"Clear {tag}", on_click=self.clear)
        self.clear_btn.setAccessibleName(f"Clear {tag}")
        head.addWidget(self.paste_btn)
        head.addWidget(self.clear_btn)
        self.body.addLayout(head)
        self.stack = QStackedWidget()
        self.drop = DropZone(f"Drop {role.lower()} file", "Or press Enter to browse", SUPPORTED_EXTENSIONS,
                             IMAGE_FILTER, "image", compact=True)
        self.drop.fileChosen.connect(self.load_path)
        self.stack.addWidget(self.drop)
        filled = QWidget()
        fv = QVBoxLayout(filled)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(6)
        self.preview = ImagePreview()
        self.preview.setMinimumHeight(120)
        self.text_preview = QPlainTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setFont(mono_font(9))
        self.inner = QStackedWidget()
        self.inner.addWidget(self.preview)
        self.inner.addWidget(self.text_preview)
        fv.addWidget(self.inner, 1)
        self.name = label("", "h3", wrap=True)
        self.info = label("", "caption", wrap=True)
        fv.addWidget(self.name)
        fv.addWidget(self.info)
        change = button("Choose another…", "link", on_click=self.drop.browse)
        fv.addWidget(change, 0, Qt.AlignmentFlag.AlignLeft)
        self.stack.addWidget(filled)
        self.body.addWidget(self.stack, 1)
        self.setAcceptDrops(True)
        self.set_mode("image")

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            event.acceptProposedAction()
            self.load_path(urls[0])

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.clear()
        if mode == "image":
            self.drop.extensions = set(SUPPORTED_EXTENSIONS)
            self.drop.dialog_filter = IMAGE_FILTER
            self.drop.subtitle.setText("PNG, JPEG or WebP. Or press Enter to browse.")
        else:
            self.drop.extensions = set()
            self.drop.dialog_filter = TEXT_FILTER
            self.drop.subtitle.setText("A text file, or use Paste exactly.")
        self.paste_btn.setVisible(mode == "text")

    @property
    def ready(self) -> bool:
        return self.path is not None if self.mode == "image" else self.sample is not None

    def clear(self) -> None:
        self.path = None
        self.sample = None
        self.stack.setCurrentIndex(0)
        self.changed.emit()

    def _show_filled(self) -> None:
        self.stack.setCurrentIndex(1)
        fade_in(self.stack.currentWidget(), 160)
        self.changed.emit()

    def load_path(self, path: str) -> None:
        if self.page.runner.busy:
            return
        if self.mode == "image":
            if os.path.splitext(path)[1].lower() not in SUPPORTED_EXTENSIONS:
                self.page.toast("warning", "Unsupported file type", "Choose a PNG, JPEG or WebP image.")
                return
            self.path = path
            self.name.setText(os.path.basename(path))
            self.info.setText("Loading preview…")
            self.preview.set_image(None, "Loading…")
            self.inner.setCurrentIndex(0)
            self._show_filled()
            self.preview_runner.run("Preview", lambda ctx: load_preview(path, ctx), self._preview_ready)
        else:
            from AIMarkLens.engines.text.sample import load_text_file

            self.page.start_task(f"Reading {self.tag}", lambda ctx: load_text_file(path, ctx), self._text_ready)

    def _preview_ready(self, result) -> None:
        image, info = result
        self.preview.set_image(image)
        self.info.setText(f"{info['format']} · {info['width']:,} × {info['height']:,} · {info['size'] / 1024:,.0f} KB")

    def _text_ready(self, sample) -> None:
        self.sample = sample
        self.path = sample.path
        self.name.setText(sample.label)
        self.info.setText(f"{len(sample.text):,} characters · {sample.encoding}")
        self.text_preview.setPlainText(sample.text[:20000])
        self.inner.setCurrentIndex(1)
        self._show_filled()

    def paste(self) -> None:
        text, exact = read_clipboard_exact()
        if not text:
            self.page.toast("info", "The clipboard has no text", "Copy some text first.")
            return
        from AIMarkLens.engines.text.sample import TextSample

        self._text_ready(TextSample(text, "clipboard", f"Clipboard text ({self.tag})" if exact
                                    else f"Clipboard text ({self.tag}, normalised by the system)"))


class ComparePage(PageBase):
    title = "Compare"

    def __init__(self, window) -> None:
        super().__init__(window)
        actions = self.build_header("Compare", "Compare an original with a processed copy: hashes, metadata, "
                                               "provenance signals, pixels and hidden characters.")
        self.mode_switch = SegmentedControl(["Images", "Text"])
        self.mode_switch.currentChanged.connect(self._mode_changed)
        actions.addWidget(self.mode_switch)
        self.swap_btn = button("Swap", icon_name="swap", tooltip="Swap A and B", on_click=self.swap)
        self.export_btn = button("Export report", icon_name="export", tooltip="Export the report (Ctrl+E)",
                                 on_click=self.export_report)
        self.compare_btn = button("Compare", "primary", "compare", "Compare A with B (F5)", on_click=self.analyse)
        self.online_box = QCheckBox("Online detectors")
        self.online_box.setToolTip("Also check A and B with Google SynthID and the AI-image detectors set up in "
                                   "Settings (uploads both images)")
        self.online_box.setAccessibleName("Also check with online detectors")
        actions.addWidget(self.online_box)
        for b in (self.swap_btn, self.export_btn, self.compare_btn):
            actions.addWidget(b)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        slots = QWidget()
        row = QHBoxLayout(slots)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self.a = Slot("A", "Original", self)
        self.b = Slot("B", "Processed", self)
        for slot in (self.a, self.b):
            slot.changed.connect(self.update_actions)
            row.addWidget(slot)
        slots.setMinimumHeight(200)
        splitter.addWidget(slots)
        splitter.addWidget(self.build_results(
            "compare", "Choose two files to compare",
            "Put the original in A and the processed copy (re-saved, uploaded, edited or cleaned) in B. AIMarkLens "
            "shows which provenance signals survived, what metadata changed and how much the pixels or characters differ."))
        splitter.setSizes([280, 600])
        self.root.addWidget(splitter, 1)
        self.update_actions()

    @property
    def mode(self) -> str:
        return "image" if self.mode_switch.current() == 0 else "text"

    def _mode_changed(self, _index: int) -> None:
        for slot in (self.a, self.b):
            slot.set_mode(self.mode)
        self.clear_document()

    def protected_paths(self) -> list[str]:
        return [p for p in (self.a.path, self.b.path) if p]

    def update_actions(self) -> None:
        busy = self.runner.busy
        ready = self.a.ready and self.b.ready
        self.compare_btn.setEnabled(not busy and ready)
        self.swap_btn.setEnabled(not busy and (self.a.ready or self.b.ready))
        self.export_btn.setEnabled(self.document is not None and not self.export_runner.busy)
        self.mode_switch.setEnabled(not busy)
        self.online_box.setEnabled(not busy)
        self.online_box.setVisible(self.mode == "image")

    def open_file(self) -> None:
        target = self.a if not self.a.ready else self.b
        target.drop.browse()

    def paste(self) -> None:
        if self.mode == "text":
            (self.a if not self.a.ready else self.b).paste()

    def swap(self) -> None:
        a_state = (self.a.path, self.a.sample)
        b_state = (self.b.path, self.b.sample)
        for slot, (path, sample) in ((self.a, b_state), (self.b, a_state)):
            slot.clear()
            if self.mode == "text" and sample is not None:
                slot._text_ready(sample)
            elif path:
                slot.load_path(path)
        self.clear_document()

    def analyse(self) -> None:
        if not (self.a.ready and self.b.ready):
            self.toast("info", "Two files are needed", "Choose an original (A) and a processed copy (B).")
            return
        if self.mode == "image":
            from AIMarkLens.engines.image.compare import compare_images

            a, b, options = self.a.path, self.b.path, self.settings.image_options()
            if not self.online_box.isChecked():
                self.start_task("Comparing images", lambda ctx: compare_images(a, b, options, ctx), self._done,
                                retry=self.analyse)
                return
            services = self.online_services()
            if not services or not self.confirm_upload([a, b], services):
                return
            import dataclasses

            from AIMarkLens.engines.online.report import attach_compare
            from AIMarkLens.engines.online.verifiers import verify_image_online

            cfg, record = dataclasses.replace(self.settings.online), self.settings.history_path

            def work(ctx):
                doc = compare_images(a, b, options, ctx.sub(0.0, 0.45))
                report_a = verify_image_online(a, services, cfg, ctx.sub(0.45, 0.72), record)
                report_b = verify_image_online(b, services, cfg, ctx.sub(0.72, 1.0), record)
                attach_compare(doc, report_a, report_b)
                return doc

            self.start_task("Comparing images and verifying online", work, self._done, retry=self.analyse)
        else:
            from AIMarkLens.engines.text.compare import compare_texts

            a, b, options = self.a.sample, self.b.sample, self.settings.text_options()
            self.start_task("Comparing text", lambda ctx: compare_texts(a, b, options, ctx), self._done,
                            retry=self.analyse)

    def load_and_compare(self, a: str, b: str, online: bool) -> None:
        """Put two images into A and B and compare them (used after saving a metadata-free copy)."""
        if self.runner.busy:
            self.toast("info", "Please wait", "A comparison is already running.")
            return
        self.mode_switch.set_current(0)
        self.a.load_path(a)
        self.b.load_path(b)
        self.online_box.setChecked(online)
        self.update_actions()
        self.analyse()

    def _done(self, doc) -> None:
        self.show_document(doc)
        if any(s.tab == "Online" for s in doc.sections):
            self.report_view.select_tab("Online")
        self.toast("ok", "Comparison complete", doc.verdict.headline if doc.verdict else "")
