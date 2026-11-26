import hashlib
import io
import json
import os
import sys
import zipfile

import pytest

from mock_server import MockServer
from AIMarkLens.core import installer, tools
from AIMarkLens.core.context import TaskContext
from AIMarkLens.core.errors import LabError

PY = sys.executable


@pytest.fixture
def server():
    s = MockServer()
    yield s
    s.close()


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _exiftool_routes(server, data, checksum=None, version="13.64"):
    server.raw("GET", "/ver.txt", version.encode(), content_type="text/plain")
    digest = checksum or hashlib.sha256(data).hexdigest()
    server.raw("GET", "/checksums.txt", f"SHA2-256(exiftool-{version}_64.zip)= {digest}\n".encode(),
               content_type="text/plain")
    server.raw("GET", f"/exiftool-{version}_64.zip", data)


@pytest.fixture
def local_sources(server, monkeypatch):
    monkeypatch.setattr(installer, "EXIFTOOL_VERSION_URL", server.url + "/ver.txt")
    monkeypatch.setattr(installer, "EXIFTOOL_CHECKSUMS_URL", server.url + "/checksums.txt")
    monkeypatch.setattr(installer, "EXIFTOOL_ZIP_URL", server.url + "/exiftool-{version}_64.zip")
    monkeypatch.setattr(installer, "C2PATOOL_RELEASES_URL", server.url + "/releases")
    return server


