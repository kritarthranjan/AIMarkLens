"""Text analysis engine: invisible characters, formatting, provenance artefacts and statistical tests."""

from __future__ import annotations

from dataclasses import dataclass

from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.integrity import fingerprint_text, verify_unchanged
from AIMarkLens.core.report import (Cell, Finding, Findings, KeyValues, Links, Paragraph, ReportDocument, Section,
                                   Table, Verdict)
from AIMarkLens.engines.providers import CONTEXT, PROVIDER_BY_KEY, STRONG, text_providers
from AIMarkLens.engines.text import greenlist
from AIMarkLens.engines.text.artefacts import ArtefactMatch, find_artefacts
from AIMarkLens.engines.text.formatting import FormattingResult, analyse_formatting
from AIMarkLens.engines.text.sample import ORIGIN_LABELS, TextSample
from AIMarkLens.engines.text.unicode_scan import CATEGORY_LABELS, CATEGORY_TONE, ScanResult, scan

TEXT_CAVEAT = ("Absence of signals is not proof of absence. Statistical watermarks such as SynthID Text cannot be "
               "checked without the provider's keys and model, and paraphrasing weakens them.")

# Characters replaced by a visible token in the reveal view (visible look-alikes are highlighted in place).
_REVEAL_IN_PLACE = {"homoglyph", "styled", "replacement"}


@dataclass
class TextOptions:
    key: str = greenlist.DEFAULT_KEY
    gamma: float = greenlist.DEFAULT_GAMMA
    statistical: bool = True  # the experimental green-list test (Personal Edition)


def analyse_text(sample: TextSample, options: TextOptions, ctx: TaskContext) -> ReportDocument:
    ctx.progress(0.02, "Fingerprinting the sample")
    before = sample.ensure_fingerprint()
    text = sample.text
    ctx.progress(0.1, "Scanning for invisible and deceptive characters")
    result = scan(text, ctx.check)
    ctx.progress(0.45, "Measuring formatting")
    fmt = analyse_formatting(text)
    ctx.progress(0.6, "Looking for provenance artefacts")
    artefacts = find_artefacts(text)
    ctx.progress(0.7, "Running the statistical watermark test")
    green = greenlist.detect(text, options.key, options.gamma) if options.statistical else None
    ctx.progress(0.8, "Building the reveal view")
    reveal_text, highlights, occurrence_map = _reveal(text, result, ctx)

    findings = _findings(sample, result, fmt, artefacts, green, options)
    doc = ReportDocument(title="Text provenance analysis", subject=sample.label, kind="text",
                         verdict=_verdict(findings))
    _sections(doc, sample, result, fmt, artefacts, green, options, findings)

    ctx.progress(0.95, "Verifying the sample is unchanged")
    if sample.origin == "file" and sample.path:
        integrity = verify_unchanged(before, ctx.sub(0.95, 1.0))
        doc.integrity = integrity.rows()
        ok = integrity.unchanged
    else:
        after = fingerprint_text(text, sample.label)
        ok = after.sha256 == before.sha256
        doc.integrity = [("Sample unchanged", "Yes, verified" if ok else "NO"),
                         ("SHA-256 (UTF-8) before", before.sha256), ("SHA-256 (UTF-8) after", after.sha256),
                         ("Size", f"{before.size:,} bytes (UTF-8)")]
    doc.extra.update(integrity_ok=ok, reveal_text=reveal_text, highlights=highlights,
                     occurrence_map=occurrence_map)
    ctx.progress(1.0, "Analysis complete")
    return doc


