"""Settings: external tools, network, online verification, output, analysis options and accessibility."""

from __future__ import annotations

import dataclasses
import os

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget)

from AIMarkLens import APP_NAME, APP_TAGLINE, APP_VERSION, PRINCIPLE
from AIMarkLens.core import tools
from AIMarkLens.edition import edition_name, features
from AIMarkLens.ui.motion import Motion
from AIMarkLens.ui.pages.base import PageBase, open_folder
from AIMarkLens.ui.tasks import TaskRunner
from AIMarkLens.ui.widgets import Card, Chip, button, label

TOOL_LINKS = {
    "exiftool": ("Website", "https://exiftool.org/"),
    "c2patool": ("Website", "https://github.com/contentauth/c2pa-rs/releases"),
}
GOOGLE_AUTH = (("adc", "Application Default Credentials (gcloud auth application-default login)"),
               ("service_account", "Service-account key file (JSON)"),
               ("gcloud", "Google Cloud CLI token (gcloud auth login)"))
REGIONS = ("us-central1", "us-east4", "europe-west4", "europe-west2", "europe-west1", "asia-northeast1", "global")


def _secret_edit(value: str, name: str) -> QLineEdit:
    edit = QLineEdit(value)
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setPlaceholderText("Not set")
    edit.setAccessibleName(name)
    edit.setToolTip("Stored encrypted for your Windows account only")
    return edit


