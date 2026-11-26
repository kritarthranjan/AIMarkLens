"""Lossless metadata management on a separate output copy.

The copy is rebuilt from the original's own segments or chunks: kept segments
are copied byte for byte and the compressed image data is never re-encoded, so
image quality and dimensions are preserved exactly. Pixel-level (invisible)
watermarks are unaffected by design: this only manages metadata.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field

from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.errors import LabError
from AIMarkLens.core.integrity import atomic_write_bytes, read_original, verify_unchanged
from AIMarkLens.formats.containers import KIND_GROUP, ContainerInfo, Segment, parse_container

REMOVABLE = {
    "exif": "EXIF (camera, software, date and GPS location)",
    "xmp": "XMP (including IPTC digital source type and edit history)",
    "c2pa": "Content Credentials (C2PA manifests)",
    "iptc": "IPTC and Photoshop resources",
    "text": "Text chunks and comments (including generation parameters)",
    "icc": "ICC colour profile (colours may display differently without it)",
    "trailing": "Data after the end of the image (secondary images, gain maps)",
}
DEFAULT_REMOVE = frozenset({"exif", "xmp", "c2pa", "iptc", "text", "trailing"})



@dataclass
class ExportResult:
    output_path: str
    removed: list[tuple[str, int]] = field(default_factory=list)
    output_sha256: str = ""
    output_size: int = 0
    image_data_identical: bool = False
    decoded_identical: bool | None = None
    original_unchanged: bool = False
    remaining: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"Removed {len(self.removed)} item(s)" if self.removed else "Nothing needed removing"]
        parts.append("compressed image data copied bit for bit" if self.image_data_identical
                     else "image data differs")
        if self.decoded_identical is not None:
            parts.append("decoded pixels identical" if self.decoded_identical else "decoded pixels differ")
        parts.append("original verified unchanged" if self.original_unchanged else "ORIGINAL CHANGED")
        return "; ".join(parts) + ". " + PIXELS_UNCHANGED_NOTE


PIXELS_UNCHANGED_NOTE = ("Only metadata was removed. The pixels are identical, so SynthID and other pixel "
                         "watermarks, and the traces AI detectors look for, are unchanged: online detectors will "
                         "give the same result as for the original.")


def _check_tiling(info: ContainerInfo, size: int) -> None:
    pos = 0
    for seg in info.segments:
        if seg.offset != pos:
            raise LabError("The file structure could not be fully mapped, so a lossless copy cannot be guaranteed.",
                           "No copy was written. The file may be damaged or use an unusual layout.")
        pos += seg.length
    if pos != size:
        raise LabError("The file structure could not be fully mapped, so a lossless copy cannot be guaranteed.",
                       "No copy was written.")


def _drop(seg: Segment, remove: set[str]) -> bool:
    group = KIND_GROUP.get(seg.kind)
    return group is not None and group in remove


def build_stripped(data: bytes, remove: set[str]) -> tuple[bytes, list[Segment]]:
    info = parse_container(data)
    _check_tiling(info, len(data))
    dropped = [s for s in info.segments if _drop(s, remove)]
    kept = [s for s in info.segments if not _drop(s, remove)]
    if info.format == "JPEG":
        mpf = next((s for s in kept if s.kind == "mpf"), None)
        if mpf is not None and any(s.offset > mpf.offset and s.kind != "trailing" for s in dropped):
            raise LabError("This JPEG contains secondary images indexed by position, and removing the selected "
                           "metadata would break that index.",
                           "Also select 'Data after the end of the image' to produce a consistent copy.")
    if info.format == "WEBP":
        return _rebuild_webp(data, kept), dropped
    return b"".join(data[s.offset:s.offset + s.length] for s in kept), dropped


def _rebuild_webp(data: bytes, kept: list[Segment]) -> bytes:
    chunks = [bytearray(data[s.offset:s.offset + s.length]) for s in kept if s.name != "RIFF header"]
    names = {bytes(c[:4]) for c in chunks}
    for chunk in chunks:
        if chunk[:4] == b"VP8X":
            flags = chunk[8]
            if b"EXIF" not in names:
                flags &= ~0x08
            if b"XMP " not in names:
                flags &= ~0x04
            if b"ICCP" not in names:
                flags &= ~0x20
            chunk[8] = flags
    body = b"WEBP" + b"".join(bytes(c) for c in chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _pixel_bytes(data: bytes) -> bytes:
    info = parse_container(data)
    return b"".join(data[s.offset:s.offset + s.length] for s in info.segments if s.kind == "pixels")


def export_without_metadata(path: str, out_path: str, remove: set[str], ctx: TaskContext,
                            history_path: str = "") -> ExportResult:
    ctx.progress(0.0, "Reading the original (read-only)")
    data, fingerprint = read_original(path, ctx.sub(0.0, 0.15))
    ctx.progress(0.2, "Rebuilding a copy without the selected metadata")
    output, dropped = build_stripped(data, set(remove))
    ctx.progress(0.45, "Writing the copy")
    atomic_write_bytes(out_path, output, protected=[path])
    result = ExportResult(out_path, [(f"{s.name} ({s.note or s.kind})", s.length) for s in dropped])
    result.output_sha256 = hashlib.sha256(output).hexdigest()
    result.output_size = len(output)

    ctx.progress(0.6, "Verifying the copy")
    with open(out_path, "rb") as fh:
        written = fh.read()
    if written != output:
        raise LabError("The copy on disk does not match what was written.", "Check the destination drive.")
    result.image_data_identical = _pixel_bytes(written) == _pixel_bytes(data)
    after = parse_container(written)
    result.remaining = sorted({KIND_GROUP[s.kind] for s in after.segments if KIND_GROUP.get(s.kind) in remove})
    ctx.progress(0.75, "Comparing decoded pixels")
    try:
        from AIMarkLens.engines.image.pixels import open_image, to_rgb_array
        import numpy as np

        a, b = open_image(data).image, open_image(written).image
        result.decoded_identical = bool(np.array_equal(to_rgb_array(a, True), to_rgb_array(b, True)))
        if history_path:
            from AIMarkLens.core import history
            from AIMarkLens.engines.image.pixels import perceptual_hash

            # Remember the lineage so re-analysing the copy reports the original's signals.
            history.record_copy(history_path, result.output_sha256, fingerprint.sha256, perceptual_hash(b),
                                os.path.basename(out_path))
    except LabError:
        result.decoded_identical = None
    ctx.progress(0.9, "Verifying the original is unchanged")
    result.original_unchanged = verify_unchanged(fingerprint, ctx.sub(0.9, 1.0)).unchanged
    ctx.progress(1.0, "Copy saved")
    return result


def default_copy_name(path: str) -> str:
    stem, ext = os.path.splitext(os.path.basename(path))
    return f"{stem}-metadata-free{ext}"
