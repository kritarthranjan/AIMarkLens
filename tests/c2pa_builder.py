"""Builds synthetic C2PA manifest stores (JUMBF + CBOR + COSE + DER) for tests.

The signature bytes are placeholders: these stores exercise parsing and the
hard-binding hash check, not cryptographic validation.
"""

import hashlib
import struct
import zlib

AI = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"


class Tag:
    def __init__(self, tag, value):
        self.tag, self.value = tag, value


def _head(major, n):
    if n < 24:
        return bytes([major << 5 | n])
    for code, fmt, limit in ((24, ">B", 1 << 8), (25, ">H", 1 << 16), (26, ">I", 1 << 32), (27, ">Q", 1 << 64)):
        if n < limit:
            return bytes([major << 5 | code]) + struct.pack(fmt, n)
    raise ValueError(n)


def cbor(v):
    if v is True:
        return b"\xf5"
    if v is False:
        return b"\xf4"
    if v is None:
        return b"\xf6"
    if isinstance(v, int):
        return _head(0, v) if v >= 0 else _head(1, -1 - v)
    if isinstance(v, bytes):
        return _head(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return _head(3, len(b)) + b
    if isinstance(v, list):
        return _head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return _head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    if isinstance(v, Tag):
        return _head(6, v.tag) + cbor(v.value)
    raise TypeError(type(v))


def _box(tbox, payload):
    return struct.pack(">I", 8 + len(payload)) + tbox + payload


def _superbox(uuid4, label, children):
    uuid = uuid4 + bytes.fromhex("00110010800000AA00389B71")
    jumd = _box(b"jumd", uuid + b"\x03" + label.encode() + b"\x00")
    return _box(b"jumb", jumd + b"".join(children))


def _cbor_box(label, obj):
    return _superbox(b"cbor", label, [_box(b"cbor", cbor(obj))])


def _der(tag, content):
    n = len(content)
    if n < 128:
        length = bytes([n])
    else:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + length + content


def _oid(text):
    parts = [int(p) for p in text.split(".")]
    out = bytes([parts[0] * 40 + parts[1]])
    for value in parts[2:]:
        chunk = [value & 0x7F]
        value >>= 7
        while value:
            chunk.insert(0, 0x80 | (value & 0x7F))
            value >>= 7
        out += bytes(chunk)
    return _der(0x06, out)


def _name(cn, org):
    def rdn(oid, value):
        return _der(0x31, _der(0x30, _oid(oid) + _der(0x0C, value.encode())))
    return _der(0x30, rdn("2.5.4.3", cn) + rdn("2.5.4.10", org) + rdn("2.5.4.6", "US"))


def certificate(subject_org="OpenAI", issuer_org="Example Trust CA"):
    alg = _der(0x30, _oid("1.2.840.10045.4.3.2"))
    tbs = _der(0x30, _der(0xA0, _der(0x02, b"\x02")) + _der(0x02, b"\x12\x34") + alg
               + _name("Example CA", issuer_org)
               + _der(0x30, _der(0x17, b"250101000000Z") + _der(0x17, b"300101000000Z"))
               + _name("Signer", subject_org) + _der(0x30, b""))
    return _der(0x30, tbs + alg + _der(0x03, b"\x00\x01\x02"))


def manifest_store(exclusion, data_hash, generator="ChatGPT", signer="OpenAI", source_type=AI):
    claim = {"claim_generator": generator, "claim_generator_info": [{"name": generator, "version": "1.0"}],
             "dc:title": "image.png", "dc:format": "image/png", "instanceID": "xmp:iid:1234", "alg": "sha256",
             "signature": "self#jumbf=c2pa.signature", "assertions": []}
    actions = {"actions": [{"action": "c2pa.created", "digitalSourceType": source_type, "softwareAgent": "GPT-4o"}]}
    hash_data = {"exclusions": [{"start": exclusion[0], "length": exclusion[1]}], "name": "jumbf manifest",
                 "alg": "sha256", "hash": data_hash, "pad": b"\x00\x00"}
    signature = Tag(18, [cbor({1: -7, 33: [certificate(signer)]}), {"sigTst": {"tstTokens": []}}, None, b"\x00" * 64])
    manifest = _superbox(b"c2ma", "urn:uuid:11111111-2222-3333-4444-555555555555", [
        _superbox(b"c2as", "c2pa.assertions", [_cbor_box("c2pa.actions", actions), _cbor_box("c2pa.hash.data", hash_data)]),
        _cbor_box("c2pa.claim", claim),
        _superbox(b"c2cs", "c2pa.signature", [_box(b"cbor", cbor(signature))]),
    ])
    return _superbox(b"c2pa", "c2pa", [manifest])


def _png_chunk(ctype, body):
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(body, zlib.crc32(ctype)) & 0xFFFFFFFF)


def _bound(container, build, **kwargs):
    """Iterate until the exclusion range and the embedded store agree, then fill in the real hash."""
    exclusion = (0, 0)
    for _ in range(4):
        store = manifest_store(exclusion, b"\x00" * 32, **kwargs)
        asset, exclusion_new = build(store)
        if exclusion_new == exclusion:
            break
        exclusion = exclusion_new
    excluded = asset[:exclusion[0]] + asset[exclusion[0] + exclusion[1]:]
    store = manifest_store(exclusion, hashlib.sha256(excluded).digest(), **kwargs)
    final, check = build(store)
    assert check == exclusion
    return final


def png_with_manifest(png: bytes, **kwargs) -> bytes:
    ihdr_end = 8 + 25

    def build(store):
        chunk = _png_chunk(b"caBX", store)
        return png[:ihdr_end] + chunk + png[ihdr_end:], (ihdr_end, len(chunk))

    return _bound(png, build, **kwargs)


def jpeg_with_manifest(jpeg: bytes, **kwargs) -> bytes:
    def build(store):
        body = b"JP" + struct.pack(">HI", 1, 1) + store
        segment = b"\xff\xeb" + struct.pack(">H", len(body) + 2) + body
        return jpeg[:2] + segment + jpeg[2:], (2, len(segment))

    return _bound(jpeg, build, **kwargs)
