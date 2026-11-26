"""EXIF, XMP, IPTC-IIM and ICC readers that work on bytes already extracted from a container."""

from __future__ import annotations

import io
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Optional

_MAX_VALUE = 20000


@dataclass
class MetaEntry:
    group: str
    key: str
    value: str


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_VALUE else text[:_MAX_VALUE] + f"… ({len(text):,} characters)"


# ---------------------------------------------------------------- EXIF

_USER_COMMENT = 0x9286
_XP_TAGS = {0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F}


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(ch.isprintable() or ch in "\r\n\t" for ch in text) / len(text)


def _format_exif_value(tag: int, value: Any, big_endian: bool) -> str:
    if tag == _USER_COMMENT and isinstance(value, bytes):
        prefix, body = value[:8], value[8:]
        if prefix.startswith(b"UNICODE"):
            for enc in (("utf-16-be", "utf-16-le") if big_endian else ("utf-16-le", "utf-16-be")):
                text = body.decode(enc, "replace").rstrip("\x00")
                if _printable_ratio(text) > 0.9:
                    return _clip(text)
        return _clip(body.decode("utf-8", "replace").rstrip("\x00 "))
    if tag in _XP_TAGS:
        raw = bytes(value) if isinstance(value, (tuple, list)) else value
        if isinstance(raw, bytes):
            return _clip(raw.decode("utf-16-le", "replace").rstrip("\x00"))
    if isinstance(value, bytes):
        text = value.decode("utf-8", "replace").rstrip("\x00")
        if text and _printable_ratio(text) > 0.95:
            return _clip(text)
        return f"<binary, {len(value):,} bytes>"
    if isinstance(value, str):
        return _clip(value.rstrip("\x00"))
    if isinstance(value, tuple):
        return ", ".join(_format_exif_value(-1, v, big_endian) for v in value[:64]) + ("…" if len(value) > 64 else "")
    try:
        if hasattr(value, "numerator") and hasattr(value, "denominator") and not isinstance(value, int):
            if value.denominator == 0:
                return "undefined"
            return f"{float(value):.6g}"
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return _clip(str(value))


def read_exif(raw: bytes) -> list[MetaEntry]:
    from PIL import ExifTags, Image

    exif = Image.Exif()
    exif.load(raw)
    tiff = raw[6:] if raw.startswith(b"Exif\x00\x00") else raw
    big_endian = tiff[:2] == b"MM"
    entries: list[MetaEntry] = []
    pointers = {0x8769, 0x8825, 0xA005}
    for tag, value in exif.items():
        if tag in pointers:
            continue
        entries.append(MetaEntry("EXIF IFD0", ExifTags.TAGS.get(tag, f"Tag 0x{tag:04X}"),
                                 _format_exif_value(tag, value, big_endian)))
    groups = ((ExifTags.IFD.Exif, "EXIF", ExifTags.TAGS), (ExifTags.IFD.GPSInfo, "GPS", ExifTags.GPSTAGS),
              (ExifTags.IFD.Interop, "Interop", ExifTags.TAGS), (ExifTags.IFD.IFD1, "EXIF IFD1 (thumbnail)", ExifTags.TAGS))
    for ifd_id, group, names in groups:
        try:
            ifd = exif.get_ifd(ifd_id)
        except Exception:  # noqa: BLE001 - malformed sub-IFDs are common; skip them
            continue
        for tag, value in ifd.items():
            if tag in pointers:
                continue
            name = names.get(tag, f"Tag 0x{tag:04X}")
            if tag == 0x927C:
                entries.append(MetaEntry(group, "MakerNote", f"<maker-specific data, {len(value) if hasattr(value, '__len__') else '?'} bytes>"))
                continue
            entries.append(MetaEntry(group, name, _format_exif_value(tag, value, big_endian)))
    return entries


# ---------------------------------------------------------------- XMP

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_XML_NS = "http://www.w3.org/XML/1998/namespace"

