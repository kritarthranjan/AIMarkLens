"""Experiments: controlled robustness studies on synthetic or authorised samples."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDoubleSpinBox, QFormLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from AIMarkLens.formats.containers import SUPPORTED_EXTENSIONS
from AIMarkLens.ui.motion import fade_in
from AIMarkLens.ui.pages.base import PageBase, open_folder
from AIMarkLens.ui.pages.image_page import IMAGE_FILTER
from AIMarkLens.ui.theme import mono_font
from AIMarkLens.ui.widgets import Card, Chip, DropZone, SegmentedControl, button, label

AUTHORISED_NOTE = ("Use only synthetic samples or content you own or are authorised to test. Transformations run on "
                   "in-memory copies; originals are never modified.")


class ExperimentsPage(PageBase):
    title = "Experiments"

    def __init__(self, window) -> None:
        super().__init__(window)
        self.sample_path: Optional[str] = None
        actions = self.build_header("Experiments", "Study how compression, resizing, editing and evasion tricks "
                                                   "affect watermark and provenance detection.")
        self.mode_switch = SegmentedControl(["Image robustness", "Text watermark"])
        self.mode_switch.currentChanged.connect(self._mode_changed)
        actions.addWidget(self.mode_switch)
        self.export_btn = button("Export report", icon_name="export", tooltip="Export the results (Ctrl+E)",
                                 on_click=self.export_report)
        self.run_btn = button("Run experiment", "primary", "flask", "Run the experiment (F5)", on_click=self.analyse)
        actions.addWidget(self.export_btn)
        actions.addWidget(self.run_btn)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        self.config_stack = QStackedWidget()
        self.config_stack.addWidget(self._image_config())
        self.config_stack.addWidget(self._text_config())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.config_stack)
        scroll.setMinimumWidth(330)
        splitter.addWidget(scroll)
        splitter.addWidget(self.build_results(
            "flask", "Set up an experiment",
            "Image robustness applies common transformations to a working copy and records which signals survive. "
            "The text watermark lab generates synthetic watermarked text, attacks it and measures detection."))
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 900])
        self.root.addWidget(splitter, 1)
        self.update_actions()

    # ------------------------------------------------------------ configuration panels

    def _image_config(self) -> QWidget:
        from AIMarkLens.engines.image.experiments import TRANSFORMS
        from AIMarkLens.engines.image.labmark import DEFAULT_STRENGTH

        card = Card(padding=14, spacing=10)
        card.body.addWidget(label("Sample", "h3"))
        self.sample_drop = DropZone("Drop a sample image", "PNG, JPEG or WebP", SUPPORTED_EXTENSIONS, IMAGE_FILTER,
                                    "image", compact=True)
        self.sample_drop.fileChosen.connect(self.set_sample)
        card.body.addWidget(self.sample_drop)
        self.sample_label = label("No sample chosen", "muted", wrap=True)
        card.body.addWidget(self.sample_label)

        card.body.addWidget(label("Lab watermark", "h3"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.key_edit = QLineEdit(self.settings.lab_key)
        self.key_edit.setAccessibleName("Lab watermark key")
        self.key_edit.setToolTip("The secret key used to embed and detect the lab watermark")
        form.addRow("Key", self.key_edit)
        self.strength_spin = QDoubleSpinBox()
        self.strength_spin.setRange(4.0, 30.0)
        self.strength_spin.setSingleStep(1.0)
        self.strength_spin.setValue(DEFAULT_STRENGTH)
        self.strength_spin.setAccessibleName("Embedding strength")
        self.strength_spin.setToolTip("Higher is more robust but more visible (10 gives about 45 dB PSNR)")
        form.addRow("Strength", self.strength_spin)
        card.body.addLayout(form)
        self.embed_btn = button("Embed into a copy…", icon_name="watermark",
                                tooltip="Save a watermarked copy of the sample and use it for the experiment",
                                on_click=self.embed_copy)
        card.body.addWidget(self.embed_btn)

        card.body.addWidget(label("Transformations", "h3"))
        self.transform_list = QListWidget()
        self.transform_list.setAccessibleName("Transformations to apply")
        for t in TRANSFORMS:
            item = QListWidgetItem(t.label)
            item.setData(Qt.ItemDataRole.UserRole, t.key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.transform_list.addItem(item)
        self.transform_list.setMinimumHeight(250)
        self.transform_list.itemChanged.connect(lambda _item: self.update_actions())
        card.body.addWidget(self.transform_list)
        row = QHBoxLayout()
        row.addWidget(button("Select all", "ghost", on_click=lambda: self._check_all(True)))
        row.addWidget(button("Select none", "ghost", on_click=lambda: self._check_all(False)))
        row.addStretch(1)
        card.body.addLayout(row)
        card.body.addWidget(label(AUTHORISED_NOTE, "caption", wrap=True))
        card.body.addStretch(1)
        return card

    def _text_config(self) -> QWidget:
        from AIMarkLens.engines.text.greenlist import DEFAULT_DELTA, DEFAULT_GAMMA

        card = Card(padding=14, spacing=10)
        card.body.addWidget(label("Synthetic watermark", "h3"))
        card.body.addWidget(label("Text is generated from a built-in neutral corpus with a keyed green-list bias, "
                                  "then attacked and re-tested.", "muted", wrap=True))
        form = QFormLayout()
        self.text_key = QLineEdit(self.settings.text_key)
        self.text_key.setAccessibleName("Text watermark key")
        form.addRow("Key", self.text_key)
        self.gamma_spin = QDoubleSpinBox()
        self.gamma_spin.setRange(0.1, 0.5)
        self.gamma_spin.setSingleStep(0.05)
        self.gamma_spin.setValue(DEFAULT_GAMMA)
        self.gamma_spin.setToolTip("Fraction of word pairs that are 'green' by chance")
        self.gamma_spin.setAccessibleName("Gamma")
        form.addRow("Gamma", self.gamma_spin)
        self.delta_spin = QDoubleSpinBox()
        self.delta_spin.setRange(0.25, 5.0)
        self.delta_spin.setSingleStep(0.25)
        self.delta_spin.setValue(DEFAULT_DELTA)
        self.delta_spin.setToolTip("How strongly generation favours green words")
        self.delta_spin.setAccessibleName("Bias")
        form.addRow("Bias", self.delta_spin)
        self.words_spin = QSpinBox()
        self.words_spin.setRange(40, 2000)
        self.words_spin.setSingleStep(20)
        self.words_spin.setValue(200)
        self.words_spin.setAccessibleName("Words per sample")
        form.addRow("Words per sample", self.words_spin)
        self.trials_spin = QSpinBox()
        self.trials_spin.setRange(1, 200)
        self.trials_spin.setValue(20)
        self.trials_spin.setAccessibleName("Trials")
        form.addRow("Trials", self.trials_spin)
        card.body.addLayout(form)
        row = QHBoxLayout()
        self.generate_btn = button("Generate sample", icon_name="text", on_click=self.generate_sample)
        row.addWidget(self.generate_btn)
        self.copy_sample_btn = button("Copy", "ghost", "paste", "Copy the sample to the clipboard",
                                      on_click=self._copy_sample)
        row.addWidget(self.copy_sample_btn)
        row.addStretch(1)
        card.body.addLayout(row)
        self.sample_score = Chip()
        card.body.addWidget(self.sample_score)
        self.sample_text = QPlainTextEdit()
        self.sample_text.setReadOnly(True)
        self.sample_text.setFont(mono_font(9))
        self.sample_text.setPlaceholderText("Generate a sample to see synthetic watermarked text.")
        self.sample_text.setMinimumHeight(180)
        self.sample_text.setAccessibleName("Generated sample")
        card.body.addWidget(self.sample_text, 1)
        card.body.addWidget(label("Paste the sample into Text Lab to see how the statistical test reports it.",
                                  "caption", wrap=True))
        return card

    # ------------------------------------------------------------ behaviour

    @property
    def mode(self) -> str:
        return "image" if self.mode_switch.current() == 0 else "text"

    def _mode_changed(self, index: int) -> None:
        self.config_stack.setCurrentIndex(index)
        fade_in(self.config_stack.currentWidget(), 150)
        self.clear_document()

    def _check_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.transform_list.count()):
            self.transform_list.item(i).setCheckState(state)

    def _selected_transforms(self) -> list[str]:
        return [self.transform_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.transform_list.count())
                if self.transform_list.item(i).checkState() == Qt.CheckState.Checked]

    def protected_paths(self) -> list[str]:
        return [self.sample_path] if self.sample_path else []

    def update_actions(self) -> None:
        busy = self.runner.busy
        if self.mode == "image":
            self.run_btn.setEnabled(not busy and self.sample_path is not None and bool(self._selected_transforms()))
        else:
            self.run_btn.setEnabled(not busy)
        self.embed_btn.setEnabled(not busy and self.sample_path is not None)
        self.generate_btn.setEnabled(not busy)
        self.export_btn.setEnabled(self.document is not None and not self.export_runner.busy)
        self.mode_switch.setEnabled(not busy)

    def set_sample(self, path: str) -> None:
        if os.path.splitext(path)[1].lower() not in SUPPORTED_EXTENSIONS:
            self.toast("warning", "Unsupported file type", "Choose a PNG, JPEG or WebP image.")
            return
        self.sample_path = path
        self.sample_label.setText(f"Sample: {os.path.basename(path)}")
        self.clear_document()
        self.update_actions()

    def open_file(self) -> None:
        if self.mode == "image":
            self.sample_drop.browse()

    def analyse(self) -> None:
        if self.mode == "image":
            if self.sample_path is None:
                self.toast("info", "Choose a sample first", "Drop or choose an image to experiment on.")
                return
            from AIMarkLens.engines.image.experiments import run_image_experiment

            path, keys, key = self.sample_path, self._selected_transforms(), self.key_edit.text().strip()
            self.start_task("Running image experiment", lambda ctx: run_image_experiment(path, keys, key, ctx),
                            self._done, retry=self.analyse)
        else:
            from AIMarkLens.engines.text.experiments import run_text_experiment

            args = (self.text_key.text().strip() or "AIMarkLens-demo", self.gamma_spin.value(), self.delta_spin.value(),
                    self.words_spin.value(), self.trials_spin.value())
            self.start_task("Running text experiment", lambda ctx: run_text_experiment(*args, ctx), self._done,
                            retry=self.analyse)

    def _done(self, doc) -> None:
        self.show_document(doc)
        self.toast("ok", "Experiment complete", "Results are ready to review or export.")

    def embed_copy(self) -> None:
        if self.sample_path is None:
            return
        key = self.key_edit.text().strip()
        if not key:
            self.toast("warning", "A key is required", "Enter a lab watermark key first.")
            self.key_edit.setFocus()
            return
        stem = os.path.splitext(os.path.basename(self.sample_path))[0]
        out, _ = self.choose_save_path("Save watermarked copy", f"{stem}-labmark.png", "PNG image (*.png)")
        if not out:
            return
        source, strength = self.sample_path, self.strength_spin.value()

        def work(ctx):
            from AIMarkLens.engines.image.experiments import embed_into_copy

            return embed_into_copy(source, out, key, strength, ctx)

        def done(result) -> None:
            tone = "ok" if result.detection.detected and result.original_unchanged else "warning"
            self.toast(tone, "Watermarked copy saved",
                       f"PSNR {result.psnr:.1f} dB; check: {result.detection.describe()}. "
                       "The copy is now the experiment sample.",
                       [("Show in folder", lambda: open_folder(result.output_path))])
            self.set_sample(result.output_path)

        self.start_task("Embedding lab watermark", work, done, retry=None)

    def generate_sample(self) -> None:
        from AIMarkLens.engines.text.experiments import generate_sample

        args = (self.text_key.text().strip() or "AIMarkLens-demo", self.gamma_spin.value(), self.delta_spin.value(),
                self.words_spin.value())

        def done(result) -> None:
            text, score = result
            self.sample_text.setPlainText(text)
            fade_in(self.sample_text, 160)
            self.sample_score.set(f"z = {score.z:.2f} · {'detected' if score.detected else 'not detected'}",
                                  "ok" if score.detected else "warning")

        self.start_task("Generating sample", lambda ctx: generate_sample(*args), done)

    def _copy_sample(self) -> None:
        text = self.sample_text.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.toast("ok", "Sample copied", "Paste it into Text Lab with Ctrl+Shift+V.")
