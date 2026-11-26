"""Public and Personal editions: feature gates, and that Public builds never load Personal-only modules."""

import os
import subprocess
import sys

import pytest

from AIMarkLens import edition
from AIMarkLens.engines.text.cleaning import PROVENANCE_OPTIONS, available_options

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def public():
    previous = edition.EDITION
    edition.set_edition(edition.PUBLIC)
    yield
    edition.set_edition(previous)


def test_feature_tables():
    assert all(vars(edition.FEATURES[edition.PERSONAL]).values())
    public = vars(edition.FEATURES[edition.PUBLIC])
    assert public.pop("metadata_copy") is True  # image and text export buttons are in both editions
    assert not any(public.values())


def test_public_cleaning_options_keep_provenance(public):
    keys = {key for key, _label, _default in available_options(edition.features().provenance_cleanup)}
    assert keys.isdisjoint(PROVENANCE_OPTIONS)
    assert {"zero-width", "tag", "bidi", "homoglyph"} <= keys


def test_public_text_analysis_has_no_statistical_test(public, tmp_path):
    from PySide6.QtCore import QSettings

    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    from AIMarkLens.core.context import TaskContext
    from AIMarkLens.engines.text.analyser import analyse_text
    from AIMarkLens.engines.text.sample import TextSample
    from AIMarkLens.ui.settings_store import SettingsStore

    options = SettingsStore().load().text_options()
    assert options.statistical is False
    doc = analyse_text(TextSample("plain words here", "typed", "t"), options, TaskContext())
    assert "Statistical" not in doc.tabs()


PROBE = r"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, sys.argv[1])
from PySide6.QtWidgets import QApplication
app = QApplication([])
from AIMarkLens.edition import PERSONAL_ONLY_MODULES, edition_name
from AIMarkLens.ui.main_window import MainWindow
w = MainWindow()
loaded = [m for m in PERSONAL_ONLY_MODULES if m in sys.modules]
print("EDITION", edition_name())
print("PAGES", ",".join(w.page_by_key))
print("COPY_BUTTON", w.page_by_key["image"].copy_btn.isVisibleTo(w.page_by_key["image"]))
print("LOADED", ",".join(loaded) or "-")
"""


@pytest.mark.parametrize("name", ["public", "personal"])
def test_edition_window(name, tmp_path):
    env = dict(os.environ, AIMarkLens_EDITION=name, PYTHONPATH=ROOT)
    out = subprocess.run([sys.executable, "-c", PROBE, str(tmp_path)], capture_output=True, text=True, env=env,
                         cwd=ROOT, timeout=300).stdout
    lines = dict(line.split(" ", 1) for line in out.strip().splitlines() if " " in line)
    if name == "public":
        assert lines["EDITION"] == "Public Edition"
        assert lines["PAGES"] == "image,text,compare,settings"
        assert lines["COPY_BUTTON"] == "True"
        assert lines["LOADED"] == "-"  # Personal-only modules were never imported
    else:
        assert lines["EDITION"] == "Personal Edition"
        assert lines["PAGES"] == "image,text,compare,experiments,settings"
        assert lines["COPY_BUTTON"] == "True"


def test_legacy_settings_are_migrated(tmp_path, monkeypatch):
    from AIMarkLens.core.legacy import migrate_previous_install

    roaming, local = tmp_path / "Roaming", tmp_path / "Local"
    (roaming / "TraceLens").mkdir(parents=True)
    (roaming / "TraceLens" / "TraceLens.ini").write_text("[online]\nhive_key=dpapi:abc\n")
    (local / "TraceLens" / "tools" / "c2patool").mkdir(parents=True)
    (local / "TraceLens" / "tools" / "c2patool" / "c2patool.exe").write_bytes(b"MZ")
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    copied = migrate_previous_install()
    assert (roaming / "AIMarkLens" / "AIMarkLens.ini").read_text().endswith("hive_key=dpapi:abc\n")
    assert (local / "AIMarkLens" / "tools" / "c2patool" / "c2patool.exe").exists()
    assert len(copied) == 2
    assert migrate_previous_install() == []  # only once


def test_aethervexa_settings_win_over_tracelens(tmp_path, monkeypatch):
    from AIMarkLens.core.legacy import migrate_previous_install

    roaming = tmp_path / "Roaming"
    for old, key in (("AETHERVEXA", "newer"), ("TraceLens", "older")):
        (roaming / old).mkdir(parents=True)
        (roaming / old / f"{old}.ini").write_text(f"[online]\nhive_key={key}\n")
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    migrate_previous_install()
    assert (roaming / "AIMarkLens" / "AIMarkLens.ini").read_text().endswith("hive_key=newer\n")
