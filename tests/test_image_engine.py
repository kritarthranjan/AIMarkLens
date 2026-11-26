import io
import os

import pytest

from c2pa_builder import jpeg_with_manifest, png_with_manifest
from conftest import natural_rgb, read
from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.errors import LabError
from AIMarkLens.engines.image.analyser import ImageOptions, analyse_image
from AIMarkLens.formats.c2pa import read_manifest_store
from AIMarkLens.formats.containers import parse_container
from AIMarkLens.formats.metadata import parse_xmp, read_exif


def _titles(doc):
    return [f.title for s in doc.sections if s.title == "Findings" for f in s.blocks[0].items]


def _tiles(info, size):
    pos = 0
    for seg in info.segments:
        assert seg.offset == pos, seg
        pos += seg.length
    assert pos == size


@pytest.mark.parametrize("fixture", ["jpeg_with_metadata", "png_with_parameters", "webp_with_exif"])
def test_container_segments_tile_the_file(fixture, request):
    data = read(request.getfixturevalue(fixture))
    info = parse_container(data)
    _tiles(info, len(data))
    assert info.width == 640 and info.height == 480
    assert not info.warnings


def test_jpeg_metadata_extracted(jpeg_with_metadata):
    info = parse_container(read(jpeg_with_metadata))
    exif = {e.key: e.value for e in read_exif(info.exif)}
    assert exif["Software"] == "Adobe Firefly"
    xmp = parse_xmp(info.xmp_packets[0])
    assert xmp.values("Iptc4xmpExt:DigitalSourceType")[0].endswith("trainedAlgorithmicMedia")
    assert info.quant_tables and info.details["Chroma subsampling"] == "4:2:0"


def test_unsupported_format_is_a_clear_error(tmp_path):
    path = tmp_path / "fake.png"
    path.write_bytes(b"not really an image")
    with pytest.raises(LabError) as excinfo:
        analyse_image(str(path), ImageOptions(), TaskContext())
    assert "not a PNG, JPEG or WebP" in excinfo.value.message


def test_xmp_with_doctype_is_refused():
    result = parse_xmp('<!DOCTYPE x [<!ENTITY a "b">]><x:xmpmeta xmlns:x="adobe:ns:meta/"/>')
    assert result.errors and not result.entries


def test_analysis_reports_signals_and_preserves_original(jpeg_with_metadata):
    before = read(jpeg_with_metadata)
    mtime = os.stat(jpeg_with_metadata).st_mtime_ns
    doc = analyse_image(jpeg_with_metadata, ImageOptions(), TaskContext())
    assert doc.verdict.tone == "signal"
    titles = " | ".join(_titles(doc))
    assert "trained algorithmic media" in titles and "Made with Google AI" in titles
    assert doc.extra["integrity_ok"] is True
    assert read(jpeg_with_metadata) == before and os.stat(jpeg_with_metadata).st_mtime_ns == mtime
    assert {"Summary", "Providers", "Content Credentials", "Metadata", "Structure", "Forensics"} <= set(doc.tabs())


def test_generation_parameters_detected(png_with_parameters):
    doc = analyse_image(png_with_parameters, ImageOptions(), TaskContext())
    assert any("Generation parameters embedded" in t for t in _titles(doc))


def test_clean_image_reports_no_signals_with_caveat(tmp_path, rgb):
    from PIL import Image

    path = tmp_path / "plain.png"
    Image.fromarray(rgb).save(path)
    doc = analyse_image(str(path), ImageOptions(), TaskContext())
    assert doc.verdict.headline == "No embedded provenance: origin not determined"
    assert "SynthID" in doc.verdict.caveat


def test_c2pa_manifest_in_png(tmp_path, rgb):
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "PNG")
    data = png_with_manifest(buf.getvalue())
    info = parse_container(data)
    _tiles(info, len(data))
    store = read_manifest_store(info.jumbf_boxes[0], data)
    m = store.active
    assert m.claim_generator == "ChatGPT"
    assert m.declares_ai
    assert m.signer == "OpenAI"
    assert m.certificate.issuer["O"] == "Example Trust CA"
    assert m.signature_algorithm == "ES256" and m.has_timestamp
    assert m.hard_binding.status == "match", m.hard_binding.detail

    # Any change to the image data must break the hard binding.
    idat = next(s for s in info.segments if s.kind == "pixels")
    tampered = bytearray(data)
    tampered[idat.offset + 20] ^= 0xFF
    assert read_manifest_store(info.jumbf_boxes[0], bytes(tampered)).active.hard_binding.status == "mismatch"

    path = tmp_path / "c2pa.png"
    path.write_bytes(data)
    doc = analyse_image(str(path), ImageOptions(), TaskContext())
    titles = " | ".join(_titles(doc))
    assert "Content Credentials (C2PA) manifest found" in titles
    assert "declares generative AI" in titles
    assert "matches the signed manifest" in titles
    providers = [s for s in doc.sections if s.tab == "Providers" and s.title == "OpenAI"][0]
    assert providers.blocks[0].text == "Provenance signals found"


def test_c2pa_manifest_in_jpeg(rgb):
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=85)
    data = jpeg_with_manifest(buf.getvalue(), generator="Adobe Firefly", signer="Adobe Inc.")
    info = parse_container(data)
    _tiles(info, len(data))
    store = read_manifest_store(info.jumbf_boxes[0], data)
    assert store.active.claim_generator == "Adobe Firefly"
    assert store.active.hard_binding.status == "match"


def test_stealth_lsb_metadata_detected():
    import gzip

    import numpy as np
    from PIL import Image

    from AIMarkLens.engines.image.forensics import detect_stealth_metadata

    rgba = np.dstack([natural_rgb(64, 64), np.full((64, 64), 255, np.uint8)])
    payload = gzip.compress(b'{"prompt": "a red fox", "software": "NovelAI"}')
    bits = np.unpackbits(np.frombuffer(b"stealth_pngcomp", np.uint8))
    bits = np.concatenate([bits, np.array([int(b) for b in f"{len(payload) * 8:032b}"], np.uint8),
                           np.unpackbits(np.frombuffer(payload, np.uint8))])
    alpha = rgba[:, :, 3].T.reshape(-1)  # column-major order, as the format specifies
    alpha[:bits.size] = (alpha[:bits.size] & 0xFE) | bits
    rgba[:, :, 3] = alpha.reshape(64, 64).T
    result = detect_stealth_metadata(Image.fromarray(rgba, "RGBA"))
    assert result is not None and "a red fox" in result.payload and result.compressed


def test_cancellation_stops_analysis(jpeg_with_metadata):
    from AIMarkLens.core.context import CancelToken, Cancelled

    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        analyse_image(jpeg_with_metadata, ImageOptions(), TaskContext(token))
