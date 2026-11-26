"""Re-analysing a metadata-free copy (or a look-alike) must not report it as having no known AI origin."""

import io

import pytest

from conftest import natural_rgb
from AIMarkLens.core import history
from AIMarkLens.core.context import TaskContext
from AIMarkLens.engines.image.analyser import ImageOptions, analyse_image
from AIMarkLens.engines.image.export import DEFAULT_REMOVE, export_without_metadata
from AIMarkLens.engines.image.pixels import perceptual_hash


def _findings(doc):
    return {f.title: f for f in doc.sections[0].blocks[0].items}


@pytest.fixture
def hist(tmp_path):
    return str(tmp_path / "history.json")


def test_metadata_free_copy_keeps_its_known_ai_origin(jpeg_with_metadata, tmp_path, hist):
    options = ImageOptions(history_path=hist)
    original = analyse_image(jpeg_with_metadata, options, TaskContext())
    assert original.verdict.tone == "signal"
    copy = tmp_path / "copy.jpg"
    export_without_metadata(jpeg_with_metadata, str(copy), set(DEFAULT_REMOVE), TaskContext(), hist)

    # Without history the copy has no metadata signals and the verdict says origin is undetermined, not "clean".
    blind = analyse_image(str(copy), ImageOptions(), TaskContext())
    assert blind.verdict.headline == "No embedded provenance: origin not determined"
    assert "does not mean the image is human-made" in blind.verdict.detail

    # With history the copy is recognised and its origin carried over.
    doc = analyse_image(str(copy), options, TaskContext())
    assert doc.verdict.tone == "signal"
    assert doc.verdict.headline == "AI origin known from an earlier analysis"
    finding = _findings(doc)["Metadata-free copy of sample.jpg"]
    assert "trained algorithmic media" in finding.detail and "pixels are identical" in finding.detail


def test_reencoded_lookalike_is_recognised(jpeg_with_metadata, tmp_path, hist):
    from PIL import Image

    analyse_image(jpeg_with_metadata, ImageOptions(history_path=hist), TaskContext())
    lookalike = tmp_path / "resaved.png"
    img = Image.open(jpeg_with_metadata)
    img.resize((img.width * 3 // 4, img.height * 3 // 4)).save(lookalike)  # new file, new hash, no metadata
    doc = analyse_image(str(lookalike), ImageOptions(history_path=hist), TaskContext())
    assert "Visually identical to sample.jpg, which had AI signals" in _findings(doc)

    unrelated = tmp_path / "other.png"
    Image.fromarray(natural_rgb(480, 640, seed=99)).save(unrelated)
    other = analyse_image(str(unrelated), ImageOptions(history_path=hist), TaskContext())
    assert not any(f.source == "History" for f in other.sections[0].blocks[0].items)


def test_online_results_are_remembered(png_with_parameters, tmp_path, hist, rgb):
    from PIL import Image

    plain = tmp_path / "plain.png"
    Image.fromarray(rgb).save(plain)
    data = plain.read_bytes()
    import hashlib

    history.record_online(hist, hashlib.sha256(data).hexdigest(), perceptual_hash(Image.open(plain)), "plain.png",
                          [{"service": "hive", "name": "Hive", "status": "detected", "summary": "Likely AI (99%)"}])
    doc = analyse_image(str(plain), ImageOptions(history_path=hist), TaskContext())
    assert "Online detectors flagged this exact file earlier" in _findings(doc)
    assert doc.verdict.tone == "signal"


def test_perceptual_hash_behaviour():
    from PIL import Image

    base = Image.fromarray(natural_rgb())
    buf = io.BytesIO()
    base.save(buf, "JPEG", quality=70)
    recompressed = Image.open(io.BytesIO(buf.getvalue()))
    other = Image.fromarray(natural_rgb(seed=42))
    assert history.hamming(perceptual_hash(base), perceptual_hash(recompressed)) <= history.PHASH_DISTANCE
    assert history.hamming(perceptual_hash(base), perceptual_hash(other)) > 16


def test_history_off_and_clear(jpeg_with_metadata, hist):
    doc = analyse_image(jpeg_with_metadata, ImageOptions(history_path=""), TaskContext())
    assert doc.extra["phash"]
    analyse_image(jpeg_with_metadata, ImageOptions(history_path=hist), TaskContext())
    assert history.size(hist) == 1
    history.clear(hist)
    assert history.size(hist) == 0
