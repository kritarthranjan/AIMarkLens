"""Download and install the optional external tools (ExifTool, c2patool).

Both come from their official sources, are verified against published SHA-256
checksums and are installed into a per-user folder that needs no admin rights:
%LOCALAPPDATA%\\AIMarkLens\\tools. Run from a console with:

    python -m AIMarkLens.core.installer
"""

from __future__ import annotations

import io
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from AIMarkLens.core import net
from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.errors import LabError

EXIFTOOL_VERSION_URL = "https://exiftool.org/ver.txt"
EXIFTOOL_CHECKSUMS_URL = "https://exiftool.org/checksums.txt"
EXIFTOOL_ZIP_URL = "https://sourceforge.net/projects/exiftool/files/exiftool-{version}_64.zip/download"
C2PATOOL_RELEASES_URL = "https://api.github.com/repos/contentauth/c2pa-rs/releases?per_page=50"
C2PATOOL_ASSET = re.compile(r"^c2patool-v[\d.]+-x86_64-pc-windows-msvc\.zip$")


@dataclass
class InstallResult:
    tool: str
    version: str
    path: str
    verified: bool

    def describe(self) -> str:
        check = "checksum verified" if self.verified else "no published checksum to verify"
        return f"{self.tool} {self.version} installed ({check})"


def user_tools_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "AIMarkLens" / "tools"


def _text(url: str, ctx: TaskContext, service: str) -> str:
    return net.download(url, ctx, 2 * 1024 * 1024, service=service).decode("utf-8", "replace")


def _safe_extract(archive: zipfile.ZipFile, member: zipfile.ZipInfo, target: Path) -> None:
    """Extract one member, refusing paths that would escape the target folder."""
    destination = (target / member.filename).resolve()
    if not str(destination).startswith(str(target.resolve()) + os.sep) and destination != target.resolve():
        raise LabError("The downloaded archive contains an unsafe path and was rejected.", member.filename)
    if member.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as src, open(destination, "wb") as dst:
        shutil.copyfileobj(src, dst)


def _replace_dir(staging: Path, final: Path) -> None:
    if final.exists():
        old = final.with_name(final.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        final.rename(old)
        shutil.rmtree(old, ignore_errors=True)
    staging.rename(final)


def install_exiftool(ctx: TaskContext, tools_dir: Path | None = None) -> InstallResult:
    tools_dir = tools_dir or user_tools_dir()
    ctx.progress(0.02, "Checking the latest ExifTool version")
    version = _text(EXIFTOOL_VERSION_URL, ctx, "exiftool.org").strip()
    if not re.fullmatch(r"\d+\.\d+", version):
        raise LabError("exiftool.org returned an unexpected version number.", version[:40])
    checksums = _text(EXIFTOOL_CHECKSUMS_URL, ctx, "exiftool.org")
    match = re.search(rf"SHA2?-?256\(exiftool-{re.escape(version)}_64\.zip\)\s*=\s*([0-9a-f]{{64}})", checksums, re.I)
    if not match:
        raise LabError("The published checksum for this ExifTool release could not be found.",
                       "The download was not attempted. Retry later or install ExifTool manually.")
    ctx.progress(0.08, f"Downloading ExifTool {version}")
    data = net.download(EXIFTOOL_ZIP_URL.format(version=version), ctx.sub(0.08, 0.85), 120 * 1024 * 1024,
                        expected_sha256=match.group(1), service="SourceForge")
    ctx.progress(0.88, "Installing ExifTool")
    staging = tools_dir / ".exiftool-staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            prefix = f"exiftool-{version}_64/"
            members = [m for m in archive.infolist() if m.filename.startswith(prefix)]
            if not any(m.filename == prefix + "exiftool(-k).exe" for m in members):
                raise LabError("The ExifTool archive does not have the expected layout.")
            for member in members:
                member.filename = member.filename[len(prefix):]
                if member.filename:
                    _safe_extract(archive, member, staging)
        # The "(-k)" in the name makes ExifTool wait for a key press; renaming turns that off.
        (staging / "exiftool(-k).exe").rename(staging / "exiftool.exe")
        _replace_dir(staging, tools_dir / "exiftool")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    ctx.progress(1.0, "ExifTool installed")
    return InstallResult("ExifTool", version, str(tools_dir / "exiftool" / "exiftool.exe"), True)


def latest_c2patool_asset(ctx: TaskContext) -> tuple[str, str, str]:
    """Return (version tag, download URL, sha256 or '') for the newest Windows c2patool release."""
    import json

    raw = net.download(C2PATOOL_RELEASES_URL, ctx, 8 * 1024 * 1024, service="GitHub",
                       headers={"Accept": "application/vnd.github+json"})
    try:
        releases = json.loads(raw)
    except ValueError as exc:
        raise LabError("GitHub returned an unexpected response.") from exc
    for release in releases:
        if release.get("draft") or release.get("prerelease") or not str(release.get("tag_name", "")).startswith("c2patool-v"):
            continue
        for asset in release.get("assets", []):
            if C2PATOOL_ASSET.match(asset.get("name", "")):
                digest = str(asset.get("digest") or "")
                sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else ""
                return release["tag_name"].replace("c2patool-", ""), asset["browser_download_url"], sha
    raise LabError("No Windows build of c2patool was found in the recent releases.",
                   "Download it manually from github.com/contentauth/c2pa-rs/releases.")


def install_c2patool(ctx: TaskContext, tools_dir: Path | None = None) -> InstallResult:
    tools_dir = tools_dir or user_tools_dir()
    ctx.progress(0.02, "Finding the latest c2patool release")
    version, url, sha = latest_c2patool_asset(ctx)
    ctx.progress(0.08, f"Downloading c2patool {version}")
    data = net.download(url, ctx.sub(0.08, 0.85), 200 * 1024 * 1024, expected_sha256=sha or None, service="GitHub")
    ctx.progress(0.9, "Installing c2patool")
    staging = tools_dir / ".c2patool-staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            exe = next((m for m in archive.infolist() if m.filename.replace("\\", "/").split("/")[-1] == "c2patool.exe"), None)
            if exe is None:
                raise LabError("The c2patool archive does not contain c2patool.exe.")
            exe.filename = "c2patool.exe"
            _safe_extract(archive, exe, staging)
        _replace_dir(staging, tools_dir / "c2patool")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    ctx.progress(1.0, "c2patool installed")
    return InstallResult("c2patool", version, str(tools_dir / "c2patool" / "c2patool.exe"), bool(sha))


INSTALLERS = {"exiftool": install_exiftool, "c2patool": install_c2patool}


def main() -> int:
    def report(fraction: float, message: str) -> None:
        print(f"\r{fraction * 100:5.1f}%  {message[:70]:<70}", end="", flush=True)

    status = 0
    for name, installer in INSTALLERS.items():
        try:
            result = installer(TaskContext(on_progress=report))
            print(f"\n{result.describe()}: {result.path}")
        except LabError as exc:
            print(f"\n{name}: {exc.message} {exc.hint}")
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
