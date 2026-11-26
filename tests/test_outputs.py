import io
import json

import numpy as np
import pytest

from c2pa_builder import png_with_manifest
from conftest import read
from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.errors import LabError
from AIMarkLens.core.integrity import atomic_write_bytes, guard_output_path, read_original, verify_unchanged
from AIMarkLens.core.report import Cell, Findings, Finding, KeyValues, ReportDocument, Section, Table, Verdict, render
from AIMarkLens.engines.image import labmark
from AIMarkLens.engines.image.export import DEFAULT_REMOVE, export_without_metadata
from AIMarkLens.formats.containers import parse_container


@pytest.mark.parametrize("fixture", ["jpeg_with_metadata", "png_with_parameters", "webp_with_exif"])
def test_metadata_free_copy_is_lossless(fixture, request, tmp_path):
    source = request.getfixturevalue(fixture)
    original = read(source)
    out = tmp_path / "copy"
    result = export_without_metadata(source, str(out), set(DEFAULT_REMOVE), TaskContext())
    assert result.original_unchanged and read(source) == original
    assert result.image_data_identical and result.decoded_identical
    assert result.remaining == []
    info = parse_container(read(str(out)))
    assert info.exif is None and not info.xmp_packets and not info.text


def test_metadata_free_copy_removes_c2pa(tmp_path, rgb):
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "PNG")
    src = tmp_path / "c2pa.png"
    src.write_bytes(png_with_manifest(buf.getvalue()))
    result = export_without_metadata(str(src), str(tmp_path / "out.png"), {"c2pa"}, TaskContext())
    assert not parse_container(read(str(tmp_path / "out.png"))).jumbf_boxes
    assert result.decoded_identical


def test_exports_never_overwrite_the_original(jpeg_with_metadata):
    with pytest.raises(LabError):
        guard_output_path(jpeg_with_metadata, [jpeg_with_metadata])
    with pytest.raises(LabError):
        export_without_metadata(jpeg_with_metadata, jpeg_with_metadata, {"exif"}, TaskContext())


def test_integrity_detects_changes(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"abc")
    _, fp = read_original(str(path))
    assert verify_unchanged(fp).unchanged
    atomic_write_bytes(str(path), b"abd")
    assert not verify_unchanged(fp).unchanged


def test_lab_watermark_round_trip(rgb):
    from PIL import Image

    marked = labmark.embed(rgb, "key-1")
    assert labmark.psnr(rgb, marked) > 40
    assert labmark.detect(marked, "key-1").detected
    assert not labmark.detect(marked, "another-key").detected
    assert not labmark.detect(rgb, "key-1").detected
    buf = io.BytesIO()
    Image.fromarray(marked).save(buf, "JPEG", quality=75)
    recompressed = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))
    assert labmark.detect(recompressed, "key-1").detected


def test_image_experiment_runs(png_with_parameters):
    from AIMarkLens.engines.image.experiments import run_image_experiment

    doc = run_image_experiment(png_with_parameters, ["png", "jpeg75", "crop90"], "k", TaskContext())
    table = doc.sections[0].blocks[-1]
    assert len(table.rows) == 4
    text_col = table.headers.index("Text chunks")
    assert table.rows[0][text_col].text == "Kept" and table.rows[2][text_col].text == "Lost"


def test_compare_reports_lost_signals(jpeg_with_metadata, tmp_path):
    from PIL import Image

    from AIMarkLens.engines.image.analyser import ImageOptions
    from AIMarkLens.engines.image.compare import compare_images

    stripped = tmp_path / "resaved.png"
    Image.open(jpeg_with_metadata).save(stripped)
    doc = compare_images(jpeg_with_metadata, str(stripped), ImageOptions(), TaskContext())
    assert doc.verdict.headline == "Provenance metadata was removed"
    assert "AI detectors will flag B" in doc.verdict.detail


def test_report_renderers_escape_content():
    doc = ReportDocument(title="T", subject="<script>alert(1)</script>.png", kind="image",
                         verdict=Verdict("signal", "Found", "x"),
                         sections=[Section("S", [Findings([Finding("signal", "<b>x</b>")]),
                                                 KeyValues([("a", "<i>")]),
                                                 Table(["h"], [[Cell("v", "ok")]])])])
    html = render(doc, "html")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert json.loads(render(doc, "json"))["verdict"]["headline"] == "Found"
    assert "## Found" in render(doc, "md")
