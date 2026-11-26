"""Local analysis history: lets AIMarkLens recognise copies of images it has seen before.

Removing metadata leaves the pixels unchanged, so an exported copy would look
"clean" to a metadata-only check. The history remembers, on this computer only:

* each analysed image's SHA-256 and perceptual hash, with its provenance signals;
* online verification results;
* which file is a metadata-free copy of which original.

Re-analysing a copy, or any visually identical image, then reports what is
already known about its origin. Nothing here ever leaves the computer.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

MAX_ENTRIES = 4000
PHASH_DISTANCE = 6  # of 64 bits; identical-looking images (re-encoded, resized, metadata removed)
_LOCK = threading.Lock()


def default_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "AIMarkLens", "history.json")


@dataclass
class HistoryMatch:
    kind: str  # "same-file" | "copy-of" | "lookalike"
    name: str
    when: str
    signals: list[str] = field(default_factory=list)
    online: list[dict[str, Any]] = field(default_factory=list)
    distance: int = 0

    @property
    def online_detections(self) -> list[dict[str, Any]]:
        return [o for o in self.online if o.get("status") == "detected"]

    @property
    def has_ai_evidence(self) -> bool:
        return bool(self.signals or self.online_detections)


def _load(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "entries": {}}


def _save(path: str, data: dict[str, Any]) -> None:
    entries = data["entries"]
    if len(entries) > MAX_ENTRIES:
        for sha in sorted(entries, key=lambda k: entries[k].get("when", ""))[: len(entries) - MAX_ENTRIES]:
            del entries[sha]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _entry(data: dict[str, Any], sha: str) -> dict[str, Any]:
    return data["entries"].setdefault(sha, {"name": "", "phash": "", "when": _now(), "signals": [], "online": [],
                                            "copy_of": None})


def record_analysis(path: str, sha: str, phash: str, name: str, signals: list[str]) -> None:
    if not path:
        return
    with _LOCK:
        data = _load(path)
        entry = _entry(data, sha)
        entry.update(name=name, phash=phash or entry.get("phash", ""), when=_now(), signals=list(signals)[:20])
        _save(path, data)


def record_online(path: str, sha: str, phash: str, name: str, results: list[dict[str, Any]]) -> None:
    if not path:
        return
    with _LOCK:
        data = _load(path)
        entry = _entry(data, sha)
        entry["name"] = entry.get("name") or name
        entry["phash"] = entry.get("phash") or phash
        kept = {o["service"]: o for o in entry.get("online", [])}
        for result in results:
            if result.get("status") in ("detected", "not_detected", "uncertain"):
                kept[result["service"]] = dict(result, when=_now())
        entry["online"] = list(kept.values())
        _save(path, data)


def record_copy(path: str, copy_sha: str, original_sha: str, phash: str, name: str) -> None:
    if not path:
        return
    with _LOCK:
        data = _load(path)
        entry = _entry(data, copy_sha)
        entry.update(name=name, phash=phash, when=_now(), copy_of=original_sha)
        _save(path, data)


def lookup(path: str, sha: str, phash: str) -> list[HistoryMatch]:
    """What is already known about this file: earlier analyses, its original, and look-alike images."""
    if not path or not os.path.exists(path):
        return []
    with _LOCK:
        entries = _load(path)["entries"]
    matches: list[HistoryMatch] = []
    seen = {sha}
    own = entries.get(sha)
    if own and own.get("online"):
        matches.append(HistoryMatch("same-file", own.get("name", ""), own.get("when", ""), [], own["online"]))
    origin_sha = own.get("copy_of") if own else None
    if origin_sha and origin_sha in entries:
        origin = entries[origin_sha]
        seen.add(origin_sha)
        matches.append(HistoryMatch("copy-of", origin.get("name", ""), origin.get("when", ""),
                                    origin.get("signals", []), origin.get("online", [])))
    if phash:
        lookalikes = []
        for other_sha, other in entries.items():
            if other_sha in seen or not other.get("phash"):
                continue
            if not (other.get("signals") or any(o.get("status") == "detected" for o in other.get("online", []))):
                continue
            distance = hamming(phash, other["phash"])
            if distance <= PHASH_DISTANCE:
                lookalikes.append(HistoryMatch("lookalike", other.get("name", ""), other.get("when", ""),
                                               other.get("signals", []), other.get("online", []), distance))
        matches += sorted(lookalikes, key=lambda m: (m.distance, m.when))[:3]
    return matches


def hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


def clear(path: str) -> None:
    with _LOCK:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def size(path: str) -> int:
    if not path or not os.path.exists(path):
        return 0
    with _LOCK:
        return len(_load(path)["entries"])


def online_summary(online: list[dict[str, Any]]) -> str:
    return "; ".join(f"{o.get('name', o.get('service'))}: {o.get('summary', o.get('status'))}" for o in online)
