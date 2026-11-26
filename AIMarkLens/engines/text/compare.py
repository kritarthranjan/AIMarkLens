"""Compare two text samples, with invisible characters made explicit."""

from __future__ import annotations

import difflib
import unicodedata

from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.integrity import verify_unchanged
from AIMarkLens.core.report import Cell, Finding, Findings, KeyValues, Paragraph, Preformatted, ReportDocument, Section, Table, Verdict
from AIMarkLens.engines.text import greenlist
from AIMarkLens.engines.text.analyser import TextOptions
from AIMarkLens.engines.text.artefacts import find_artefacts
from AIMarkLens.engines.text.cleaning import DEFAULT_OPTIONS, clean_text
from AIMarkLens.engines.text.sample import ORIGIN_LABELS, TextSample
from AIMarkLens.engines.text.unicode_scan import CATEGORY_LABELS, classify, scan

_CHAR_DIFF_LIMIT = 200_000
_MAX_CHANGES = 300


def visible(text: str, limit: int = 120) -> str:
    """Render a snippet with invisible and control characters spelled out."""
    out = []
    for ch in text[:limit]:
        if ch == "\n":
            out.append("⏎")
        elif ch == "\r":
            out.append("␍")
        elif ch == "\t":
            out.append("⇥")
        elif classify(ch) is not None and not ch.isalpha():
            out.append(f"⟦U+{ord(ch):04X}⟧")
        else:
            out.append(ch)
    return "".join(out) + ("…" if len(text) > limit else "")


