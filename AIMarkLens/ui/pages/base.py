"""Shared page behaviour: header, results area, task lifecycle, error handling and report export."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from AIMarkLens.core.integrity import atomic_write_text
from AIMarkLens.core.report import ReportDocument, render, suggested_filename
from AIMarkLens.ui.motion import fade_in
from AIMarkLens.ui.report_view import ReportView
from AIMarkLens.ui.tasks import TaskRunner
from AIMarkLens.ui.widgets import Banner, EmptyState, TaskOverlay, label

if TYPE_CHECKING:
    from AIMarkLens.ui.main_window import MainWindow

REPORT_FILTERS = {"HTML report (*.html)": "html", "JSON data (*.json)": "json", "Markdown (*.md)": "md"}


def open_folder(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path if os.path.isdir(path) else os.path.dirname(path)))


def open_file(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class PageBase(QWidget):
    title = ""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window_ = window
        self.runner = TaskRunner(self)
        self.export_runner = TaskRunner(self)
        self.document: Optional[ReportDocument] = None
        self._retry: Optional[Callable[[], None]] = None
        self._task_label = ""
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(28, 22, 28, 18)
        self.root.setSpacing(16)
        self.runner.progressed.connect(self._on_progress)
        self.runner.failed.connect(self._on_failed)
        self.runner.cancelled.connect(self._on_cancelled)
        self.runner.busyChanged.connect(lambda _busy: self.update_actions())
        self.export_runner.failed.connect(self._on_export_failed)
        self.export_runner.busyChanged.connect(lambda _busy: self.update_actions())

    # ------------------------------------------------------------ helpers

    @property
    def settings(self):
        return self.window_.settings

    def toast(self, tone: str, title: str, message: str = "", actions: Iterable = ()) -> None:
        self.window_.toasts.show(tone, title, message, actions)

    def build_header(self, title: str, subtitle: str) -> QHBoxLayout:
        header = QVBoxLayout()
        header.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label(title, "h1"))
        row.addStretch(1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        row.addLayout(actions)
        header.addLayout(row)
        header.addWidget(label(subtitle, "muted", wrap=True))
        self.root.addLayout(header)
        return actions

    def build_results(self, icon_name: str, empty_title: str, empty_text: str) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.banner = Banner()
        layout.addWidget(self.banner)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        self.results_stack = QStackedWidget()
        self.empty_state = EmptyState(icon_name, empty_title, empty_text)
        self.report_view = ReportView()
        self.results_stack.addWidget(self.empty_state)
        self.results_stack.addWidget(self.report_view)
        holder_layout.addWidget(self.results_stack)
        layout.addWidget(holder, 1)
        self.overlay = TaskOverlay(holder)
        self.overlay.cancelRequested.connect(self.runner.cancel)
        return container

    def protected_paths(self) -> list[str]:
        """Original files that exports must never overwrite."""
        return []

    # ------------------------------------------------------------ online verification helpers

    def online_services(self, quiet: bool = False) -> Optional[list[str]]:
        """The configured online services, or None (with an explanation) when none can be used."""
        from AIMarkLens.engines.online.verifiers import MANUAL_CHECKS, configured_services

        if not self.settings.allow_network:
            if not quiet:
                self.banner.show_message("warning", "Network access is turned off",
                                         "Turn on 'Work online' in Settings to use online detectors.",
                                         actions=[("Open Settings", self.window_.open_settings)])
            return None
        services = configured_services(self.settings.online)
        if not services and not quiet:
            gemini = MANUAL_CHECKS[0][1]
            self.banner.show_message(
                "info", "No online detectors are set up yet",
                "Add a Google Cloud project (for SynthID) or a key for Hive, Sightengine or AI or Not in "
                "Settings → Online verification. You can also check by hand: upload the image to Gemini and ask "
                "whether it was made with Google AI.",
                actions=[("Set up detectors", lambda: self.window_.open_settings(online=True)),
                         ("Open Gemini", lambda: QDesktopServices.openUrl(QUrl(gemini)))])
        return services or None

    def confirm_upload(self, files: list[str], services: list[str]) -> bool:
        if not self.settings.ask_before_upload:
            return True
        from AIMarkLens.engines.online.verifiers import SERVICE_BY_KEY
        from AIMarkLens.ui.dialogs import UploadConsentDialog

        dialog = UploadConsentDialog(self, [os.path.basename(f) for f in files],
                                     [SERVICE_BY_KEY[k].name for k in services])
        if dialog.exec() != UploadConsentDialog.DialogCode.Accepted:
            return False
        if dialog.remember.isChecked():
            self.settings.ask_before_upload = False
            self.window_.save_settings()
        return True

    # ------------------------------------------------------------ task lifecycle

    def start_task(self, label_text: str, fn: Callable, on_success: Callable[[Any], None],
                   retry: Optional[Callable[[], None]] = None) -> bool:
        if self.runner.busy:
            return False
        self.banner.hide()
        self._retry = retry
        self._task_label = label_text
        started = self.runner.run(label_text, fn, lambda result: self._finish(result, on_success))
        if started:
            self.overlay.start(label_text)
            self.window_.set_status(f"{label_text}…")
        return started

    def _finish(self, result: Any, on_success: Callable[[Any], None]) -> None:
        self.overlay.finish()
        self.window_.set_status("Ready")
        on_success(result)

    def _on_progress(self, fraction: float, message: str) -> None:
        self.overlay.set_progress(fraction, message)

    def _on_failed(self, message: str, hint: str, details: str) -> None:
        self.overlay.finish()
        self.window_.set_status("Last task failed")
        actions = [("Retry", self._retry)] if self._retry else []
        self.banner.show_message("error", message, hint, details, actions)
        if not self.isVisible():
            self.toast("error", f"{self._task_label} failed", message, actions)

    def _on_cancelled(self) -> None:
        self.overlay.finish()
        self.window_.set_status("Cancelled")
        self.toast("info", "Cancelled", "The task was stopped. Nothing was changed.")

    def _on_export_failed(self, message: str, hint: str, details: str) -> None:
        self.banner.show_message("error", message, hint, details)

    def show_document(self, doc: ReportDocument) -> None:
        self.document = doc
        self.report_view.set_document(doc)
        if self.results_stack.currentIndex() != 1:
            self.results_stack.setCurrentIndex(1)
            fade_in(self.report_view, 200)
        self.update_actions()

    def clear_document(self) -> None:
        self.document = None
        self.results_stack.setCurrentIndex(0)
        self.banner.hide()
        self.update_actions()

    # ------------------------------------------------------------ actions (shortcut targets)

    def update_actions(self) -> None:
        pass

    def open_file(self) -> None:
        pass

    def analyse(self) -> None:
        pass

    def paste(self) -> None:
        pass

    def cancel(self) -> None:
        if self.runner.busy:
            self.overlay.set_cancelling()
            self.runner.cancel()

    def choose_save_path(self, caption: str, default_name: str, filters: str) -> tuple[str, str]:
        folder = self.settings.output_dir
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            folder = os.path.expanduser("~")
        return QFileDialog.getSaveFileName(self, caption, os.path.join(folder, default_name), filters)

    def export_report(self) -> None:
        doc = self.document
        if doc is None:
            self.toast("info", "Nothing to export yet", "Run an analysis first, then export its report.")
            return
        if self.export_runner.busy:
            return
        path, chosen = self.choose_save_path("Export report", suggested_filename(doc, "html"), ";;".join(REPORT_FILTERS))
        if not path:
            return
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext not in ("html", "json", "md"):
            ext = REPORT_FILTERS.get(chosen, "html")
            path += f".{ext}"
        protected = self.protected_paths()

        def work(ctx):
            ctx.progress(0.2, "Rendering report")
            text = render(doc, ext)
            ctx.progress(0.7, "Writing report")
            atomic_write_text(path, text, protected)
            return path

        def done(saved: str) -> None:
            self.toast("ok", "Report exported", os.path.basename(saved),
                       [("Open report", lambda: open_file(saved)), ("Show in folder", lambda: open_folder(saved))])

        self.export_runner.run("Exporting report", work, done)
