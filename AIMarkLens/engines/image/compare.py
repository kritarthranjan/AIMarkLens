"""Compare an original image with a processed copy: hashes, structure, metadata, signals and pixels."""

from __future__ import annotations

import os

from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.integrity import verify_unchanged
from AIMarkLens.core.report import Cell, Findings, Finding, KeyValues, Paragraph, ReportDocument, Section, Table, Verdict
from AIMarkLens.engines.image.analyser import ImageOptions, ImageState, inspect_image, png_picture
from AIMarkLens.engines.image.metrics import fidelity

_COMPARE_MAX_SIDE = 4096


def _metadata_map(state: ImageState) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in state.exif:
        out[f"EXIF · {e.group} · {e.key}"] = e.value
    for xmp in state.xmp:
        for e in xmp.entries:
            out[f"XMP · {e.key}"] = e.value
    for e in state.iptc:
        out[f"IPTC · {e.key}"] = e.value
    for t in state.container.text:
        out[f"{t.source} · {t.key}"] = t.value
    return out


def _carriers(state: ImageState) -> dict[str, bool]:
    c = state.container
    return {
        "EXIF": c.exif is not None,
        "XMP": bool(c.xmp_packets),
        "IPTC": c.iptc is not None,
        "Content Credentials (C2PA)": state.c2pa is not None or c.has_kind("c2pa"),
        "ICC colour profile": c.icc_profile is not None,
        "Text chunks / comments": bool(c.text),
        "Hidden LSB metadata": state.stealth is not None,
        "Data after end of image": c.trailing_bytes > 0,
    }