def _reveal(text: str, result: ScanResult, ctx: TaskContext):
    """Build a copy of the text where invisible characters become visible ⟦tokens⟧.

    Returns the reveal text, highlight ranges (UTF-16 start, length, tone) and,
    for each stored occurrence, the index of its highlight.
    """
    pieces: list[str] = []
    highlights: list[tuple[int, int, str]] = []
    occurrence_map: list[int] = []
    position = 0  # UTF-16 length emitted so far (Qt text positions are UTF-16)

    def emit(piece: str) -> None:
        nonlocal position
        pieces.append(piece)
        position += len(piece.encode("utf-16-le", "surrogatepass")) // 2

    def emit_plain(segment: str) -> None:
        # Qt turns CR/LF variants into a single block break, so normalise here to keep positions exact.
        emit(segment.replace("\r\n", "\n").replace("\r", "\n"))

    cursor = 0
    for number, occ in enumerate(result.occurrences):
        if number % 2000 == 0:
            ctx.check()
        if occ.index < cursor:
            occurrence_map.append(-1)
            continue
        emit_plain(text[cursor:occ.index])
        start = position
        tone = "neutral" if occ.benign else CATEGORY_TONE.get(occ.category, "info")
        emit(occ.char if occ.category in _REVEAL_IN_PLACE else f"⟦{occ.abbr}⟧")
        length = position - start
        if occ.category == "separator":
            emit("\n")
        occurrence_map.append(len(highlights))
        highlights.append((start, length, tone))
        cursor = occ.index + 1
    emit_plain(text[cursor:])
    return "".join(pieces), highlights, occurrence_map


def _findings(sample: TextSample, result: ScanResult, fmt: FormattingResult, artefacts: list[ArtefactMatch],
              green: greenlist.GreenResult, options: TextOptions) -> list[Finding]:
    f: list[Finding] = []
    for payload in result.payloads:
        f.append(Finding("signal", f"Hidden text decoded: “{payload.decoded[:80]}”",
                         f"{payload.scheme}, {payload.length} characters starting at position {payload.start:,}.",
                         "Unicode"))
    for category, count in result.counts.most_common():
        tone = CATEGORY_TONE.get(category, "info")
        label = CATEGORY_LABELS.get(category, category)
        detail = {
            "zero-width": "Invisible characters that can carry hidden data or fingerprints.",
            "tag": "Invisible characters that mirror ASCII and can smuggle hidden instructions.",
            "variation": "Selectors outside emoji can encode arbitrary bytes.",
            "bidi": "Can reorder how text is displayed (a 'Trojan Source' risk).",
            "homoglyph": "Letters from other scripts that look like Latin letters.",
            "private-use": "Characters with no standard meaning; chat apps use some as citation markers.",
            "space": "Spaces that look ordinary but are different characters.",
            "control": "Non-printing control characters.",
        }.get(category, "")
        if category == "private-use" and any(0xE200 <= ord(o.char) <= 0xE2FF for o in result.occurrences
                                             if o.category == "private-use"):
            detail += " Characters in the U+E200 block are consistent with ChatGPT citation markers."
            tone = "signal"
        if category == "space":
            nnbsp = sum(1 for o in result.occurrences if o.char == " " and not o.benign)
            if nnbsp:
                detail += (f" Includes {nnbsp} narrow no-break space(s) (U+202F), reported in some AI model "
                           "outputs but also produced by French typography and word processors.")
        f.append(Finding(tone, f"{count:,} × {label}", detail, "Unicode"))
    benign = sum(result.benign_counts.values())
    if benign:
        f.append(Finding("info", f"{benign:,} invisible character(s) in normal contexts",
                         "Emoji sequences, script joiners or a leading byte order mark. Not counted as suspicious.",
                         "Unicode"))
    for match in artefacts:
        tone = "signal" if match.rule.strength == STRONG else "info"
        f.append(Finding(tone, f"{match.rule.title} (×{match.count})", match.rule.explanation,
                         PROVIDER_BY_KEY[match.rule.provider].name if match.rule.provider in PROVIDER_BY_KEY else "Artefact"))
    if green is not None and green.detected:
        f.append(Finding("signal", f"Lab green-list watermark detected (z = {green.z:.1f})",
                         f"With key '{options.key}' and gamma {options.gamma}. This only detects text produced with "
                         "the same scheme and key, such as AIMarkLens synthetic samples.", "Statistical"))
    if fmt.mixed_line_endings:
        f.append(Finding("info", "Mixed line endings",
                         ", ".join(f"{k}: {v}" for k, v in fmt.line_endings.items() if v), "Formatting"))
    dashes = dict(fmt.typography).get("Em dashes (—)", 0)
    if fmt.words >= 150 and fmt.per_thousand_words(dashes) >= 6:
        f.append(Finding("info", f"Frequent em dashes ({fmt.per_thousand_words(dashes):.1f} per 1,000 words)",
                         "A style often associated with assistant writing, and with many human writers. Context only.",
                         "Formatting"))
    if sample.warning:
        f.append(Finding("warning", "Encoding", sample.warning, "File"))
    order = {"error": 0, "signal": 1, "warning": 2, "ok": 3, "info": 4, "neutral": 5}
    f.sort(key=lambda item: order.get(item.tone, 9))
    return f


