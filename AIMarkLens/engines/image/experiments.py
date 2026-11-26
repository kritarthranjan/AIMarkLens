"""Controlled robustness experiments: how do common transformations affect provenance signals?

Each transformation is applied in memory to a working copy of the sample. The
original file is only read. For every result the experiment measures which
signals survive: EXIF, XMP, C2PA, hidden LSB metadata and the AIMarkLens lab
watermark, plus image fidelity (PSNR / SSIM).
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Callable, Optional

from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.integrity import atomic_write_bytes, read_original, verify_unchanged
from AIMarkLens.core.report import Cell, KeyValues, Paragraph, ReportDocument, Section, Table
from AIMarkLens.engines.image import labmark
from AIMarkLens.engines.image.forensics import detect_stealth_metadata
from AIMarkLens.engines.image.metrics import fidelity
from AIMarkLens.engines.image.pixels import open_image, to_rgb_array
from AIMarkLens.formats.containers import parse_container

WORKING_MAX_SIDE = 4096


@dataclass(frozen=True)
class Transform:
    key: str
    label: str
    geometric: bool
    apply: Callable  # (PIL.Image, original_bytes, format) -> bytes


def _encode(img, fmt: str, **kwargs) -> bytes:
    buf = io.BytesIO()
    if fmt == "JPEG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(buf, fmt, **kwargs)
    return buf.getvalue()


def _resize(img, factor: float):
    from PIL import Image

    size = (max(1, round(img.width * factor)), max(1, round(img.height * factor)))
    return img.resize(size, Image.Resampling.LANCZOS)


def _crop(img, keep: float):
    w, h = img.width, img.height
    cw, ch = round(w * keep), round(h * keep)
    left, top = (w - cw) // 2, (h - ch) // 2
    return img.crop((left, top, left + cw, top + ch))


def _noise(img, sigma: float):
    import numpy as np
    from PIL import Image

    arr = to_rgb_array(img).astype(np.float32)
    rng = np.random.default_rng(1234)
    arr += rng.normal(0, sigma, arr.shape).astype(np.float32)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _blur(img, sigma: float):
    from PIL import ImageFilter

    return img.filter(ImageFilter.GaussianBlur(sigma))


def _brightness(img, factor: float):
    from PIL import ImageEnhance

    return ImageEnhance.Brightness(img.convert("RGB")).enhance(factor)


def _rotate(img, degrees: float):
    from PIL import Image

    return img.convert("RGB").rotate(degrees, resample=Image.Resampling.BICUBIC, expand=False)


def _mirror(img):
    from PIL import ImageOps

    return ImageOps.mirror(img)


TRANSFORMS: tuple[Transform, ...] = (
    Transform("png", "Lossless PNG re-save", False, lambda im, raw, f: _encode(im, "PNG")),
    Transform("jpeg90", "JPEG quality 90", False, lambda im, raw, f: _encode(im, "JPEG", quality=90)),
    Transform("jpeg75", "JPEG quality 75", False, lambda im, raw, f: _encode(im, "JPEG", quality=75)),
    Transform("jpeg50", "JPEG quality 50", False, lambda im, raw, f: _encode(im, "JPEG", quality=50)),
    Transform("webp80", "WebP quality 80", False, lambda im, raw, f: _encode(im, "WEBP", quality=80)),
    Transform("resize50", "Downscale to 50%", True, lambda im, raw, f: _encode(_resize(im, 0.5), "PNG")),
    Transform("resize25", "Downscale to 25%", True, lambda im, raw, f: _encode(_resize(im, 0.25), "PNG")),
    Transform("screenshot", "Screenshot simulation (80% + PNG)", True, lambda im, raw, f: _encode(_resize(im, 0.8), "PNG")),
    Transform("crop90", "Centre crop to 90%", True, lambda im, raw, f: _encode(_crop(im, 0.9), "PNG")),
    Transform("rotate2", "Rotate 2°", True, lambda im, raw, f: _encode(_rotate(im, 2), "PNG")),
    Transform("mirror", "Mirror horizontally", True, lambda im, raw, f: _encode(_mirror(im), "PNG")),
    Transform("blur", "Gaussian blur (radius 1)", False, lambda im, raw, f: _encode(_blur(im, 1.0), "PNG")),
    Transform("noise", "Gaussian noise (σ 5)", False, lambda im, raw, f: _encode(_noise(im, 5), "PNG")),
    Transform("bright", "Brightness +15%", False, lambda im, raw, f: _encode(_brightness(im, 1.15), "PNG")),
)
TRANSFORM_BY_KEY = {t.key: t for t in TRANSFORMS}


def _metadata_flags(data: bytes) -> dict[str, bool]:
    info = parse_container(data)
    return {"EXIF": info.exif is not None, "XMP": bool(info.xmp_packets),
            "C2PA": info.has_kind("c2pa"), "Text": bool(info.text)}


def _kept(original: bool, now: bool) -> Cell:
    if not original:
        return Cell("n/a")
    return Cell("Kept", "ok") if now else Cell("Lost", "warning")


def run_image_experiment(path: str, transform_keys: list[str], key: str, ctx: TaskContext) -> ReportDocument:
    ctx.progress(0.0, "Reading the sample (read-only)")
    data, fingerprint = read_original(path, ctx.sub(0.0, 0.05))
    fmt = parse_container(data).format
    decoded = open_image(data)
    base = decoded.image
    notes = []
    if max(base.width, base.height) > WORKING_MAX_SIDE:
        from AIMarkLens.engines.image.pixels import reduced

        base = reduced(base, WORKING_MAX_SIDE)
        notes.append(f"The working copy was reduced to {base.width}×{base.height} to limit memory use, so "
                     "LSB-based signals cannot survive any transformation in this run.")
    base_rgb = to_rgb_array(base)
    flags = _metadata_flags(data)
    stealth0 = detect_stealth_metadata(decoded.image) is not None
    mark0 = labmark.detect(base_rgb, key) if key else None

    headers = ["Transformation", "Size", "PSNR", "SSIM", "EXIF", "XMP", "C2PA", "Text chunks", "Hidden LSB data"]
    if key:
        headers.append("Lab watermark")
    rows = []
    base_row = ["Original (no change)", f"{len(data) / 1024:,.0f} KB", "identical", "1.000",
                _kept(flags["EXIF"], True), _kept(flags["XMP"], True), _kept(flags["C2PA"], True),
                _kept(flags["Text"], True), _kept(stealth0, True)]
    if mark0 is not None:
        base_row.append(Cell(mark0.describe(), "ok" if mark0.detected else "neutral"))
    rows.append(base_row)

    chosen = [TRANSFORM_BY_KEY[k] for k in transform_keys if k in TRANSFORM_BY_KEY]
    for index, transform in enumerate(chosen):
        ctx.progress(0.08 + 0.88 * index / max(1, len(chosen)), f"Applying: {transform.label}")
        out_bytes = transform.apply(base, data, fmt)
        out_img = open_image(out_bytes).image
        out_rgb = to_rgb_array(out_img)
        psnr, ssim = fidelity(base_rgb, out_rgb)
        now = _metadata_flags(out_bytes)
        stealth = detect_stealth_metadata(out_img) is not None if stealth0 else False
        row = [transform.label, f"{len(out_bytes) / 1024:,.0f} KB",
               ("identical" if psnr == float("inf") else f"{psnr:.1f} dB") if psnr is not None else "n/a (resized)",
               f"{ssim:.3f}" if ssim is not None else "n/a",
               _kept(flags["EXIF"], now["EXIF"]), _kept(flags["XMP"], now["XMP"]),
               _kept(flags["C2PA"], now["C2PA"]), _kept(flags["Text"], now["Text"]), _kept(stealth0, stealth)]
        if key:
            result = labmark.detect(out_rgb, key)
            if mark0 is not None and mark0.detected:
                row.append(Cell(result.describe(), "ok" if result.detected else "warning"))
            else:
                row.append(Cell(result.describe(), "neutral"))
        rows.append(row)
        del out_img, out_rgb

    ctx.progress(0.97, "Verifying the original is unchanged")
    integrity = verify_unchanged(fingerprint, ctx.sub(0.97, 1.0))
    doc = ReportDocument(title="Image robustness experiment", subject=os.path.basename(path), kind="experiment")
    doc.integrity = integrity.rows()
    explanation = ("Transformations are applied in memory, the way a re-upload, screenshot or edit would. Pillow "
                   "re-encodes without copying metadata, which mirrors many platforms. ")
    if key and (mark0 is None or not mark0.detected):
        explanation += ("The sample does not carry the lab watermark for this key, so the watermark column shows "
                        "chance-level matches. Embed the lab watermark into a copy first to study its robustness.")
    blocks = [Paragraph(explanation)] + [Paragraph(n, "warning") for n in notes]
    blocks.append(Table(headers, rows))
    doc.sections.append(Section("Results", blocks, "Results"))
    doc.sections.append(Section("Sample", [KeyValues([
        ("File", os.path.basename(path)), ("Format", fmt),
        ("Working size", f"{base.width}×{base.height}"),
        ("Lab watermark key", key or "(not used)")])], "Results"))
    doc.extra["integrity_ok"] = integrity.unchanged
    ctx.progress(1.0, "Experiment complete")
    return doc


@dataclass
class EmbedResult:
    output_path: str
    psnr: float
    detection: labmark.LabmarkResult
    original_unchanged: bool


def embed_into_copy(path: str, out_path: str, key: str, strength: float, ctx: TaskContext) -> EmbedResult:
    from PIL import Image

    ctx.progress(0.0, "Reading the sample (read-only)")
    data, fingerprint = read_original(path, ctx.sub(0.0, 0.1))
    decoded = open_image(data)
    has_alpha = decoded.has_alpha
    rgb = to_rgb_array(decoded.image, with_alpha=has_alpha)
    ctx.progress(0.25, "Embedding the lab watermark")
    marked = labmark.embed(rgb, key, strength)
    ctx.progress(0.7, "Saving the watermarked copy (lossless PNG)")
    buf = io.BytesIO()
    Image.fromarray(marked, "RGBA" if has_alpha else "RGB").save(buf, "PNG")
    atomic_write_bytes(out_path, buf.getvalue(), protected=[path])
    ctx.progress(0.85, "Verifying the copy")
    with open(out_path, "rb") as fh:
        check = to_rgb_array(open_image(fh.read()).image)
    detection = labmark.detect(check, key)
    unchanged = verify_unchanged(fingerprint, ctx.sub(0.9, 1.0)).unchanged
    ctx.progress(1.0, "Lab watermark embedded")
    return EmbedResult(out_path, labmark.psnr(rgb[:, :, :3], marked[:, :, :3]), detection, unchanged)