def compare_images(path_a: str, path_b: str, options: ImageOptions, ctx: TaskContext) -> ReportDocument:
    import cv2
    import numpy as np

    from AIMarkLens.engines.image.pixels import encode_png, reduced, to_rgb_array

    opts = ImageOptions(options.exiftool_path, options.c2patool_path, options.allow_network,
                        options.crop_size, pixel_analysis=False, external_tools=False)
    a = inspect_image(path_a, opts, ctx.sub(0.0, 0.35))
    b = inspect_image(path_b, opts, ctx.sub(0.35, 0.7))
    name_a, name_b = os.path.basename(path_a), os.path.basename(path_b)
    doc = ReportDocument(title="Image comparison", subject=f"{name_a} vs {name_b}", kind="compare")
    identical = a.fingerprint.sha256 == b.fingerprint.sha256

    files = Table(["", "A: original", "B: processed"], [
        ["File", name_a, name_b],
        ["SHA-256", a.fingerprint.sha256, b.fingerprint.sha256],
        ["Size", f"{a.fingerprint.size:,} bytes", f"{b.fingerprint.size:,} bytes"],
        ["Format", a.container.format, b.container.format],
        ["Dimensions", f"{a.container.width}×{a.container.height}", f"{b.container.width}×{b.container.height}"],
    ])
    findings: list[Finding] = []
    if identical:
        findings.append(Finding("ok", "The files are byte-for-byte identical", "Their SHA-256 hashes match."))

    carriers_a, carriers_b = _carriers(a), _carriers(b)
    carrier_rows = []
    for label in carriers_a:
        was, now = carriers_a[label], carriers_b[label]
        if was and now:
            status = Cell("Kept", "ok")
        elif was:
            status = Cell("Removed", "warning")
            findings.append(Finding("warning", f"{label} removed in B"))
        elif now:
            status = Cell("Added", "info")
            findings.append(Finding("info", f"{label} added in B"))
        else:
            status = Cell("Absent in both")
        carrier_rows.append([label, "Yes" if was else "No", "Yes" if now else "No", status])

    sig_a, sig_b = {f.title for f in a.findings if f.tone == "signal"}, {f.title for f in b.findings if f.tone == "signal"}
    signal_rows = [[title, "Yes" if title in sig_a else "No", "Yes" if title in sig_b else "No",
                    Cell("Kept", "ok") if title in sig_a and title in sig_b else
                    Cell("Lost", "warning") if title in sig_a else Cell("New", "info")]
                   for title in sorted(sig_a | sig_b)]
    lost = sig_a - sig_b
    if lost:
        findings.append(Finding("warning", f"{len(lost)} provenance signal(s) did not survive",
                                "; ".join(sorted(lost))[:300]))

    meta_a, meta_b = _metadata_map(a), _metadata_map(b)
    meta_rows, same = [], 0
    for key in sorted(set(meta_a) | set(meta_b)):
        va, vb = meta_a.get(key), meta_b.get(key)
        if va == vb:
            same += 1
            continue
        status = Cell("Removed", "warning") if vb is None else Cell("Added", "info") if va is None else Cell("Changed", "info")
        meta_rows.append([key, (va or "")[:300], (vb or "")[:300], status])

    ctx.progress(0.72, "Comparing pixels")
    pixel_blocks: list = []
    if a.decoded is not None and b.decoded is not None and not identical:
        img_a, img_b = reduced(a.decoded.image, _COMPARE_MAX_SIDE), reduced(b.decoded.image, _COMPARE_MAX_SIDE)
        arr_a, arr_b = to_rgb_array(img_a), to_rgb_array(img_b)
        notes = []
        if arr_a.shape[:2] != arr_b.shape[:2]:
            notes.append(f"Dimensions differ ({arr_a.shape[1]}×{arr_a.shape[0]} vs {arr_b.shape[1]}×{arr_b.shape[0]}); "
                         "B was resampled to A's size for the measurements below.")
            arr_b = cv2.resize(arr_b, (arr_a.shape[1], arr_a.shape[0]), interpolation=cv2.INTER_AREA)
        if img_a is not a.decoded.image:
            notes.append("Both images were reduced to fit 4096 pixels before comparison.")
        ctx.check()
        diff = np.abs(arr_a.astype(np.int16) - arr_b.astype(np.int16))
        per_pixel = diff.max(axis=2)
        psnr, ssim = fidelity(arr_a, arr_b)
        rows = [("PSNR", "identical" if psnr == float("inf") else f"{psnr:.2f} dB"),
                ("SSIM (structural similarity)", f"{ssim:.4f}"),
                ("Mean absolute difference", f"{float(diff.mean()):.3f} levels"),
                ("Maximum difference", f"{int(diff.max())} levels"),
                ("Pixels changed at all", f"{float((per_pixel > 0).mean()) * 100:.2f}%"),
                ("Pixels changed by more than 8 levels", f"{float((per_pixel > 8).mean()) * 100:.2f}%")]
        pixel_blocks += [Paragraph(n, "info") for n in notes]
        pixel_blocks.append(KeyValues(rows))
        heat = np.clip(diff.mean(axis=2) * 8, 0, 255).astype(np.uint8)
        scale = min(1.0, 512 / max(heat.shape))
        if scale < 1.0:
            heat = cv2.resize(heat, (max(1, int(heat.shape[1] * scale)), max(1, int(heat.shape[0] * scale))),
                              interpolation=cv2.INTER_AREA)
        pixel_blocks += png_picture("Difference heat map (×8)", encode_png(cv2.applyColorMap(heat, cv2.COLORMAP_INFERNO)))
        if psnr == float("inf"):
            findings.append(Finding("ok", "Decoded pixels are identical", "Only the file encoding or metadata differs."))
        else:
            findings.append(Finding("info", f"Pixels differ: PSNR {psnr:.1f} dB, SSIM {ssim:.3f}"))
    elif identical:
        pixel_blocks.append(Paragraph("The files are identical, so the pixels are identical too."))
    else:
        pixel_blocks.append(Paragraph("One of the images could not be decoded, so pixels were not compared.", "warning"))

    ctx.progress(0.92, "Verifying both originals are unchanged")
    ia = verify_unchanged(a.fingerprint, ctx.sub(0.92, 0.96))
    ib = verify_unchanged(b.fingerprint, ctx.sub(0.96, 1.0))
    doc.integrity = [("A unchanged", "Yes, verified" if ia.unchanged else "NO"),
                     ("B unchanged", "Yes, verified" if ib.unchanged else "NO")]
    if identical:
        doc.verdict = Verdict("ok", "Identical files", "A and B have the same SHA-256 hash.", caveat="")
    elif lost:
        same_pixels = any(f.title == "Decoded pixels are identical" for f in findings)
        detail = f"{len(lost)} metadata signal(s) present in A are missing from B."
        if same_pixels:
            detail += (" The pixels are identical, so B is still the same image: pixel watermarks such as SynthID "
                       "and AI-generation traces are unchanged, and AI detectors will flag B just as they flag A.")
        doc.verdict = Verdict("warning", "Provenance metadata was removed", detail, caveat="")
    else:
        doc.verdict = Verdict("info", "Files differ", "See the sections below for what changed.", caveat="")
    doc.sections += [
        Section("Summary", [Findings(findings)], "Summary"),
        Section("Files", [files], "Summary"),
        Section("Metadata carriers", [Table(["Carrier", "In A", "In B", "Status"], carrier_rows)], "Summary"),
        Section("Provenance signals", [Table(["Signal", "In A", "In B", "Status"], signal_rows)
                                       if signal_rows else Paragraph("Neither file has provenance signals.")], "Summary"),
        Section("Metadata differences", [Table(["Field", "A", "B", "Status"], meta_rows,
                                               note=f"{same} field(s) are identical and not shown.")
                                         if meta_rows else Paragraph(f"All {same} metadata field(s) are identical.")], "Metadata"),
        Section("Pixel comparison", pixel_blocks, "Pixels"),
    ]
    doc.extra["integrity_ok"] = ia.unchanged and ib.unchanged
    ctx.progress(1.0, "Comparison complete")
    return doc
