"""Read-only parsers for JPEG, PNG and WebP container structures.

These walk the raw bytes and record every segment or chunk, extracting the
metadata carriers (EXIF, XMP, IPTC, ICC, text chunks and C2PA JUMBF) without
decoding pixels and without modifying anything.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Optional

from AIMarkLens.core.errors import LabError

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_TEXT_LIMIT = 4 * 1024 * 1024
_ICC_LIMIT = 16 * 1024 * 1024

# Metadata group of each segment kind (what a metadata-free copy can leave out).
KIND_GROUP = {"exif": "exif", "xmp": "xmp", "xmp-ext": "xmp", "c2pa": "c2pa", "iptc": "iptc",
              "comment": "text", "text": "text", "icc": "icc", "trailing": "trailing", "mpf": "trailing"}

SUPPORTED_EXTENSIONS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
FORMAT_SUFFIX = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}

XMP_JPEG_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_EXT_HEADER = b"http://ns.adobe.com/xmp/extension/\x00"


@dataclass
class Segment:
    name: str
    offset: int
    length: int
    kind: str = "other"  # exif, xmp, xmp-ext, c2pa, icc, iptc, comment, text, mpf, pixels, header, other
    note: str = ""


@dataclass
class TextEntry:
    source: str
    key: str
    value: str


@dataclass
class ContainerInfo:
    format: str
    width: Optional[int] = None
    height: Optional[int] = None
    segments: list[Segment] = field(default_factory=list)
    exif: Optional[bytes] = None
    xmp_packets: list[str] = field(default_factory=list)
    text: list[TextEntry] = field(default_factory=list)
    icc_profile: Optional[bytes] = None
    iptc: Optional[bytes] = None
    jumbf_boxes: list[bytes] = field(default_factory=list)
    quant_tables: dict[int, list[int]] = field(default_factory=dict)
    trailing_bytes: int = 0
    animated: bool = False
    details: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def has_kind(self, kind: str) -> bool:
        return any(s.kind == kind for s in self.segments)


def sniff_format(data: bytes) -> Optional[str]:
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:8] == PNG_MAGIC:
        return "PNG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def parse_container(data: bytes) -> ContainerInfo:
    fmt = sniff_format(data)
    if fmt is None:
        raise LabError("This file is not a PNG, JPEG or WebP image.",
                       "The format is identified from the file contents, not its extension.")
    info = ContainerInfo(fmt)
    {"JPEG": _parse_jpeg, "PNG": _parse_png, "WEBP": _parse_webp}[fmt](data, info)
    if not info.xmp_packets:
        _raw_xmp_scan(data, info)
    return info


def _u16(b: bytes) -> int:
    return struct.unpack(">H", b)[0]


def _u32(b: bytes) -> int:
    return struct.unpack(">I", b)[0]


def _inflate(data: bytes, limit: int, info: ContainerInfo, what: str) -> bytes:
    try:
        d = zlib.decompressobj()
        out = d.decompress(data, limit)
        if d.unconsumed_tail:
            info.warnings.append(f"{what} was larger than {limit // 1024 // 1024} MB and was truncated for safety.")
        return out
    except zlib.error as exc:
        info.warnings.append(f"{what} could not be decompressed: {exc}")
        return b""


def _decode_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _raw_xmp_scan(data: bytes, info: ContainerInfo) -> None:
    start = data.find(b"<x:xmpmeta")
    if start < 0:
        return
    end = data.find(b"</x:xmpmeta>", start)
    if end < 0:
        return
    info.xmp_packets.append(_decode_text(data[start:end + 12]))
    info.warnings.append("An XMP packet was found outside the usual metadata location (raw scan).")


# ---------------------------------------------------------------- JPEG

_JPEG_NAMES = {0xC4: "DHT", 0xCC: "DAC", 0xDB: "DQT", 0xDD: "DRI", 0xDA: "SOS", 0xFE: "COM", 0xDC: "DNL"}
_SOF_NAMES = {0xC0: "baseline", 0xC1: "extended sequential", 0xC2: "progressive", 0xC3: "lossless",
              0xC9: "extended sequential (arithmetic)", 0xCA: "progressive (arithmetic)",
              0xCB: "lossless (arithmetic)"}


def _jpeg_name(marker: int) -> str:
    if 0xE0 <= marker <= 0xEF:
        return f"APP{marker - 0xE0}"
    if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
        return f"SOF{marker - 0xC0}"
    return _JPEG_NAMES.get(marker, f"Marker 0x{marker:02X}")


def _skip_entropy(data: bytes, pos: int) -> int:
    """Return the offset of the next real marker after entropy-coded data."""
    n = len(data)
    while True:
        idx = data.find(b"\xff", pos)
        if idx < 0 or idx + 1 >= n:
            return n
        nxt = data[idx + 1]
        if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
            pos = idx + 2
        elif nxt == 0xFF:
            pos = idx + 1
        else:
            return idx


def _parse_jpeg(data: bytes, info: ContainerInfo) -> None:
    n = len(data)
    info.segments.append(Segment("SOI", 0, 2, "header"))
    pos = 2
    icc_parts: dict[int, bytes] = {}
    xmp_ext: dict[str, dict[int, bytes]] = {}
    jumbf_parts: dict[int, dict[int, bytes]] = {}
    exif_seen = False
    while pos < n:
        if data[pos] != 0xFF:
            info.warnings.append(f"Unexpected data at offset {pos:,}; structure parsing stopped there.")
            break
        start = pos
        while pos < n and data[pos] == 0xFF:
            pos += 1
        if pos >= n:
            break
        marker = data[pos]
        pos += 1
        if marker == 0xD9:
            info.segments.append(Segment("EOI", start, pos - start, "header"))
            info.trailing_bytes = n - pos
            if info.trailing_bytes:
                info.segments.append(Segment("Data after EOI", pos, info.trailing_bytes, "trailing",
                                             "Extra data after the end of the image (for example a "
                                             "secondary image, gain map or appended content)"))
            break
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            info.segments.append(Segment(f"Marker 0x{marker:02X}", start, pos - start))
            continue
        if pos + 2 > n:
            info.warnings.append("The file ends in the middle of a segment header (truncated).")
            break
        length = _u16(data[pos:pos + 2])
        if length < 2 or pos + length > n:
            info.warnings.append(f"Segment at offset {start:,} has an invalid length; the file may be truncated.")
            break
        body = data[pos + 2:pos + length]
        seg = Segment(_jpeg_name(marker), start, pos + length - start)

        if marker == 0xE0:
            if body.startswith(b"JFIF\x00") and len(body) >= 7:
                seg.note = f"JFIF {body[5]}.{body[6]:02d}"
            elif body.startswith(b"JFXX\x00"):
                seg.note = "JFIF extension (thumbnail)"
        elif marker == 0xE1:
            if body.startswith(b"Exif\x00\x00"):
                seg.kind, seg.note = "exif", "EXIF"
                if exif_seen:
                    info.warnings.append("More than one EXIF segment found; only the first is decoded.")
                else:
                    info.exif = body[6:]
                    exif_seen = True
            elif body.startswith(XMP_JPEG_HEADER):
                seg.kind, seg.note = "xmp", "XMP"
                info.xmp_packets.append(_decode_text(body[len(XMP_JPEG_HEADER):]))
            elif body.startswith(XMP_EXT_HEADER) and len(body) >= 75:
                seg.kind, seg.note = "xmp-ext", "Extended XMP"
                h = len(XMP_EXT_HEADER)
                guid = body[h:h + 32].decode("ascii", "replace")
                offset = _u32(body[h + 36:h + 40])
                xmp_ext.setdefault(guid, {})[offset] = body[h + 40:]
            else:
                seg.note = "Unrecognised APP1 data"
        elif marker == 0xE2:
            if body.startswith(b"ICC_PROFILE\x00") and len(body) >= 14:
                seg.kind, seg.note = "icc", f"ICC profile part {body[12]} of {body[13]}"
                icc_parts[body[12]] = body[14:]
            elif body.startswith(b"MPF\x00"):
                seg.kind, seg.note = "mpf", "Multi-Picture Format index (secondary images follow the main image)"
        elif marker == 0xEB:
            if body[:2] == b"JP" and len(body) >= 8:
                seg.kind, seg.note = "c2pa", "JUMBF box (C2PA Content Credentials)"
                en = _u16(body[2:4])
                z = _u32(body[4:8])
                jumbf_parts.setdefault(en, {})[z] = body[8:]
            else:
                seg.note = "APP11 data"
        elif marker == 0xED:
            if body.startswith(b"Photoshop 3.0\x00"):
                seg.kind, seg.note = "iptc", "Photoshop resources (IPTC)"
                iptc = _photoshop_iptc(body)
                if iptc:
                    info.iptc = iptc
        elif marker == 0xEE and body.startswith(b"Adobe"):
            seg.note = "Adobe colour transform"
        elif marker == 0xFE:
            seg.kind, seg.note = "comment", "JPEG comment"
            info.text.append(TextEntry("JPEG comment", "Comment", _decode_text(body).rstrip("\x00")))
        elif marker == 0xDB:
            seg.note = _parse_dqt(body, info)
        elif 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            seg.note = _parse_sof(marker, body, info)

        info.segments.append(seg)
        pos += length
        if marker == 0xDA:
            end = _skip_entropy(data, pos)
            info.segments.append(Segment("Entropy-coded image data", pos, end - pos, "pixels"))
            pos = end

    if icc_parts:
        info.icc_profile = b"".join(icc_parts[k] for k in sorted(icc_parts))
    for guid, parts in xmp_ext.items():
        info.xmp_packets.append(_decode_text(b"".join(parts[k] for k in sorted(parts))))
        info.details["Extended XMP"] = guid
    for en in sorted(jumbf_parts):
        parts = jumbf_parts[en]
        chunks = []
        for i, z in enumerate(sorted(parts)):
            payload = parts[z]
            if i > 0:
                # Continuation segments repeat the box header (LBox + TBox, plus XLBox if used).
                payload = payload[16:] if len(payload) >= 4 and _u32(payload[:4]) == 1 else payload[8:]
            chunks.append(payload)
        info.jumbf_boxes.append(b"".join(chunks))


def _parse_dqt(body: bytes, info: ContainerInfo) -> str:
    pos, ids = 0, []
    while pos < len(body):
        pq, tq = body[pos] >> 4, body[pos] & 0x0F
        pos += 1
        size = 128 if pq else 64
        raw = body[pos:pos + size]
        if len(raw) < size:
            break
        values = list(struct.unpack(">64H", raw)) if pq else list(raw)
        info.quant_tables[tq] = values
        ids.append(str(tq))
        pos += size
    return f"Quantisation table(s) {', '.join(ids)}"


def _parse_sof(marker: int, body: bytes, info: ContainerInfo) -> str:
    if len(body) < 6:
        return "Truncated frame header"
    precision = body[0]
    info.height, info.width = _u16(body[1:3]), _u16(body[3:5])
    count = body[5]
    mode = _SOF_NAMES.get(marker, f"SOF{marker - 0xC0}")
    info.details["Encoding"] = mode.capitalize()
    info.details["Bit depth"] = f"{precision}-bit"
    info.details["Components"] = str(count)
    if count == 3 and len(body) >= 6 + 9:
        factors = [(body[6 + i * 3 + 1] >> 4, body[6 + i * 3 + 1] & 0x0F) for i in range(3)]
        (yh, yv), (ch, cv) = factors[0], factors[1]
        if ch and cv:
            ratio = (yh // ch, yv // cv)
            info.details["Chroma subsampling"] = {(1, 1): "4:4:4", (2, 1): "4:2:2", (2, 2): "4:2:0",
                                                  (1, 2): "4:4:0", (4, 1): "4:1:1"}.get(ratio, f"{ratio}")
    return f"{mode}, {info.width}×{info.height}"


def _photoshop_iptc(body: bytes) -> Optional[bytes]:
    pos = len(b"Photoshop 3.0\x00")
    while pos + 12 <= len(body):
        if body[pos:pos + 4] != b"8BIM":
            break
        rid = _u16(body[pos + 4:pos + 6])
        pos += 6
        name_len = body[pos]
        padded = name_len + 1
        pos += padded + (padded & 1)
        if pos + 4 > len(body):
            break
        size = _u32(body[pos:pos + 4])
        pos += 4
        rdata = body[pos:pos + size]
        pos += size + (size & 1)
        if rid == 0x0404:
            return rdata
    return None


# ---------------------------------------------------------------- PNG

_PNG_COLOUR = {0: "Greyscale", 2: "RGB", 3: "Indexed colour", 4: "Greyscale + alpha", 6: "RGBA"}
_PNG_TEXT_KINDS = {"tEXt", "zTXt", "iTXt"}


def _parse_png(data: bytes, info: ContainerInfo) -> None:
    pos, n = 8, len(data)
    info.segments.append(Segment("PNG signature", 0, 8, "header"))
    idat: Optional[list[int]] = None  # [offset, total_length, count]

    def flush_idat() -> None:
        nonlocal idat
        if idat:
            info.segments.append(Segment(f"IDAT × {idat[2]}", idat[0], idat[1], "pixels", "Compressed image data"))
            idat = None

    while pos + 12 <= n:
        length = _u32(data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        name = ctype.decode("latin-1")
        if pos + 12 + length > n:
            info.warnings.append(f"Chunk {name!r} at offset {pos:,} is truncated.")
            break
        body = data[pos + 8:pos + 8 + length]
        crc = _u32(data[pos + 8 + length:pos + 12 + length])
        if zlib.crc32(body, zlib.crc32(ctype)) & 0xFFFFFFFF != crc:
            info.warnings.append(f"CRC mismatch in {name} chunk at offset {pos:,} (the chunk is damaged or edited).")
        total = 12 + length
        if name == "IDAT":
            if idat is None:
                idat = [pos, 0, 0]
            idat[1] += total
            idat[2] += 1
            pos += total
            continue
        flush_idat()
        seg = Segment(name, pos, total)
        if name == "IHDR" and length >= 13:
            w, h, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body[:13])
            info.width, info.height = w, h
            info.details["Bit depth"] = f"{depth}-bit"
            info.details["Colour type"] = _PNG_COLOUR.get(colour, str(colour))
            info.details["Interlace"] = "Adam7" if interlace else "None"
            seg.kind, seg.note = "header", f"{w}×{h}, {depth}-bit {_PNG_COLOUR.get(colour, colour)}"
        elif name in _PNG_TEXT_KINDS:
            seg.kind = "text"
            entry = _png_text(name, body, info)
            if entry:
                seg.note = entry.key
                if entry.key == "XML:com.adobe.xmp":
                    seg.kind = "xmp"
                    info.xmp_packets.append(entry.value)
                elif entry.key.lower().startswith("raw profile type"):
                    _raw_profile(entry, info, seg)
                else:
                    info.text.append(entry)
        elif name == "eXIf":
            seg.kind, seg.note = "exif", "EXIF"
            info.exif = body[6:] if body.startswith(b"Exif\x00\x00") else body
        elif name == "iCCP":
            seg.kind = "icc"
            key, _, rest = body.partition(b"\x00")
            seg.note = f"ICC profile: {key.decode('latin-1')}"
            if rest:
                info.icc_profile = _inflate(rest[1:], _ICC_LIMIT, info, "ICC profile")
        elif name == "caBX":
            seg.kind, seg.note = "c2pa", "JUMBF box (C2PA Content Credentials)"
            info.jumbf_boxes.append(body)
        elif name == "acTL" and length >= 8:
            info.animated = True
            info.details["Animation frames"] = str(_u32(body[:4]))
            seg.note = "Animated PNG"
        elif name == "tIME" and length >= 7:
            y, mo, d, hh, mm, ss = struct.unpack(">HBBBBB", body[:7])
            seg.note = f"Last modified {y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mm:02d}:{ss:02d} UTC"
            info.details["PNG tIME"] = seg.note[len("Last modified "):]
        elif name in ("sRGB", "gAMA", "cHRM", "pHYs", "bKGD", "sBIT", "cICP"):
            seg.note = "Colour/display information" if name != "pHYs" else "Physical pixel dimensions"
        info.segments.append(seg)
        pos += total
        if name == "IEND":
            info.trailing_bytes = n - pos
            if info.trailing_bytes:
                info.segments.append(Segment("Data after IEND", pos, info.trailing_bytes, "trailing",
                                             "Extra data appended after the end of the image"))
            break
    flush_idat()


def _png_text(name: str, body: bytes, info: ContainerInfo) -> Optional[TextEntry]:
    key_raw, sep, rest = body.partition(b"\x00")
    if not sep:
        info.warnings.append(f"Malformed {name} chunk (missing keyword terminator).")
        return None
    key = key_raw.decode("latin-1")
    if name == "tEXt":
        return TextEntry("PNG tEXt", key, rest.decode("latin-1"))
    if name == "zTXt":
        return TextEntry("PNG zTXt", key, _inflate(rest[1:], _TEXT_LIMIT, info, f"zTXt '{key}'").decode("latin-1"))
    if len(rest) < 2:
        return None
    compressed = rest[0] == 1
    lang, _, rest2 = rest[2:].partition(b"\x00")
    _, _, text = rest2.partition(b"\x00")
    if compressed:
        text = _inflate(text, _TEXT_LIMIT, info, f"iTXt '{key}'")
    return TextEntry("PNG iTXt", key, text.decode("utf-8", "replace"))


def _raw_profile(entry: TextEntry, info: ContainerInfo, seg: Segment) -> None:
    """Decode ImageMagick-style hex 'Raw profile type exif/xmp/iptc' text chunks."""
    lines = entry.value.strip().splitlines()
    try:
        raw = bytes.fromhex("".join(lines[2:]))
    except ValueError:
        info.warnings.append(f"Could not decode text chunk '{entry.key}'.")
        return
    kind = entry.key.lower().rsplit(" ", 1)[-1]
    if kind in ("exif", "app1") and info.exif is None:
        seg.kind = "exif"
        info.exif = raw[6:] if raw.startswith(b"Exif\x00\x00") else raw
    elif kind == "xmp":
        seg.kind = "xmp"
        info.xmp_packets.append(_decode_text(raw))
    elif kind in ("iptc", "8bim"):
        seg.kind = "iptc"
        info.iptc = _photoshop_iptc(b"Photoshop 3.0\x00" + raw) or raw


# ---------------------------------------------------------------- WebP

def _parse_webp(data: bytes, info: ContainerInfo) -> None:
    n = len(data)
    riff_size = struct.unpack("<I", data[4:8])[0]
    end = 8 + riff_size
    info.segments.append(Segment("RIFF header", 0, 12, "header"))
    if end > n:
        info.warnings.append("The RIFF size is larger than the file; the file may be truncated.")
        end = n
    pos = 12
    frames = 0
    while pos + 8 <= end:
        fourcc = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        name = fourcc.decode("latin-1")
        if pos + 8 + size > n:
            info.warnings.append(f"Chunk {name!r} at offset {pos:,} is truncated.")
            break
        body = data[pos + 8:pos + 8 + size]
        seg = Segment(name.strip(), pos, 8 + size + (size & 1))
        if name == "VP8 ":
            seg.kind, seg.note = "pixels", "Lossy bitstream"
            info.details.setdefault("Compression", "Lossy (VP8)")
            if len(body) >= 10 and body[3:6] == b"\x9d\x01\x2a" and info.width is None:
                info.width = struct.unpack("<H", body[6:8])[0] & 0x3FFF
                info.height = struct.unpack("<H", body[8:10])[0] & 0x3FFF
        elif name == "VP8L":
            seg.kind, seg.note = "pixels", "Lossless bitstream"
            info.details.setdefault("Compression", "Lossless (VP8L)")
            if len(body) >= 5 and body[0] == 0x2F and info.width is None:
                bits = struct.unpack("<I", body[1:5])[0]
                info.width, info.height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        elif name == "VP8X" and len(body) >= 10:
            seg.kind = "header"
            flags = body[0]
            info.width = 1 + int.from_bytes(body[4:7], "little")
            info.height = 1 + int.from_bytes(body[7:10], "little")
            info.animated = bool(flags & 0x02)
            present = [label for bit, label in ((0x20, "ICC"), (0x10, "alpha"), (0x08, "EXIF"),
                                                (0x04, "XMP"), (0x02, "animation")) if flags & bit]
            seg.note = "Extended format" + (f": {', '.join(present)}" if present else "")
        elif name == "ALPH":
            seg.kind, seg.note = "pixels", "Alpha channel"
        elif name == "ICCP":
            seg.kind, seg.note = "icc", "ICC profile"
            info.icc_profile = body
        elif name == "EXIF":
            seg.kind, seg.note = "exif", "EXIF"
            info.exif = body[6:] if body.startswith(b"Exif\x00\x00") else body
        elif name == "XMP ":
            seg.kind, seg.note = "xmp", "XMP"
            info.xmp_packets.append(_decode_text(body))
        elif name == "C2PA":
            seg.kind, seg.note = "c2pa", "JUMBF box (C2PA Content Credentials)"
            info.jumbf_boxes.append(body)
        elif name == "ANIM":
            seg.note = "Animation parameters"
        elif name == "ANMF":
            seg.kind = "pixels"
            frames += 1
            seg.note = f"Animation frame {frames}"
        info.segments.append(seg)
        pos += 8 + size + (size & 1)
    if frames:
        info.details["Animation frames"] = str(frames)
    if end < n:
        info.trailing_bytes = n - end
        info.segments.append(Segment("Data after RIFF", end, n - end, "trailing", "Extra data appended to the file"))
