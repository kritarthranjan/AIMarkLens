import base64
import json
import os

import pytest

from mock_server import MockServer
from AIMarkLens.core.context import CancelToken, Cancelled, TaskContext
from AIMarkLens.engines.image.analyser import ImageOptions, analyse_image
from AIMarkLens.engines.online import verifiers
from AIMarkLens.engines.online.report import TAB, attach_compare, merge_into
from AIMarkLens.engines.online.verifiers import OnlineConfig, verify_bytes, verify_image_online


@pytest.fixture
def server():
    s = MockServer()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def fake_google_token(monkeypatch):
    monkeypatch.setattr(verifiers, "google_token", lambda cfg, ctx: "g-token")


def config(server, **kwargs):
    cfg = OnlineConfig(google_project="proj", hive_key="h-key", sightengine_user="u",
                       sightengine_secret="s", aiornot_key="a-key", **kwargs)
    cfg.endpoints = {"google": server.url + "/vertex", "hive": server.url + "/hive", "hive_v3": server.url + "/v3",
                     "sightengine": server.url + "/sightengine", "aiornot": server.url + "/aiornot"}
    return cfg


def install_detecting_services(server, ai=True):
    server.json("POST", "/vertex", {"predictions": [{"decision": "ACCEPT" if ai else "REJECT"}]})
    server.json("POST", "/hive", {"status": [{"response": {"output": [{"classes": [
        {"class": "ai_generated", "score": 0.993 if ai else 0.02}, {"class": "not_ai_generated", "score": 0.007},
        {"class": "imagen", "score": 0.91 if ai else 0.0}, {"class": "midjourney", "score": 0.01}]}]}}]})
    server.json("POST", "/sightengine", {"status": "success", "type": {"ai_generated": 0.97 if ai else 0.05}})
    server.json("POST", "/aiornot", {"report": {"verdict": "ai" if ai else "human",
                                                "ai": {"is_detected": ai, "confidence": 0.96 if ai else 0.04},
                                                "generator": {"dall_e": {"is_detected": False, "confidence": 0.1}}}})


def test_all_services_parse_detection(server, jpeg_with_metadata):
    install_detecting_services(server, ai=True)
    report = verify_image_online(jpeg_with_metadata, ["google", "hive", "sightengine", "aiornot"],
                                 config(server), TaskContext())
    results = report.by_service()
    assert report.original_unchanged
    assert results["google"].status == "detected" and results["google"].kind == "synthid"
    assert results["hive"].status == "detected" and "imagen" in results["hive"].summary
    assert results["sightengine"].score == pytest.approx(0.97)
    assert results["aiornot"].status == "detected"
    vertex = next(r for r in server.requests if r["path"].startswith("/vertex"))
    assert vertex["headers"]["Authorization"] == "Bearer g-token"
    body = json.loads(vertex["body"])
    assert base64.b64decode(body["instances"][0]["image"]["bytesBase64Encoded"]).startswith(b"\xff\xd8")
    hive = next(r for r in server.requests if r["path"] == "/hive")
    assert hive["headers"]["Authorization"] == "Token h-key"
    assert b'name="media"' in hive["body"]


def test_not_detected_and_webp_conversion_for_google(server, webp_with_exif):
    install_detecting_services(server, ai=False)
    report = verify_image_online(webp_with_exif, ["google", "hive"], config(server), TaskContext())
    results = report.by_service()
    assert results["google"].status == "not_detected"
    assert "PNG copy" in results["google"].upload_note  # Vertex does not take WebP; Hive gets the original
    assert results["hive"].upload_note == ""
    sent = json.loads(next(r for r in server.requests if r["path"].startswith("/vertex"))["body"])
    assert base64.b64decode(sent["instances"][0]["image"]["bytesBase64Encoded"]).startswith(b"\x89PNG")


def test_errors_are_reported_not_swallowed(server, jpeg_with_metadata):
    server.json("POST", "/hive", {"message": "Invalid API token"}, status=401)
    server.raw("POST", "/sightengine", b"<html>oops</html>", content_type="text/html")
    report = verify_image_online(jpeg_with_metadata, ["hive", "sightengine", "aiornot"], config(server), TaskContext())
    results = report.by_service()
    assert results["hive"].status == "error" and "401" in results["hive"].detail
    assert "Invalid API token" in results["hive"].detail and "credentials" in results["hive"].detail
    assert results["sightengine"].status == "error" and "not JSON" in results["sightengine"].detail
    assert results["aiornot"].status == "error" and "404" in results["aiornot"].detail


def test_unreachable_service_and_unconfigured(jpeg_with_metadata):
    cfg = OnlineConfig(hive_key="k", endpoints={"hive": "http://127.0.0.1:9/hive"})
    report = verify_image_online(jpeg_with_metadata, ["hive", "sightengine"], cfg, TaskContext())
    results = report.by_service()
    assert results["hive"].status == "error" and "could not be reached" in results["hive"].detail
    assert results["sightengine"].status == "not_configured"


def test_cancellation_returns_promptly(server, jpeg_with_metadata):
    import time

    def slow(_request):
        time.sleep(3)
        return 200, {}, b"{}"

    server.routes[("POST", "/hive")] = slow
    token = CancelToken()
    started = time.monotonic()
    ctx = TaskContext(token, lambda f, m: token.cancel() if time.monotonic() - started > 0.3 else None)
    with pytest.raises(Cancelled):
        verify_bytes(open(jpeg_with_metadata, "rb").read(), ["hive"], config(server), ctx)
    assert time.monotonic() - started < 2


