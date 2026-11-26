"""Online verification services.

Two kinds of service are supported, and the report keeps them apart:

* Google SynthID watermark verification (Vertex AI "imageverification@001").
  This reads the actual SynthID watermark that Google models embed.
* AI-image classifiers (Hive, Sightengine, AI or Not). These estimate how likely
  an image is AI-generated from its pixels. They do not read SynthID.

Each service needs your own account credentials (set in Settings). The image
is uploaded only when you start a verification, after you have agreed to it.
"""

from __future__ import annotations

import base64
import io
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from AIMarkLens.core import net
from AIMarkLens.core.context import Cancelled, TaskContext
from AIMarkLens.core.errors import LabError, describe_exception
from AIMarkLens.core.integrity import read_original, verify_unchanged
from AIMarkLens.formats.containers import sniff_format

CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
DEFAULT_ENDPOINTS = {
    "google": "https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}"
              "/publishers/google/models/imageverification@001:predict",
    "hive": "https://api.thehive.ai/api/v2/task/sync",
    "sightengine": "https://api.sightengine.com/1.0/check.json",
    "aiornot": "https://api.aiornot.com/v1/reports/image",
}
DETECTED, NOT_DETECTED, UNCERTAIN, ERROR, NOT_CONFIGURED = "detected", "not_detected", "uncertain", "error", "not_configured"
HIGH, LOW = 0.85, 0.15  # classifier score thresholds


@dataclass
class OnlineConfig:
    google_project: str = ""
    google_location: str = "us-central1"
    google_auth: str = "adc"  # adc | service_account | gcloud (the verifier does not accept API keys)
    google_credentials: str = ""
    hive_key: str = ""
    sightengine_user: str = ""
    sightengine_secret: str = ""
    aiornot_key: str = ""
    endpoints: dict[str, str] = field(default_factory=dict)

    def endpoint(self, key: str) -> str:
        return self.endpoints.get(key) or DEFAULT_ENDPOINTS[key]


@dataclass
class OnlineResult:
    service: str
    name: str
    kind: str  # synthid | classifier
    status: str
    score: Optional[float] = None
    summary: str = ""
    detail: str = ""
    raw: str = ""
    upload_note: str = ""
    duration: float = 0.0


@dataclass
class OnlineReport:
    path: str
    sha256: str
    results: list[OnlineResult]
    original_unchanged: bool

    def by_service(self) -> dict[str, OnlineResult]:
        return {r.service: r for r in self.results}


@dataclass
class Upload:
    data: bytes
    filename: str
    mime: str
    note: str = ""


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    kind: str
    description: str
    signup: str
    accepts: tuple[str, ...]
    max_bytes: int
    configured: Callable[[OnlineConfig], bool]
    run: Callable[[Upload, OnlineConfig, TaskContext], OnlineResult]


# ---------------------------------------------------------------- upload preparation

