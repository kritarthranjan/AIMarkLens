"""Application entry point."""

from __future__ import annotations

import sys

APP_MUTEX = "AIMarkLens.SingleInstance"  # the installer uses this to tell when the app is running


def self_test() -> int:
    """Exercise every engine on generated samples; used to verify packaged builds (exit code 0 = healthy)."""
    import io
    import os
    import tempfile

    import numpy as np
    from PIL import Image

    from AIMarkLens.core.context import TaskContext
    from AIMarkLens.edition import PERSONAL_ONLY_MODULES, features
    from AIMarkLens.engines.image.analyser import ImageOptions, analyse_image
    from AIMarkLens.engines.text.analyser import TextOptions, analyse_text
    from AIMarkLens.engines.text.sample import TextSample

    rng = np.random.default_rng(0)
    rgb = (rng.normal(128, 30, (256, 256, 3))).clip(0, 255).astype(np.uint8)
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "self-test.png")
        Image.fromarray(rgb).save(path)
        doc = analyse_image(path, ImageOptions(), TaskContext())
        ok = doc.extra.get("integrity_ok") is True and bool(doc.extra.get("phash"))
        buf = io.BytesIO()
        Image.open(path).convert("RGB").save(buf, "JPEG", quality=80)
    text = analyse_text(TextSample("self​test", "typed", "self-test"), TextOptions(), TaskContext())
    ok &= text.verdict.tone == "signal"

    if features().experiments:
        from AIMarkLens.engines.image import labmark

        marked = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
        ok &= labmark.detect(labmark.embed(np.asarray(marked), "self-test"), "self-test").detected
    elif getattr(sys, "frozen", False):
        # Packaged Public builds must not contain the Personal-only modules at all.
        import importlib.util

        ok &= all(importlib.util.find_spec(name) is None for name in PERSONAL_ONLY_MODULES)

    # Online stack: HTTP client, certificate bundle, Google sign-in libraries and key encryption.
    import certifi
    import google.auth.transport.requests  # noqa: F401
    import google.oauth2.service_account  # noqa: F401

    from AIMarkLens.core import net
    from AIMarkLens.core.secrets import protect, unprotect
    from AIMarkLens.engines.online.verifiers import SERVICES, prepare_upload

    ok &= os.path.isfile(certifi.where()) and net.session() is not None
    ok &= unprotect(protect("self-test")) == "self-test"
    ok &= len(SERVICES) == 4 and prepare_upload(buf.getvalue(), ("PNG",), 10_000_000).mime == "image/png"
    return 0 if ok else 1


def _hold_instance_mutex():
    """Create a named mutex for the lifetime of the app, so Setup can ask the user to close it first."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX)
    except (AttributeError, OSError):
        return None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv if argv is None else argv
    if "--self-test" in args:
        return self_test()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from AIMarkLens import APP_ID, APP_NAME, APP_VERSION
    from AIMarkLens.core.legacy import migrate_previous_install

    mutex = _hold_instance_mutex()  # noqa: F841 - released when the process exits
    migrate_previous_install()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    if sys.platform == "win32":
        try:  # give the taskbar our own icon and grouping instead of python.exe's
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.{APP_VERSION}")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_ID)

    from AIMarkLens.ui.theme import apply_theme

    apply_theme(app)
    from AIMarkLens.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    _close_splash()
    return app.exec()


def _close_splash() -> None:
    """The portable single-file build shows a splash while it unpacks; close it once the window is up."""
    try:
        import pyi_splash  # only exists inside the portable build

        pyi_splash.close()
    except Exception:  # noqa: BLE001 - no splash in source runs or the installed build
        pass


if __name__ == "__main__":
    raise SystemExit(main())