def test_exiftool_install_verifies_and_renames(local_sources, tmp_path):
    data = _zip({"exiftool-13.64_64/exiftool(-k).exe": b"MZ fake", "exiftool-13.64_64/exiftool_files/perl.dll": b"x"})
    _exiftool_routes(local_sources, data)
    result = installer.install_exiftool(TaskContext(), tmp_path)
    assert result.verified and result.version == "13.64"
    assert (tmp_path / "exiftool" / "exiftool.exe").read_bytes() == b"MZ fake"
    assert (tmp_path / "exiftool" / "exiftool_files" / "perl.dll").exists()
    assert not (tmp_path / "exiftool" / "exiftool(-k).exe").exists()
    # Reinstalling replaces the previous copy cleanly.
    installer.install_exiftool(TaskContext(), tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["exiftool"]


def test_exiftool_checksum_mismatch_is_rejected(local_sources, tmp_path):
    data = _zip({"exiftool-13.64_64/exiftool(-k).exe": b"MZ"})
    _exiftool_routes(local_sources, data, checksum="0" * 64)
    with pytest.raises(LabError) as excinfo:
        installer.install_exiftool(TaskContext(), tmp_path)
    assert "integrity check" in excinfo.value.message
    assert not (tmp_path / "exiftool").exists()


def test_archive_path_traversal_is_rejected(local_sources, tmp_path):
    data = _zip({"exiftool-13.64_64/exiftool(-k).exe": b"MZ", "exiftool-13.64_64/../../evil.txt": b"x"})
    _exiftool_routes(local_sources, data)
    with pytest.raises(LabError):
        installer.install_exiftool(TaskContext(), tmp_path / "tools")
    assert not (tmp_path / "evil.txt").exists()


def test_c2patool_install_picks_windows_asset(local_sources, tmp_path):
    data = _zip({"c2patool/c2patool.exe": b"MZ c2pa", "c2patool/README.md": b"r"})
    url = local_sources.url + "/c2patool.zip"
    local_sources.raw("GET", "/c2patool.zip", data)
    local_sources.json("GET", "/releases", [
        {"tag_name": "c2pa-v0.70.0", "draft": False, "prerelease": False, "assets": []},
        {"tag_name": "c2patool-v0.27.23", "draft": False, "prerelease": False, "assets": [
            {"name": "c2patool-v0.27.23-x86_64-apple-darwin.zip", "browser_download_url": "x"},
            {"name": "c2patool-v0.27.23-x86_64-pc-windows-msvc.zip", "browser_download_url": url,
             "digest": "sha256:" + hashlib.sha256(data).hexdigest()}]}])
    result = installer.install_c2patool(TaskContext(), tmp_path)
    assert result.version == "v0.27.23" and result.verified
    assert (tmp_path / "c2patool" / "c2patool.exe").read_bytes() == b"MZ c2pa"


def _fake_tool(tmp_path, name, script):
    """A stand-in for a real tool: a .cmd wrapper around a small Python script."""
    py = tmp_path / f"{name}.py"
    py.write_text(script)
    cmd = tmp_path / f"{name}.cmd"
    cmd.write_text(f'@"{PY}" "{py}" %*\r\n')
    return str(cmd)


def test_exiftool_integration_reads_from_stdin(tmp_path, jpeg_with_metadata):
    tool = _fake_tool(tmp_path, "exiftool", (
        "import sys, json, hashlib\n"
        "data = sys.stdin.buffer.read()\n"
        "assert sys.argv[-1] == '-'\n"
        "print(json.dumps([{'SourceFile': '-', 'System:FileSize': len(data), 'IFD0:Software': 'Adobe Firefly',"
        " 'Composite:SHA': hashlib.sha256(data).hexdigest()}]))\n"))
    data = open(jpeg_with_metadata, "rb").read()
    result = tools.run_exiftool(tool, data, TaskContext())
    assert result.status == "ok"
    assert result.data["System:FileSize"] == len(data)
    assert result.data["Composite:SHA"] == hashlib.sha256(data).hexdigest()


def test_c2patool_trust_and_fallbacks(tmp_path):
    manifest = json.dumps({"active_manifest": "urn:1", "validation_state": "Trusted", "manifests": {}})
    trusting = _fake_tool(tmp_path, "c2p_trust", (
        "import sys\n"
        f"print({manifest!r}) if 'trust' in sys.argv else sys.exit(5)\n"))
    result = tools.run_c2patool(trusting, b"x", ".jpg", TaskContext(), online=True)
    assert result.status == "ok" and result.extra["trust_checked"]
    assert "official C2PA trust list" in result.message

    old = _fake_tool(tmp_path, "c2p_old", (
        "import sys\n"
        "if 'trust' in sys.argv:\n    print('error: unrecognized subcommand', file=sys.stderr); sys.exit(2)\n"
        f"print({manifest!r})\n"))
    result = tools.run_c2patool(old, b"x", ".jpg", TaskContext(), online=True)
    assert result.status == "ok" and not result.extra["trust_checked"]
    assert "does not accept trust-list options" in result.message

    none = _fake_tool(tmp_path, "c2p_none", "import sys\nprint('Error: No claim found', file=sys.stderr)\nsys.exit(1)\n")
    assert tools.run_c2patool(none, b"x", ".jpg", TaskContext(), online=True).status == "no-manifest"
    offline = tools.run_c2patool(old, b"x", ".jpg", TaskContext(), online=False)
    assert "network verification is off" in offline.message


def test_discovery_and_pausing_exiftool(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(tools, "app_base_dir", lambda: tmp_path / "app")
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    assert tools.find_tool("exiftool") is None
    folder = tmp_path / "downloads"
    folder.mkdir()
    (folder / "exiftool(-k).exe").write_bytes(b"MZ")
    status = tools.probe("exiftool", str(folder / "exiftool(-k).exe"))
    assert not status.available and "Rename it to exiftool.exe" in status.message
    installed = tmp_path / "local" / "AIMarkLens" / "tools" / "c2patool"
    installed.mkdir(parents=True)
    (installed / "c2patool.exe").write_bytes(b"MZ")
    assert tools.find_tool("c2patool") == str(installed / "c2patool.exe")
    assert tools.find_tool("c2patool", str(installed)) == str(installed / "c2patool.exe")  # a folder also works


def test_connectivity_check_reports_offline(monkeypatch):
    from AIMarkLens.core import net

    monkeypatch.setattr(net, "CONNECTIVITY_URLS", ("http://127.0.0.1:9/",))
    result = net.check_connectivity(timeout=1)
    assert not result.online and "No internet" in result.detail


def test_download_resumes_after_dropped_connection(server):
    from AIMarkLens.core import net

    payload = bytes(range(256)) * 800
    calls = []

    def flaky(request):
        calls.append(request["headers"].get("Range"))
        if len(calls) == 1:
            return 200, {}, payload, 70000  # send 70,000 bytes, then drop the connection
        start = int(request["headers"]["Range"].split("=")[1].rstrip("-"))
        return 206, {"Content-Range": f"bytes {start}-{len(payload) - 1}/{len(payload)}"}, payload[start:]

    server.routes[("GET", "/big.bin")] = flaky
    data = net.download(server.url + "/big.bin", TaskContext(), 10 * 1024 * 1024,
                        expected_sha256=hashlib.sha256(payload).hexdigest())
    assert data == payload
    assert calls == [None, "bytes=65536-"]  # resumed after the last complete 64 KB block