def test_merge_updates_verdict_and_tab(server, png_with_parameters, tmp_path, rgb):
    from PIL import Image

    install_detecting_services(server, ai=True)
    plain = tmp_path / "plain.png"
    Image.fromarray(rgb).save(plain)
    doc = analyse_image(str(plain), ImageOptions(), TaskContext())
    assert doc.verdict.tone == "info"
    report = verify_image_online(str(plain), ["google", "hive"], config(server), TaskContext())
    merge_into(doc, report)
    assert doc.verdict.headline == "SynthID watermark detected"
    assert TAB in doc.tabs()
    titles = [f.title for f in doc.sections[0].blocks[0].items]
    assert "Google SynthID watermark detected" in titles
    merge_into(doc, report)  # merging twice must not duplicate
    assert [f.title for f in doc.sections[0].blocks[0].items].count("Google SynthID watermark detected") == 1


def test_compare_online_table(server, jpeg_with_metadata, tmp_path):
    from AIMarkLens.engines.image.compare import compare_images
    from AIMarkLens.engines.image.export import DEFAULT_REMOVE, export_without_metadata

    install_detecting_services(server, ai=True)
    copy = tmp_path / "copy.jpg"
    export_without_metadata(jpeg_with_metadata, str(copy), set(DEFAULT_REMOVE), TaskContext())
    doc = compare_images(jpeg_with_metadata, str(copy), ImageOptions(), TaskContext())
    cfg = config(server)
    a = verify_image_online(jpeg_with_metadata, ["google", "hive"], cfg, TaskContext())
    b = verify_image_online(str(copy), ["google", "hive"], cfg, TaskContext())
    attach_compare(doc, a, b)
    table = next(s for s in doc.sections if s.tab == "Online").blocks[0]
    assert [row[3].text for row in table.rows] == ["Still detected", "Still detected"]


def test_google_project_mode_uses_bearer_token(server, jpeg_with_metadata, monkeypatch):
    server.json("POST", "/v1/projects/p1/locations/europe-west4/publishers/google/models/imageverification@001:predict",
                {"predictions": [{"decision": "REJECT"}]})
    monkeypatch.setattr(verifiers, "google_token", lambda cfg, ctx: "tok-123")
    cfg = OnlineConfig(google_auth="adc", google_project="p1", google_location="europe-west4",
                       endpoints={"google": server.url + "/v1/projects/{project}/locations/{location}/publishers/"
                                                         "google/models/imageverification@001:predict"})
    result = verify_image_online(jpeg_with_metadata, ["google"], cfg, TaskContext()).results[0]
    assert result.status == "not_detected"
    request = server.requests[-1]
    assert request["headers"]["Authorization"] == "Bearer tok-123"
    assert request["headers"]["x-goog-user-project"] == "p1"


def test_secrets_round_trip():
    from AIMarkLens.core.secrets import protect, unprotect

    stored = protect("sk-secret-value")
    assert stored != "sk-secret-value" and unprotect(stored) == "sk-secret-value"
    assert unprotect("") == "" and protect("") == ""


def test_settings_store_encrypts_keys(tmp_path):
    from PySide6.QtCore import QSettings

    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    from AIMarkLens.ui.settings_store import SettingsStore

    store = SettingsStore()
    settings = store.load()
    assert settings.allow_network is True  # online by default
    settings.online.hive_key = "hive-secret-123"
    settings.online.google_project = "my-project"
    store.save(settings)
    ini = next(tmp_path.rglob("*.ini")).read_text()
    assert "hive-secret-123" not in ini and "my-project" in ini
    again = SettingsStore().load()
    assert again.online.hive_key == "hive-secret-123"


def test_hive_v3_secret_key_with_value_scores(server, jpeg_with_metadata):
    """Hive's current API: Bearer secret key, JSON body, scores reported as "value"."""
    server.json("POST", "/v3/hive/ai-generated-and-deepfake-content-detection",
                {"task_id": "t1", "output": [{"classes": [{"class": "ai_generated", "value": 0.98},
                                                          {"class": "flux", "value": 0.81}]}]})
    result = verify_image_online(jpeg_with_metadata, ["hive"], config(server), TaskContext()).results[0]
    assert result.status == "detected" and "flux" in result.summary and "API V3" in result.detail
    request = next(r for r in server.requests if r["path"].startswith("/v3/"))
    assert request["headers"]["Authorization"] == "Bearer h-key"
    assert json.loads(request["body"])["input"][0]["media_base64"]
    assert not any(r["path"] == "/hive" for r in server.requests)  # no fallback needed


def test_hive_key_refused_on_both_apis_explains_which_key(server, jpeg_with_metadata):
    for model in ("ai-generated-and-deepfake-content-detection",):
        server.json("POST", f"/v3/hive/{model}", {"status_code": 401, "message": "Invalid or expired token"}, status=401)
    server.json("POST", "/hive", {"return_code": 403, "message": "Invalid Auth Token."}, status=403)
    result = verify_image_online(jpeg_with_metadata, ["hive"], config(server), TaskContext()).results[0]
    assert result.status == "error"
    assert "rejected this key on both" in result.detail and "Secret Key" in result.detail
    assert "Access Key ID is not used" in result.detail
