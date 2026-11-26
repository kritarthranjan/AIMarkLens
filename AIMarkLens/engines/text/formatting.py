"""Descriptive formatting and typography statistics.

These describe how a text is formatted. Style features such as em dashes or
heavy Markdown are common in assistant output but also in human writing, so
they are reported as context, never as proof of origin.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

_WORDS = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
_SENTENCES = re.compile(r"[^.!?…]+[.!?…]+", re.UNICODE)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")

TYPOGRAPHY = (
    ("—", "Em dashes (—)"), ("–", "En dashes (–)"), ("“", "Curly double quotes (“ ”)"),
    ("‘", "Curly single quotes / apostrophes (‘ ’)"), ("…", "Ellipsis character (…)"),
    ("•", "Bullet characters (•)"), ("→", "Arrows (→)"), ("×", "Multiplication sign (×)"),
)

MARKDOWN = (
    ("Headings", re.compile(r"^#{1,6} \S", re.M)),
    ("Bold spans", re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__")),
    ("Bulleted list items", re.compile(r"^\s*[-*+] \S", re.M)),
    ("Numbered list items", re.compile(r"^\s*\d+[.)] \S", re.M)),
    ("Table rows", re.compile(r"^\s*\|.*\|\s*$", re.M)),
    ("Code fences", re.compile(r"^```", re.M)),
    ("Links", re.compile(r"\[[^\]\n]+\]\([^)\s]+\)")),
    ("Horizontal rules", re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.M)),
    ("Bold lead-in labels (**Term:**)", re.compile(r"^\s*(?:[-*+] )?\*\*[^*\n]{1,60}:\*\*", re.M)),
)


@dataclass
class FormattingResult:
    characters: int = 0
    code_points_non_ascii: int = 0
    words: int = 0
    sentences: int = 0
    lines: int = 0
    paragraphs: int = 0
    line_endings: dict[str, int] = field(default_factory=dict)
    trailing_whitespace_lines: int = 0
    double_spaces: int = 0
    tabs: int = 0
    typography: list[tuple[str, int]] = field(default_factory=list)
    markdown: list[tuple[str, int]] = field(default_factory=list)
    emoji: int = 0
    sentence_mean: float = 0.0
    sentence_stdev: float = 0.0
    type_token_ratio: float = 0.0
    mean_word_length: float = 0.0
    top_words: list[tuple[str, int]] = field(default_factory=list)

    @property
    def mixed_line_endings(self) -> bool:
        return sum(1 for v in self.line_endings.values() if v) > 1

    @property
    def dominant_line_ending(self) -> str:
        if not any(self.line_endings.values()):
            return "\n"
        label = max(self.line_endings, key=self.line_endings.get)
        return {"CRLF (Windows)": "\r\n", "LF (Unix)": "\n", "CR (classic Mac)": "\r"}[label]

    def per_thousand_words(self, count: int) -> float:
        return count * 1000.0 / self.words if self.words else 0.0


_STOP = set("the a an and or of to in on for is are was were be been it this that with as by at from not but "
            "you your we our they their he she his her its i me my".split())


def analyse_formatting(text: str) -> FormattingResult:
    r = FormattingResult()
    r.characters = len(text)
    r.code_points_non_ascii = sum(1 for ch in text if ord(ch) > 0x7F)
    crlf = text.count("\r\n")
    r.line_endings = {"CRLF (Windows)": crlf, "LF (Unix)": text.count("\n") - crlf, "CR (classic Mac)": text.count("\r") - crlf}
    lines = re.split(r"\r\n|\n|\r", text)
    r.lines = len(lines) if text else 0
    r.paragraphs = len([p for p in re.split(r"(?:\r?\n|\r)\s*(?:\r?\n|\r)", text) if p.strip()])
    r.trailing_whitespace_lines = sum(1 for line in lines if line != line.rstrip(" \t"))
    r.double_spaces = len(re.findall(r"(?<=[.!?])  (?=\S)", text))
    r.tabs = text.count("\t")
    words = _WORDS.findall(text)
    r.words = len(words)
    sentences = [s for s in _SENTENCES.findall(text) if _WORDS.search(s)]
    r.sentences = len(sentences)
    lengths = [len(_WORDS.findall(s)) for s in sentences]
    if lengths:
        r.sentence_mean = statistics.fmean(lengths)
        r.sentence_stdev = statistics.pstdev(lengths)
    lowered = [w.lower() for w in words]
    if lowered:
        r.type_token_ratio = len(set(lowered)) / len(lowered)
        r.mean_word_length = statistics.fmean(len(w) for w in lowered)
        r.top_words = Counter(w for w in lowered if w not in _STOP and len(w) > 2).most_common(12)
    for char, label in TYPOGRAPHY:
        count = text.count(char)
        if char == "“":
            count += text.count("”")
        if char == "‘":
            count += text.count("’")
        r.typography.append((label, count))
    for label, pattern in MARKDOWN:
        r.markdown.append((label, len(pattern.findall(text))))
    r.emoji = len(_EMOJI.findall(text))
    return r
