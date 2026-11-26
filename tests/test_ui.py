import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    from PySide6.QtCore import QSettings

    # Keep test runs out of the user's real settings file.
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path_factory.mktemp("settings")))
    app = QApplication.instance() or QApplication([])
    from AIMarkLens.ui.theme import apply_theme

    apply_theme(app)
    from AIMarkLens.ui.main_window import MainWindow

    win = MainWindow()
    win.settings.auto_analyse = False
    win.settings.remember_history = False  # never touch the real history file
    win.show()
    yield win
    win.close()


def pump(ms=50):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_idle(page, timeout=40):
    end = time.time() + timeout
    pump()
    while page.runner.busy and time.time() < end:
        pump()
    pump(300)


def test_image_analysis_and_duplicate_protection(window, jpeg_with_metadata):
    page = window.pages[0]
    window.switch_to(0)
    page.load_path(jpeg_with_metadata)
    page.analyse()
    assert page.runner.busy and not page.analyse_btn.isEnabled()
    assert page.runner.run("again", lambda ctx: None) is False  # a second task cannot start
    wait_idle(page)
    assert page.document is not None and page.document.verdict.tone == "signal"
    assert page.export_btn.isEnabled() and not page.banner.isVisible()


def test_cancel_and_failure_are_visible(window, jpeg_with_metadata, tmp_path):
    page = window.pages[0]
    page.path = jpeg_with_metadata

    def slow(ctx):
        for i in range(200):
            ctx.progress(i / 200, "Working")
            time.sleep(0.01)

    page.start_task("Slow task", slow, lambda _r: None)
    pump(100)
    page.cancel()
    wait_idle(page)
    assert not page.runner.busy and not page.overlay.isVisible()

    page.path = str(tmp_path / "missing.png")
    page.analyse()
    wait_idle(page)
    assert page.banner.isVisible() and "could not be found" in page.banner.title.text()


def test_text_page_analyses_pasted_sample(window):
    from AIMarkLens.engines.text.sample import TextSample

    page = window.pages[1]
    window.switch_to(1)
    page._set_sample(TextSample("hidden​ text " + chr(0xE0041), "clipboard", "Clipboard text"))
    page.analyse()
    wait_idle(page)
    assert page.document.verdict.tone == "signal"
    assert "⟦ZWSP⟧" in page.reveal.toPlainText()
    page._row_activated("occurrences", 0)
    assert page.reveal.textCursor().selectedText() == "⟦ZWSP⟧"


def test_navigation_shortcut_targets_exist(window):
    for index, page in enumerate(window.pages):
        window.switch_to(index)
        for name in ("open_file", "analyse", "paste", "export_report", "cancel"):
            assert callable(getattr(page, name))
