"""Main window: navigation rail, module pages, status bar, shortcuts and toasts."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow, QSizePolicy, QStackedWidget, QStatusBar,
                               QToolButton, QVBoxLayout, QWidget)

from AIMarkLens import APP_NAME, APP_TAGLINE, APP_VERSION, PRINCIPLE
from AIMarkLens.edition import edition_name, features
from AIMarkLens.ui import icons
from AIMarkLens.ui.dialogs import ShortcutsDialog
from AIMarkLens.ui.motion import Motion, fade_in
from AIMarkLens.ui.pages.compare_page import ComparePage
from AIMarkLens.ui.pages.image_page import ImagePage
from AIMarkLens.ui.pages.settings_page import SettingsPage
from AIMarkLens.ui.pages.text_page import TextPage
from AIMarkLens.ui.settings_store import SettingsStore
from AIMarkLens.ui.theme import C
from AIMarkLens.ui.tasks import TaskRunner
from AIMarkLens.ui.toast import ToastHost
from AIMarkLens.ui.widgets import Chip, label

COLLAPSE_BELOW = 1120


def navigation() -> tuple[tuple[str, str, str, str], ...]:
    """(key, label, icon, shortcut) for each page in this edition."""
    pages = [("image", "Image Lab", "image", "Ctrl+1"), ("text", "Text Lab", "text", "Ctrl+2"),
             ("compare", "Compare", "compare", "Ctrl+3")]
    if features().experiments:
        pages.append(("experiments", "Experiments", "flask", "Ctrl+4"))
    pages.append(("settings", "Settings", "settings", "Ctrl+,"))
    return tuple(pages)


class NavRail(QFrame):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.setObjectName("Rail")
        self.window_ = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 14)
        layout.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        self.logo = QLabel()
        self.logo.setPixmap(QPixmap.fromImage(icons.logo_image(64)).scaled(
            30, 30, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(self.logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.brand_name = label(APP_NAME, "brand")
        self.brand_tag = label(edition_name(), "caption")
        names.addWidget(self.brand_name)
        names.addWidget(self.brand_tag)
        brand.addLayout(names, 1)
        layout.addLayout(brand)
        layout.addSpacing(18)
        self.buttons: list[QToolButton] = []
        self.nav = navigation()
        for index, (_key, text, icon_name, keys) in enumerate(self.nav):
            btn = QToolButton()
            btn.setObjectName("NavButton")
            btn.setText(f"  {text}")
            btn.setIcon(icons.icon(icon_name, C["muted"], 20, C["text"]))
            btn.setIconSize(QSize(20, 20))
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setToolTip(f"{text} ({keys})")
            btn.setAccessibleName(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            btn.clicked.connect(lambda _=False, i=index: window.switch_to(i))
            if text == "Settings":
                layout.addStretch(1)
            layout.addWidget(btn)
            self.buttons.append(btn)
        layout.addSpacing(10)
        self.principle = label(PRINCIPLE, "caption", wrap=True)
        layout.addWidget(self.principle)
        self.version = label(f"Version {APP_VERSION}", "caption")
        layout.addWidget(self.version)
        self.collapsed = False
        self.set_collapsed(False)

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = collapsed
        self.setFixedWidth(68 if collapsed else 224)
        for btn, (_key, text, _icon, _keys) in zip(self.buttons, self.nav):
            btn.setText("" if collapsed else f"  {text}")
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if collapsed
                                   else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for widget in (self.brand_name, self.brand_tag, self.principle, self.version):
            widget.setVisible(not collapsed)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = SettingsStore()
        self.settings = self.store.load()
        Motion.reduced = self.settings.reduce_motion
        self.setWindowTitle(f"{APP_NAME}: {APP_TAGLINE}")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(960, 640)

        central = QWidget()
        central.setObjectName("Content")
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.rail = NavRail(self)
        row.addWidget(self.rail)
        self.stack = QStackedWidget()
        row.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.toasts = ToastHost(self)
        factories = {"image": ImagePage, "text": TextPage, "compare": ComparePage, "settings": SettingsPage}
        if features().experiments:
            from AIMarkLens.ui.pages.experiments_page import ExperimentsPage  # Personal Edition only

            factories["experiments"] = ExperimentsPage
        self.page_by_key = {}
        self.pages = []
        for key, *_rest in self.rail.nav:
            page = factories[key](self)
            self.page_by_key[key] = page
            self.pages.append(page)
            self.stack.addWidget(page)

        status = QStatusBar()
        status.setSizeGripEnabled(True)
        self.status_text = label("Ready", "muted")
        self.status_text.setContentsMargins(12, 0, 0, 0)
        status.addWidget(self.status_text, 1)
        self.tool_chips = {"exiftool": Chip("ExifTool: checking", "neutral"),
                           "c2patool": Chip("c2patool: checking", "neutral")}
        self.network_chip = Chip()
        for chip in (*self.tool_chips.values(), self.network_chip):
            status.addPermanentWidget(chip)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda: self.open_settings())
        self.setStatusBar(status)
        self._tool_offer_shown = False
        self._connection = None  # None = not checked yet
        self.net_runner = TaskRunner(self)
        self.update_network_chip()
        self._net_timer = QTimer(self)
        self._net_timer.setInterval(120_000)
        self._net_timer.timeout.connect(self.check_connection)
        self._net_timer.start()
        QTimer.singleShot(200, self.check_connection)

        self._shortcuts()
        self._restore_geometry()
        self.switch_to(0, animate=False)
        self.stack.setFocus()  # no focus ring on start-up; Tab moves into the page

    # ------------------------------------------------------------ navigation

    def current_page(self):
        return self.stack.currentWidget()

    def switch_to(self, index: int, animate: bool = True) -> None:
        if index == self.stack.currentIndex() and self.rail.buttons[index].isChecked():
            return
        self.stack.setCurrentIndex(index)
        self.rail.buttons[index].setChecked(True)
        page = self.pages[index]
        self.setWindowTitle(f"{page.title}: {APP_NAME} {edition_name()}")
        if animate:
            fade_in(page, 160)

    def _shortcuts(self) -> None:
        def add(keys, handler) -> None:
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)

        for index, (_key, _text, _icon, keys) in enumerate(self.rail.nav):
            add(keys, lambda i=index: self.switch_to(i))
        add("Ctrl+O", lambda: self.current_page().open_file())
        add("F5", lambda: self.current_page().analyse())
        add("Ctrl+Return", lambda: self.current_page().analyse())
        add("Ctrl+Enter", lambda: self.current_page().analyse())
        add("Esc", self._escape)
        add("Ctrl+E", lambda: self.current_page().export_report())
        add("Ctrl+Shift+V", lambda: self.current_page().paste())
        add("Ctrl+Shift+O", self._verify_shortcut)
        add("F1", self.show_shortcuts)
        add("Ctrl+Q", self.close)

    def _escape(self) -> None:
        page = self.current_page()
        if page.runner.busy:
            page.cancel()
        elif self.toasts.toasts:
            self.toasts.toasts[-1].dismiss()

    def show_shortcuts(self) -> None:
        ShortcutsDialog(self).exec()

    def switch_to_key(self, key: str) -> None:
        self.switch_to(self.pages.index(self.page_by_key[key]))

    def _verify_shortcut(self) -> None:
        self.switch_to_key("image")
        self.page_by_key["image"].verify_online()

    def open_settings(self, online: bool = False) -> None:
        self.switch_to_key("settings")
        if online:
            QTimer.singleShot(50, self.page_by_key["settings"].scroll_to_online)

    def compare_files(self, a: str, b: str, online: bool = False) -> None:
        self.switch_to_key("compare")
        self.page_by_key["compare"].load_and_compare(a, b, online)

    # ------------------------------------------------------------ status

    def set_status(self, text: str) -> None:
        self.status_text.setText(text)

    def update_tool_status(self, statuses) -> None:
        missing = []
        for name, status in statuses.items():
            chip = self.tool_chips[name]
            label_text = "ExifTool" if name == "exiftool" else "c2patool"
            chip.set(f"{label_text}: {'ready' if status.available else 'not installed'}",
                     "ok" if status.available else "warning")
            chip.setToolTip(f"{status.path or status.message}\nClick to open Settings.")
            if not status.available:
                missing.append(name)
        if missing and not self._tool_offer_shown and self.settings.offer_tool_install and self.settings.allow_network:
            self._tool_offer_shown = True
            names = " and ".join("ExifTool" if n == "exiftool" else "c2patool" for n in missing)
            self.toasts.show("info", f"{names} {'is' if len(missing) == 1 else 'are'} not installed",
                             "They add thousands of metadata tags and cryptographic checks of Content Credentials. "
                             "AIMarkLens can download and verify the official releases for you.",
                             [("Install now", lambda: (self.open_settings(),
                                                       self.page_by_key["settings"].install_missing(missing))),
                              ("Don't ask again", self._stop_tool_offer)], timeout=None)

    def _stop_tool_offer(self) -> None:
        self.settings.offer_tool_install = False
        self.save_settings()

    def check_connection(self) -> None:
        if not self.settings.allow_network:
            self._connection = None
            self.update_network_chip()
            return
        if self.net_runner.busy:
            return
        from AIMarkLens.core.net import check_connectivity

        self.net_runner.run("Checking connection", lambda ctx: check_connectivity(), self._connection_checked)

    def _connection_checked(self, result) -> None:
        self._connection = result
        self.update_network_chip()

    def update_network_chip(self) -> None:
        if not self.settings.allow_network:
            text, tone, tip = "Network off", "neutral", "Online features are turned off. Click to open Settings."
        elif self._connection is None:
            text, tone, tip = "Checking connection…", "neutral", "Checking the internet connection"
        elif self._connection.online:
            text, tone, tip = "Online", "ok", "Connected. Online verification and downloads are available."
        else:
            text, tone, tip = "No internet", "warning", self._connection.detail + ". Retrying every two minutes."
        self.network_chip.set(text, tone)
        self.network_chip.setToolTip(tip)
        settings_page = getattr(self, "page_by_key", {}).get("settings")
        if settings_page is not None:
            settings_page.set_connection(text, tone)

    def save_settings(self) -> None:
        was_online = getattr(self, "_saved_network", None)
        self.store.save(self.settings)
        self._saved_network = self.settings.allow_network
        if was_online is not None and was_online != self.settings.allow_network:
            self._connection = None
            self.check_connection()
        self.update_network_chip()

    # ------------------------------------------------------------ window lifecycle

    def _restore_geometry(self) -> None:
        geometry = self.store.geometry()
        if geometry is not None and self.restoreGeometry(geometry):
            return
        screen = self.screen().availableGeometry()
        width = max(self.minimumWidth(), min(1440, int(screen.width() * 0.82)))
        height = max(self.minimumHeight(), min(920, int(screen.height() * 0.86)))
        self.resize(width, height)
        self.move(screen.center().x() - width // 2, screen.center().y() - height // 2)

    def resizeEvent(self, event) -> None:
        collapse = self.width() < COLLAPSE_BELOW
        if collapse != self.rail.collapsed:
            self.rail.set_collapsed(collapse)
        super().resizeEvent(event)

    def closeEvent(self, event) -> None:
        self.store.set_geometry(self.saveGeometry())
        for page in self.pages:
            for runner in (page.runner, page.export_runner):
                runner.cancel()
        QThreadPool.globalInstance().waitForDone(2000)
        super().closeEvent(event)