KNOWN_PREFIXES = {
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://purl.org/dc/terms/": "dcterms",
    "http://ns.adobe.com/xap/1.0/": "xmp",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://ns.adobe.com/xap/1.0/rights/": "xmpRights",
    "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#": "stEvt",
    "http://ns.adobe.com/xap/1.0/sType/ResourceRef#": "stRef",
    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://ns.adobe.com/tiff/1.0/": "tiff",
    "http://ns.adobe.com/exif/1.0/": "exif",
    "http://ns.adobe.com/exif/1.0/aux/": "aux",
    "http://cipa.jp/exif/1.0/": "exifEX",
    "http://ns.adobe.com/camera-raw-settings/1.0/": "crs",
    "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/": "Iptc4xmpCore",
    "http://iptc.org/std/Iptc4xmpExt/2008-02-29/": "Iptc4xmpExt",
    "http://ns.useplus.org/ldf/xmp/1.0/": "plus",
    "http://ns.google.com/photos/1.0/camera/": "GCamera",
    "http://ns.google.com/photos/1.0/container/": "GContainer",
    "http://ns.google.com/photos/1.0/image/": "GImage",
    "http://ns.adobe.com/xmp/note/": "xmpNote",
    "http://ns.adobe.com/pdf/1.3/": "pdf",
    "http://ns.adobe.com/xmp/1.0/DynamicMedia/": "xmpDM",
    RDF: "rdf",
}


