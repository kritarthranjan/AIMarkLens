"""Turn online verification results into report sections, findings and verdict updates."""

from __future__ import annotations

import os
from typing import Optional

from AIMarkLens.core.report import (Cell, Finding, Findings, KeyValues, Links, Paragraph, Preformatted,
                                   ReportDocument, Section, Table, Verdict)
from AIMarkLens.engines.online.verifiers import (DETECTED, ERROR, MANUAL_CHECKS, NOT_CONFIGURED, NOT_DETECTED,
                                                UNCERTAIN, OnlineReport, OnlineResult)

TAB = "Online verification"
SOURCE_PREFIX = "Online: "
_STATUS_LABEL = {DETECTED: "AI detected", NOT_DETECTED: "Not detected", UNCERTAIN: "Inconclusive",
                 ERROR: "Failed", NOT_CONFIGURED: "Not set up"}
_STATUS_TONE = {DETECTED: "signal", NOT_DETECTED: "ok", UNCERTAIN: "warning", ERROR: "error", NOT_CONFIGURED: "neutral"}


def status_cell(result: Optional[OnlineResult]) -> Cell:
    if result is None:
        return Cell("Not run")
    label = _STATUS_LABEL.get(result.status, result.status)
    if result.kind == "synthid" and result.status == DETECTED:
        label = "SynthID detected"
    if result.score is not None and result.kind == "classifier":
        label += f" ({result.score * 100:.0f}%)"
    return Cell(label, _STATUS_TONE.get(result.status, "neutral"))


def findings_for(report: OnlineReport, label: str = "") -> list[Finding]:
    prefix = f"{label}: " if label else ""
    out: list[Finding] = []
    for r in report.results:
        source = SOURCE_PREFIX + r.name
        if r.status == DETECTED:
            title = "Google SynthID watermark detected" if r.kind == "synthid" else f"{r.name}: {r.summary}"
            out.append(Finding("signal", prefix + title, r.detail, source))
        elif r.status == NOT_DETECTED:
            out.append(Finding("info", prefix + (r.summary if r.kind == "synthid" else f"{r.name}: {r.summary}"),
                               r.detail, source))
        elif r.status == UNCERTAIN:
            out.append(Finding("warning", prefix + f"{r.name}: {r.summary}", r.detail, source))
        elif r.status == ERROR:
            out.append(Finding("error", prefix + f"{r.name}: check failed", r.detail, source))
    return out


def sections_for(report: OnlineReport) -> list[Section]:
    rows = []
    for r in report.results:
        rows.append([r.name, "SynthID watermark" if r.kind == "synthid" else "AI classifier", status_cell(r),
                     r.summary if r.status != ERROR else r.detail, f"{r.duration:.1f} s" if r.duration else ""])
    intro = (f"{os.path.basename(report.path)} (SHA-256 {report.sha256[:16]}…) was uploaded to the services below. "
             "Google's verifier reads the SynthID watermark itself; the other services are classifiers that "
             "estimate AI generation from the pixels and cannot read SynthID.")
    sections = [Section("Results", [Paragraph(intro), Table(["Service", "Method", "Result", "Details", "Time"], rows),
                                    KeyValues([("Original unchanged", "Yes, verified" if report.original_unchanged
                                                else "NO")])], TAB)]
    for r in report.results:
        if r.status == NOT_CONFIGURED:
            continue
        blocks: list = [Paragraph(r.detail, _STATUS_TONE.get(r.status, "neutral"))]
        if r.upload_note:
            blocks.append(Paragraph(r.upload_note, "info"))
        if r.raw:
            blocks.append(Preformatted(r.raw))
        sections.append(Section(r.name, blocks, TAB, "Raw service response"))
    sections.append(Section("Check manually", [
        Paragraph("Google's SynthID Detector portal and the Gemini app can also check images by hand. Upload the "
                  "file there and ask whether it was made with Google AI."),
        Links(list(MANUAL_CHECKS))], TAB))
    return sections