def compare_texts(a: TextSample, b: TextSample, options: TextOptions, ctx: TaskContext) -> ReportDocument:
    fa, fb = a.ensure_fingerprint(), b.ensure_fingerprint()
    doc = ReportDocument(title="Text comparison", subject=f"{a.label} vs {b.label}", kind="compare")
    ctx.progress(0.1, "Scanning both samples")
    sa, sb = scan(a.text, ctx.check), scan(b.text, ctx.check)
    findings: list[Finding] = []
    identical = a.text == b.text

    rows = [["Source", ORIGIN_LABELS.get(a.origin, a.origin), ORIGIN_LABELS.get(b.origin, b.origin)],
            ["Name", a.label, b.label], ["SHA-256", fa.sha256, fb.sha256],
            ["Characters", f"{len(a.text):,}", f"{len(b.text):,}"],
            ["Lines", f"{a.text.count(chr(10)) + 1:,}", f"{b.text.count(chr(10)) + 1:,}"],
            ["CRLF line endings", f"{a.text.count(chr(13) + chr(10)):,}", f"{b.text.count(chr(13) + chr(10)):,}"]]

    ctx.progress(0.3, "Checking normalisation equivalence")
    equivalence = []
    if identical:
        findings.append(Finding("ok", "The texts are identical", "Every character matches."))
    else:
        nfc = unicodedata.normalize("NFC", a.text) == unicodedata.normalize("NFC", b.text)
        nfkc = unicodedata.normalize("NFKC", a.text) == unicodedata.normalize("NFKC", b.text)
        cleaned = clean_text(a.text, set(DEFAULT_OPTIONS))[0] == clean_text(b.text, set(DEFAULT_OPTIONS))[0]
        newline = a.text.replace("\r\n", "\n") == b.text.replace("\r\n", "\n")
        equivalence = [("Equal after NFC normalisation", "Yes" if nfc else "No"),
                       ("Equal after NFKC normalisation", "Yes" if nfkc else "No"),
                       ("Equal after removing invisible characters", "Yes" if cleaned else "No"),
                       ("Equal apart from line endings", "Yes" if newline else "No")]
        if newline:
            findings.append(Finding("info", "Only the line endings differ"))
        elif cleaned:
            findings.append(Finding("signal", "The texts differ only by invisible characters",
                                    "They look identical on screen but are different strings."))
        elif nfkc:
            findings.append(Finding("info", "The texts differ only in Unicode normalisation"))

    cat_rows = []
    for category, label in CATEGORY_LABELS.items():
        ca, cb = sa.counts.get(category, 0), sb.counts.get(category, 0)
        if ca or cb:
            status = Cell("Removed", "warning") if ca and not cb else Cell("Added", "signal") if cb and not ca else \
                Cell("Changed", "info") if ca != cb else Cell("Same")
            cat_rows.append([label, f"{ca:,}", f"{cb:,}", status])
            if cb > ca:
                findings.append(Finding("signal" if category in ("zero-width", "tag", "variation") else "info",
                                        f"B has {cb - ca:,} more {label.lower()}"))
            elif ca > cb:
                findings.append(Finding("info", f"B has {ca - cb:,} fewer {label.lower()}"))

    art_a = {m.rule.title: m.count for m in find_artefacts(a.text)}
    art_b = {m.rule.title: m.count for m in find_artefacts(b.text)}
    art_rows = [[t, str(art_a.get(t, 0)), str(art_b.get(t, 0))] for t in sorted(set(art_a) | set(art_b))]
    ga = greenlist.detect(a.text, options.key, options.gamma) if options.statistical else None
    gb = greenlist.detect(b.text, options.key, options.gamma) if options.statistical else None

    ctx.progress(0.55, "Computing differences")
    change_blocks: list = []
    if not identical:
        if len(a.text) <= _CHAR_DIFF_LIMIT and len(b.text) <= _CHAR_DIFF_LIMIT:
            matcher = difflib.SequenceMatcher(None, a.text, b.text, autojunk=False)
            ops = [op for op in matcher.get_opcodes() if op[0] != "equal"]
            inserted = sum(j2 - j1 for tag, i1, i2, j1, j2 in ops if tag in ("insert", "replace"))
            deleted = sum(i2 - i1 for tag, i1, i2, j1, j2 in ops if tag in ("delete", "replace"))
            change_blocks.append(KeyValues([("Similarity", f"{matcher.ratio() * 100:.2f}%"),
                                            ("Edits", f"{len(ops):,}"),
                                            ("Characters removed from A", f"{deleted:,}"),
                                            ("Characters added in B", f"{inserted:,}")]))
            change_rows = [[tag.title(), f"{i1:,}", f"{j1:,}", visible(a.text[i1:i2]), visible(b.text[j1:j2])]
                           for tag, i1, i2, j1, j2 in ops[:_MAX_CHANGES]]
            change_blocks.append(Table(["Change", "Position in A", "Position in B", "A", "B"], change_rows,
                                       note=f"Showing {min(len(ops), _MAX_CHANGES):,} of {len(ops):,} edits. "
                                            "Invisible characters are shown as ⟦U+XXXX⟧."))
        else:
            diff = difflib.unified_diff(a.text.splitlines(), b.text.splitlines(), "A", "B", lineterm="", n=1)
            lines = [visible(line, 400) for _, line in zip(range(2000), diff)]
            change_blocks.append(Paragraph("The texts are long, so a line-level diff is shown.", "info"))
            change_blocks.append(Preformatted("\n".join(lines)))

    ctx.progress(0.9, "Verifying the samples are unchanged")
    integrity = []
    ok = True
    for tag, sample, fp in (("A", a, fa), ("B", b, fb)):
        if sample.origin == "file" and sample.path:
            same = verify_unchanged(fp, ctx).unchanged
        else:
            same = sample.ensure_fingerprint().sha256 == fp.sha256
        ok &= same
        integrity.append((f"{tag} unchanged", "Yes, verified" if same else "NO"))
    doc.integrity = integrity
    if identical:
        doc.verdict = Verdict("ok", "Identical texts", "A and B are the same string.", caveat="")
    elif any(f.tone == "signal" for f in findings):
        doc.verdict = Verdict("signal", "Hidden differences found",
                              "; ".join(f.title for f in findings if f.tone == "signal")[:300] + ".", caveat="")
    else:
        doc.verdict = Verdict("info", "Texts differ", "See the changes below.", caveat="")
    doc.sections += [
        Section("Summary", [Findings(findings)], "Summary"),
        Section("Samples", [Table(["", "A: original", "B: processed"], rows)], "Summary"),
    ]
    if equivalence:
        doc.sections.append(Section("Equivalence", [KeyValues(equivalence)], "Summary"))
    doc.sections += [
        Section("Invisible characters", [Table(["Category", "A", "B", "Status"], cat_rows) if cat_rows
                                         else Paragraph("Neither text contains invisible or look-alike characters.")], "Characters"),
        Section("Assistant artefacts", [Table(["Artefact", "A", "B"], art_rows) if art_rows
                                        else Paragraph("No assistant artefacts in either text.")], "Characters"),
        Section("Changes", change_blocks or [Paragraph("No changes.")], "Changes"),
    ]
    if ga is not None and gb is not None:
        doc.sections.insert(-1, Section("Lab watermark (green list)", [KeyValues([
            ("Key", options.key), ("A", f"z = {ga.z:.2f} ({'detected' if ga.detected else 'not detected'})"),
            ("B", f"z = {gb.z:.2f} ({'detected' if gb.detected else 'not detected'})")])], "Characters"))
    doc.extra["integrity_ok"] = ok
    ctx.progress(1.0, "Comparison complete")
    return doc