@dataclass
class XmpData:
    entries: list[MetaEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def values(self, *keys: str) -> list[str]:
        wanted = {k.lower() for k in keys}
        out = []
        for e in self.entries:
            base = e.key.lower()
            leaf = base.rsplit("/", 1)[-1].split("[", 1)[0]
            if base in wanted or leaf in wanted:
                out.append(e.value)
        return out


def parse_xmp(packet: str, group: str = "XMP") -> XmpData:
    result = XmpData()
    upper = packet[:4096].upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in packet:
        result.errors.append("The XMP packet declares a DOCTYPE or entities and was not parsed, for safety.")
        return result
    start = packet.find("<")
    end = packet.rfind(">")
    if start < 0 or end < start:
        result.errors.append("The XMP packet is empty or not XML.")
        return result
    text = packet[start:end + 1]
    prefixes = dict(KNOWN_PREFIXES)
    try:
        for _event, (prefix, uri) in ET.iterparse(io.StringIO(text), events=("start-ns",)):
            prefixes.setdefault(uri, prefix or "ns")
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        result.errors.append(f"The XMP packet is not well-formed XML ({exc}).")
        return result

    def qname(tag: str) -> str:
        if tag.startswith("{"):
            uri, local = tag[1:].split("}", 1)
            return f"{prefixes.get(uri, uri)}:{local}"
        return tag

    rdf_roots = [root] if root.tag == f"{{{RDF}}}RDF" else list(root.iter(f"{{{RDF}}}RDF"))
    for rdf_root in rdf_roots:
        for desc in rdf_root.findall(f"{{{RDF}}}Description"):
            for attr, value in desc.attrib.items():
                if attr.startswith(f"{{{RDF}}}") or attr.startswith(f"{{{_XML_NS}}}"):
                    continue
                result.entries.append(MetaEntry(group, qname(attr), _clip(value)))
            for child in desc:
                _walk(child, qname(child.tag), result.entries, qname, group, 0)
    return result


def _walk(elem: ET.Element, path: str, out: list[MetaEntry], qname, group: str, depth: int) -> None:
    if depth > 12:
        return
    resource = elem.get(f"{{{RDF}}}resource")
    if resource is not None:
        out.append(MetaEntry(group, path, _clip(resource)))
    for attr, value in elem.attrib.items():
        if attr.startswith(f"{{{RDF}}}") or attr.startswith(f"{{{_XML_NS}}}"):
            continue
        out.append(MetaEntry(group, f"{path}/{qname(attr)}", _clip(value)))
    children = list(elem)
    if not children:
        text = (elem.text or "").strip()
        if text:
            out.append(MetaEntry(group, path, _clip(text)))
        return
    for child in children:
        tag = child.tag
        if tag in (f"{{{RDF}}}Seq", f"{{{RDF}}}Bag", f"{{{RDF}}}Alt"):
            simple: list[str] = []
            for index, li in enumerate(child.findall(f"{{{RDF}}}li"), start=1):
                if len(li) or any(not a.startswith(f"{{{RDF}}}") and not a.startswith(f"{{{_XML_NS}}}")
                                  for a in li.attrib):
                    _walk(li, f"{path}[{index}]", out, qname, group, depth + 1)
                else:
                    text = (li.text or "").strip()
                    if text:
                        simple.append(text)
            if simple:
                out.append(MetaEntry(group, path, _clip("; ".join(simple))))
        elif tag == f"{{{RDF}}}Description":
            _walk(child, path, out, qname, group, depth + 1)
        else:
            _walk(child, f"{path}/{qname(tag)}", out, qname, group, depth + 1)


# ---------------------------------------------------------------- IPTC-IIM

_IIM_NAMES = {
    5: "Object Name", 10: "Urgency", 15: "Category", 25: "Keywords", 40: "Special Instructions",
    55: "Date Created", 60: "Time Created", 65: "Originating Program", 70: "Program Version",
    80: "By-line", 85: "By-line Title", 90: "City", 95: "Province/State", 101: "Country",
    103: "Original Transmission Reference", 105: "Headline", 110: "Credit", 115: "Source",
    116: "Copyright Notice", 118: "Contact", 120: "Caption/Abstract", 122: "Writer/Editor",
}


def read_iptc(raw: bytes) -> list[MetaEntry]:
    entries: list[MetaEntry] = []
    pos = 0
    while pos + 5 <= len(raw):
        if raw[pos] != 0x1C:
            pos += 1
            continue
        record, dataset = raw[pos + 1], raw[pos + 2]
        size = struct.unpack(">H", raw[pos + 3:pos + 5])[0]
        pos += 5
        if size & 0x8000:
            count = size & 0x7FFF
            size = int.from_bytes(raw[pos:pos + count], "big")
            pos += count
        value = raw[pos:pos + size]
        pos += size
        if record != 2 or dataset == 0:
            continue
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            text = value.decode("latin-1")
        entries.append(MetaEntry("IPTC", _IIM_NAMES.get(dataset, f"2:{dataset}"), _clip(text)))
    return entries


# ---------------------------------------------------------------- ICC

def icc_description(profile: bytes) -> Optional[str]:
    if len(profile) < 132:
        return None
    count = struct.unpack(">I", profile[128:132])[0]
    for i in range(min(count, 256)):
        base = 132 + i * 12
        if base + 12 > len(profile):
            break
        sig, offset, size = struct.unpack(">4sII", profile[base:base + 12])
        if sig != b"desc":
            continue
        data = profile[offset:offset + size]
        if data[:4] == b"desc" and len(data) >= 12:
            n = struct.unpack(">I", data[8:12])[0]
            return data[12:12 + n].split(b"\x00", 1)[0].decode("latin-1", "replace") or None
        if data[:4] == b"mluc" and len(data) >= 16:
            records = struct.unpack(">I", data[8:12])[0]
            if records:
                length, rec_offset = struct.unpack(">II", data[20:28])
                return data[rec_offset:rec_offset + length].decode("utf-16-be", "replace").strip("\x00") or None
    return None


# ---------------------------------------------------------------- IPTC digital source type vocabulary

DIGITAL_SOURCE_TYPES = {
    "trainedalgorithmicmedia": "Created using generative AI (trained algorithmic media)",
    "compositewithtrainedalgorithmicmedia": "Composite that includes generative AI elements",
    "algorithmicmedia": "Created algorithmically, without a trained model",
    "algorithmicallyenhanced": "Algorithmically enhanced",
    "compositesynthetic": "Composite of synthetic elements",
    "digitalcapture": "Original digital capture (camera)",
    "computationalcapture": "Computational capture",
    "negativefilm": "Scanned negative film",
    "positivefilm": "Scanned positive film",
    "print": "Scanned print",
    "humanedits": "Human edits",
    "minorhumanedits": "Minor human edits",
    "compositecapture": "Composite of captured elements",
    "digitalcreation": "Digital creation by a person",
    "datadrivenmedia": "Data-driven media",
    "screencapture": "Screen capture",
    "virtualrecording": "Virtual recording",
    "composite": "Composite",
}

AI_SOURCE_TYPES = {"trainedalgorithmicmedia", "compositewithtrainedalgorithmicmedia"}
SYNTHETIC_SOURCE_TYPES = {"algorithmicmedia", "compositesynthetic"}


def source_type_key(uri: str) -> str:
    return uri.strip().rstrip("/").rsplit("/", 1)[-1].lower()


def describe_source_type(uri: str) -> str:
    return DIGITAL_SOURCE_TYPES.get(source_type_key(uri), uri)
