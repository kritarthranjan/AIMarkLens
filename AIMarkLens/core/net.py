"""Network access: one HTTP session, a connectivity probe and verified downloads.

Every network failure is turned into a LabError with a plain explanation, so
nothing fails silently and the UI can offer a retry.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Optional

from AIMarkLens import APP_NAME, APP_VERSION
from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.errors import LabError

USER_AGENT = f"{APP_NAME}/{APP_VERSION} (+desktop research tool)"
CONNECT_TIMEOUT = 8
READ_TIMEOUT = 60
CONNECTIVITY_URLS = ("https://www.gstatic.com/generate_204", "https://www.cloudflare.com/cdn-cgi/trace")


def session():
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def explain_request_error(exc: Exception, service: str) -> LabError:
    import requests

    if isinstance(exc, requests.exceptions.SSLError):
        return LabError(f"A secure connection to {service} could not be established.",
                        "Check the system clock and any proxy or antivirus HTTPS inspection, then retry.")
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return LabError(f"{service} did not respond in time.", "Check your internet connection, then retry.")
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return LabError(f"{service} took too long to answer.", "The service may be busy. Retry in a moment.")
    if isinstance(exc, requests.exceptions.ConnectionError):
        return LabError(f"{service} could not be reached.",
                        "Check your internet connection, firewall or proxy settings, then retry.")
    return LabError(f"The request to {service} failed: {exc}")


def http_error(response, service: str) -> LabError:
    """Describe an HTTP error response, including the service's own message when it sends one."""
    message = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error") or body.get("message") or body.get("detail") or body.get("status")
            if isinstance(error, dict):
                error = error.get("message") or error.get("status") or error
            message = str(error)[:300] if error else ""
    except ValueError:
        message = response.text[:200].strip()
    code = response.status_code
    hint = {401: "The credentials were rejected. Check the key in Settings → Online verification.",
            403: "Access was refused. Check that the key or account is allowed to use this service.",
            404: "The service endpoint was not found. Check the endpoint in Settings.",
            413: "The image is too large for this service.",
            429: "The service's rate limit or quota was reached. Wait a moment, then retry."}.get(
        code, "The service reported a problem. Retry later.")
    detail = f"{service} answered HTTP {code}" + (f": {message}" if message else ".")
    return LabError(detail, hint)


@dataclass
class Connectivity:
    online: bool
    detail: str


def check_connectivity(timeout: float = 5.0) -> Connectivity:
    import requests

    s = session()
    last = ""
    for url in CONNECTIVITY_URLS:
        try:
            response = s.get(url, timeout=(timeout, timeout), allow_redirects=False)
            if response.status_code < 500:
                return Connectivity(True, "Connected")
        except requests.RequestException as exc:
            last = type(exc).__name__
    return Connectivity(False, f"No internet connection ({last or 'unreachable'})")


def download(url: str, ctx: TaskContext, max_bytes: int, expected_sha256: Optional[str] = None,
             service: str = "the download server", headers: Optional[dict[str, Any]] = None,
             attempts: int = 4) -> bytes:
    """Download into memory with a size cap, automatic resume after dropped connections and optional
    SHA-256 verification."""
    import requests

    s = session()
    chunks: list[bytes] = []
    received = total = 0
    for attempt in range(1, attempts + 1):
        request_headers = dict(headers or {})
        if received:
            request_headers["Range"] = f"bytes={received}-"
        try:
            with s.get(url, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), headers=request_headers) as response:
                if response.status_code >= 400:
                    raise http_error(response, service)
                if received and response.status_code != 206:
                    chunks, received = [], 0  # the server ignored the range request; start again
                if not total:
                    total = int(response.headers.get("Content-Length") or 0) + received
                if total > max_bytes:
                    raise LabError(f"The download is larger than expected ({total / 1e6:.0f} MB).", url)
                for chunk in response.iter_content(65536):
                    ctx.check()
                    received += len(chunk)
                    if received > max_bytes:
                        raise LabError("The download exceeded the size limit and was stopped.", url)
                    chunks.append(chunk)
                    if total:
                        ctx.progress(min(1.0, received / total),
                                     f"Downloading ({received / 1e6:.1f} of {total / 1e6:.1f} MB)")
            break
        except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout) as exc:
            if attempt == attempts:
                if isinstance(exc, requests.exceptions.ChunkedEncodingError):
                    raise LabError(f"The download from {service} kept being interrupted.",
                                   "Check your connection and retry.") from exc
                raise explain_request_error(exc, service) from exc
            ctx.progress(received / total if total else 0.0,
                         f"Connection dropped; resuming (attempt {attempt + 1} of {attempts})")
            time.sleep(min(2.0 * attempt, 6.0))
        except requests.RequestException as exc:
            raise explain_request_error(exc, service) from exc
    data = b"".join(chunks)
    if expected_sha256:
        actual = hashlib.sha256(data).hexdigest()
        if actual.lower() != expected_sha256.lower():
            raise LabError("The downloaded file failed its integrity check and was discarded.",
                           f"Expected SHA-256 {expected_sha256}, received {actual}. Retry, or download manually.")
    return data