_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def prepare_upload(data: bytes, accepts: tuple[str, ...], max_bytes: int) -> Upload:
    """Send the file exactly as it is when the service accepts it; otherwise convert a copy in memory."""
    fmt = sniff_format(data) or "PNG"
    if fmt in accepts and len(data) <= max_bytes:
        return Upload(data, f"image.{fmt.lower().replace('jpeg', 'jpg')}", _MIME[fmt])
    from PIL import Image

    from AIMarkLens.engines.image.pixels import open_image

    img = open_image(data).image
    img = img.convert("RGBA") if img.mode in ("RGBA", "LA", "P") else img.convert("RGB")
    buf = io.BytesIO()
    if "PNG" in accepts:
        img.save(buf, "PNG")
        if buf.tell() <= max_bytes:
            return Upload(buf.getvalue(), "image.png", "image/png",
                          f"Sent as a lossless PNG copy ({fmt} {'is not accepted' if fmt not in accepts else 'was too large'}).")
    for side in (None, 4096, 2048):
        candidate = img.convert("RGB")
        if side and max(candidate.size) > side:
            candidate.thumbnail((side, side), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        candidate.save(buf, "JPEG", quality=95)
        if buf.tell() <= max_bytes:
            size = f" reduced to {candidate.width}×{candidate.height}" if side else ""
            return Upload(buf.getvalue(), "image.jpg", "image/jpeg",
                          f"Sent as a quality-95 JPEG copy{size} to fit the service's limits.")
    raise LabError("The image is too large for this service, even after conversion.")


def _json(response, service: str) -> Any:
    try:
        return response.json()
    except ValueError:
        raise LabError(f"{service} returned a response that is not JSON.", response.text[:200])


def _post(service: str, url: str, ctx: TaskContext, **kwargs):
    import requests

    ctx.check()
    try:
        response = net.session().post(url, timeout=(net.CONNECT_TIMEOUT, 90), **kwargs)
    except requests.RequestException as exc:
        raise net.explain_request_error(exc, service) from exc
    ctx.check()
    if response.status_code >= 400:
        raise net.http_error(response, service)
    return response


def _classify(score: Optional[float]) -> str:
    if score is None:
        return UNCERTAIN
    return DETECTED if score >= HIGH else NOT_DETECTED if score <= LOW else UNCERTAIN


def _classifier_summary(score: Optional[float], source: str = "") -> str:
    if score is None:
        return "No score returned"
    verdict = {DETECTED: "Likely AI-generated", NOT_DETECTED: "Likely not AI-generated",
               UNCERTAIN: "Inconclusive"}[_classify(score)]
    return f"{verdict} ({score * 100:.1f}% AI){f'; likely source: {source}' if source else ''}"


# ---------------------------------------------------------------- Google SynthID (Vertex AI)

def google_token(cfg: OnlineConfig, ctx: TaskContext) -> str:
    if cfg.google_auth == "gcloud":
        import shutil

        from AIMarkLens.core.tools import run_process

        exe = shutil.which("gcloud") or shutil.which("gcloud.cmd")
        if not exe:
            raise LabError("The Google Cloud CLI (gcloud) was not found.",
                           "Install it and run 'gcloud auth login', or choose another sign-in method in Settings.")
        code, out, err = run_process([exe, "auth", "print-access-token"], timeout=60, ctx=ctx)
        token = out.decode("utf-8", "replace").strip().splitlines()[-1] if out.strip() else ""
        if code != 0 or not token:
            raise LabError("gcloud could not provide an access token.",
                           (err.decode("utf-8", "replace").strip() or "Run 'gcloud auth login' first.")[:300])
        return token
    try:
        import google.auth
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise LabError("The google-auth package is not installed.", "Run: pip install google-auth") from exc
    try:
        if cfg.google_auth == "service_account":
            from google.oauth2 import service_account

            if not cfg.google_credentials:
                raise LabError("No service-account key file is set.", "Choose the JSON key file in Settings.")
            creds = service_account.Credentials.from_service_account_file(cfg.google_credentials, scopes=[CLOUD_SCOPE])
        else:
            creds, _project = google.auth.default(scopes=[CLOUD_SCOPE])
        creds.refresh(Request())
        return creds.token
    except LabError:
        raise
    except (GoogleAuthError, OSError, ValueError) as exc:
        raise LabError("Google Cloud sign-in failed.",
                       f"{str(exc).rstrip('. ')}. Run 'gcloud auth application-default login', or choose a "
                       "service-account key file in Settings.") from exc


def run_google(upload: Upload, cfg: OnlineConfig, ctx: TaskContext) -> OnlineResult:
    body = {"instances": [{"image": {"bytesBase64Encoded": base64.b64encode(upload.data).decode("ascii")}}],
            "parameters": {"watermarkVerification": True}}
    if not cfg.google_project:
        raise LabError("No Google Cloud project ID is set.", "Enter the project ID in Settings.")
    location = cfg.google_location or "us-central1"
    url = cfg.endpoint("google").format(project=cfg.google_project, location=location)
    if location == "global":
        url = url.replace("https://global-aiplatform", "https://aiplatform")
    headers = {"Content-Type": "application/json; charset=utf-8",
               "Authorization": f"Bearer {google_token(cfg, ctx)}", "x-goog-user-project": cfg.google_project}
    response = _post("Google Vertex AI", url, ctx, json=body, headers=headers)
    data = _json(response, "Google Vertex AI")
    predictions = data.get("predictions") if isinstance(data, dict) else None
    decision = str(predictions[0].get("decision", "")).upper() if predictions and isinstance(predictions[0], dict) else ""
    raw = json.dumps(data, indent=2)[:4000]
    if decision == "ACCEPT":
        return OnlineResult("google", "", "synthid", DETECTED, 1.0, "SynthID watermark detected",
                            "Google's watermark verification accepted the image: it carries a SynthID watermark "
                            "from a Google model.", raw)
    if decision == "REJECT":
        return OnlineResult("google", "", "synthid", NOT_DETECTED, 0.0, "No SynthID watermark detected",
                            "Google's verifier did not find a SynthID watermark. Only images made or edited by "
                            "Google AI carry one.", raw)
    return OnlineResult("google", "", "synthid", UNCERTAIN, None, "Unexpected response from Google",
                        "The verifier answered without an ACCEPT or REJECT decision. See the raw response.", raw)


# ---------------------------------------------------------------- classifiers

_HIVE_GENERIC = {"ai_generated", "not_ai_generated", "none", "inconclusive", "inconclusive_video", "deepfake",
                 "not_deepfake", "ai_generated_audio", "not_ai_generated_audio"}


HIVE_V3_MODELS = ("hive/ai-generated-and-deepfake-content-detection", "hive/ai-generated-image-and-video-detection",
                  "hive/ai-generated-content-detection")
HIVE_KEY_HELP = ("In Hive's dashboard open API Keys and paste the Secret Key into AIMarkLens (the Access Key ID is "
                 "not used). A V2 project API key also works.")


def _hive_classes(data: Any) -> dict[str, float]:
    """Collect {class: score} from V2 ("score") or V3 ("value") responses, wherever the classes list sits."""
    found: dict[str, float] = {}

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 12:
            return
        if isinstance(node, dict):
            classes = node.get("classes")
            if isinstance(classes, list):
                for item in classes:
                    if isinstance(item, dict) and "class" in item:
                        value = item.get("score", item.get("value"))
                        try:
                            found[str(item["class"])] = max(found.get(str(item["class"]), 0.0), float(value))
                        except (TypeError, ValueError):
                            pass
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(data)
    return found


def _send(service: str, ctx: TaskContext, url: str, **kwargs):
    """POST and return the response whatever its status (callers decide what an error means)."""
    import requests

    ctx.check()
    try:
        response = net.session().post(url, timeout=(net.CONNECT_TIMEOUT, 90), **kwargs)
    except requests.RequestException as exc:
        raise net.explain_request_error(exc, service) from exc
    ctx.check()
    return response


def _hive_v3(upload: Upload, cfg: OnlineConfig, ctx: TaskContext):
    """Hive's current API: Bearer secret key, JSON body. Returns (response or None, notes)."""
    base = cfg.endpoints.get("hive_v3", "https://api.thehive.ai/api/v3/")
    models = [cfg.endpoints["hive_v3_model"]] if cfg.endpoints.get("hive_v3_model") else list(HIVE_V3_MODELS)
    b64 = base64.b64encode(upload.data).decode("ascii")
    bodies = ({"input": [{"media_base64": b64}]}, {"input": [{"media_url": f"data:{upload.mime};base64,{b64}"}]})
    headers = {"Authorization": f"Bearer {cfg.hive_key}", "Accept": "application/json"}
    notes: list[str] = []
    for model in models:
        for body in bodies:
            response = _send("Hive", ctx, base.rstrip("/") + "/" + model, json=body, headers=headers)
            if response.status_code < 400:
                return response, notes
            notes.append(f"V3 {model}: HTTP {response.status_code}")
            if response.status_code in (401, 403):
                return None, notes  # the key itself was refused; other paths will not help
            if response.status_code == 404:
                break  # try the next model name
    return None, notes


def run_hive(upload: Upload, cfg: OnlineConfig, ctx: TaskContext) -> OnlineResult:
    response, notes = _hive_v3(upload, cfg, ctx)
    api = "V3"
    if response is None:
        legacy = _send("Hive", ctx, cfg.endpoint("hive"),
                       headers={"Authorization": f"Token {cfg.hive_key}", "Accept": "application/json"},
                       files={"media": (upload.filename, upload.data, upload.mime)})
        if legacy.status_code >= 400:
            v3_refused = any(n.endswith(("HTTP 401", "HTTP 403")) for n in notes)
            notes.append(f"V2 task/sync: HTTP {legacy.status_code}")
            if v3_refused and legacy.status_code in (401, 403):
                raise LabError("Hive rejected this key on both of its APIs (" + "; ".join(notes) + ").", HIVE_KEY_HELP)
            error = net.http_error(legacy, "Hive")
            raise LabError(f"{error.message} Attempts: {'; '.join(notes)}.", error.hint)
        response, api = legacy, "V2"
    data = _json(response, "Hive")
    classes = _hive_classes(data)
    if not classes:
        raise LabError("Hive's response did not contain classification results.", json.dumps(data)[:300])
    score = classes.get("ai_generated")
    sources = sorted(((v, k) for k, v in classes.items() if k not in _HIVE_GENERIC), reverse=True)
    source = sources[0][1] if sources and sources[0][0] >= 0.5 else ""
    return OnlineResult("hive", "", "classifier", _classify(score), score, _classifier_summary(score, source),
                        f"Hive's classifier (API {api}) scores how likely the pixels are AI-generated and guesses "
                        "the generator.", json.dumps(data, indent=2)[:4000])


def run_sightengine(upload: Upload, cfg: OnlineConfig, ctx: TaskContext) -> OnlineResult:
    response = _post("Sightengine", cfg.endpoint("sightengine"), ctx,
                     data={"models": "genai", "api_user": cfg.sightengine_user, "api_secret": cfg.sightengine_secret},
                     files={"media": (upload.filename, upload.data, upload.mime)})
    data = _json(response, "Sightengine")
    if not isinstance(data, dict) or data.get("status") != "success":
        error = data.get("error", {}) if isinstance(data, dict) else {}
        raise LabError(f"Sightengine reported an error: {error.get('message') or data}",
                       "Check the API user and secret in Settings.")
    score = (data.get("type") or {}).get("ai_generated")
    score = float(score) if score is not None else None
    return OnlineResult("sightengine", "", "classifier", _classify(score), score, _classifier_summary(score),
                        "Sightengine's GenAI model scores how likely the image is AI-generated.",
                        json.dumps(data, indent=2)[:4000])


def _find(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = _find(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find(value, key)
            if found is not None:
                return found
    return None


def run_aiornot(upload: Upload, cfg: OnlineConfig, ctx: TaskContext) -> OnlineResult:
    response = _post("AI or Not", cfg.endpoint("aiornot"), ctx,
                     headers={"Authorization": f"Bearer {cfg.aiornot_key}", "Accept": "application/json"},
                     files={"object": (upload.filename, upload.data, upload.mime)})
    data = _json(response, "AI or Not")
    report = _find(data, "report") or data
    ai = _find(report, "ai") if isinstance(report, dict) else None
    score = None
    if isinstance(ai, dict) and ai.get("confidence") is not None:
        # API versions differ on whether "confidence" is P(AI) or confidence in the verdict; using the
        # detection flag to pick the side gives the right answer under either reading.
        confidence = float(ai["confidence"])
        high, low = max(confidence, 1.0 - confidence), min(confidence, 1.0 - confidence)
        score = high if ai.get("is_detected") else low
    verdict = str(_find(report, "verdict") or "").lower()
    if score is None and verdict:
        score = 1.0 if verdict == "ai" else 0.0 if verdict == "human" else None
    generators = _find(report, "generator")
    source = ""
    if isinstance(generators, dict):
        detected = [(float(v.get("confidence", 0)), k) for k, v in generators.items()
                    if isinstance(v, dict) and v.get("is_detected")]
        source = max(detected)[1].replace("_", " ") if detected else ""
    if score is None:
        raise LabError("AI or Not's response did not contain a verdict.", json.dumps(data)[:300])
    return OnlineResult("aiornot", "", "classifier", _classify(score), score, _classifier_summary(score, source),
                        "AI or Not's classifier judges whether the image is AI-generated and which generator.",
                        json.dumps(data, indent=2)[:4000])


SERVICES: tuple[Service, ...] = (
    Service("google", "Google SynthID (Vertex AI)", "synthid",
            "Reads Google's SynthID watermark through Vertex AI watermark verification. Needs a Google Cloud "
            "project with the Vertex AI API enabled.",
            "https://console.cloud.google.com/vertex-ai", ("PNG", "JPEG"), 15 * 1024 * 1024,
            lambda c: bool(c.google_project), run_google),
    Service("hive", "Hive AI-generated image detection", "classifier",
            "Classifier that estimates AI generation and the likely generator. Does not read SynthID.",
            "https://thehive.ai/", ("PNG", "JPEG", "WEBP"), 20 * 1024 * 1024, lambda c: bool(c.hive_key), run_hive),
    Service("sightengine", "Sightengine GenAI detection", "classifier",
            "Classifier that estimates AI generation. Does not read SynthID.",
            "https://sightengine.com/", ("PNG", "JPEG", "WEBP"), 20 * 1024 * 1024,
            lambda c: bool(c.sightengine_user and c.sightengine_secret), run_sightengine),
    Service("aiornot", "AI or Not", "classifier",
            "Classifier that estimates AI generation and the likely generator. Does not read SynthID.",
            "https://www.aiornot.com/", ("PNG", "JPEG", "WEBP"), 10 * 1024 * 1024, lambda c: bool(c.aiornot_key),
            run_aiornot),
)
SERVICE_BY_KEY = {s.key: s for s in SERVICES}

MANUAL_CHECKS = (
    ("Ask Gemini (\"Was this made with Google AI?\")", "https://gemini.google.com/app"),
    ("About Google SynthID", "https://deepmind.google/models/synthid/"),
    ("Content Credentials Verify", "https://contentcredentials.org/verify"),
)


def configured_services(cfg: OnlineConfig) -> list[str]:
    return [s.key for s in SERVICES if s.configured(cfg)]


def _run_one(service: Service, data: bytes, cfg: OnlineConfig, ctx: TaskContext) -> OnlineResult:
    started = time.monotonic()
    if not service.configured(cfg):
        return OnlineResult(service.key, service.name, service.kind, NOT_CONFIGURED,
                            summary="Not set up", detail="Add credentials in Settings → Online verification.")
    try:
        upload = prepare_upload(data, service.accepts, service.max_bytes)
        result = service.run(upload, cfg, ctx)
        result.upload_note = upload.note
    except Cancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure is shown, never swallowed
        message, hint = describe_exception(exc)
        result = OnlineResult(service.key, service.name, service.kind, ERROR, summary="Check failed",
                              detail=f"{message} {hint}".strip())
    result.name = service.name
    result.duration = time.monotonic() - started
    return result


def verify_bytes(data: bytes, services: list[str], cfg: OnlineConfig, ctx: TaskContext) -> list[OnlineResult]:
    """Query the chosen services in parallel. Cancelling returns at once; late answers are discarded."""
    chosen = [SERVICE_BY_KEY[k] for k in services if k in SERVICE_BY_KEY]
    if not chosen:
        return []
    results: dict[str, OnlineResult] = {}
    lock = threading.Lock()

    def work(service: Service) -> None:
        try:
            result = _run_one(service, data, cfg, ctx)
        except Cancelled:
            return
        with lock:
            results[service.key] = result

    threads = [threading.Thread(target=work, args=(s,), name=f"verify-{s.key}", daemon=True) for s in chosen]
    for thread in threads:
        thread.start()
    while any(t.is_alive() for t in threads):
        for thread in threads:
            thread.join(0.1)
        with lock:
            answered = len(results)
        ctx.progress(answered / len(chosen), f"{answered} of {len(chosen)} services answered")
    ctx.check()
    return [results[s.key] for s in chosen]


def verify_image_online(path: str, services: list[str], cfg: OnlineConfig, ctx: TaskContext,
                        history_path: str = "") -> OnlineReport:
    ctx.progress(0.0, "Reading the image (read-only)")
    data, fingerprint = read_original(path, ctx.sub(0.0, 0.05))
    if sniff_format(data) is None:
        raise LabError("This file is not a PNG, JPEG or WebP image.")
    results = verify_bytes(data, services, cfg, ctx.sub(0.05, 0.95))
    unchanged = verify_unchanged(fingerprint, ctx.sub(0.95, 1.0)).unchanged
    if history_path:
        import os

        from AIMarkLens.core import history
        from AIMarkLens.engines.image.pixels import open_image, perceptual_hash

        history.record_online(history_path, fingerprint.sha256, perceptual_hash(open_image(data).image),
                              os.path.basename(path),
                              [{"service": r.service, "name": r.name, "status": r.status, "summary": r.summary}
                               for r in results])
    ctx.progress(1.0, "Online verification complete")
    return OnlineReport(path, fingerprint.sha256, results, unchanged)
