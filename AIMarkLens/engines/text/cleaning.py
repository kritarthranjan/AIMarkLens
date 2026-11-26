"""Produce a cleaned output copy of a text sample. The original sample is never modified."""

from __future__ import annotations

import unicodedata
from collections import Counter

from AIMarkLens.engines.text.artefacts import remove_ai_utm, remove_citations
from AIMarkLens.engines.text.unicode_scan import CONFUSABLES, scan

CLEAN_OPTIONS: tuple[tuple[str, str, bool], ...] = (
    ("zero-width", "Remove zero-width characters (keeps emoji and script joiners)", True),
    ("bidi", "Remove bidirectional control characters", True),
    ("tag", "Remove Unicode tag characters (keeps flag emoji)", True),
    ("variation", "Remove variation selectors outside emoji", True),
    ("invisible-math", "Remove invisible mathematical operators", True),
    ("filler", "Remove blank filler characters", True),
    ("format", "Remove other invisible format characters", True),
    ("control", "Remove control characters (keeps tabs and line breaks)", True),
    ("soft-hyphen", "Remove soft hyphens", True),
    ("private-use", "Remove private-use characters (such as citation markers)", True),
    ("space", "Replace non-standard spaces with ordinary spaces", False),
    ("homoglyph", "Replace look-alike letters in mixed-script words", False),
    ("citations", "Remove chat citation markup", False),
    ("utm", "Remove AI tracking parameters from links", False),
    ("line-endings", "Make line endings consistent", False),
    ("nfkc", "Apply Unicode NFKC normalisation (also folds styled letters)", False),
)
DEFAULT_OPTIONS = frozenset(key for key, _label, default in CLEAN_OPTIONS if default)
# These remove evidence of where a text came from (assistant citation markup, tracking parameters and the
# private-use characters chat apps use as citation markers), so only the Personal Edition offers them.
PROVENANCE_OPTIONS = frozenset({"private-use", "citations", "utm"})


def available_options(provenance_cleanup: bool) -> tuple[tuple[str, str, bool], ...]:
    return tuple(o for o in CLEAN_OPTIONS if provenance_cleanup or o[0] not in PROVENANCE_OPTIONS)
OPTION_LABELS = {key: label for key, label, _ in CLEAN_OPTIONS}


def clean_text(text: str, options: set[str]) -> tuple[str, list[tuple[str, int]]]:
    """Return the cleaned text and (option label, number of changes) for each option used."""
    result = scan(text, limit=None)
    changes: Counter = Counter()
    replace: dict[int, str] = {}
    for occ in result.occurrences:
        if occ.benign or occ.category not in options:
            continue
        if occ.category == "space":
            replace[occ.index] = " "
        elif occ.category == "homoglyph":
            replace[occ.index] = CONFUSABLES.get(occ.char, occ.char)
        else:
            replace[occ.index] = ""
        changes[occ.category] += 1
    if replace:
        text = "".join(replace.get(i, ch) for i, ch in enumerate(text))
    if "citations" in options:
        text, n = remove_citations(text)
        changes["citations"] += n
    if "utm" in options:
        text, n = remove_ai_utm(text)
        changes["utm"] += n
    if "line-endings" in options:
        crlf = text.count("\r\n")
        lf = text.count("\n") - crlf
        target = "\r\n" if crlf > lf else "\n"
        normalised = text.replace("\r\n", "\n").replace("\r", "\n")
        if target == "\r\n":
            normalised = normalised.replace("\n", "\r\n")
        if normalised != text:
            changes["line-endings"] += 1
        text = normalised
    if "nfkc" in options:
        normalised = unicodedata.normalize("NFKC", text)
        if normalised != text:
            changes["nfkc"] += sum(1 for a, b in zip(text, normalised) if a != b) or 1
        text = normalised
    return text, [(OPTION_LABELS[k], v) for k, v in changes.items() if v]
