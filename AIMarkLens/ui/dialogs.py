"""Dialogs for output copies and the keyboard shortcut reference."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout,
                               QWidget)

from AIMarkLens import APP_NAME, APP_VERSION, PRINCIPLE
from AIMarkLens.edition import features
from AIMarkLens.engines.text.cleaning import available_options
from AIMarkLens.ui.theme import C
from AIMarkLens.ui.widgets import Card, label

SHORTCUTS = (
    ("Ctrl+1 … Ctrl+4", "Switch between Image Lab, Text Lab, Compare and (Personal Edition) Experiments"),
    ("Ctrl+,", "Settings"),
    ("Ctrl+O", "Open a file in the current module"),
    ("Ctrl+Shift+V", "Paste text exactly as copied (Text Lab and Compare)"),
    ("F5 or Ctrl+Enter", "Analyse, compare or run the experiment"),
    ("Esc", "Cancel the running task"),
    ("Ctrl+E", "Export the current report"),
    ("Ctrl+Shift+O", "Verify the current image with online detectors"),
    ("Ctrl+C", "Copy selected table rows"),
    ("F1", "Show this list"),
    ("Ctrl+Q", "Quit"),
)


class _OptionsDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], title: str, intro: str, options: list[tuple[str, str, bool]],
                 confirm_text: str = "", accept_text: str = "Continue…") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(label(title, "h2"))
        layout.addWidget(label(intro, "muted", wrap=True))
        card = Card(padding=12, spacing=4)
        self.boxes: dict[str, QCheckBox] = {}
        for key, text, default in options:
            box = QCheckBox(text)
            box.setChecked(default)
            box.toggled.connect(self._validate)
            self.boxes[key] = box
            card.body.addWidget(box)
        layout.addWidget(card)
        self.confirm: Optional[QCheckBox] = None
        if confirm_text:
            self.confirm = QCheckBox(confirm_text)
            self.confirm.toggled.connect(self._validate)
            layout.addWidget(self.confirm)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.buttons.addButton(accept_text, QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok.setProperty("variant", "primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._validate()

    def _validate(self) -> None:
        any_selected = any(box.isChecked() for box in self.boxes.values())
        confirmed = self.confirm is None or self.confirm.isChecked()
        self.ok.setEnabled(any_selected and confirmed)

    def selected(self) -> set[str]:
        return {key for key, box in self.boxes.items() if box.isChecked()}


class MetadataCopyDialog(_OptionsDialog):
    def __init__(self, parent: Optional[QWidget], present: set[str]) -> None:
        from AIMarkLens.engines.image.export import DEFAULT_REMOVE, REMOVABLE

        options = []
        for key, text in REMOVABLE.items():
            suffix = "" if key in present else "  (not present)"
            options.append((key, text + suffix, key in DEFAULT_REMOVE and key in present))
        super().__init__(
            parent, "Save a metadata-free copy",
            "A new file is built from the original's own segments with the selected metadata left out. The "
            "compressed image data is copied byte for byte, so quality and dimensions are unchanged, and the "
            "original is not modified.\n\nThis is metadata management, not a way to make an AI image look "
            "human-made: SynthID and the traces AI detectors look for are in the pixels, which stay identical, so "
            "online detectors will still flag the copy if they flag the original.",
            options,
            confirm_text="I own this image or am authorised to manage its metadata, and I understand that removing "
                         "provenance information can breach platform rules or laws where disclosure is required.",
            accept_text="Choose where to save…")
        for key, box in self.boxes.items():
            if key not in present:
                box.setEnabled(False)


class CleanCopyDialog(_OptionsDialog):
    def __init__(self, parent: Optional[QWidget]) -> None:
        super().__init__(
            parent, "Save a cleaned copy",
            "The selected changes are applied to a separate copy. The original text or file is not modified. "
            "Emoji sequences, flag emoji and normal script joiners are always kept.",
            list(available_options(features().provenance_cleanup)), accept_text="Choose where to save…")


class UploadConsentDialog(QDialog):
    """Explains exactly what will be uploaded, and where, before any image leaves the computer."""

    def __init__(self, parent: Optional[QWidget], files: list[str], services: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Upload for online verification")
        self.setModal(True)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(label("Upload for online verification?", "h2"))
        names = ", ".join(files)
        layout.addWidget(label(f"AIMarkLens will send {names} to these services, using your own accounts:", wrap=True))
        card = Card(padding=12, spacing=4)
        for name in services:
            card.body.addWidget(label(f"•  {name}", wrap=True))
        layout.addWidget(card)
        layout.addWidget(label("The file is sent exactly as it is, including any metadata such as GPS location. "
                               "Each service's own privacy terms apply. Your original file is not changed.",
                               "muted", wrap=True))
        self.remember = QCheckBox("Don't ask again (you can change this in Settings)")
        layout.addWidget(self.remember)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok = box.addButton("Upload and verify", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setProperty("variant", "primary")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        ok.setFocus()


class ShortcutsDialog(QDialog):
    def __init__(self, parent: Optional[QWidget]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME}: keyboard shortcuts")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(label("Keyboard shortcuts", "h2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        for row, (keys, action) in enumerate(SHORTCUTS):
            k = QLabel(keys)
            k.setStyleSheet(f"QLabel {{ background: {C['input']}; border: 1px solid {C['border_strong']}; "
                            f"border-radius: 6px; padding: 3px 8px; font-weight: 600; }}")
            grid.addWidget(k, row, 0, Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(label(action, wrap=True), row, 1)
        layout.addLayout(grid)
        layout.addWidget(label(f"{APP_NAME} {APP_VERSION} · {PRINCIPLE}", "caption"))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        layout.addWidget(box)
