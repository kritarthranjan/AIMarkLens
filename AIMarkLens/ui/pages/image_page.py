"""Image Lab: open an image read-only, analyse it, export reports and metadata-free copies."""

from __future__ import annotations

import io
import os
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QHBoxLayout, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from AIMarkLens.edition import features
from AIMarkLens.formats.containers import SUPPORTED_EXTENSIONS
from AIMarkLens.ui.dialogs import MetadataCopyDialog
from AIMarkLens.ui.motion import fade_in
from AIMarkLens.ui.pages.base import PageBase, open_folder
from AIMarkLens.ui.tasks import TaskRunner
from AIMarkLens.ui.widgets import Card, Chip, DropZone, ImagePreview, button, label

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp);;All files (*.*)"
PREVIEW_SIDE = 1600


def load_preview(path: str, ctx) -> tuple[QImage, dict]:
    """Decode a display-sized preview off the UI thread (JPEG uses draft mode to save memory)."""
    from PIL import Image

    from AIMarkLens.core.integrity import read_original
    from AIMarkLens.formats.containers import sniff_format

    data, fingerprint = read_original(path, ctx)
    fmt = sniff_format(data) or "Unknown"
    ctx.progress(0.5, "Decoding preview")
    Image.MAX_IMAGE_PIXELS = 220_000_000
    with Image.open(io.BytesIO(data)) as img:
        width, height, mode = img.width, img.height, img.mode
        if img.format == "JPEG":
            img.draft("RGB", (PREVIEW_SIDE, PREVIEW_SIDE))
        img.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), Image.Resampling.LANCZOS)
        rgba = img.convert("RGBA")
    raw = rgba.tobytes()
    image = QImage(raw, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()
    return image, {"format": fmt, "width": width, "height": height, "mode": mode, "size": fingerprint.size}


class ImagePage(PageBase):
    title = "Image Lab"

    def __init__(self, window) -> None:
        super().__init__(window)
        self.path: Optional[str] = None
        self.preview_runner = TaskRunner(self)
        self.preview_runner.failed.connect(self._preview_failed)
        actions = self.build_header("Image Lab", "Inspect metadata, Content Credentials and pixel-level signals. "
                                                 "Images are opened read-only and verified by hash.")
        self.open_btn = button("Open image…", icon_name="open", tooltip="Open an image (Ctrl+O)", on_click=self.open_file)
        self.copy_btn = button("Metadata-free copy…", icon_name="clean",
                               tooltip="Save a separate copy without selected metadata", on_click=self.export_copy)
        self.export_btn = button("Export report", icon_name="export", tooltip="Export the report (Ctrl+E)",
                                 on_click=self.export_report)
        self.analyse_btn = button("Analyse", "primary", "analyse", "Analyse the image (F5)", on_click=self.analyse)
        self.verify_btn = button("Verify online", icon_name="cloud",
                                 tooltip="Check with Google SynthID and AI-image detectors (Ctrl+Shift+O)",
                                 on_click=self.verify_online)
        self.copy_btn.setVisible(features().metadata_copy)
        for b in (self.open_btn, self.copy_btn, self.export_btn, self.verify_btn, self.analyse_btn):
            actions.addWidget(b)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)

        left = Card(padding=14, spacing=10)
        left.setMinimumWidth(270)
        self.left_stack = QStackedWidget()
        self.drop = DropZone("Drop an image here", "PNG, JPEG or WebP. The original is never modified.",
                             SUPPORTED_EXTENSIONS, IMAGE_FILTER, "image")
        self.drop.fileChosen.connect(self.load_path)
        self.left_stack.addWidget(self.drop)
        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(8)
        self.preview = ImagePreview()
        pv.addWidget(self.preview, 1)
        self.name_label = label("", "h3", wrap=True, selectable=True)
        pv.addWidget(self.name_label)
        self.info_label = label("", "muted", wrap=True)
        pv.addWidget(self.info_label)
        chips = QHBoxLayout()
        self.integrity_chip = Chip()
        self.verdict_chip = Chip()
        chips.addWidget(self.integrity_chip)
        chips.addWidget(self.verdict_chip)
        chips.addStretch(1)
        pv.addLayout(chips)
        change = button("Choose another image…", "link", on_click=self.open_file)
        pv.addWidget(change, 0, Qt.AlignmentFlag.AlignLeft)
        self.left_stack.addWidget(panel)
        left.body.addWidget(self.left_stack)
        splitter.addWidget(left)

        splitter.addWidget(self.build_results(
            "analyse", "Open an image to begin",
            "AIMarkLens reads EXIF, XMP, IPTC and C2PA Content Credentials, checks for AI generation parameters and "
            "hidden LSB metadata, and measures pixel statistics. Your file is opened read-only and its SHA-256 hash "
            "is verified before and after."))
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 900])
        self.root.addWidget(splitter, 1)
        self.setAcceptDrops(True)
        self.update_actions()

    # ------------------------------------------------------------ drag and drop anywhere on the page

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            event.acceptProposedAction()
            self.load_path(urls[0])

    # ------------------------------------------------------------ actions

    def protected_paths(self) -> list[str]:
        return [self.path] if self.path else []

    def update_actions(self) -> None:
        busy = self.runner.busy
        self.open_btn.setEnabled(not busy)
        self.analyse_btn.setEnabled(not busy and self.path is not None)
        self.analyse_btn.setText("Analyse again" if self.document is not None else "Analyse")
        self.export_btn.setEnabled(self.document is not None and not self.export_runner.busy)
        self.copy_btn.setEnabled(not busy and self.document is not None)
        self.verify_btn.setEnabled(not busy and self.document is not None)
        self.drop.choose.setEnabled(not busy)

    def open_file(self) -> None:
        if self.runner.busy:
            self.toast("info", "Please wait", "Finish or cancel the current analysis first (Esc).")
            return
        self.drop.start_dir = self.window_.store.last_dir("image")
        self.drop.browse()

    def load_path(self, path: str) -> None:
        if self.runner.busy:
            self.toast("info", "Please wait", "Finish or cancel the current analysis first (Esc).")
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            self.banner.show_message("warning", "Unsupported file type",
                                     f"'{os.path.basename(path)}' is not a PNG, JPEG or WebP image.",
                                     actions=[("Choose another…", self.open_file)])
            return
        self.window_.store.set_last_dir("image", path)
        self.path = path
        self.clear_document()
        self.integrity_chip.set("")
        self.verdict_chip.set("")
        self.name_label.setText(os.path.basename(path))
        self.info_label.setText("Loading preview…")
        self.preview.set_image(None, "Loading preview…")
        self.left_stack.setCurrentIndex(1)
        fade_in(self.left_stack.currentWidget(), 180)
        self.preview_runner.run("Loading preview", lambda ctx: load_preview(path, ctx), self._preview_ready)
        self.update_actions()
        if self.settings.auto_analyse:
            self.analyse()

    def _preview_ready(self, result) -> None:
        image, info = result
        self.preview.set_image(image)
        fade_in(self.preview, 220)
        self.info_label.setText(f"{info['format']} · {info['width']:,} × {info['height']:,} · "
                                f"{info['mode']} · {info['size'] / 1024:,.0f} KB")

    def _preview_failed(self, message: str, hint: str, _details: str) -> None:
        self.preview.set_image(None, "Preview unavailable")
        self.info_label.setText(f"{message} {hint}".strip())

    def analyse(self) -> None:
        if self.path is None:
            self.open_file()
            return
        path, options = self.path, self.settings.image_options()
        from AIMarkLens.engines.image.analyser import analyse_image

        self.start_task("Analysing image", lambda ctx: analyse_image(path, options, ctx), self._analysed,
                        retry=self.analyse)

    def _analysed(self, doc) -> None:
        self.show_document(doc)
        ok = doc.extra.get("integrity_ok", False)
        self.integrity_chip.set("Original unchanged" if ok else "File changed", "ok" if ok else "error")
        verdict = doc.verdict
        self.verdict_chip.set({"signal": "Signals found", "info": "Origin not determined", "warning": "Check findings"}.get(
            verdict.tone, verdict.tone), verdict.tone)
        self.toast("ok", "Analysis complete", verdict.headline)
        if self.settings.auto_online and self.online_services(quiet=True):
            QTimer.singleShot(0, lambda: self.verify_online(auto=True))

    def verify_online(self, auto: bool = False) -> None:
        if self.runner.busy:
            return
        if self.path is None or self.document is None:
            self.toast("info", "Analyse an image first", "Open an image, then verify it online.")
            return
        services = self.online_services(quiet=auto)
        if not services or (not auto and not self.confirm_upload([self.path], services)):
            return
        import dataclasses

        from AIMarkLens.engines.online.verifiers import verify_image_online

        path, cfg, record = self.path, dataclasses.replace(self.settings.online), self.settings.history_path
        self.start_task("Verifying online", lambda ctx: verify_image_online(path, services, cfg, ctx, record),
                        self._verified, retry=self.verify_online)

    def _verified(self, report) -> None:
        from AIMarkLens.engines.online.report import TAB, merge_into
        from AIMarkLens.engines.online.verifiers import DETECTED, ERROR

        if self.document is None or report.path != self.path:
            return
        merge_into(self.document, report)
        self.show_document(self.document)
        self.report_view.select_tab(TAB)
        verdict = self.document.verdict
        self.verdict_chip.set("Signals found" if verdict.tone == "signal" else "Origin not determined", verdict.tone)
        detected = [r for r in report.results if r.status == DETECTED]
        failed = [r for r in report.results if r.status == ERROR]
        if any(r.kind == "synthid" for r in detected):
            tone, title = "signal", "SynthID watermark detected"
        elif detected:
            tone, title = "signal", f"{len(detected)} of {len(report.results)} detectors flagged AI generation"
        else:
            tone, title = "ok", "No online detector flagged AI generation"
        message = "Details are in the Online verification tab."
        if failed:
            tone = "warning" if not detected else tone
            message = f"{len(failed)} of {len(report.results)} check(s) failed. {message}"
        self.toast(tone, title, message)

    def export_copy(self) -> None:
        if not features().metadata_copy or self.path is None or self.document is None:
            return
        from AIMarkLens.engines.image.export import default_copy_name

        present = set(self.document.extra.get("metadata_groups", ()))
        dialog = MetadataCopyDialog(self, present)
        if dialog.exec() != MetadataCopyDialog.DialogCode.Accepted:
            return
        remove = dialog.selected()
        ext = os.path.splitext(self.path)[1]
        out, _ = self.choose_save_path("Save metadata-free copy", default_copy_name(self.path),
                                       f"Same format (*{ext});;All files (*.*)")
        if not out:
            return
        source, record = self.path, self.settings.history_path

        def work(ctx):
            from AIMarkLens.engines.image.export import export_without_metadata

            return export_without_metadata(source, out, remove, ctx, record)

        def done(result) -> None:
            tone = "ok" if (result.original_unchanged and result.image_data_identical and not result.remaining) else "warning"
            online = self.online_services(quiet=True) is not None
            actions = [("Compare online" if online else "Compare with original",
                        lambda: self.window_.compare_files(source, result.output_path, online=online)),
                       ("Open copy", lambda: self.load_path(result.output_path)),
                       ("Show in folder", lambda: open_folder(result.output_path))]
            self.toast(tone, "Metadata-free copy saved", result.summary(), actions)

        self.start_task("Saving metadata-free copy", work, done, retry=None)