def merge_into(doc: ReportDocument, report: OnlineReport) -> None:
    """Add (or replace) the online results in an image analysis report and update its verdict."""
    doc.sections = [s for s in doc.sections if s.tab != TAB]
    doc.sections.extend(sections_for(report))
    findings_section = next((s for s in doc.sections if s.title == "Findings"), None)
    new = findings_for(report)
    if findings_section is not None and findings_section.blocks and isinstance(findings_section.blocks[0], Findings):
        kept = [f for f in findings_section.blocks[0].items if not f.source.startswith(SOURCE_PREFIX)]
        order = {"error": 0, "signal": 1, "warning": 2, "ok": 3, "info": 4, "neutral": 5}
        findings_section.blocks[0].items = sorted(kept + new, key=lambda f: order.get(f.tone, 9))
    detected = [r for r in report.results if r.status == DETECTED]
    answered = [r for r in report.results if r.status in (NOT_DETECTED, UNCERTAIN)]
    if not detected and answered and doc.verdict is not None and doc.verdict.tone != "signal":
        names = ", ".join(r.name for r in answered)
        doc.verdict = Verdict("info", "No AI signals found offline or online",
                              f"No embedded provenance, and these services did not flag the image: {names}. "
                              "Classifiers can miss AI images, and only Google's verifier reads SynthID.",
                              doc.verdict.caveat)
    if detected and doc.verdict is not None:
        names = ", ".join(r.name for r in detected)
        if any(r.kind == "synthid" for r in detected):
            headline = "SynthID watermark detected"
        else:
            headline = "AI generation detected online" if doc.verdict.tone != "signal" else doc.verdict.headline
        detail = f"Online verification flagged this image ({names})."
        if doc.verdict.tone == "signal":
            detail = doc.verdict.detail.rstrip(".") + f"; {detail[0].lower()}{detail[1:]}"
        doc.verdict = Verdict("signal", headline, detail, doc.verdict.caveat)
    doc.extra["online"] = report


def compare_section(a: OnlineReport, b: OnlineReport) -> Section:
    rows = []
    by_b = b.by_service()
    for ra in a.results:
        rb = by_b.get(ra.service)
        if ra.status == DETECTED and rb is not None and rb.status == DETECTED:
            change = Cell("Still detected", "signal")
        elif ra.status == DETECTED and rb is not None and rb.status == NOT_DETECTED:
            change = Cell("No longer detected", "warning")
        elif ra.status != DETECTED and rb is not None and rb.status == DETECTED:
            change = Cell("Newly detected", "signal")
        elif ERROR in (ra.status, rb.status if rb else ERROR):
            change = Cell("Incomplete", "error")
        else:
            change = Cell("Same")
        rows.append([ra.name, status_cell(ra), status_cell(rb), change])
    note = ("Removing metadata does not remove pixel watermarks: SynthID and classifier results usually stay the "
            "same. A change here means the processing altered the pixels enough to affect the detector.")
    return Section("Online detectors: original vs processed", [Table(["Service", "A: original", "B: processed",
                                                                      "Change"], rows), Paragraph(note, "info")],
                   "Online")


def attach_compare(doc: ReportDocument, a: OnlineReport, b: OnlineReport) -> None:
    """Add the online before/after table and summary findings to a comparison report."""
    doc.sections.append(compare_section(a, b))
    for report, tag in ((a, "A"), (b, "B")):
        for section in sections_for(report)[1:]:
            if section.title != "Check manually":
                doc.sections.append(Section(f"{tag}: {section.title}", section.blocks, "Online", section.description))
    summary = next((s for s in doc.sections if s.title == "Summary"), None)
    if summary is None or not summary.blocks or not isinstance(summary.blocks[0], Findings):
        return
    by_b = b.by_service()
    for ra in a.results:
        rb = by_b.get(ra.service)
        if rb is None:
            continue
        if ra.status == DETECTED and rb.status == DETECTED:
            summary.blocks[0].items.append(Finding("signal", f"{ra.name}: still detects AI in B",
                                                   "The processing did not hide the image's AI origin from this "
                                                   "detector.", SOURCE_PREFIX + ra.name))
        elif ra.status == DETECTED and rb.status == NOT_DETECTED:
            summary.blocks[0].items.append(Finding("warning", f"{ra.name}: no longer detects AI in B",
                                                   "The processing changed the pixels enough to affect this "
                                                   "detector.", SOURCE_PREFIX + ra.name))
        elif ERROR in (ra.status, rb.status):
            summary.blocks[0].items.append(Finding("error", f"{ra.name}: online check incomplete",
                                                   (rb.detail if rb.status == ERROR else ra.detail),
                                                   SOURCE_PREFIX + ra.name))
