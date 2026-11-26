"""Persistent user settings (an INI file under the user's AppData folder).

API keys and secrets are encrypted with Windows DPAPI, so only the same Windows
user on the same machine can read them back.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from PySide6.QtCore import QByteArray, QSettings, QStandardPaths

from AIMarkLens import APP_ID
from AIMarkLens.core.secrets import protect, unprotect
from AIMarkLens.engines.image.analyser import ImageOptions
from AIMarkLens.engines.online.verifiers import OnlineConfig
from AIMarkLens.engines.text.analyser import TextOptions

_SECRET_FIELDS = ("hive_key", "sightengine_secret", "aiornot_key")
_ONLINE_FIELDS = ("google_project", "google_location", "google_auth", "google_credentials",
                  "hive_key", "sightengine_user", "sightengine_secret", "aiornot_key")


@dataclass
class AppSettings:
    exiftool_path: str = ""
    c2patool_path: str = ""
    allow_network: bool = True
    output_dir: str = ""
    crop_size: int = 1024
    auto_analyse: bool = True
    reduce_motion: bool = False
    text_key: str = "AIMarkLens-demo"
    text_gamma: float = 0.25
    lab_key: str = "AIMarkLens-lab"
    ask_before_upload: bool = True
    auto_online: bool = False
    offer_tool_install: bool = True
    remember_history: bool = True
    online: OnlineConfig = field(default_factory=OnlineConfig)

    @property
    def history_path(self) -> str:
        from AIMarkLens.core.history import default_path

        return default_path() if self.remember_history else ""

    def image_options(self) -> ImageOptions:
        return ImageOptions(self.exiftool_path, self.c2patool_path, self.allow_network, self.crop_size,
                            history_path=self.history_path)

    def text_options(self) -> TextOptions:
        from AIMarkLens.edition import features

        return TextOptions(self.text_key or "AIMarkLens-demo", self.text_gamma, features().statistical_text)


def default_output_dir() -> str:
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return os.path.join(documents or os.path.expanduser("~"), "AIMarkLens Output")


class SettingsStore:
    def __init__(self) -> None:
        self._qs = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, APP_ID, APP_ID)

    def load(self) -> AppSettings:
        s = AppSettings()
        q = self._qs
        s.exiftool_path = str(q.value("tools/exiftool", "", str))
        s.c2patool_path = str(q.value("tools/c2patool", "", str))
        s.allow_network = q.value("network/online", True, bool)
        s.output_dir = str(q.value("output/dir", "", str)) or default_output_dir()
        s.crop_size = int(q.value("analysis/crop", 1024, int))
        s.auto_analyse = q.value("analysis/auto", True, bool)
        s.reduce_motion = q.value("ui/reduce_motion", False, bool)
        s.text_key = str(q.value("text/key", "AIMarkLens-demo", str))
        s.text_gamma = float(q.value("text/gamma", 0.25, float))
        s.lab_key = str(q.value("lab/key", "AIMarkLens-lab", str))
        s.ask_before_upload = q.value("online/ask_before_upload", True, bool)
        s.auto_online = q.value("online/auto", False, bool)
        s.offer_tool_install = q.value("tools/offer_install", True, bool)
        s.remember_history = q.value("analysis/history", True, bool)
        for name in _ONLINE_FIELDS:
            value = str(q.value(f"online/{name}", getattr(s.online, name), str))
            setattr(s.online, name, unprotect(value) if name in _SECRET_FIELDS else value)
        if s.online.google_auth not in ("adc", "service_account", "gcloud"):
            s.online.google_auth = "adc"
        return s

    def save(self, s: AppSettings) -> None:
        q = self._qs
        q.setValue("tools/exiftool", s.exiftool_path)
        q.setValue("tools/c2patool", s.c2patool_path)
        q.setValue("network/online", s.allow_network)
        q.setValue("output/dir", s.output_dir)
        q.setValue("analysis/crop", s.crop_size)
        q.setValue("analysis/auto", s.auto_analyse)
        q.setValue("ui/reduce_motion", s.reduce_motion)
        q.setValue("text/key", s.text_key)
        q.setValue("text/gamma", s.text_gamma)
        q.setValue("lab/key", s.lab_key)
        q.setValue("online/ask_before_upload", s.ask_before_upload)
        q.setValue("online/auto", s.auto_online)
        q.setValue("tools/offer_install", s.offer_tool_install)
        q.setValue("analysis/history", s.remember_history)
        for name in _ONLINE_FIELDS:
            value = getattr(s.online, name)
            q.setValue(f"online/{name}", protect(value) if name in _SECRET_FIELDS else value)
        q.sync()

    def geometry(self) -> QByteArray | None:
        value = self._qs.value("window/geometry")
        return value if isinstance(value, QByteArray) else None

    def set_geometry(self, data: QByteArray) -> None:
        self._qs.setValue("window/geometry", data)

    def last_dir(self, kind: str) -> str:
        return str(self._qs.value(f"dirs/{kind}", "", str))

    def set_last_dir(self, kind: str, path: str) -> None:
        self._qs.setValue(f"dirs/{kind}", os.path.dirname(path))
