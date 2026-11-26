"""Image analysis engine: orchestrates every read-only check and builds the report.

Each stage is isolated, so a failure in one (for example a damaged EXIF block)
is recorded as a visible warning instead of stopping the whole analysis.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from AIMarkLens.core import history, tools
from AIMarkLens.core.context import Cancelled, TaskContext
from AIMarkLens.core.errors import LabError, describe_exception
from AIMarkLens.core.integrity import Fingerprint, read_original, verify_unchanged
from AIMarkLens.core.report import (Cell, Finding, Findings, KeyValues, Links, Paragraph, Picture,
                                   Preformatted, ReportDocument, Section, Table, Verdict)
from AIMarkLens.engines.providers import CONTEXT, PROVIDERS, STRONG, Provider, SignalMatch, excerpt, match_text
from AIMarkLens.formats.c2pa import C2paManifest, C2paStore, describe_action, read_manifest_store
from AIMarkLens.formats.containers import KIND_GROUP, SUPPORTED_EXTENSIONS, ContainerInfo, parse_container
from AIMarkLens.formats.metadata import (AI_SOURCE_TYPES, SYNTHETIC_SOURCE_TYPES, MetaEntry, XmpData,
                                        describe_source_type, icc_description, parse_xmp, read_exif,
                                        read_iptc, source_type_key)


@dataclass
class ImageOptions:
    exiftool_path: str = ""
    c2patool_path: str = ""
    allow_network: bool = False
    crop_size: int = 1024
    pixel_analysis: bool = True
    external_tools: bool = True
    history_path: str = ""  # local analysis history (empty = off)


@dataclass
class ImageState:
    path: str
    options: ImageOptions
    data: bytes = b""
    fingerprint: Optional[Fingerprint] = None
    container: Optional[ContainerInfo] = None
    exif: list[MetaEntry] = field(default_factory=list)
    xmp: list[XmpData] = field(default_factory=list)
    iptc: list[MetaEntry] = field(default_factory=list)
    icc: str = ""
    c2pa: Optional[C2paStore] = None
    remote_url: str = ""
    remote_note: str = ""
    exiftool: Optional[tools.ToolResult] = None
    c2patool: Optional[tools.ToolResult] = None
    decoded: Any = None
    forensics: Any = None
    stealth: Any = None
    findings: list[Finding] = field(default_factory=list)
    stage_errors: list[tuple[str, str]] = field(default_factory=list)
    provider_matches: dict[str, list[SignalMatch]] = field(default_factory=dict)
    phash: str = ""
    history: list = field(default_factory=list)


GENERATION_CHUNK_KEYS = {
    "parameters": "AUTOMATIC1111 / Forge generation parameters",
    "prompt": "ComfyUI prompt graph",
    "workflow": "ComfyUI workflow",
    "invokeai_metadata": "InvokeAI generation metadata",
    "sd-metadata": "InvokeAI (legacy) generation metadata",
    "dream": "InvokeAI (legacy) dream command",
    "fooocus_scheme": "Fooocus generation metadata",
    "generation_data": "Generation metadata",
}


def inspect_image(path: str, options: ImageOptions, ctx: TaskContext) -> ImageState:
    """Run every read-only stage and collect the results (no report, no integrity re-check)."""
    state = ImageState(path=path, options=options)
    ctx.progress(0.0, "Reading the original file (read-only)")
    state.data, state.fingerprint = read_original(path, ctx.sub(0.0, 0.09))
    ctx.progress(0.09, "Parsing file structure")
    state.container = parse_container(state.data)

    stages: list[tuple[str, float, Callable[[ImageState, TaskContext], None]]] = [
        ("Reading EXIF, XMP and IPTC metadata", 0.15, _stage_metadata),
        ("Reading Content Credentials (C2PA)", 0.24, _stage_c2pa),
    ]
    if options.external_tools:
        stages += [("Running ExifTool", 0.36, _stage_exiftool), ("Running c2patool", 0.48, _stage_c2patool)]
    stages.append(("Decoding pixels", 0.56, _stage_decode))
    if options.pixel_analysis:
        stages.append(("Analysing pixels", 0.88, _stage_forensics))
    stages.append(("Checking for hidden LSB metadata", 0.93, _stage_stealth))
    stages.append(("Checking your analysis history", 0.95, _stage_history))
    previous = 0.09
    for label, end, func in stages:
        ctx.progress(previous, label)
        try:
            func(state, ctx.sub(previous, end))
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - every stage failure is surfaced in the report
            message, hint = describe_exception(exc)
            state.stage_errors.append((label, f"{message} {hint}".strip()))
        previous = end
    ctx.progress(0.97, "Matching provider signatures")
    _match_providers(state)
    _build_findings(state)
    ctx.progress(1.0, "Inspection complete")
    return state


def analyse_image(path: str, options: ImageOptions, ctx: TaskContext) -> ReportDocument:
    state = inspect_image(path, options, ctx.sub(0.0, 0.93))
    ctx.progress(0.93, "Verifying the original is unchanged")
    integrity = verify_unchanged(state.fingerprint, ctx.sub(0.93, 1.0))
    if not integrity.unchanged:
        state.findings.insert(0, Finding("error", "The file changed during analysis",
                                         "Another program modified the file while it was open. AIMarkLens only "
                                         "reads files; re-run the analysis once the other program has finished.",
                                         "Integrity"))
    doc = _build_report(state)
    doc.integrity = integrity.rows()
    doc.extra["integrity_ok"] = integrity.unchanged
    doc.extra["path"] = path
    doc.extra["format"] = state.container.format
    doc.extra["metadata_groups"] = sorted({KIND_GROUP[s.kind] for s in state.container.segments if s.kind in KIND_GROUP})
    doc.extra["sha256"] = state.fingerprint.sha256
    doc.extra["phash"] = state.phash
    if options.history_path:
        own_signals = [f.title for f in state.findings if f.tone == "signal" and f.source != "History"]
        history.record_analysis(options.history_path, state.fingerprint.sha256, state.phash,
                                os.path.basename(path), own_signals)
    ctx.progress(1.0, "Analysis complete")
    return doc


def signal_titles(state: ImageState) -> list[str]:
    return [f.title for f in state.findings if f.tone == "signal"]


# ---------------------------------------------------------------- stages

def _stage_metadata(state: ImageState, ctx: TaskContext) -> None:
    c = state.container
    errors = []
    if c.exif:
        try:
            state.exif = read_exif(c.exif)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"EXIF could not be decoded ({exc}).")
    for index, packet in enumerate(c.xmp_packets, start=1):
        data = parse_xmp(packet, "XMP" if len(c.xmp_packets) == 1 else f"XMP #{index}")
        state.xmp.append(data)
        errors.extend(data.errors)
    if c.iptc:
        state.iptc = read_iptc(c.iptc)
    if c.icc_profile:
        state.icc = icc_description(c.icc_profile) or "Embedded (unnamed)"
    for xmp in state.xmp:
        for url in xmp.values("dcterms:provenance", "provenance"):
            if url.strip():
                state.remote_url = url.strip()
    if errors:
        state.stage_errors.append(("Reading metadata", " ".join(errors)))


def _stage_c2pa(state: ImageState, ctx: TaskContext) -> None:
    for blob in state.container.jumbf_boxes:
        ctx.check()
        store = read_manifest_store(blob, state.data)
        if store is not None:
            state.c2pa = store
            break
    if state.c2pa is None and state.remote_url:
        if not state.options.allow_network:
            state.remote_note = ("This image references a remote Content Credentials manifest. Enable network "
                                 "verification in Settings to fetch it.")
            return
        from AIMarkLens.engines.image.remote import fetch_remote_manifest

        ctx.progress(0.3, "Fetching remote manifest")
        blob = fetch_remote_manifest(state.remote_url, ctx)
        store = read_manifest_store(blob, state.data, origin="remote")
        if store is None:
            state.remote_note = "The remote URL did not return a C2PA manifest store."
        else:
            state.c2pa = store
            state.remote_note = f"The manifest was fetched from {state.remote_url}."


def _stage_exiftool(state: ImageState, ctx: TaskContext) -> None:
    exe = tools.find_tool("exiftool", state.options.exiftool_path)
    if not exe:
        state.exiftool = tools.ToolResult("exiftool", "unavailable",
                                          "ExifTool is not installed, so the built-in readers were used. Install "
                                          "it from Settings → External tools for thousands of extra tags.")
        return
    state.exiftool = tools.run_exiftool(exe, state.data, ctx)


def _stage_c2patool(state: ImageState, ctx: TaskContext) -> None:
    exe = tools.find_tool("c2patool", state.options.c2patool_path)
    if not exe:
        state.c2patool = tools.ToolResult("c2patool", "unavailable",
                                          "c2patool is not installed, so signatures were not cryptographically "
                                          "validated. The built-in reader still decodes manifests and checks the "
                                          "content hash. Install it from Settings → External tools.")
        return
    suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[state.container.format]
    state.c2patool = tools.run_c2patool(exe, state.data, suffix, ctx, online=state.options.allow_network)


def _stage_decode(state: ImageState, ctx: TaskContext) -> None:
    from AIMarkLens.engines.image.pixels import open_image

    state.decoded = open_image(state.data)


def _stage_forensics(state: ImageState, ctx: TaskContext) -> None:
    if state.decoded is None:
        return
    from AIMarkLens.engines.image.forensics import analyse_pixels

    state.forensics = analyse_pixels(state.decoded.image, state.options.crop_size, ctx)


def _stage_history(state: ImageState, ctx: TaskContext) -> None:
    if state.decoded is not None:
        from AIMarkLens.engines.image.pixels import perceptual_hash

        state.phash = perceptual_hash(state.decoded.image)
    if state.options.history_path:
        state.history = history.lookup(state.options.history_path, state.fingerprint.sha256, state.phash)


def _history_findings(state: ImageState) -> None:
    """Report what is already known about this image from earlier analyses on this computer."""
    for match in state.history:
        evidence = "; ".join(match.signals[:3])
        online = history.online_summary(match.online_detections)
        known = "; ".join(x for x in (evidence, f"online detectors: {online}" if online else "") if x)
        if match.kind == "copy-of":
            tone = "signal" if match.has_ai_evidence else "info"
            state.findings.append(Finding(
                tone, f"Metadata-free copy of {match.name}",
                (f"AIMarkLens made this file from {match.name} (analysed {match.when}), which had: {known}. " if known
                 else f"AIMarkLens made this file from {match.name} (analysed {match.when}). ")
                + "Only metadata was removed: the pixels are identical, so pixel watermarks such as SynthID and "
                  "AI-generation traces are still present.", "History"))
        elif match.kind == "lookalike" and match.has_ai_evidence:
            state.findings.append(Finding(
                "signal", f"Visually identical to {match.name}, which had AI signals",
                f"Analysed on this computer on {match.when}: {known}. Re-saving, resizing or removing metadata does "
                "not change what an image shows, so its AI origin still applies.", "History"))
        elif match.kind == "same-file" and match.online_detections:
            state.findings.append(Finding(
                "signal", "Online detectors flagged this exact file earlier",
                f"{history.online_summary(match.online_detections)} (checked {match.online_detections[0].get('when', '')}).",
                "History"))


def _stage_stealth(state: ImageState, ctx: TaskContext) -> None:
    if state.decoded is None:
        return
    from AIMarkLens.engines.image.forensics import detect_stealth_metadata

    state.stealth = detect_stealth_metadata(state.decoded.image)


# ---------------------------------------------------------------- providers

def _signal_sources(state: ImageState) -> list[tuple[str, str, str]]:
    sources: list[tuple[str, str, str]] = []
    sources += [("EXIF", f"{e.group}: {e.key}", e.value) for e in state.exif]
    for xmp in state.xmp:
        sources += [("XMP", e.key, e.value) for e in xmp.entries]
    sources += [("IPTC", e.key, e.value) for e in state.iptc]
    sources += [(t.source, t.key, t.value) for t in state.container.text]
    if state.c2pa:
        for m in state.c2pa.manifests:
            if m.generator_text:
                sources.append(("C2PA", "Claim generator", m.generator_text))
            for a in m.actions:
                if a.software_agent:
                    sources.append(("C2PA", f"Action {a.action}: software agent", a.software_agent))
                if a.description:
                    sources.append(("C2PA", f"Action {a.action}: description", a.description))
    if state.exiftool and isinstance(state.exiftool.data, dict):
        for key, value in state.exiftool.data.items():
            if isinstance(value, (str, int, float)):
                sources.append(("ExifTool", key, str(value)))
    if state.stealth and state.stealth.payload:
        sources.append(("Stealth LSB metadata", state.stealth.signature, state.stealth.payload))
    return sources


def _match_providers(state: ImageState) -> None:
    sources = _signal_sources(state)
    file_name = os.path.basename(state.path)
    for provider in PROVIDERS:
        matches: list[SignalMatch] = []
        seen: set[tuple[str, str]] = set()
        for source, fld, value in sources:
            found = match_text(provider, value)
            if not found:
                continue
            text, strength = next(((t, st) for t, st in found if st == STRONG), found[0])
            leaf = fld.rsplit(":", 1)[-1].strip().lower()
            key = (leaf, value[:200].lower())
            if key in seen:
                continue
            seen.add(key)
            matches.append(SignalMatch(source, fld, excerpt(value, text), strength))
        for text, _strength in match_text(provider, file_name.replace("_", " ")):
            matches.append(SignalMatch("File name", "Name", file_name, CONTEXT))
            break
        if state.c2pa:
            for m in state.c2pa.manifests:
                signer = (m.signer or "").lower()
                if signer and any(org in signer for org in provider.signer_orgs):
                    matches.append(SignalMatch("C2PA", "Signed by", m.signer, STRONG))
        state.provider_matches[provider.key] = matches


def _provider_status(matches: list[SignalMatch]) -> tuple[str, str]:
    if any(m.strength == STRONG for m in matches):
        return "signal", "Provenance signals found"
    if matches:
        return "info", "Vendor mentioned, no AI product identified"
    return "neutral", "No signals found"


# ---------------------------------------------------------------- findings and verdict

def _build_findings(state: ImageState) -> None:
    f = state.findings
    c = state.container
    store = state.c2pa
    if store:
        active = store.active
        origin = "embedded" if store.origin == "embedded" else "remote"
        if active is not None:
            who = active.signer or "an unknown signer"
            tool = active.generator_text or "an unnamed tool"
            f.append(Finding("signal", f"Content Credentials (C2PA) manifest found ({origin})",
                             f"{len(store.manifests)} manifest(s). Active manifest produced by {tool}, signed by {who}.",
                             "C2PA"))
            if active.declares_ai:
                types = ", ".join(sorted({describe_source_type(t) for t in active.source_types()}))
                f.append(Finding("signal", "The C2PA manifest declares generative AI", types, "C2PA"))
            binding = active.hard_binding
            if binding and binding.status == "match":
                f.append(Finding("ok", "File content matches the signed manifest", binding.detail, "C2PA"))
            elif binding and binding.status == "mismatch":
                f.append(Finding("warning", "File content no longer matches the signed manifest", binding.detail, "C2PA"))
        for error in store.errors + [e for m in store.manifests for e in m.errors]:
            f.append(Finding("warning", "C2PA structure problem", error, "C2PA"))
    elif c.has_kind("c2pa"):
        f.append(Finding("warning", "A JUMBF box was found but it is not a readable C2PA manifest store",
                         "The provenance data may be damaged or use an unsupported layout.", "C2PA"))
    if state.remote_note:
        f.append(Finding("info", "Remote Content Credentials referenced", state.remote_note, "XMP"))

    for xmp in state.xmp:
        for value in xmp.values("Iptc4xmpExt:DigitalSourceType", "DigitalSourceType"):
            key = source_type_key(value)
            tone = "signal" if key in AI_SOURCE_TYPES or key in SYNTHETIC_SOURCE_TYPES else "info"
            f.append(Finding(tone, f"IPTC digital source type: {describe_source_type(value)}", value, "XMP"))
        for credit in xmp.values("photoshop:Credit", "Credit"):
            if "ai" in credit.lower().split() or "google ai" in credit.lower():
                f.append(Finding("signal", f"Credit line: {credit}", "Recorded in XMP photoshop:Credit.", "XMP"))

    for entry in c.text:
        label = GENERATION_CHUNK_KEYS.get(entry.key.lower())
        if label and entry.value.strip():
            f.append(Finding("signal", f"Generation parameters embedded ({label})",
                             f"Text chunk '{entry.key}': {entry.value[:160]}{'…' if len(entry.value) > 160 else ''}",
                             entry.source))
    for e in state.exif:
        if e.key == "UserComment" and ("Steps:" in e.value or "Negative prompt:" in e.value):
            f.append(Finding("signal", "Generation parameters in EXIF UserComment", e.value[:160], "EXIF"))

    if state.stealth:
        if state.stealth.payload:
            f.append(Finding("signal", f"Hidden generation metadata in {state.stealth.channel} LSBs",
                             f"Signature '{state.stealth.signature}'. {state.stealth.payload[:160]}",
                             "Pixels"))
        else:
            f.append(Finding("warning", "A stealth metadata signature was found but could not be read",
                             state.stealth.error, "Pixels"))

    for provider in PROVIDERS:
        matches = state.provider_matches.get(provider.key, [])
        strong = [m for m in matches if m.strength == STRONG and m.source not in ("C2PA",)]
        if strong:
            sample = strong[0]
            f.append(Finding("signal", f"{provider.name} referenced in metadata",
                             f"{sample.field}: {sample.excerpt}", sample.source))

    camera = {e.key for e in state.exif if e.key in ("Make", "Model")}
    if not camera:
        f.append(Finding("info", "No camera make or model recorded",
                         "Common for AI images, but also for screenshots, edited and web-optimised images.", "EXIF"))

    if state.forensics:
        colour = [ch for ch in state.forensics.lsb if ch.channel != "Alpha"]
        suspicious = [ch.channel for ch in colour if ch.pair_balance_p > 0.95]
        if suspicious:
            f.append(Finding("warning", "LSB statistics consistent with hidden data",
                             f"Pair-balance test p > 0.95 in {', '.join(suspicious)}. This can indicate full-capacity "
                             "LSB embedding, but also heavy noise or synthetic gradients.", "Pixels"))
        unexplained = [p for p in state.forensics.peaks if not p.note]
        if len(unexplained) >= 3:
            f.append(Finding("info", f"{len(unexplained)} periodic patterns in the noise residual",
                             "Could come from texture, resampling or a periodic watermark. See the Forensics tab.",
                             "Pixels"))
    if c.trailing_bytes:
        f.append(Finding("info", f"{c.trailing_bytes:,} bytes of data after the end of the image",
                         "May be a secondary image (for example a gain map) or appended content.", "Structure"))
    ext = os.path.splitext(state.path)[1].lower()
    if ext in SUPPORTED_EXTENSIONS and SUPPORTED_EXTENSIONS[ext] != c.format:
        f.append(Finding("warning", "The file extension does not match its contents",
                         f"Named {ext} but the data is {c.format}.", "Structure"))
    for warning in c.warnings:
        f.append(Finding("warning", "Structure warning", warning, "Structure"))
    for stage, message in state.stage_errors:
        f.append(Finding("error", f"{stage}: not completed", message, "AIMarkLens"))
    for result in (state.exiftool, state.c2patool):
        if result and result.status == "error":
            f.append(Finding("error", f"{tools.TOOL_LABELS[result.tool]} reported an error",
                             f"{result.message} {result.stderr[:300]}".strip(), tools.TOOL_LABELS[result.tool]))

    _history_findings(state)
    if not any(x.tone == "signal" for x in f):
        f.append(Finding("info", "Not checked for SynthID or AI-generation traces",
                         "These live in the pixels, not the metadata, so offline checks cannot see them. Use Verify "
                         "online (Google SynthID and AI-image detectors) to check.", "Online: not run"))
    order = {"error": 0, "signal": 1, "warning": 2, "ok": 3, "info": 4, "neutral": 5}
    f.sort(key=lambda item: order.get(item.tone, 9))


def _verdict(state: ImageState) -> Verdict:
    signals = [x for x in state.findings if x.tone == "signal"]
    if signals:
        detail = "; ".join(s.title for s in signals[:3]) + ("; and more." if len(signals) > 3 else ".")
        if all(s.source == "History" for s in signals):
            return Verdict("signal", "AI origin known from an earlier analysis",
                           detail + " This file has no AI metadata of its own, but its pixels match an image with "
                                    "AI signals.")
        return Verdict("signal", "Provenance signals found", detail)
    not_proof = (" This does not mean the image is human-made: metadata is easily removed, and pixel watermarks "
                 "such as SynthID and AI-generation traces can only be checked online (Verify online).")
    mentions = [p.name for p in PROVIDERS if state.provider_matches.get(p.key)]
    if mentions:
        return Verdict("info", "No AI provenance metadata: origin not determined",
                       f"Vendor names appear in metadata ({', '.join(mentions)}), but none identify an AI product "
                       "or declare AI generation." + not_proof)
    return Verdict("info", "No embedded provenance: origin not determined",
                   "No C2PA manifest, AI source declaration, generation parameters or readable hidden metadata "
                   "were found in this file." + not_proof)


# ---------------------------------------------------------------- report

def png_picture(caption: str, png: Optional[bytes]) -> list:
    """Wrap PNG bytes as a Picture block, reading the size from the IHDR chunk."""
    if not png or len(png) < 24:
        return []
    width, height = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return [Picture(caption, png, width, height)]


def _build_report(state: ImageState) -> ReportDocument:
    c = state.container
    name = os.path.basename(state.path)
    doc = ReportDocument(title="Image provenance analysis", subject=name, kind="image", verdict=_verdict(state))
    sections = doc.sections

    image_rows = [("File", name), ("Format", c.format)]
    if c.width and c.height:
        image_rows.append(("Dimensions", f"{c.width:,} × {c.height:,} pixels ({c.width * c.height / 1e6:.1f} MP)"))
    if state.decoded is not None:
        image_rows.append(("Pixel mode", f"{state.decoded.mode}{', with transparency' if state.decoded.has_alpha else ''}"))
    image_rows += list(c.details.items())
    if state.icc:
        image_rows.append(("ICC profile", state.icc))
    if c.format == "JPEG":
        from AIMarkLens.engines.image.forensics import estimate_jpeg_quality

        quality = estimate_jpeg_quality(c.quant_tables)
        if quality:
            image_rows.append(("Estimated JPEG quality", f"about {quality} (from quantisation tables)"))
    image_rows.append(("File size", f"{len(state.data):,} bytes"))
    sections.append(Section("Findings", [Findings(state.findings)], "Summary"))
    sections.append(Section("Image", [KeyValues(image_rows)], "Summary"))

    _provider_sections(state, sections)
    _c2pa_sections(state, sections)
    _metadata_sections(state, sections)

    seg_rows = [[f"{s.offset:,}", f"{s.length:,}", s.name, s.kind, s.note] for s in c.segments]
    blocks: list = [Table(["Offset", "Length", "Segment", "Kind", "Notes"], seg_rows)]
    if c.warnings:
        blocks.append(Paragraph(" ".join(c.warnings), "warning"))
    sections.append(Section("File structure", blocks, "Structure",
                            "Every segment or chunk in the file, in order. Nothing was rewritten."))
    _forensics_sections(state, sections)
    return doc


def _provider_sections(state: ImageState, sections: list[Section]) -> None:
    ranked: list[tuple[int, Provider]] = []
    for provider in PROVIDERS:
        tone, _ = _provider_status(state.provider_matches.get(provider.key, []))
        ranked.append(({"signal": 0, "info": 1}.get(tone, 2), provider))
    ranked.sort(key=lambda pair: pair[0])
    for _, provider in ranked:
        matches = state.provider_matches.get(provider.key, [])
        tone, status = _provider_status(matches)
        blocks: list = [Paragraph(status, tone if tone != "neutral" else "neutral")]
        if matches:
            rows = [[m.source, m.field, m.excerpt, Cell(m.strength, "signal" if m.strength == STRONG else "info")]
                    for m in matches]
            blocks.append(Table(["Source", "Field", "Match", "Strength"], rows))
        tech = [("Image technology", " ".join(f"• {t}" for t in provider.image_tech))] if provider.image_tech else []
        tech.append(("Invisible watermark check", provider.invisible_check))
        blocks.append(KeyValues(tech))
        if provider.links:
            blocks.append(Links(list(provider.links)))
        sections.append(Section(provider.name, blocks, "Providers", provider.products))


def _manifest_rows(m: C2paManifest) -> list[tuple[str, str]]:
    rows = [("Manifest", m.label), ("Claim version", str(m.claim_version))]
    if m.generator_text:
        rows.append(("Claim generator", m.generator_text))
    if m.title:
        rows.append(("Title", m.title))
    if m.format:
        rows.append(("Format", m.format))
    if m.certificate:
        from AIMarkLens.formats.der import CertificateInfo

        rows.append(("Signed by", CertificateInfo.format_name(m.certificate.subject)))
        rows.append(("Certificate issuer", CertificateInfo.format_name(m.certificate.issuer)))
        rows.append(("Certificate valid", f"{m.certificate.not_before} to {m.certificate.not_after}"))
    if m.signature_algorithm:
        rows.append(("Signature algorithm", m.signature_algorithm))
    rows.append(("Trusted timestamp", "Present" if m.has_timestamp else "Not present"))
    if m.hard_binding:
        rows.append(("Content hash binding", f"{m.hard_binding.status.upper()} ({m.hard_binding.algorithm}): "
                                             f"{m.hard_binding.detail}"))
    if m.authors:
        rows.append(("Author(s)", ", ".join(m.authors)))
    return rows


def _c2pa_sections(state: ImageState, sections: list[Section]) -> None:
    tab = "Content Credentials"
    store = state.c2pa
    if store is None:
        blocks: list = [Paragraph("No Content Credentials (C2PA) manifest was found in this file.")]
        if state.remote_note:
            blocks.append(Paragraph(state.remote_note, "info"))
        blocks.append(Paragraph("Screenshots, many social platforms and most editors remove C2PA manifests, so "
                                "their absence says little about origin."))
        sections.append(Section("Manifest store", blocks, tab))
    else:
        summary = [("Location", "Embedded in the file" if store.origin == "embedded" else "Remote (fetched)"),
                   ("Store size", f"{store.size:,} bytes"), ("Manifests", str(len(store.manifests)))]
        sections.append(Section("Manifest store", [KeyValues(summary)], tab,
                                "Decoded by AIMarkLens' built-in reader. The last manifest is the active one."))
        for index, m in enumerate(reversed(store.manifests)):
            title = "Active manifest" if index == 0 else f"Earlier manifest {len(store.manifests) - index}"
            blocks = [KeyValues(_manifest_rows(m))]
            if m.actions:
                rows = []
                for a in m.actions:
                    tone = "signal" if source_type_key(a.digital_source_type) in AI_SOURCE_TYPES else "neutral"
                    rows.append([a.action, a.software_agent, Cell(describe_source_type(a.digital_source_type), tone)
                                 if a.digital_source_type else "", a.when])
                blocks.append(Table(["Action", "Software agent", "Digital source type", "When"], rows))
            if m.ingredients:
                blocks.append(Table(["Ingredient", "Relationship", "Format", "Has own manifest"],
                                    [[i.title, i.relationship, i.format, "Yes" if i.has_manifest else "No"]
                                     for i in m.ingredients]))
            if m.training_mining:
                blocks.append(Table(["AI training and mining permission", "Use"],
                                    [[k, v] for k, v in m.training_mining]))
            if m.assertions:
                blocks.append(Table(["Assertions"], [[a] for a in m.assertions]))
            for error in m.errors:
                blocks.append(Paragraph(error, "warning"))
            sections.append(Section(title, blocks, tab))
    result = state.c2patool
    if result is not None:
        blocks = [Paragraph(result.message, {"ok": "ok", "no-manifest": "neutral", "error": "error"}.get(result.status, "info"))]
        if result.status == "ok":
            blocks.append(KeyValues(tools.summarise_c2patool(result.data)))
            text = json.dumps(result.data, indent=2, ensure_ascii=False)
            blocks.append(Preformatted(text[:60000] + ("\n…" if len(text) > 60000 else "")))
        elif result.stderr:
            blocks.append(Preformatted(result.stderr[:4000]))
        sections.append(Section("Cryptographic validation (c2patool)", blocks, tab))
    sections.append(Section("Verify online", [
        Paragraph("You can upload a copy to the public Content Credentials verifier to cross-check the signature "
                  "chain. This sends the file to a third party."),
        Links([("Content Credentials Verify", "https://contentcredentials.org/verify")])], tab))


def _metadata_sections(state: ImageState, sections: list[Section]) -> None:
    tab = "Metadata"
    if state.exif:
        sections.append(Section("EXIF", [Table(["Group", "Tag", "Value"], [[e.group, e.key, e.value] for e in state.exif])], tab))
    else:
        sections.append(Section("EXIF", [Paragraph("No EXIF data.")], tab))
    if state.xmp:
        for xmp in state.xmp:
            blocks: list = [Table(["Property", "Value"], [[e.key, e.value] for e in xmp.entries])] if xmp.entries else []
            blocks += [Paragraph(err, "warning") for err in xmp.errors]
            sections.append(Section(xmp.entries[0].group if xmp.entries else "XMP", blocks or [Paragraph("Empty XMP packet.")], tab))
    else:
        sections.append(Section("XMP", [Paragraph("No XMP packet.")], tab))
    if state.iptc:
        sections.append(Section("IPTC (IIM)", [Table(["Dataset", "Value"], [[e.key, e.value] for e in state.iptc])], tab))
    if state.container.text:
        sections.append(Section("Text chunks and comments",
                                [Table(["Source", "Key", "Value"], [[t.source, t.key, t.value] for t in state.container.text])], tab))
    result = state.exiftool
    if result is not None:
        if result.status == "ok" and isinstance(result.data, dict):
            rows = [[k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v] for k, v in result.data.items()]
            sections.append(Section("ExifTool (all tags)", [Table(["Group:Tag", "Value"], rows)], tab,
                                    f"{result.message}. The file contents were piped to ExifTool; it never saw the original path."))
        else:
            tone = "error" if result.status == "error" else "info"
            sections.append(Section("ExifTool", [Paragraph(result.message, tone)], tab))


def _forensics_sections(state: ImageState, sections: list[Section]) -> None:
    tab = "Forensics"
    fr = state.forensics
    if fr is None:
        sections.append(Section("Pixel analysis", [Paragraph("Pixel analysis did not run because the image could not "
                                                             "be decoded. See the findings for details.", "warning")], tab))
        return
    left, top, right, bottom = fr.crop_box
    note = (f"Measured on a full-resolution {right - left}×{bottom - top} centre crop (x {left}–{right}, y {top}–{bottom}). "
            "Resampling would destroy LSB evidence, so the crop is never resized before measurement.")
    blocks: list = [Paragraph(note)]
    blocks += png_picture("Green-channel LSB plane", fr.lsb_png)
    blocks += png_picture("Noise residual (×10)", fr.residual_png)
    blocks += png_picture("Noise-residual spectrum (log magnitude)", fr.spectrum_png)
    sections.append(Section("Visualisations", blocks, tab))
    rows = []
    for ch in fr.lsb:
        p_text = "n/a" if ch.pair_balance_p != ch.pair_balance_p else f"{ch.pair_balance_p:.4f}"
        tone = "warning" if ch.pair_balance_p == ch.pair_balance_p and ch.pair_balance_p > 0.95 else "neutral"
        rows.append([ch.channel, f"{ch.ones_ratio:.4f}", Cell(p_text, tone), f"{ch.neighbour_corr:+.4f}"])
    lsb_blocks: list = [Table(["Channel", "LSB ones ratio", "Pair-balance p", "Neighbour correlation"], rows,
                              note="Pair-balance p near 1 means even/odd value pairs are equalised, the classic sign of "
                                   "full-capacity LSB embedding. Natural images usually give p close to 0.")]
    if fr.alpha_note:
        lsb_blocks.append(Paragraph(fr.alpha_note))
    lsb_blocks.append(KeyValues([("Noise residual standard deviation", f"{fr.residual_std:.3f}")]))
    sections.append(Section("Least-significant-bit statistics", lsb_blocks, tab))
    if fr.peaks:
        peak_rows = [[f"{p.fx:+.4f}", f"{p.fy:+.4f}",
                      f"{1 / max(1e-9, (p.fx ** 2 + p.fy ** 2) ** 0.5):.2f} px", f"{p.strength:.2f}",
                      p.note or "Unexplained"] for p in fr.peaks]
        sections.append(Section("Periodic patterns", [Table(["fx (cycles/px)", "fy (cycles/px)", "Period", "Strength", "Interpretation"],
                                                            peak_rows)], tab,
                                "Peaks in the spectrum of the noise residual. Grid-aligned peaks usually come from "
                                "compression or resampling; others may be texture or embedded patterns."))
    else:
        sections.append(Section("Periodic patterns", [Paragraph("No strong periodic patterns were found in the noise residual.")], tab))
    stealth = state.stealth
    if stealth:
        rows = [("Signature", stealth.signature), ("Channel", stealth.channel),
                ("Compressed", "Yes" if stealth.compressed else "No")]
        blocks = [KeyValues(rows)]
        blocks.append(Preformatted(stealth.payload) if stealth.payload else Paragraph(stealth.error, "warning"))
        sections.append(Section("Hidden LSB metadata", blocks, tab))
    else:
        sections.append(Section("Hidden LSB metadata", [Paragraph(
            "No stealth generation metadata (as written by AUTOMATIC1111 extensions or NovelAI) was found.")], tab))
    sections.append(Section("About invisible watermarks", [Paragraph(
        "Production watermarks such as Google SynthID are designed to be undetectable by statistical tests like "
        "these, and can only be confirmed with the provider's detector. Use these measurements to study how "
        "watermarking and processing change images, not as proof that a watermark is absent.", "info")], tab))
