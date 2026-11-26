"""Tiny DER reader that extracts the subject, issuer and validity of an X.509 certificate.

It does not verify anything cryptographically; it only lets the report show who
signed a C2PA manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

_OID_NAMES = {
    "2.5.4.3": "CN", "2.5.4.6": "C", "2.5.4.7": "L", "2.5.4.8": "ST", "2.5.4.10": "O",
    "2.5.4.11": "OU", "2.5.4.5": "serialNumber", "2.5.4.97": "organizationIdentifier",
    "1.2.840.113549.1.9.1": "emailAddress",
}


class DERError(ValueError):
    pass


@dataclass
class CertificateInfo:
    subject: dict[str, str] = field(default_factory=dict)
    issuer: dict[str, str] = field(default_factory=dict)
    not_before: str = ""
    not_after: str = ""
    serial: str = ""

    @staticmethod
    def format_name(name: dict[str, str]) -> str:
        order = ["CN", "O", "OU", "L", "ST", "C"]
        keys = [k for k in order if k in name] + [k for k in name if k not in order]
        return ", ".join(f"{k}={name[k]}" for k in keys)


def _tlv(data: bytes, pos: int) -> tuple[int, int, int]:
    """Return (tag, value_start, value_length) for the element at pos."""
    if pos + 2 > len(data):
        raise DERError("Truncated DER element.")
    tag = data[pos]
    pos += 1
    if tag & 0x1F == 0x1F:
        while pos < len(data) and data[pos] & 0x80:
            pos += 1
        pos += 1
    length = data[pos]
    pos += 1
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4:
            raise DERError("Unsupported DER length.")
        length = int.from_bytes(data[pos:pos + count], "big")
        pos += count
    if pos + length > len(data):
        raise DERError("DER element exceeds the available data.")
    return tag, pos, length


def _children(data: bytes, start: int, length: int) -> list[tuple[int, int, int]]:
    out, pos, end = [], start, start + length
    while pos < end:
        tag, vstart, vlen = _tlv(data, pos)
        out.append((tag, vstart, vlen))
        pos = vstart + vlen
    return out


def _oid(raw: bytes) -> str:
    if not raw:
        return ""
    parts = [raw[0] // 40, raw[0] % 40]
    value = 0
    for byte in raw[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(value)
            value = 0
    return ".".join(str(p) for p in parts)


def _string(tag: int, raw: bytes) -> str:
    if tag == 0x1E:
        return raw.decode("utf-16-be", "replace")
    if tag == 0x1C:
        return raw.decode("utf-32-be", "replace")
    if tag in (0x0C, 0x13, 0x16, 0x14, 0x1A):
        return raw.decode("utf-8", "replace")
    return raw.hex()


def _name(data: bytes, start: int, length: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for set_tag, set_start, set_len in _children(data, start, length):
        for seq_tag, seq_start, seq_len in _children(data, set_start, set_len):
            parts = _children(data, seq_start, seq_len)
            if len(parts) < 2 or parts[0][0] != 0x06:
                continue
            oid = _oid(data[parts[0][1]:parts[0][1] + parts[0][2]])
            vtag, vstart, vlen = parts[1]
            key = _OID_NAMES.get(oid, oid)
            value = _string(vtag, data[vstart:vstart + vlen])
            result[key] = f"{result[key]}; {value}" if key in result else value
    return result


def _time(tag: int, raw: bytes) -> str:
    text = raw.decode("ascii", "replace").rstrip("Z")
    if tag == 0x17 and len(text) >= 12:
        year = int(text[:2])
        text = f"{1900 + year if year >= 50 else 2000 + year}{text[2:]}"
    if len(text) >= 14:
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}:{text[12:14]} UTC"
    return text


def parse_certificate(der: bytes) -> Optional[CertificateInfo]:
    try:
        tag, start, length = _tlv(der, 0)
        cert_parts = _children(der, start, length)
        tbs_tag, tbs_start, tbs_len = cert_parts[0]
        tbs = _children(der, tbs_start, tbs_len)
        idx = 1 if tbs[0][0] == 0xA0 else 0
        info = CertificateInfo()
        serial = tbs[idx]
        info.serial = der[serial[1]:serial[1] + serial[2]].hex()
        issuer = tbs[idx + 2]
        info.issuer = _name(der, issuer[1], issuer[2])
        validity = _children(der, tbs[idx + 3][1], tbs[idx + 3][2])
        if len(validity) >= 2:
            info.not_before = _time(validity[0][0], der[validity[0][1]:validity[0][1] + validity[0][2]])
            info.not_after = _time(validity[1][0], der[validity[1][1]:validity[1][1] + validity[1][2]])
        subject = tbs[idx + 4]
        info.subject = _name(der, subject[1], subject[2])
        return info
    except (DERError, IndexError, ValueError):
        return None