def _verdict(findings: list[Finding]) -> Verdict:
    signals = [x for x in findings if x.tone == "signal"]
    if signals:
        detail = "; ".join(s.title for s in signals[:3]) + ("; and more." if len(signals) > 3 else ".")
        return Verdict("signal", "Provenance or hidden-data signals found", detail, TEXT_CAVEAT)
    warnings = [x for x in findings if x.tone == "warning"]
    if warnings:
        return Verdict("warning", "No provenance signals, but deceptive characters found",
                       "; ".join(w.title for w in warnings[:3]) + ".", TEXT_CAVEAT)
    return Verdict("info", "No detectable provenance or hidden-data signals",
                   "No invisible payloads, assistant artefacts or lab watermark were found.", TEXT_CAVEAT)


def _sections(doc: ReportDocument, sample: TextSample, result: ScanResult, fmt: FormattingResult,
              artefacts: list[ArtefactMatch], green: greenlist.GreenResult, options: TextOptions,
              findings: list[Finding]) -> None:
    s = doc.sections
    info = [("Source", ORIGIN_LABELS.get(sample.origin, sample.origin)), ("Name", sample.label)]
    if sample.origin == "file":
        info.append(("Encoding", sample.encoding + (" with byte order mark" if sample.bom else "")))
    info += [("Characters", f"{fmt.characters:,}"), ("Non-ASCII characters", f"{fmt.code_points_non_ascii:,}"),
             ("Words", f"{fmt.words:,}"), ("Lines", f"{fmt.lines:,}"), ("Paragraphs", f"{fmt.paragraphs:,}")]
    s.append(Section("Findings", [Findings(findings)], "Summary"))
    s.append(Section("Sample", [KeyValues(info)], "Summary"))

    tab = "Invisible characters"
    rows = []
    for category, label in CATEGORY_LABELS.items():
        found, benign = result.counts.get(category, 0), result.benign_counts.get(category, 0)
        if found or benign:
            tone = CATEGORY_TONE.get(category, "info") if found else "neutral"
            rows.append([label, Cell(f"{found:,}", tone), f"{benign:,}"])
    s.append(Section("Summary by category", [Table(["Category", "Suspicious", "Normal context"], rows)] if rows
                     else [Paragraph("No invisible, control or look-alike characters were found.", "ok")], tab))
    if result.payloads:
        s.append(Section("Hidden payloads", [Table(["Scheme", "Position", "Length", "Decoded text"],
                                                   [[p.scheme, f"{p.start:,}", str(p.length), p.decoded]
                                                    for p in result.payloads])], tab,
                         "Text recovered from known hiding schemes."))
    if result.occurrences:
        occ_rows = []
        for occ in result.occurrences:
            tone = "neutral" if occ.benign else CATEGORY_TONE.get(occ.category, "info")
            occ_rows.append([f"{occ.line}:{occ.column}", occ.codepoint, Cell(occ.abbr, tone), occ.name.title(),
                             CATEGORY_LABELS.get(occ.category, occ.category), occ.note])
        note = "Select a row to show the character in the reveal view."
        if result.truncated:
            note += f" Only the first {len(result.occurrences):,} occurrences are listed; counts above are complete."
        s.append(Section("Occurrences", [Table(["Line:Col", "Code point", "Token", "Name", "Category", "Context"],
                                               occ_rows, key="occurrences",
                                               payloads=list(range(len(result.occurrences))), note=note)], tab))

    tab = "Formatting"
    endings = ", ".join(f"{k} {v:,}" for k, v in fmt.line_endings.items() if v) or "None (single line)"
    overview = [("Line endings", endings), ("Lines with trailing spaces", f"{fmt.trailing_whitespace_lines:,}"),
                ("Double spaces after sentences", f"{fmt.double_spaces:,}"), ("Tab characters", f"{fmt.tabs:,}"),
                ("Sentences", f"{fmt.sentences:,}"),
                ("Words per sentence", f"{fmt.sentence_mean:.1f} on average (spread {fmt.sentence_stdev:.1f})"),
                ("Vocabulary variety (type/token ratio)", f"{fmt.type_token_ratio:.3f}"),
                ("Average word length", f"{fmt.mean_word_length:.2f} letters"), ("Emoji", f"{fmt.emoji:,}")]
    s.append(Section("Overview", [KeyValues(overview)], tab))
    s.append(Section("Typography", [Table(["Feature", "Count", "Per 1,000 words"],
                                          [[label, f"{n:,}", f"{fmt.per_thousand_words(n):.1f}"] for label, n in fmt.typography])], tab))
    s.append(Section("Markdown structure", [Table(["Feature", "Count"], [[label, f"{n:,}"] for label, n in fmt.markdown])], tab))
    if fmt.top_words:
        s.append(Section("Most frequent content words", [Table(["Word", "Count"], [[w, str(n)] for w, n in fmt.top_words])], tab))
    s.append(Section("Interpreting style", [Paragraph(
        "Style features such as em dashes, bold lead-ins and heavy Markdown are common in assistant output but are "
        "also used by many writers. AIMarkLens reports them as context and never as evidence of origin.", "info")], tab))

    tab = "Providers"
    by_provider: dict[str, list[ArtefactMatch]] = {}
    for match in artefacts:
        by_provider.setdefault(match.rule.provider, []).append(match)
    for provider in text_providers():
        matches = by_provider.get(provider.key, [])
        strong = any(m.rule.strength == STRONG for m in matches)
        blocks: list = [Paragraph("Artefacts found" if strong else "Context only" if matches else "No artefacts found",
                                  "signal" if strong else "info" if matches else "neutral")]
        if matches:
            blocks.append(Table(["Artefact", "Count", "Example"],
                                [[m.rule.title, str(m.count), m.examples[0]] for m in matches]))
        check = provider.invisible_check if provider.key != "google" else (
            "SynthID Text cannot be checked offline. It needs the generating model's tokenizer and Google's private "
            "watermarking keys; Google's own tools are the only way to confirm it.")
        rows = [("Text technology", " ".join(f"• {t}" for t in provider.text_tech))] if provider.text_tech else []
        rows.append(("Watermark check", check if provider.key == "google" else
                     "No public text-watermark detector is available." if provider.key in ("openai", "microsoft")
                     else provider.invisible_check))
        blocks.append(KeyValues(rows))
        if provider.key == "google":
            blocks.append(Links([("About SynthID", "https://deepmind.google/models/synthid/")]))
        s.append(Section(provider.name, blocks, tab, provider.products))
    others = by_provider.get("others", [])
    s.append(Section("Other assistant artefacts", [Table(["Artefact", "Count", "Strength", "Example"],
                                                         [[m.rule.title, str(m.count),
                                                           Cell(m.rule.strength, "signal" if m.rule.strength == STRONG else "info"),
                                                           m.examples[0]] for m in others])] if others
                     else [Paragraph("None found.")], tab))

    if green is None:
        return
    tab = "Statistical"
    expected = green.gamma * green.tokens
    rows = [("Key", options.key), ("Gamma (green-list fraction)", f"{green.gamma:.2f}"),
            ("Scored word pairs", f"{green.tokens:,} (repeated pairs ignored)"),
            ("Green words", f"{green.green:,} (expected by chance: {expected:.1f})"),
            ("z-score", f"{green.z:.2f} (detection threshold {greenlist.Z_THRESHOLD:.0f})"),
            ("One-sided p-value", f"{green.p_value:.2e}"),
            ("Result", "Watermark detected" if green.detected else
             "Too little text to test" if green.tokens < 16 else "Not detected")]
    s.append(Section("Lab watermark test (green list)", [
        KeyValues(rows),
        Paragraph("This tests for the AIMarkLens lab watermark, a transparent word-level version of the green-list "
                  "scheme from Kirchenbauer et al. (2023). It is for controlled experiments: commercial watermarks "
                  "use secret keys and model tokenizers and cannot be tested this way.", "info")], tab))
