"""Provenance artefacts that chat assistants leave in copied text.

These are not watermarks: they are by-products of copying (citation markup,
tracking parameters, private-use markers). They are reliable when present but
trivially removed, so their absence says nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from AIMarkLens.engines.providers import CONTEXT, STRONG

_AI_UTM = r"(?:chatgpt\.com|chatgpt|openai|perplexity(?:\.ai)?|copilot(?:\.com|\.microsoft\.com)?|gemini(?:\.google\.com)?|claude(?:\.ai)?|deepseek|mistral|grok|you\.com|phind)"


@dataclass(frozen=True)
class ArtefactRule:
    provider: str
    title: str
    pattern: re.Pattern
    strength: str
    explanation: str
    removable: str = ""  # cleaning option that removes it


RULES: tuple[ArtefactRule, ...] = (
    ArtefactRule("openai", "Links tagged utm_source=chatgpt.com", re.compile(r"utm_source=chatgpt\.com", re.I), STRONG,
                 "ChatGPT adds this tracking parameter to links in answers that used web search.", "utm"),
    ArtefactRule("openai", "Links tagged utm_source=openai", re.compile(r"utm_source=openai\b", re.I), STRONG,
                 "OpenAI products add this tracking parameter to some links.", "utm"),
    ArtefactRule("perplexity", "Links tagged utm_source=perplexity", re.compile(r"utm_source=perplexity", re.I), STRONG,
                 "Perplexity adds this tracking parameter to cited links.", "utm"),
    ArtefactRule("microsoft", "Links tagged with a Copilot source", re.compile(r"utm_source=copilot", re.I), STRONG,
                 "A tracking parameter that names Copilot.", "utm"),
    ArtefactRule("others", "Links with an AI service as tracking source",
                 re.compile(r"utm_source=(?:gemini|claude|deepseek|mistral|grok|you\.com|phind)", re.I), STRONG,
                 "A link's tracking parameter names an AI service.", "utm"),
    ArtefactRule("openai", "ChatGPT citation markup (citeturn…)", re.compile(r"\bcite(?:turn\d+[a-z]+\d+)+", re.I), STRONG,
                 "Left behind when ChatGPT answers with web citations are copied as plain text.", "citations"),
    ArtefactRule("openai", "ChatGPT tool-reference markup (turnNsearchN)", re.compile(r"\bturn\d+(?:search|news|view|fetch|file|image|forecast|finance)\d+\b", re.I),
                 STRONG, "Internal references to ChatGPT tool results.", "citations"),
    ArtefactRule("openai", "Assistant file-search citation (【n†source】)", re.compile(r"【\d+(?::\d+)?†[^】]{0,80}】"), STRONG,
                 "Citation markers produced by OpenAI file search (ChatGPT and the Assistants API).", "citations"),
    ArtefactRule("openai", "ChatGPT internal markup (filecite, navlist, entity)", re.compile(r"\b(?:oai_citation|filecite|navlist|entity\[\")", re.I),
                 STRONG, "Rendering markup from ChatGPT that survived copying.", "citations"),
    ArtefactRule("google", "Gemini citation markup ([cite_start], [cite: n])", re.compile(r"\[cite_start\]|\[cite:\s?\d+(?:,\s?\d+)*\]"),
                 STRONG, "Citation markers seen when Gemini answers with sources are copied.", "citations"),
    ArtefactRule("others", "Assistant self-reference ('As an AI language model')", re.compile(r"\bas an ai(?: language)? model\b", re.I),
                 STRONG, "A disclaimer phrase typical of chat assistants."),
    ArtefactRule("others", "Assistant boilerplate phrasing", re.compile(
        r"\b(?:i hope this helps|certainly! here(?:'|’)s|let me know if you(?:'|’)d like|feel free to ask|great question)\b", re.I),
                 CONTEXT, "Phrases common in assistant replies, and in ordinary writing too."),
)


@dataclass
class ArtefactMatch:
    rule: ArtefactRule
    count: int
    examples: list[str]
    first_index: int


def find_artefacts(text: str) -> list[ArtefactMatch]:
    found: list[ArtefactMatch] = []
    for rule in RULES:
        matches = list(rule.pattern.finditer(text))
        if not matches:
            continue
        examples = []
        for m in matches[:3]:
            start, end = max(0, m.start() - 30), min(len(text), m.end() + 30)
            examples.append(text[start:end].replace("\n", " "))
        found.append(ArtefactMatch(rule, len(matches), examples, matches[0].start()))
    return found


_CITATION_PATTERNS = [r.pattern for r in RULES if r.removable == "citations"]
_UTM = re.compile(rf"([?&])utm_source={_AI_UTM}(&?)", re.I)


def remove_citations(text: str) -> tuple[str, int]:
    total = 0
    for pattern in _CITATION_PATTERNS:
        text, n = pattern.subn("", text)
        total += n
    return text, total


def remove_ai_utm(text: str) -> tuple[str, int]:
    # "?utm=x&b" -> "?b", "&utm=x&b" -> "&b", trailing "?utm=x" or "&utm=x" -> ""
    return _UTM.subn(lambda m: m.group(1) if m.group(2) else "", text)
