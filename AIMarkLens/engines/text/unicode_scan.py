"""Invisible and deceptive Unicode character scanner, with decoders for known hiding schemes.

Context matters: a zero-width joiner inside an emoji sequence or a Persian word
is ordinary orthography, so such occurrences are marked benign and excluded
from the counts that drive findings.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

CATEGORY_LABELS = {
    "zero-width": "Zero-width and joiner characters",
    "bidi": "Bidirectional text controls",
    "tag": "Unicode tag characters",
    "variation": "Variation selectors",
    "space": "Non-standard spaces",
    "soft-hyphen": "Soft hyphens",
    "control": "Control characters",
    "private-use": "Private-use characters",
    "invisible-math": "Invisible mathematical operators",
    "filler": "Blank filler characters",
    "format": "Other invisible format characters",
    "replacement": "Replacement and object characters",
    "separator": "Unicode line and paragraph separators",
    "homoglyph": "Look-alike letters (homoglyphs)",
    "styled": "Styled letters (fullwidth or mathematical)",
}

# Tone per category: "signal" = can carry hidden data, "warning" = can deceive, "info" = typographic
CATEGORY_TONE = {
    "zero-width": "signal", "tag": "signal", "variation": "signal", "bidi": "warning", "homoglyph": "warning",
    "control": "warning", "private-use": "warning", "invisible-math": "signal", "filler": "warning",
    "format": "warning", "replacement": "info", "separator": "info", "space": "info", "soft-hyphen": "info",
    "styled": "info",
}

_KNOWN: dict[int, tuple[str, str]] = {
    0x200B: ("ZWSP", "zero-width"), 0x200C: ("ZWNJ", "zero-width"), 0x200D: ("ZWJ", "zero-width"),
    0x2060: ("WJ", "zero-width"), 0xFEFF: ("BOM", "zero-width"), 0x180E: ("MVS", "zero-width"),
    0x034F: ("CGJ", "zero-width"),
    0x200E: ("LRM", "bidi"), 0x200F: ("RLM", "bidi"), 0x061C: ("ALM", "bidi"), 0x202A: ("LRE", "bidi"),
    0x202B: ("RLE", "bidi"), 0x202C: ("PDF", "bidi"), 0x202D: ("LRO", "bidi"), 0x202E: ("RLO", "bidi"),
    0x2066: ("LRI", "bidi"), 0x2067: ("RLI", "bidi"), 0x2068: ("FSI", "bidi"), 0x2069: ("PDI", "bidi"),
    0x2061: ("FA", "invisible-math"), 0x2062: ("ITIMES", "invisible-math"), 0x2063: ("ISEP", "invisible-math"),
    0x2064: ("IPLUS", "invisible-math"),
    0x00AD: ("SHY", "soft-hyphen"),
    0x00A0: ("NBSP", "space"), 0x202F: ("NNBSP", "space"), 0x2000: ("NQSP", "space"), 0x2001: ("MQSP", "space"),
    0x2002: ("ENSP", "space"), 0x2003: ("EMSP", "space"), 0x2004: ("3MSP", "space"), 0x2005: ("4MSP", "space"),
    0x2006: ("6MSP", "space"), 0x2007: ("FSP", "space"), 0x2008: ("PSP", "space"), 0x2009: ("THSP", "space"),
    0x200A: ("HSP", "space"), 0x205F: ("MMSP", "space"), 0x3000: ("IDSP", "space"), 0x1680: ("OGSP", "space"),
    0x115F: ("HCF", "filler"), 0x1160: ("HJF", "filler"), 0x3164: ("HF", "filler"), 0xFFA0: ("HWHF", "filler"),
    0x17B4: ("KIVAQ", "filler"), 0x17B5: ("KIVAA", "filler"), 0x2800: ("BRAILLE-BLANK", "filler"),
    0xFFFD: ("REPLACEMENT", "replacement"), 0xFFFC: ("OBJ", "replacement"),
    0x2028: ("LSEP", "separator"), 0x2029: ("PSEP", "separator"), 0x0085: ("NEL", "separator"),
}

CONFUSABLES = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w", "ɡ": "g", "А": "A", "В": "B", "Е": "E", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ј": "J",
    "Ѕ": "S", "Һ": "H", "Ԛ": "Q", "Ԝ": "W", "ο": "o", "α": "a", "ν": "v", "ι": "i", "ρ": "p", "υ": "u",
    "χ": "x", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
    "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X", "օ": "o", "ս": "u", "հ": "h", "ո": "n",
}

_SCRIPTS_OF_INTEREST = ("LATIN", "CYRILLIC", "GREEK", "ARMENIAN")
_JOINING_SCRIPTS = ("ARABIC", "DEVANAGARI", "BENGALI", "GURMUKHI", "GUJARATI", "ORIYA", "TAMIL", "TELUGU",
                    "KANNADA", "MALAYALAM", "SINHALA", "SYRIAC", "MONGOLIAN", "KHMER", "MYANMAR", "HEBREW")
MAX_OCCURRENCES = 20000


@dataclass
class Occurrence:
    index: int
    line: int
    column: int
    char: str
    abbr: str
    category: str
    benign: bool = False
    note: str = ""

    @property
    def codepoint(self) -> str:
        return f"U+{ord(self.char):04X}"

    @property
    def name(self) -> str:
        return unicodedata.name(self.char, "UNNAMED CHARACTER")


@dataclass
class HiddenPayload:
    scheme: str
    start: int
    length: int
    decoded: str


@dataclass
class ScanResult:
    occurrences: list[Occurrence] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    benign_counts: Counter = field(default_factory=Counter)
    truncated: bool = False
    payloads: list[HiddenPayload] = field(default_factory=list)
    zero_width_runs: list[tuple[int, int]] = field(default_factory=list)
    scripts: Counter = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _emojiish(cp: int) -> bool:
    return (0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2300 <= cp <= 0x23FF or
            0x2B00 <= cp <= 0x2BFF or 0x2190 <= cp <= 0x21FF or cp in (0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122,
                                                                         0x2139, 0x3030, 0x303D, 0x3297, 0x3299))


def _script(ch: str) -> str:
    name = unicodedata.name(ch, "")
    return name.split(" ", 1)[0] if name else ""


def classify(ch: str) -> Optional[tuple[str, str]]:
    cp = ord(ch)
    known = _KNOWN.get(cp)
    if known:
        return known
    if 0xE0000 <= cp <= 0xE007F:
        return "TAG", "tag"
    if 0xFE00 <= cp <= 0xFE0F:
        return f"VS{cp - 0xFE00 + 1}", "variation"
    if 0xE0100 <= cp <= 0xE01EF:
        return f"VS{cp - 0xE0100 + 17}", "variation"
    if 0xE000 <= cp <= 0xF8FF or 0xF0000 <= cp <= 0xFFFFD or 0x100000 <= cp <= 0x10FFFD:
        return "PUA", "private-use"
    if (cp < 0x20 and ch not in "\t\n\r") or cp == 0x7F or 0x80 <= cp <= 0x9F:
        return f"CTRL-{cp:02X}", "control"
    if 0xFF21 <= cp <= 0xFF5A or 0x1D400 <= cp <= 0x1D7FF:
        return "STYLED", "styled"
    if cp > 0x7F and unicodedata.category(ch) == "Cf":
        return "FMT", "format"
    return None


def _benign(text: str, i: int, cp: int, category: str) -> str:
    prev_cp = ord(text[i - 1]) if i > 0 else 0
    next_cp = ord(text[i + 1]) if i + 1 < len(text) else 0
    if cp == 0x200D and (_emojiish(prev_cp) or 0x1F3FB <= prev_cp <= 0x1F3FF or prev_cp == 0xFE0F) and _emojiish(next_cp):
        return "Part of an emoji sequence"
    if cp in (0x200C, 0x200D) and (_script(chr(prev_cp)) in _JOINING_SCRIPTS or _script(chr(next_cp)) in _JOINING_SCRIPTS):
        return "Normal joining control in this script"
    if cp in (0xFE0E, 0xFE0F) and (_emojiish(prev_cp) or next_cp == 0x20E3 or (0x30 <= prev_cp <= 0x39) or prev_cp in (0x23, 0x2A)):
        return "Emoji or text presentation selector"
    if category == "tag":
        j = i
        while j > 0 and 0xE0000 <= ord(text[j - 1]) <= 0xE007F:
            j -= 1
        if j > 0 and ord(text[j - 1]) == 0x1F3F4:
            return "Part of a subdivision flag emoji"
    if cp == 0xFEFF and i == 0:
        return "Byte order mark at the start of the text"
    return ""


def scan(text: str, check_cancel=None, limit: Optional[int] = MAX_OCCURRENCES) -> ScanResult:
    """Classify every notable character. limit=None keeps all occurrences (used for cleaning)."""
    result = ScanResult()
    line, line_start = 1, 0
    for i, ch in enumerate(text):
        if check_cancel is not None and i % 50000 == 0:
            check_cancel()
        if ch == "\n":
            line += 1
            line_start = i + 1
            continue
        cp = ord(ch)
        if cp < 0x80 and cp >= 0x20:
            continue
        if ch.isalpha():
            result.scripts[_script(ch)] += 1
        info = classify(ch)
        if info is None:
            continue
        abbr, category = info
        note = _benign(text, i, cp, category)
        if note:
            result.benign_counts[category] += 1
        else:
            result.counts[category] += 1
        if limit is None or len(result.occurrences) < limit:
            result.occurrences.append(Occurrence(i, line, i - line_start + 1, ch, abbr, category, bool(note), note))
        else:
            result.truncated = True
    _scan_homoglyphs(text, result, limit)
    _decode_payloads(text, result)
    result.occurrences.sort(key=lambda o: o.index)
    return result


_WORD = re.compile(r"\w+", re.UNICODE)


def _scan_homoglyphs(text: str, result: ScanResult, limit: Optional[int]) -> None:
    latin = result.scripts.get("LATIN", 0)
    letters = sum(result.scripts.values()) + sum(1 for ch in text if "a" <= ch.lower() <= "z")
    mostly_latin = letters and (latin + sum(1 for ch in text if ch.isascii() and ch.isalpha())) / letters > 0.8
    if not result.scripts or not any(s in result.scripts for s in ("CYRILLIC", "GREEK", "ARMENIAN")):
        return
    line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
    import bisect

    for match in _WORD.finditer(text):
        word = match.group(0)
        scripts = {_script(c) for c in word if c.isalpha()}
        scripts &= set(_SCRIPTS_OF_INTEREST)
        foreign = [c for c in word if c in CONFUSABLES]
        if not foreign:
            continue
        mixed = len(scripts) > 1
        disguised = mostly_latin and scripts <= {"CYRILLIC", "GREEK", "ARMENIAN"} and len(foreign) == len(word)
        if not (mixed or disguised):
            continue
        for offset, c in enumerate(word):
            if c in CONFUSABLES:
                index = match.start() + offset
                line = bisect.bisect_right(line_starts, index)
                result.counts["homoglyph"] += 1
                if limit is None or len(result.occurrences) < limit:
                    result.occurrences.append(Occurrence(index, line, index - line_starts[line - 1] + 1, c,
                                                         f"{c}→{CONFUSABLES[c]}", "homoglyph", False,
                                                         f"In the word '{word}'"))


def _decode_payloads(text: str, result: ScanResult) -> None:
    # Tag-character "ASCII smuggling": U+E0020-E007E mirror printable ASCII.
    for m in re.finditer("[\U000E0000-\U000E007F]+", text):
        if m.start() > 0 and ord(text[m.start() - 1]) == 0x1F3F4:
            continue
        decoded = "".join(chr(ord(c) - 0xE0000) for c in m.group(0) if 0xE0020 <= ord(c) <= 0xE007E)
        if decoded:
            result.payloads.append(HiddenPayload("Unicode tag characters (ASCII smuggling)", m.start(), len(m.group(0)), decoded))
    # Variation-selector byte encoding: each selector carries one byte.
    for m in re.finditer("[︀-️\U000E0100-\U000E01EF]{2,}", text):
        values = []
        for c in m.group(0):
            cp = ord(c)
            values.append(cp - 0xFE00 if cp <= 0xFE0F else cp - 0xE0100 + 16)
        decoded = bytes(values).decode("utf-8", "replace")
        printable = sum(ch.isprintable() for ch in decoded) / max(1, len(decoded))
        if printable > 0.8:
            result.payloads.append(HiddenPayload("Variation selectors (byte encoding)", m.start(), len(m.group(0)), decoded))
    # Zero-width binary: runs of two distinct zero-width characters.
    for m in re.finditer("[​‌‍⁠﻿⁢⁣⁤]{8,}", text):
        run = m.group(0)
        result.zero_width_runs.append((m.start(), len(run)))
        symbols = sorted(set(run))
        if len(symbols) != 2:
            continue
        for zero, one in ((symbols[0], symbols[1]), (symbols[1], symbols[0])):
            bits = "".join("0" if c == zero else "1" for c in run)
            chars = [chr(int(bits[k:k + 8], 2)) for k in range(0, len(bits) - 7, 8)]
            decoded = "".join(chars)
            if decoded and sum(32 <= ord(c) < 127 for c in decoded) / len(decoded) > 0.9:
                result.payloads.append(HiddenPayload(
                    f"Zero-width binary (U+{ord(zero):04X}=0, U+{ord(one):04X}=1)", m.start(), len(run), decoded))
                break


def utf16_offsets(text: str) -> list[int]:
    """Map each code-point index to its UTF-16 offset (Qt text positions are UTF-16)."""
    offsets, pos = [], 0
    for ch in text:
        offsets.append(pos)
        pos += 2 if ord(ch) > 0xFFFF else 1
    offsets.append(pos)
    return offsets
