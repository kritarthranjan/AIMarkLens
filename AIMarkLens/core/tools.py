"""Discovery and safe invocation of optional external tools (ExifTool, c2patool).

ExifTool receives the file contents through stdin, so it never sees or touches
the original path. c2patool needs a path, so it is given a private temporary
copy that is deleted afterwards.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from AIMarkLens.core.context import Cancelled, TaskContext
from AIMarkLens.core.errors import LabError

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TOOL_NAMES = {
    "exiftool": ("exiftool.exe", "exiftool"),
    "c2patool": ("c2patool.exe", "c2patool"),
}
# The Windows ExifTool download ships as "exiftool(-k).exe"; that name makes it wait for a key press,
# which would hang a background analysis, so it is never run under that name.
EXIFTOOL_PAUSING_NAME = "exiftool(-k).exe"

C2PA_TRUST = ("https://contentcredentials.org/trust/anchors.pem",
              "https://contentcredentials.org/trust/allowed.sha256.txt",
              "https://contentcredentials.org/trust/store.cfg")

TOOL_LABELS = {"exiftool": "ExifTool", "c2patool": "c2patool"}


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def search_folders(name: str) -> list[Path]:
    from AIMarkLens.core.installer import user_tools_dir

    base = app_base_dir()
    folders = [base / "tools" / name, base / "tools", user_tools_dir() / name, user_tools_dir(), base]
    winget = os.environ.get("LOCALAPPDATA")
    if winget:
        folders.append(Path(winget) / "Microsoft" / "WinGet" / "Links")
    return folders


def _resolve(configured: str, name: str) -> str:
    """Accept either the program itself or the folder that contains it."""
    if os.path.isdir(configured):
        for candidate in TOOL_NAMES[name]:
            path = os.path.join(configured, candidate)
            if os.path.isfile(path):
                return path
        return ""
    return configured if os.path.isfile(configured) else ""


def find_tool(name: str, configured: str = "") -> Optional[str]:
    if configured:
        resolved = _resolve(configured, name)
        if resolved and os.path.basename(resolved).lower() != EXIFTOOL_PAUSING_NAME:
            return resolved
    for folder in search_folders(name):
        for candidate in TOOL_NAMES[name]:
            path = folder / candidate
            if path.is_file():
                return str(path)
    for candidate in TOOL_NAMES[name]:
        found = shutil.which(candidate)
        if found and os.path.basename(found).lower() != EXIFTOOL_PAUSING_NAME:
            return found
    return None


def find_pausing_exiftool(configured: str = "") -> Optional[str]:
    """Return an 'exiftool(-k).exe' that needs renaming, so the user can be told how to fix it."""
    candidates = [Path(configured)] if configured else []
    candidates += [folder / EXIFTOOL_PAUSING_NAME for folder in search_folders("exiftool")]
    for path in candidates:
        if path.is_file() and path.name.lower() == EXIFTOOL_PAUSING_NAME:
            return str(path)
    return None


@dataclass
class ToolStatus:
    name: str
    path: str = ""
    version: str = ""
    message: str = ""

    @property
    def available(self) -> bool:
        return bool(self.path and self.version)


@dataclass
class ToolResult:
    tool: str
    status: str  # "ok" | "no-manifest" | "error" | "unavailable"
    message: str = ""
    data: Any = None
    stderr: str = ""
    duration: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def run_process(args: list[str], input_bytes: Optional[bytes] = None, timeout: float = 60.0,
                ctx: Optional[TaskContext] = None) -> tuple[int, bytes, bytes]:
    """Run a process without a console window, honouring cancellation and a timeout."""
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    except OSError as exc:
        raise LabError(f"Could not start {os.path.basename(args[0])}: {exc.strerror or exc}",
                       "Check the tool path in Settings.") from exc
    deadline = time.monotonic() + timeout
    first = True
    while True:
        try:
            out, err = proc.communicate(input=input_bytes if first else None, timeout=0.2)
            return proc.returncode, out, err
        except subprocess.TimeoutExpired:
            first = False
            cancelled = ctx is not None and ctx.cancelled
            if cancelled or time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                if cancelled:
                    raise Cancelled()
                raise LabError(f"{os.path.basename(args[0])} did not finish within {timeout:.0f} seconds.")


def probe(name: str, configured: str = "") -> ToolStatus:
    path = find_tool(name, configured)
    if not path:
        pausing = find_pausing_exiftool(configured) if name == "exiftool" else None
        if pausing:
            return ToolStatus(name, message=f"Found '{pausing}'. Rename it to exiftool.exe (the '(-k)' "
                                            "makes it wait for a key press), or use Install in Settings.")
        return ToolStatus(name, message="Not installed. Use Install in Settings, or set its path.")
    flag = "-ver" if name == "exiftool" else "--version"
    try:
        code, out, err = run_process([path, flag], timeout=20)
    except LabError as exc:
        return ToolStatus(name, path, message=exc.message)
    text = (out or err).decode("utf-8", "replace").strip()
    if code != 0 or not text:
        return ToolStatus(name, path, message=f"The tool did not report a version (exit code {code}).")
    return ToolStatus(name, path, version=text.splitlines()[0][:80], message="Ready")


def run_exiftool(exe: str, data: bytes, ctx: Optional[TaskContext] = None) -> ToolResult:
    started = time.monotonic()
    args = [exe, "-j", "-G1", "-a", "-s", "-u", "-charset", "UTF8", "-"]
    code, out, err = run_process(args, input_bytes=data, timeout=90, ctx=ctx)
    stderr = err.decode("utf-8", "replace").strip()
    try:
        parsed = json.loads(out.decode("utf-8", "replace") or "[]")
    except json.JSONDecodeError:
        return ToolResult("exiftool", "error", "ExifTool returned output that could not be read.", stderr=stderr)
    if not parsed:
        return ToolResult("exiftool", "error", f"ExifTool returned no data (exit code {code}).", stderr=stderr)
    record = parsed[0]
    record.pop("SourceFile", None)
    return ToolResult("exiftool", "ok", f"{len(record)} tags read", data=record, stderr=stderr,
                      duration=time.monotonic() - started)


_NO_MANIFEST_MARKERS = ("no claim found", "manifestnotfound", "jumbfnotfound", "no manifest", "notfound")


def _no_manifest(stdout: str, stderr: str) -> bool:
    combined = (stdout + " " + stderr).lower().replace(" ", "")
    return any(marker.replace(" ", "") in combined for marker in _NO_MANIFEST_MARKERS)


def _c2patool_once(args: list[str], ctx: Optional[TaskContext]) -> tuple[int, str, str]:
    code, out, err = run_process(args, timeout=120, ctx=ctx)
    return code, out.decode("utf-8", "replace").strip(), err.decode("utf-8", "replace").strip()


def _is_usage_error(code: int, stdout: str, stderr: str) -> bool:
    text = (stdout + " " + stderr).lower()
    return code == 2 or any(m in text for m in ("unrecognized subcommand", "unexpected argument", "usage:",
                                                "unrecognized argument", "invalid value"))


def run_c2patool(exe: str, data: bytes, suffix: str, ctx: Optional[TaskContext] = None,
                 online: bool = False) -> ToolResult:
    """Read and validate the manifest store. When online, signer certificates are also checked
    against the official C2PA trust list (contentcredentials.org)."""
    started = time.monotonic()
    folder = tempfile.mkdtemp(prefix="AIMarkLens-c2pa-")
    path = os.path.join(folder, "sample" + suffix)
    trust_note = ""
    try:
        with open(path, "wb") as fh:
            fh.write(data)
        code, stdout, stderr = -1, "", ""
        if online:
            anchors, allowed, config = C2PA_TRUST
            code, stdout, stderr = _c2patool_once([exe, path, "trust", "--trust_anchors", anchors,
                                                   "--allowed_list", allowed, "--trust_config", config], ctx)
            if _is_usage_error(code, stdout, stderr):
                trust_note = "This c2patool version does not accept trust-list options, so trust was not checked."
                code = -1
            elif code != 0 and not _no_manifest(stdout, stderr):
                trust_note = "The C2PA trust list could not be applied, so the manifest was validated without it."
                code = -1
            elif code == 0:
                trust_note = "Signer certificates were checked against the official C2PA trust list."
        if code == -1:
            code, stdout, stderr = _c2patool_once([exe, path], ctx)
            if not online:
                trust_note = "Trust list not checked (network verification is off)."
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    duration = time.monotonic() - started
    if code != 0:
        if _no_manifest(stdout, stderr):
            return ToolResult("c2patool", "no-manifest", "No C2PA manifest found by c2patool.",
                              stderr=stderr, duration=duration)
        return ToolResult("c2patool", "error", f"c2patool failed (exit code {code}).", stderr=stderr or stdout,
                          duration=duration)
    try:
        start = stdout.find("{")
        parsed = json.loads(stdout[start:] if start > 0 else stdout)
    except json.JSONDecodeError:
        return ToolResult("c2patool", "error", "c2patool returned output that could not be read.",
                          stderr=stderr, data=stdout, duration=duration)
    message = "Manifest store read and validated by c2patool."
    if trust_note:
        message += " " + trust_note
    return ToolResult("c2patool", "ok", message, data=parsed, stderr=stderr, duration=duration,
                      extra={"trust_checked": trust_note.startswith("Signer")})


def summarise_c2patool(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Pull the validation outcome out of c2patool's JSON (handles old and new layouts)."""
    rows: list[tuple[str, str]] = []
    if not isinstance(data, dict):
        return rows
    if "validation_state" in data:
        rows.append(("Validation state", str(data["validation_state"])))
    statuses = data.get("validation_status") or []
    results = data.get("validation_results") or {}
    active = results.get("activeManifest") if isinstance(results, dict) else None
    if isinstance(active, dict):
        for bucket in ("success", "informational", "failure"):
            for item in active.get(bucket, []) or []:
                rows.append((f"{bucket.title()}", f"{item.get('code', '')}: {item.get('explanation', '')}".strip(": ")))
    for item in statuses:
        if isinstance(item, dict):
            rows.append(("Status", f"{item.get('code', '')}: {item.get('explanation', '')}".strip(": ")))
    if not rows:
        rows.append(("Validation", "No validation problems reported"))
    if data.get("active_manifest"):
        rows.insert(0, ("Active manifest", str(data["active_manifest"])))
    return rows
