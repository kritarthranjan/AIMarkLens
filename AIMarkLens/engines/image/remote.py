"""Optional network access: fetch a remote C2PA manifest referenced by an image.

Only used when the user has enabled network verification in Settings. Only
HTTPS URLs are fetched, with a size cap and timeout. Nothing about the image is
uploaded.
"""

from __future__ import annotations

import urllib.request
from urllib.parse import urlparse

from AIMarkLens import APP_NAME, APP_VERSION
from AIMarkLens.core.errors import LabError

MAX_REMOTE_BYTES = 16 * 1024 * 1024


def fetch_remote_manifest(url: str, ctx, timeout: float = 15.0) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise LabError("The remote manifest URL is not a secure HTTPS address, so it was not fetched.", url)
    request = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}",
                                                   "Accept": "application/c2pa, application/octet-stream"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            chunks, total = [], 0
            while True:
                ctx.check()
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_REMOTE_BYTES:
                    raise LabError("The remote manifest is larger than 16 MB and was not downloaded.")
                chunks.append(chunk)
            return b"".join(chunks)
    except LabError:
        raise
    except OSError as exc:
        raise LabError("The remote manifest could not be downloaded.",
                       f"{exc}. Check your internet connection, or disable network verification.") from exc
