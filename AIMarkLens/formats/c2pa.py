"""C2PA (Content Credentials) manifest store reader.

Parses the JUMBF box tree, decodes claims and assertions (CBOR / JSON), reads
the signing certificate's subject and validates the hard-binding data hash
against the asset bytes. Cryptographic signature validation is left to
c2patool when it is installed.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Optional

from AIMarkLens.formats import cbor
from AIMarkLens.formats.der import CertificateInfo, parse_certificate
from AIMarkLens.formats.metadata import AI_SOURCE_TYPES, describe_source_type, source_type_key

_MAX_DEPTH = 24
_COSE_ALGS = {-7: "ES256", -35: "ES384", -36: "ES512", -37: "PS256", -38: "PS384", -39: "PS512", -8: "Ed25519"}


@dataclass
class JumbfBox:
    box_type: str
    offset: int
    size: int
    label: Optional[str] = None
    content_uuid: Optional[bytes] = None
    children: list["JumbfBox"] = field(default_factory=list)
    payload: Optional[bytes] = None

    @property
    def is_super(self) -> bool:
        return self.box_type == "jumb"

    def content(self) -> Optional["JumbfBox"]:
        for child in self.children:
            if child.box_type != "jumd":
                return child
        return None


class JumbfError(ValueError):
    pass


def parse_jumbf(data: bytes, start: int = 0, end: Optional[int] = None, depth: int = 0) -> list[JumbfBox]:
    end = len(data) if end is None else end
    if depth > _MAX_DEPTH:
        raise JumbfError("JUMBF nesting is too deep.")
    boxes: list[JumbfBox] = []
    pos = start
    while pos + 8 <= end:
        lbox, tbox = struct.unpack(">I4s", data[pos:pos + 8])
        header = 8
        if lbox == 1:
            if pos + 16 > end:
                raise JumbfError("Truncated extended box header.")
            lbox = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header = 16
        elif lbox == 0:
            lbox = end - pos
        if lbox < header or pos + lbox > end:
            raise JumbfError(f"Invalid box size at offset {pos}.")
        box = JumbfBox(tbox.decode("latin-1"), pos, lbox)
        body_start, body_end = pos + header, pos + lbox
        if box.box_type == "jumb":
            box.children = parse_jumbf(data, body_start, body_end, depth + 1)
            desc = box.children[0] if box.children and box.children[0].box_type == "jumd" else None
            if desc is not None and desc.payload is not None:
                box.content_uuid, box.label = _description(desc.payload)
        else:
            box.payload = data[body_start:body_end]
        boxes.append(box)
        pos += lbox
    return boxes


def _description(payload: bytes) -> tuple[bytes, Optional[str]]:
    uuid = payload[:16]
    if len(payload) < 17:
        return uuid, None
    toggles = payload[16]
    label = None
    if toggles & 0x02:
        raw = payload[17:].split(b"\x00", 1)[0]
        label = raw.decode("utf-8", "replace")
    return uuid, label


# ---------------------------------------------------------------- model

@dataclass
class C2paAction:
    action: str
    when: str = ""
    software_agent: str = ""
    digital_source_type: str = ""
    description: str = ""


@dataclass
class C2paIngredient:
    title: str
    relationship: str = ""
    format: str = ""
    has_manifest: bool = False


@dataclass
class HashCheck:
    algorithm: str
    status: str  # match | mismatch | unsupported | error | skipped
    detail: str = ""


@dataclass
class C2paManifest:
    label: str
    claim_version: int = 1
    claim_generator: str = ""
    generator_info: list[str] = field(default_factory=list)
    title: str = ""
    format: str = ""
    instance_id: str = ""
    algorithm: str = ""
    assertions: list[str] = field(default_factory=list)
    actions: list[C2paAction] = field(default_factory=list)
    ingredients: list[C2paIngredient] = field(default_factory=list)
    training_mining: list[tuple[str, str]] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    metadata_source_types: list[str] = field(default_factory=list)
    signature_algorithm: str = ""
    certificate: Optional[CertificateInfo] = None
    has_timestamp: bool = False
    hard_binding: Optional[HashCheck] = None
    errors: list[str] = field(default_factory=list)

    @property
    def signer(self) -> str:
        if self.certificate is None:
            return ""
        subject = self.certificate.subject
        return subject.get("O") or subject.get("CN") or CertificateInfo.format_name(subject)

    @property
    def generator_text(self) -> str:
        return self.claim_generator or "; ".join(self.generator_info)

    def source_types(self) -> list[str]:
        values = [a.digital_source_type for a in self.actions if a.digital_source_type]
        return values + self.metadata_source_types

    @property
    def declares_ai(self) -> bool:
        return any(source_type_key(t) in AI_SOURCE_TYPES for t in self.source_types())


@dataclass
class C2paStore:
    manifests: list[C2paManifest] = field(default_factory=list)
    origin: str = "embedded"  # embedded | remote
    size: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def active(self) -> Optional[C2paManifest]:
        return self.manifests[-1] if self.manifests else None


# ---------------------------------------------------------------- reading

def read_manifest_store(jumbf: bytes, asset: Optional[bytes], origin: str = "embedded") -> Optional[C2paStore]:
    """Return the C2PA manifest store in a JUMBF blob, or None if it is not C2PA."""
    try:
        boxes = parse_jumbf(jumbf)
    except JumbfError as exc:
        store = C2paStore(origin=origin, size=len(jumbf))
        store.errors.append(f"The JUMBF structure is damaged: {exc}")
        return store
    root = next((b for b in boxes if b.is_super and (b.label == "c2pa" or (b.content_uuid or b"")[:4] == b"c2pa")), None)
    if root is None:
        return None
    store = C2paStore(origin=origin, size=len(jumbf))
    for child in root.children:
        if child.is_super:
            store.manifests.append(_read_manifest(child, asset))
    if not store.manifests:
        store.errors.append("The manifest store contains no manifests.")
    return store


def _decode_content(box: JumbfBox) -> Any:
    content = box.content()
    if content is None or content.payload is None:
        return None
    if content.box_type == "cbor":
        return cbor.loads(content.payload)
    if content.box_type == "json":
        return json.loads(content.payload.decode("utf-8", "replace"))
    return content.payload


def _base_label(label: str) -> str:
    return re.sub(r"__\d+$", "", label or "")


def _text(value: Any) -> str:
    if isinstance(value, dict):
        name = value.get("name") or value.get("@id") or ""
        version = value.get("version") or ""
        return f"{name} {version}".strip() if name else json.dumps(cbor.to_plain(value))[:200]
    if value is None:
        return ""
    return str(value)


def _read_manifest(box: JumbfBox, asset: Optional[bytes]) -> C2paManifest:
    manifest = C2paManifest(label=box.label or "(unlabelled manifest)")
    by_label = {child.label or "": child for child in box.children if child.is_super}
    claim_box = by_label.get("c2pa.claim.v2") or by_label.get("c2pa.claim")
    if claim_box is not None:
        manifest.claim_version = 2 if claim_box.label == "c2pa.claim.v2" else 1
        try:
            _read_claim(_decode_content(claim_box), manifest)
        except Exception as exc:  # noqa: BLE001 - report and continue with what we have
            manifest.errors.append(f"Claim could not be decoded: {exc}")
    else:
        manifest.errors.append("The manifest has no claim.")
    assertion_store = by_label.get("c2pa.assertions")
    if assertion_store is not None:
        for assertion in assertion_store.children:
            if assertion.is_super:
                try:
                    _read_assertion(assertion, manifest, asset)
                except Exception as exc:  # noqa: BLE001
                    manifest.errors.append(f"Assertion {assertion.label!r} could not be decoded: {exc}")
    signature = by_label.get("c2pa.signature")
    if signature is not None:
        try:
            _read_signature(_decode_content(signature), manifest)
        except Exception as exc:  # noqa: BLE001
            manifest.errors.append(f"Signature could not be decoded: {exc}")
    else:
        manifest.errors.append("The manifest has no signature.")
    return manifest


def _read_claim(claim: Any, manifest: C2paManifest) -> None:
    if not isinstance(claim, dict):
        raise ValueError("claim is not a map")
    manifest.claim_generator = _text(claim.get("claim_generator"))
    info = claim.get("claim_generator_info")
    for item in (info if isinstance(info, list) else [info] if info else []):
        manifest.generator_info.append(_text(item))
    manifest.title = _text(claim.get("dc:title") or claim.get("title"))
    manifest.format = _text(claim.get("dc:format"))
    manifest.instance_id = _text(claim.get("instanceID"))
    manifest.algorithm = _text(claim.get("alg"))


def _read_assertion(box: JumbfBox, manifest: C2paManifest, asset: Optional[bytes]) -> None:
    label = box.label or ""
    manifest.assertions.append(label)
    base = _base_label(label)
    if base.startswith("c2pa.thumbnail"):
        return
    data = _decode_content(box)
    if base in ("c2pa.actions", "c2pa.actions.v2") and isinstance(data, dict):
        for item in data.get("actions") or []:
            if not isinstance(item, dict):
                continue
            agent = item.get("softwareAgent")
            params = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
            manifest.actions.append(C2paAction(
                action=_text(item.get("action")),
                when=_text(item.get("when")),
                software_agent=_text(agent),
                digital_source_type=_text(item.get("digitalSourceType")),
                description=_text(item.get("description") or params.get("description")),
            ))
    elif base == "c2pa.hash.data" and isinstance(data, dict):
        manifest.hard_binding = _check_data_hash(asset, data, manifest.algorithm)
    elif base.startswith("c2pa.hash."):
        manifest.hard_binding = HashCheck(base, "unsupported", "This binding type is validated by c2patool only.")
    elif base.startswith("c2pa.ingredient") and isinstance(data, dict):
        manifest.ingredients.append(C2paIngredient(
            title=_text(data.get("dc:title") or data.get("title")) or "(untitled)",
            relationship=_text(data.get("relationship")),
            format=_text(data.get("dc:format") or data.get("format")),
            has_manifest=bool(data.get("c2pa_manifest") or data.get("activeManifest")),
        ))
    elif base in ("c2pa.training-mining", "cawg.training-mining") and isinstance(data, dict):
        for key, entry in (data.get("entries") or {}).items():
            use = entry.get("use") if isinstance(entry, dict) else entry
            manifest.training_mining.append((str(key), _text(use)))
    elif base == "stds.schema-org.CreativeWork" and isinstance(data, dict):
        authors = data.get("author") or []
        for author in authors if isinstance(authors, list) else [authors]:
            manifest.authors.append(_text(author.get("name") if isinstance(author, dict) else author))
    elif base in ("c2pa.metadata", "stds.iptc", "stds.iptc.photo-metadata") and isinstance(data, dict):
        for key, value in data.items():
            if "digitalsourcetype" in str(key).lower() and value:
                manifest.metadata_source_types.append(_text(value))


def _check_data_hash(asset: Optional[bytes], assertion: dict, default_alg: str) -> HashCheck:
    alg = (_text(assertion.get("alg")) or default_alg or "sha256").lower()
    expected = assertion.get("hash")
    if asset is None:
        return HashCheck(alg, "skipped", "Asset bytes were not available.")
    if alg not in ("sha256", "sha384", "sha512"):
        return HashCheck(alg, "unsupported", f"Hash algorithm {alg} is not supported offline.")
    if not isinstance(expected, bytes):
        return HashCheck(alg, "error", "The assertion has no hash value.")
    ranges = []
    for item in assertion.get("exclusions") or []:
        try:
            ranges.append((int(item["start"]), int(item["length"])))
        except (KeyError, TypeError, ValueError):
            return HashCheck(alg, "error", "The exclusion ranges are malformed.")
    ranges.sort()
    digest = hashlib.new(alg)
    view = memoryview(asset)
    pos = 0
    for start, length in ranges:
        if start < pos or start > len(asset) or length < 0:
            return HashCheck(alg, "error", "The exclusion ranges overlap or fall outside the file.")
        digest.update(view[pos:start])
        pos = start + length
    digest.update(view[pos:])
    if digest.digest() == expected:
        return HashCheck(alg, "match", "The file content matches the hash recorded when the manifest was signed.")
    return HashCheck(alg, "mismatch", "The file has changed since the manifest was signed (the hard binding is broken).")


def _read_signature(sig: Any, manifest: C2paManifest) -> None:
    if isinstance(sig, cbor.Tagged):
        sig = sig.value
    if not isinstance(sig, list) or len(sig) < 4:
        raise ValueError("not a COSE_Sign1 structure")
    protected_raw, unprotected = sig[0], sig[1] if isinstance(sig[1], dict) else {}
    protected = cbor.loads(protected_raw) if isinstance(protected_raw, bytes) and protected_raw else {}
    if not isinstance(protected, dict):
        protected = {}
    alg = protected.get(1)
    manifest.signature_algorithm = _COSE_ALGS.get(alg, str(alg) if alg is not None else "")
    chain = protected.get(33) or unprotected.get(33) or protected.get("x5chain") or unprotected.get("x5chain")
    cert = chain[0] if isinstance(chain, list) and chain else chain
    if isinstance(cert, bytes):
        manifest.certificate = parse_certificate(cert)
    manifest.has_timestamp = any(k in unprotected for k in ("sigTst", "sigTst2")) or any(
        k in protected for k in ("sigTst", "sigTst2"))


def describe_action(action: C2paAction) -> str:
    parts = [action.action.replace("c2pa.", "") or "action"]
    if action.software_agent:
        parts.append(f"by {action.software_agent}")
    if action.digital_source_type:
        parts.append(f"({describe_source_type(action.digital_source_type)})")
    return " ".join(parts)