class SettingsPage(PageBase):
    title = "Settings"

    def __init__(self, window) -> None:
        super().__init__(window)
        self.install_runner = TaskRunner(self)
        self.install_runner.progressed.connect(self._install_progress)
        self.install_runner.failed.connect(self._install_failed)
        self.service_runner = TaskRunner(self)
        self.service_runner.failed.connect(lambda m, h, d: self.toast("error", m, h))
        self._installing = ""
        self._install_queue: list[str] = []
        self._loading = True  # no saving while the widgets are being filled in
        self.build_header("Settings", "Changes are saved automatically.")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.column = QVBoxLayout(content)
        self.column.setContentsMargins(0, 0, 8, 8)
        self.column.setSpacing(14)
        self._tools_card()
        self._network_card()
        self._online_card()
        self._output_card()
        self._analysis_card()
        self._about_cards()
        self.column.addStretch(1)
        scroll.setWidget(content)
        self.scroll = scroll
        self.root.addWidget(scroll, 1)
        self.banner = None  # settings has no results area
        self._loading = False
        QTimer.singleShot(300, self.test_tools)

    # ------------------------------------------------------------ cards

    def _tools_card(self) -> None:
        from AIMarkLens.core.installer import user_tools_dir

        card = Card()
        card.body.addWidget(label("External tools", "h2"))
        card.body.addWidget(label(
            "ExifTool reads thousands of extra metadata tags; c2patool cryptographically validates Content "
            "Credentials and, when online, checks signers against the official C2PA trust list. Install downloads "
            "the official release, verifies its SHA-256 checksum and installs it for your Windows account only "
            f"(no administrator rights needed): {user_tools_dir()}", "muted", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(10)
        self.tool_edits: dict[str, QLineEdit] = {}
        self.tool_chips: dict[str, Chip] = {}
        self.install_buttons = {}
        for row, name in enumerate(("exiftool", "c2patool")):
            grid.addWidget(label(tools.TOOL_LABELS[name], "h3"), row, 0)
            edit = QLineEdit(getattr(self.settings, f"{name}_path"))
            edit.setPlaceholderText("Automatic (installed copy, tools folder or PATH)")
            edit.setAccessibleName(f"{tools.TOOL_LABELS[name]} path")
            edit.editingFinished.connect(lambda n=name: self._tool_path_changed(n))
            self.tool_edits[name] = edit
            grid.addWidget(edit, row, 1)
            grid.addWidget(button("Browse…", on_click=lambda n=name: self._browse_tool(n)), row, 2)
            install = button("Install", "primary", "download",
                             f"Download, verify and install the latest {tools.TOOL_LABELS[name]}",
                             on_click=lambda n=name: self.install_tool(n))
            self.install_buttons[name] = install
            grid.addWidget(install, row, 3)
            chip = Chip("Checking…", "neutral")
            self.tool_chips[name] = chip
            grid.addWidget(chip, row, 4)
            text, url = TOOL_LINKS[name]
            grid.addWidget(button(text, "link", on_click=lambda u=url: QDesktopServices.openUrl(QUrl(u))), row, 5)
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)
        row = QHBoxLayout()
        row.addWidget(button("Check again", "ghost", "analyse", on_click=self.test_tools))
        row.addStretch(1)
        card.body.addLayout(row)
        self.column.addWidget(card)

    def _network_card(self) -> None:
        card = Card()
        card.body.addWidget(label("Network", "h2"))
        self.network_box = QCheckBox("Work online: online verification, remote Content Credentials, the C2PA "
                                     "trust list and tool downloads")
        self.network_box.setChecked(self.settings.allow_network)
        self.network_box.toggled.connect(self._save)
        card.body.addWidget(self.network_box)
        row = QHBoxLayout()
        self.connection_chip = Chip("Not checked", "neutral")
        row.addWidget(self.connection_chip)
        row.addWidget(button("Check connection", "ghost", on_click=self.window_.check_connection))
        row.addStretch(1)
        card.body.addLayout(row)
        card.body.addWidget(label("Images are only uploaded when you ask for online verification (or turn on automatic "
                                  "verification below). Downloads of manifests and trust lists send nothing about "
                                  "your files.", "muted", wrap=True))
        self.column.addWidget(card)

    def _online_card(self) -> None:
        o = self.settings.online
        card = Card(spacing=10)
        self.online_card = card
        card.body.addWidget(label("Online verification", "h2"))
        card.body.addWidget(label(
            "Google's verifier reads the SynthID watermark itself. Hive, Sightengine and AI or Not are AI-image "
            "classifiers: they estimate AI generation from the pixels but cannot read SynthID. Each service uses "
            "your own account; keys are stored encrypted for your Windows user.", "muted", wrap=True))
        self.ask_box = QCheckBox("Ask before uploading an image")
        self.ask_box.setChecked(self.settings.ask_before_upload)
        self.ask_box.toggled.connect(self._save)
        card.body.addWidget(self.ask_box)
        self.auto_online_box = QCheckBox("Verify online automatically after each image analysis (uploads every "
                                         "analysed image to the services set up below)")
        self.auto_online_box.setChecked(self.settings.auto_online)
        self.auto_online_box.toggled.connect(self._save)
        card.body.addWidget(self.auto_online_box)
        self.service_chips: dict[str, Chip] = {}

        def header(key: str, title: str, url: str) -> None:
            row = QHBoxLayout()
            row.addWidget(label(title, "h3"))
            chip = Chip("Not set up", "neutral")
            self.service_chips[key] = chip
            row.addWidget(chip)
            row.addStretch(1)
            row.addWidget(button("Test", on_click=lambda k=key: self.test_service(k)))
            row.addWidget(button("Get access", "link", on_click=lambda u=url: QDesktopServices.openUrl(QUrl(u))))
            card.body.addSpacing(4)
            card.body.addLayout(row)

        header("google", "Google SynthID (Vertex AI)", "https://console.cloud.google.com/vertex-ai")
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        self.google_auth = QComboBox()
        for key, text in GOOGLE_AUTH:
            self.google_auth.addItem(text, key)
        self.google_auth.setCurrentIndex(max(0, self.google_auth.findData(o.google_auth)))
        self.google_auth.currentIndexChanged.connect(self._google_auth_changed)
        self.google_auth.setAccessibleName("Google sign-in method")
        form.addRow("Sign-in", self.google_auth)
        self.google_project = QLineEdit(o.google_project)
        self.google_project.setPlaceholderText("your-project-id")
        self.google_project.setAccessibleName("Google Cloud project ID")
        form.addRow("Project ID", self.google_project)
        self.google_region = QComboBox()
        self.google_region.setEditable(True)
        self.google_region.addItems(REGIONS)
        self.google_region.setCurrentText(o.google_location or "us-central1")
        self.google_region.setAccessibleName("Vertex AI region")
        form.addRow("Region", self.google_region)
        key_row = QHBoxLayout()
        self.google_keyfile = QLineEdit(o.google_credentials)
        self.google_keyfile.setPlaceholderText("Path to the service-account JSON key")
        self.google_keyfile.setAccessibleName("Service-account key file")
        key_row.addWidget(self.google_keyfile, 1)
        key_row.addWidget(button("Browse…", on_click=self._browse_keyfile))
        self.keyfile_holder = QWidget()
        self.keyfile_holder.setLayout(key_row)
        key_row.setContentsMargins(0, 0, 0, 0)
        form.addRow("Key file", self.keyfile_holder)
        form.addRow("", label("Google's watermark verifier needs a signed-in Google Cloud account with the Vertex AI "
                              "API enabled; it does not accept API keys.", "caption", wrap=True))
        card.body.addLayout(form)
        self._google_form = form

        header("hive", "Hive", "https://thehive.ai/")
        form = QFormLayout()
        self.hive_key = _secret_edit(o.hive_key, "Hive secret key")
        form.addRow("Secret key", self.hive_key)
        form.addRow("", label("Paste the Secret Key from Hive → API Keys. The Access Key ID is not needed. Older V2 "
                              "project API keys also work.", "caption", wrap=True))
        card.body.addLayout(form)

        header("sightengine", "Sightengine", "https://sightengine.com/")
        form = QFormLayout()
        self.se_user = QLineEdit(o.sightengine_user)
        self.se_user.setPlaceholderText("Not set")
        self.se_user.setAccessibleName("Sightengine API user")
        form.addRow("API user", self.se_user)
        self.se_secret = _secret_edit(o.sightengine_secret, "Sightengine API secret")
        form.addRow("API secret", self.se_secret)
        card.body.addLayout(form)

        header("aiornot", "AI or Not", "https://www.aiornot.com/")
        form = QFormLayout()
        self.aiornot_key = _secret_edit(o.aiornot_key, "AI or Not API key")
        form.addRow("API key", self.aiornot_key)
        card.body.addLayout(form)

        for edit in (self.google_project, self.google_keyfile, self.hive_key, self.se_user,
                     self.se_secret, self.aiornot_key):
            edit.editingFinished.connect(self._save_online)
        self.google_region.currentTextChanged.connect(lambda _t: self._save_online())
        self._google_auth_changed()
        self._refresh_service_chips()
        self.column.addWidget(card)

    def _output_card(self) -> None:
        card = Card()
        card.body.addWidget(label("Output", "h2"))
        row = QHBoxLayout()
        self.output_edit = QLineEdit(self.settings.output_dir)
        self.output_edit.setAccessibleName("Output folder")
        self.output_edit.editingFinished.connect(self._save)
        row.addWidget(self.output_edit, 1)
        row.addWidget(button("Browse…", on_click=self._browse_output))
        row.addWidget(button("Open", icon_name="folder", on_click=self._open_output))
        card.body.addLayout(row)
        card.body.addWidget(label("Reports, metadata-free copies, cleaned text and watermarked samples are suggested "
                                  "here. Originals are never overwritten: every export goes to a separate file.",
                                  "muted", wrap=True))
        self.column.addWidget(card)

    def _analysis_card(self) -> None:
        s = self.settings
        card = Card()
        card.body.addWidget(label("Analysis", "h2"))
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        self.auto_box = QCheckBox("Analyse automatically when a file is opened or text is pasted")
        self.auto_box.setChecked(s.auto_analyse)
        self.auto_box.toggled.connect(self._save)
        form.addRow(self.auto_box)
        self.crop_combo = QComboBox()
        for size in (512, 1024, 2048):
            self.crop_combo.addItem(f"{size} × {size} pixels", size)
        self.crop_combo.setCurrentIndex(max(0, self.crop_combo.findData(s.crop_size)))
        self.crop_combo.currentIndexChanged.connect(self._save)
        self.crop_combo.setAccessibleName("Pixel analysis crop size")
        form.addRow("Pixel analysis area", self.crop_combo)
        form.setRowVisible(self.crop_combo, features().advanced_settings)
        self.key_edit = QLineEdit(s.text_key)
        self.key_edit.setAccessibleName("Text watermark test key")
        self.key_edit.editingFinished.connect(self._save)
        form.addRow("Text lab watermark key", self.key_edit)
        form.setRowVisible(self.key_edit, features().statistical_text)
        self.gamma_spin = QDoubleSpinBox()
        self.gamma_spin.setRange(0.1, 0.5)
        self.gamma_spin.setSingleStep(0.05)
        self.gamma_spin.setValue(s.text_gamma)
        self.gamma_spin.setAccessibleName("Text watermark gamma")
        self.gamma_spin.valueChanged.connect(self._save)
        form.addRow("Text lab gamma", self.gamma_spin)
        form.setRowVisible(self.gamma_spin, features().statistical_text)
        card.body.addLayout(form)
        self.history_box = QCheckBox("Remember analysed images on this computer, so copies and look-alikes of "
                                     "images with AI signals are recognised")
        self.history_box.setChecked(s.remember_history)
        self.history_box.toggled.connect(self._save)
        card.body.addWidget(self.history_box)
        row = QHBoxLayout()
        self.history_label = label("", "caption")
        row.addWidget(self.history_label)
        row.addStretch(1)
        row.addWidget(button("Clear history", "ghost", "clear", "Forget every remembered image",
                             on_click=self._clear_history))
        card.body.addLayout(row)
        self._refresh_history_label()
        self.column.addWidget(card)

    def _about_cards(self) -> None:
        access = Card()
        access.body.addWidget(label("Accessibility", "h2"))
        self.motion_box = QCheckBox("Reduce motion (turn off transitions and slow the activity indicator)")
        self.motion_box.setChecked(self.settings.reduce_motion)
        self.motion_box.toggled.connect(self._save)
        access.body.addWidget(self.motion_box)
        access.body.addWidget(button("Keyboard shortcuts (F1)", icon_name="keyboard",
                                     on_click=self.window_.show_shortcuts), 0, Qt.AlignmentFlag.AlignLeft)
        self.column.addWidget(access)
        about = Card()
        about.body.addWidget(label(f"{APP_NAME} {APP_VERSION} · {edition_name()}", "h2"))
        about.body.addWidget(label(APP_TAGLINE, "muted"))
        about.body.addWidget(label(PRINCIPLE, "h3"))
        about.body.addWidget(label(
            "AIMarkLens reports what it can detect and is explicit about what it cannot. SynthID can only be confirmed "
            "by Google's own verifier, and AI classifiers give probabilities, not proof. 'No signals found' never "
            "means 'not AI-generated'.", "muted", wrap=True))
        self.column.addWidget(about)

    # ------------------------------------------------------------ settings plumbing

    def update_actions(self) -> None:
        busy = self.install_runner.busy
        for btn in self.install_buttons.values():
            btn.setEnabled(not busy and self.settings.allow_network)

    def _on_progress(self, fraction: float, message: str) -> None:
        pass

    def _on_failed(self, message: str, hint: str, details: str) -> None:
        self.toast("error", message, hint, [("Retry", self.test_tools)])

    def _on_cancelled(self) -> None:
        pass

    def scroll_to_online(self) -> None:
        self.scroll.ensureWidgetVisible(self.online_card, 0, 0)
        self.scroll.verticalScrollBar().setValue(self.online_card.y())

    def _save(self, *_args) -> None:
        if self._loading:
            return
        s = self.settings
        s.allow_network = self.network_box.isChecked()
        s.output_dir = self.output_edit.text().strip() or s.output_dir
        s.auto_analyse = self.auto_box.isChecked()
        s.crop_size = int(self.crop_combo.currentData())
        s.text_key = self.key_edit.text().strip() or "AIMarkLens-demo"
        s.text_gamma = float(self.gamma_spin.value())
        s.reduce_motion = self.motion_box.isChecked()
        s.ask_before_upload = self.ask_box.isChecked()
        s.auto_online = self.auto_online_box.isChecked()
        s.remember_history = self.history_box.isChecked()
        Motion.reduced = s.reduce_motion
        self.window_.save_settings()
        self.update_actions()

    def _save_online(self) -> None:
        if self._loading:
            self._refresh_service_chips()
            return
        o = self.settings.online
        o.google_auth = self.google_auth.currentData()
        o.google_project = self.google_project.text().strip()
        o.google_location = self.google_region.currentText().strip() or "us-central1"
        o.google_credentials = self.google_keyfile.text().strip()
        o.hive_key = self.hive_key.text().strip()
        o.sightengine_user = self.se_user.text().strip()
        o.sightengine_secret = self.se_secret.text().strip()
        o.aiornot_key = self.aiornot_key.text().strip()
        self.window_.save_settings()
        self._refresh_service_chips()

    def _google_auth_changed(self, *_args) -> None:
        mode = self.google_auth.currentData()
        self._google_form.setRowVisible(self.keyfile_holder, mode == "service_account")
        self._save_online()

    def _refresh_service_chips(self) -> None:
        from AIMarkLens.engines.online.verifiers import SERVICE_BY_KEY

        for key, chip in self.service_chips.items():
            ready = SERVICE_BY_KEY[key].configured(self.settings.online)
            chip.set("Set up" if ready else "Not set up", "info" if ready else "neutral")
            chip.setToolTip("")

    def _refresh_history_label(self) -> None:
        from AIMarkLens.core import history

        count = history.size(history.default_path())
        self.history_label.setText(f"{count:,} image(s) remembered. Stored only on this computer: file hashes, "
                                   "names and findings, never the images.")

    def _clear_history(self) -> None:
        from AIMarkLens.core import history

        history.clear(history.default_path())
        self._refresh_history_label()
        self.toast("ok", "History cleared", "AIMarkLens no longer remembers earlier analyses.")

    def showEvent(self, event) -> None:
        self._refresh_history_label()
        super().showEvent(event)

    def _browse_keyfile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose the service-account key", "", "JSON key (*.json)")
        if path:
            self.google_keyfile.setText(path)
            self._save_online()

    def _tool_path_changed(self, name: str) -> None:
        setattr(self.settings, f"{name}_path", self.tool_edits[name].text().strip())
        self.window_.save_settings()
        self.test_tools()

    def _browse_tool(self, name: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"Locate {tools.TOOL_LABELS[name]}", "",
                                              "Programs (*.exe);;All files (*.*)")
        if path:
            self.tool_edits[name].setText(path)
            self._tool_path_changed(name)

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose the output folder", self.output_edit.text())
        if path:
            self.output_edit.setText(path)
            self._save()

    def _open_output(self) -> None:
        folder = self.settings.output_dir
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            self.toast("error", "The output folder could not be created", str(exc))
            return
        open_folder(folder)

    # ------------------------------------------------------------ tools

    def test_tools(self) -> None:
        if self.runner.busy:
            return
        for chip in self.tool_chips.values():
            chip.set("Checking…", "neutral")
        paths = {name: getattr(self.settings, f"{name}_path") for name in self.tool_chips}

        def work(ctx):
            return {name: tools.probe(name, path) for name, path in paths.items()}

        def done(statuses) -> None:
            for name, status in statuses.items():
                chip = self.tool_chips[name]
                if status.available:
                    chip.set(f"Ready · {status.version}", "ok")
                    chip.setToolTip(status.path)
                    self.install_buttons[name].setText("Update")
                else:
                    chip.set("Not installed" if not status.path else "Not working", "warning")
                    chip.setToolTip(status.message)
                    self.install_buttons[name].setText("Install")
            self.window_.update_tool_status(statuses)

        self.runner.run("Checking tools", work, done)

    def install_tool(self, name: str) -> None:
        if self.install_runner.busy:
            return
        if not self.settings.allow_network:
            self.toast("warning", "Network access is off", "Turn on 'Work online' to download tools.")
            return
        from AIMarkLens.core.installer import INSTALLERS

        self._installing = name
        self.tool_chips[name].set("Starting download…", "info")
        installer = INSTALLERS[name]

        def done(result) -> None:
            if not getattr(self.settings, f"{name}_path"):
                self.tool_edits[name].setText("")
            self.toast("ok", f"{result.tool} installed", f"{result.describe()}.\n{result.path}")
            self._installing = ""
            self.test_tools()
            if self._install_queue:
                QTimer.singleShot(0, lambda: self.install_tool(self._install_queue.pop(0)))

        self.install_runner.run(f"Installing {tools.TOOL_LABELS[name]}", installer, done)

    def install_missing(self, names: list[str]) -> None:
        """Install several tools one after another (used by the start-up prompt)."""
        if not names or self.install_runner.busy:
            return
        self._install_queue = list(names[1:])
        self.install_tool(names[0])

    def _install_progress(self, fraction: float, message: str) -> None:
        if self._installing:
            self.tool_chips[self._installing].set(f"{message} ({fraction * 100:.0f}%)", "info")

    def _install_failed(self, message: str, hint: str, _details: str) -> None:
        name = self._installing
        self._installing = ""
        self._install_queue = []
        if name:
            self.tool_chips[name].set("Install failed", "error")
            self.tool_chips[name].setToolTip(f"{message} {hint}")
        self.toast("error", f"Installing {tools.TOOL_LABELS.get(name, 'the tool')} failed", f"{message} {hint}",
                   [("Retry", lambda: self.install_tool(name))] if name else [])

    # ------------------------------------------------------------ online services

    def test_service(self, key: str) -> None:
        from AIMarkLens.engines.online.verifiers import SERVICE_BY_KEY, verify_bytes

        self._save_online()
        service = SERVICE_BY_KEY[key]
        if not self.settings.allow_network:
            self.toast("warning", "Network access is off", "Turn on 'Work online' first.")
            return
        if not service.configured(self.settings.online):
            self.toast("info", f"{service.name} is not set up", "Enter its credentials first.")
            return
        if self.service_runner.busy:
            return
        chip = self.service_chips[key]
        chip.set("Testing…", "info")
        cfg = dataclasses.replace(self.settings.online)

        def work(ctx):
            import io

            import numpy as np
            from PIL import Image

            gradient = np.dstack([np.tile(np.linspace(0, 255, 256, dtype=np.uint8), (256, 1))] * 3)
            buf = io.BytesIO()
            Image.fromarray(gradient).save(buf, "PNG")
            return verify_bytes(buf.getvalue(), [key], cfg, ctx)[0]

        def done(result) -> None:
            if result.status == "error":
                chip.set("Failed", "error")
                chip.setToolTip(result.detail)
                self.toast("error", f"{service.name}: test failed", result.detail)
            else:
                chip.set("Working", "ok")
                chip.setToolTip(result.summary)
                self.toast("ok", f"{service.name} is working",
                           f"Test image (a plain gradient): {result.summary}.")

        self.service_runner.run(f"Testing {service.name}", work, done)

    def set_connection(self, text: str, tone: str) -> None:
        self.connection_chip.set(text, tone)
