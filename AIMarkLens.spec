# PyInstaller build specification for AIMarkLens. Driven by build.ps1 through two environment variables:
#
#   AIMarkLens_EDITION   public | personal   Public builds leave the Personal-only modules out entirely.
#   AIMarkLens_PORTABLE  1                   one self-contained portable .exe (with a splash screen) instead of
#                                            the application folder that the Setup installer packages.

import os
import sys

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = os.path.abspath(SPECPATH)
sys.path.insert(0, ROOT)
from AIMarkLens import APP_NAME, APP_PUBLISHER, APP_VERSION  # noqa: E402
from AIMarkLens.edition import PERSONAL_ONLY_MODULES, PUBLIC  # noqa: E402

EDITION = os.environ.get("AIMarkLens_EDITION", "personal").lower()
PORTABLE = os.environ.get("AIMarkLens_PORTABLE") == "1"
EDITION_LABEL = "Public Edition" if EDITION == PUBLIC else "Personal Edition"
parts = tuple(int(p) for p in APP_VERSION.split(".")) + (0,)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=parts, prodvers=parts),
    kids=[
        StringFileInfo([StringTable("080904B0", [
            StringStruct("CompanyName", APP_PUBLISHER),
            StringStruct("FileDescription", f"{APP_NAME} {EDITION_LABEL}: AI watermark & provenance lab"),
            StringStruct("FileVersion", APP_VERSION),
            StringStruct("InternalName", APP_NAME),
            StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
            StringStruct("ProductName", f"{APP_NAME} {EDITION_LABEL}"),
            StringStruct("ProductVersion", APP_VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0809, 1200])]),
    ],
)

# Qt modules AIMarkLens never uses; leaving them out keeps the build small and start-up fast.
EXCLUDES = [
    "pytest", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtXml",
    "PySide6.QtDBus", "PySide6.QtConcurrent", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtPrintSupport",
    "PySide6.QtUiTools", "PySide6.QtSvgWidgets", "PySide6.QtSvg",
]
if EDITION == PUBLIC:
    EXCLUDES += list(PERSONAL_ONLY_MODULES)

a = Analysis(
    [os.path.join(ROOT, "run_AIMarkLens.pyw")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "AIMarkLens", "resources"), os.path.join("AIMarkLens", "resources"))],
    hiddenimports=["AIMarkLens._build_info", "google.auth.transport.requests", "google.oauth2.service_account"],
    excludes=EXCLUDES,
    noarchive=False,
)

# Not needed by a widgets app working on PNG/JPEG/WebP stills: OpenCV's FFmpeg video backend, Qt's software
# OpenGL fallback and Direct2D platform plugin, and Pillow's AVIF codec (its plugin degrades gracefully).
DROP = ("opencv_videoio_ffmpeg", "opengl32sw.dll", "qdirect2d.dll", "_avif.")
a.binaries = [b for b in a.binaries if not any(d in b[0].lower() for d in DROP)]
# Keep only the Qt plugins a desktop image tool needs.
KEEP_PLUGIN_DIRS = ("platforms", "styles", "imageformats", "iconengines")
a.datas = [d for d in a.datas
           if "plugins" not in d[0].replace("\\", "/").split("/")[:3]
           or any(f"/{k}/" in d[0].replace("\\", "/") for k in KEEP_PLUGIN_DIRS)]
a.binaries = [b for b in a.binaries
              if "/plugins/" not in b[0].replace("\\", "/")
              or any(f"/{k}/" in b[0].replace("\\", "/") for k in KEEP_PLUGIN_DIRS)]

pyz = PYZ(a.pure)
common = dict(version=version_info, console=False, upx=False)

if PORTABLE:
    splash = Splash(os.path.join(ROOT, "assets", "generated", f"splash-{EDITION}.png"), binaries=a.binaries,
                    datas=a.datas, text_pos=(24, 302), text_size=10, text_color="#8A919C",
                    text_default="Starting…", always_on_top=False)
    name = f"{APP_NAME}-Portable" if EDITION == PUBLIC else f"{APP_NAME}-Personal-Portable"
    exe = EXE(pyz, splash, splash.binaries, a.scripts, a.binaries, a.datas, [], name=name, runtime_tmpdir=None,
              icon=os.path.join(ROOT, "assets", "generated", "portable.ico"), **common)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP_NAME,
              icon=os.path.join(ROOT, "AIMarkLens", "resources", "app-icon.ico"), **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)
