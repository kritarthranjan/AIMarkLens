"""Editions: which features a build contains.

* Public Edition: the stable analysis tool for general release. Experimental
  research features are not included: they are hidden in the interface and
  their modules are left out of the build entirely.
* Personal Edition: everything, including the experimental research lab and
  advanced settings.

A packaged build records its edition in ``_build_info.py`` (written by
build.ps1). When running from source, the edition comes from the
AIMarkLens_EDITION environment variable and defaults to Personal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

PUBLIC, PERSONAL = "public", "personal"


@dataclass(frozen=True)
class Features:
    experiments: bool  # Experiments page: robustness lab, lab watermark, synthetic text watermark
    metadata_copy: bool  # image export: metadata-free copies (removing EXIF, XMP, C2PA and so on)
    statistical_text: bool  # the experimental green-list text watermark test and its settings
    provenance_cleanup: bool  # text cleaning options that remove chat citation markup and tracking links
    advanced_settings: bool  # pixel crop size, service endpoint overrides and other research options


FEATURES = {
    PUBLIC: Features(experiments=False, metadata_copy=True, statistical_text=False, provenance_cleanup=False,
                     advanced_settings=False),
    PERSONAL: Features(experiments=True, metadata_copy=True, statistical_text=True, provenance_cleanup=True,
                       advanced_settings=True),
}

# Modules that exist only for Personal features. build.ps1 excludes them from Public builds.
PERSONAL_ONLY_MODULES = (
    "AIMarkLens.engines.image.experiments",
    "AIMarkLens.engines.image.labmark",
    "AIMarkLens.engines.text.experiments",
    "AIMarkLens.ui.pages.experiments_page",
)


def _detect() -> str:
    try:
        from AIMarkLens._build_info import EDITION  # type: ignore[import-not-found]

        return EDITION
    except ImportError:
        value = os.environ.get("AIMarkLens_EDITION", PERSONAL).strip().lower()
        return value if value in FEATURES else PERSONAL


EDITION = _detect()


def features() -> Features:
    return FEATURES[EDITION]


def edition_name() -> str:
    return "Public Edition" if EDITION == PUBLIC else "Personal Edition"


def set_edition(edition: str) -> None:
    """For tests: switch edition in-process."""
    global EDITION
    if edition not in FEATURES:
        raise ValueError(edition)
    EDITION = edition
