"""Report document model shared by the UI and the HTML / Markdown / JSON exporters.

Every analysis produces a ReportDocument. The UI renders the same document as
tabs and tables, so what you see on screen is exactly what gets exported.
"""

from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Union

from AIMarkLens import APP_NAME, APP_VERSION, PRINCIPLE

# Tones drive colour coding everywhere: signal = provenance/watermark signal found.
TONES = ("neutral", "signal", "ok", "warning", "error", "info")

ABSENCE_CAVEAT = ("Absence of detectable signals is not proof of absence. Invisible watermarks such as "
                  "Google SynthID can only be confirmed with the provider's own detector.")


@dataclass
class Finding:
    tone: str
    title: str
    detail: str = ""
    source: str = ""


@dataclass
class Verdict:
    tone: str
    headline: str
    detail: str
    caveat: str = ABSENCE_CAVEAT


@dataclass
class Cell:
    text: str
    tone: str = "neutral"


CellValue = Union[str, Cell]


@dataclass
class Paragraph:
    text: str
    tone: str = "neutral"


@dataclass
class KeyValues:
    rows: list[tuple[str, str]]


@dataclass
class Table:
    headers: list[str]
    rows: list[list[CellValue]]
    key: str = ""
    payloads: Optional[list[Any]] = None
    note: str = ""


@dataclass
class Findings:
    items: list[Finding]


@dataclass
class Picture:
    caption: str
    png: bytes
    width: int
    height: int


@dataclass
class Preformatted:
    text: str


@dataclass
class Links:
    items: list[tuple[str, str]]


Block = Union[Paragraph, KeyValues, Table, Findings, Picture, Preformatted, Links]


@dataclass
class Section:
    title: str
    blocks: list[Block]
    tab: str = "Summary"
    description: str = ""


@dataclass
class ReportDocument:
    title: str
    subject: str
    kind: str
    verdict: Optional[Verdict] = None
    integrity: list[tuple[str, str]] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    created: datetime = field(default_factory=lambda: datetime.now().astimezone())
    extra: dict[str, Any] = field(default_factory=dict)  # engine data for the UI; not exported

    def tabs(self) -> list[str]:
        seen: list[str] = []
        for section in self.sections:
            if section.tab not in seen:
                seen.append(section.tab)
        return seen

    @property
    def created_text(self) -> str:
        offset = self.created.strftime("%z")
        offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "local time"
        return f"{self.created.strftime('%Y-%m-%d %H:%M:%S')} ({offset})"


def cell_text(value: CellValue) -> str:
    return value.text if isinstance(value, Cell) else str(value)


def cell_tone(value: CellValue) -> str:
    return value.tone if isinstance(value, Cell) else "neutral"


def suggested_filename(doc: ReportDocument, extension: str) -> str:
    stem = re.sub(r"[^\w\-. ]+", "_", doc.subject).strip() or doc.kind
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    stamp = doc.created.strftime("%Y%m%d-%H%M")
    return f"{stem[:60]}-{doc.kind}-report-{stamp}.{extension}"


# ---------------------------------------------------------------- HTML

_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1b2230;--muted:#5d6878;--border:#e1e5eb;--alt:#f3f5f8;
--signal:#7b4fd6;--ok:#1d8a5a;--warning:#a86a00;--error:#c43d3d;--info:#2d6fb8;--neutral:#5d6878}
@media (prefers-color-scheme: dark){:root{--bg:#0e1116;--card:#151a21;--text:#e7ebf2;--muted:#98a2b3;
--border:#262e3a;--alt:#1a2029;--signal:#b48cff;--ok:#3cc48a;--warning:#e8a93b;--error:#ef6b6b;--info:#5bb4f0;--neutral:#98a2b3}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.55 "Segoe UI",system-ui,-apple-system,sans-serif}
main{max-width:1080px;margin:0 auto;padding:32px 20px 48px}
header h1{margin:0 0 4px;font-size:24px}header p{margin:0;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin:16px 0}
h2{font-size:17px;margin:0 0 10px}h3{font-size:14px;margin:18px 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.verdict{border-left:5px solid var(--neutral)}.verdict h2{font-size:19px}
.tone-signal{border-left-color:var(--signal)}.tone-ok{border-left-color:var(--ok)}
.tone-warning{border-left-color:var(--warning)}.tone-error{border-left-color:var(--error)}.tone-info{border-left-color:var(--info)}
.caveat{color:var(--muted);font-size:13px;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:6px 8px;vertical-align:top;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:600}tr:nth-child(even) td{background:var(--alt)}
td{word-break:break-word}td.k{width:30%;color:var(--muted)}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid currentColor}
.c-signal{color:var(--signal)}.c-ok{color:var(--ok)}.c-warning{color:var(--warning)}.c-error{color:var(--error)}.c-info{color:var(--info)}.c-neutral{color:var(--neutral)}
.finding{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)}.finding:last-child{border-bottom:0}
.finding .t{font-weight:600}.finding .d{color:var(--muted)}
.note{padding:10px 12px;border-radius:8px;background:var(--alt);border-left:4px solid var(--neutral);margin:8px 0}
pre{background:var(--alt);padding:12px;border-radius:8px;overflow:auto;font:12px/1.45 "Cascadia Mono",Consolas,monospace;white-space:pre-wrap;word-break:break-all}
figure{margin:8px 0;display:inline-block;margin-right:12px;vertical-align:top}figure img{max-width:100%;border-radius:8px;border:1px solid var(--border)}
figcaption{color:var(--muted);font-size:12px}footer{color:var(--muted);font-size:12px;margin-top:28px}
.tab-title{margin:28px 0 0;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
"""


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _html_block(block: Block) -> str:
    if isinstance(block, Paragraph):
        if block.tone == "neutral":
            return f"<p>{_e(block.text)}</p>"
        return f'<div class="note tone-{block.tone}">{_e(block.text)}</div>'
    if isinstance(block, KeyValues):
        rows = "".join(f'<tr><td class="k">{_e(k)}</td><td>{_e(v)}</td></tr>' for k, v in block.rows)
        return f"<table>{rows}</table>"
    if isinstance(block, Table):
        head = "".join(f"<th>{_e(h)}</th>" for h in block.headers)
        body = []
        for row in block.rows:
            cells = []
            for value in row:
                tone = cell_tone(value)
                text = _e(cell_text(value))
                cells.append(f'<td><span class="chip c-{tone}">{text}</span></td>' if tone != "neutral"
                             else f"<td>{text}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        note = f'<p class="caveat">{_e(block.note)}</p>' if block.note else ""
        return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>{note}"
    if isinstance(block, Findings):
        if not block.items:
            return '<p class="caveat">No findings.</p>'
        items = []
        for f in block.items:
            source = f" · {_e(f.source)}" if f.source else ""
            items.append(f'<div class="finding"><span class="chip c-{f.tone}">{_e(f.tone)}</span>'
                         f'<div><div class="t">{_e(f.title)}</div><div class="d">{_e(f.detail)}{source}</div></div></div>')
        return "".join(items)
    if isinstance(block, Picture):
        data = base64.b64encode(block.png).decode("ascii")
        return (f'<figure><img alt="{_e(block.caption)}" src="data:image/png;base64,{data}" '
                f'width="{block.width}" height="{block.height}"><figcaption>{_e(block.caption)}</figcaption></figure>')
    if isinstance(block, Preformatted):
        return f"<pre>{_e(block.text)}</pre>"
    if isinstance(block, Links):
        return "<p>" + " · ".join(f'<a href="{_e(u)}">{_e(label)}</a>' for label, u in block.items) + "</p>"
    return ""


def render_html(doc: ReportDocument) -> str:
    parts = [f"<!doctype html><html lang=\"en-GB\"><head><meta charset=\"utf-8\">"
             f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
             f"<title>{_e(doc.title)}: {_e(doc.subject)}</title><style>{_CSS}</style></head><body><main>",
             f"<header><h1>{_e(doc.title)}</h1><p>{_e(doc.subject)} · Generated {_e(doc.created_text)} "
             f"by {_e(APP_NAME)} {_e(APP_VERSION)}</p></header>"]
    if doc.verdict:
        v = doc.verdict
        parts.append(f'<section class="card verdict tone-{v.tone}"><h2>{_e(v.headline)}</h2><p>{_e(v.detail)}</p>'
                     f'<p class="caveat">{_e(v.caveat)}</p></section>')
    if doc.integrity:
        parts.append('<section class="card"><h2>File integrity</h2>' + _html_block(KeyValues(doc.integrity)) + "</section>")
    current_tab = None
    for section in doc.sections:
        if section.tab != current_tab and section.tab != "Summary":
            parts.append(f'<h2 class="tab-title">{_e(section.tab)}</h2>')
        current_tab = section.tab
        desc = f'<p class="caveat">{_e(section.description)}</p>' if section.description else ""
        parts.append(f'<section class="card"><h2>{_e(section.title)}</h2>{desc}'
                     + "".join(_html_block(b) for b in section.blocks) + "</section>")
    parts.append(f"<footer>{_e(PRINCIPLE)} The analysed original was opened read-only. {_e(ABSENCE_CAVEAT)}</footer>")
    parts.append("</main></body></html>")
    return "".join(parts)


# ---------------------------------------------------------------- Markdown

def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _md_block(block: Block) -> str:
    if isinstance(block, Paragraph):
        return f"> {block.text}\n" if block.tone != "neutral" else f"{block.text}\n"
    if isinstance(block, KeyValues):
        rows = "\n".join(f"| {_md(k)} | {_md(v)} |" for k, v in block.rows)
        return f"| Field | Value |\n|---|---|\n{rows}\n"
    if isinstance(block, Table):
        head = "| " + " | ".join(_md(h) for h in block.headers) + " |"
        sep = "|" + "---|" * len(block.headers)
        rows = "\n".join("| " + " | ".join(_md(cell_text(c)) for c in row) + " |" for row in block.rows)
        note = f"\n_{block.note}_\n" if block.note else ""
        return f"{head}\n{sep}\n{rows}\n{note}"
    if isinstance(block, Findings):
        if not block.items:
            return "_No findings._\n"
        return "\n".join(f"- **[{f.tone}] {f.title}**" + (f": {f.detail}" if f.detail else "")
                         + (f" _({f.source})_" if f.source else "") for f in block.items) + "\n"
    if isinstance(block, Picture):
        return f"_Image: {block.caption} (embedded in HTML and JSON exports)_\n"
    if isinstance(block, Preformatted):
        return f"```\n{block.text}\n```\n"
    if isinstance(block, Links):
        return " · ".join(f"[{label}]({url})" for label, url in block.items) + "\n"
    return ""


def render_markdown(doc: ReportDocument) -> str:
    out = [f"# {doc.title}", "", f"**Subject:** {doc.subject}  ",
           f"**Generated:** {doc.created_text} by {APP_NAME} {APP_VERSION}", ""]
    if doc.verdict:
        out += [f"## {doc.verdict.headline}", "", doc.verdict.detail, "", f"_{doc.verdict.caveat}_", ""]
    if doc.integrity:
        out += ["## File integrity", "", _md_block(KeyValues(doc.integrity))]
    for section in doc.sections:
        out += [f"## {section.title}", ""]
        if section.description:
            out += [f"_{section.description}_", ""]
        out += [_md_block(b) for b in section.blocks]
    out += ["---", f"{PRINCIPLE} The analysed original was opened read-only."]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- JSON

def _json_block(block: Block) -> dict[str, Any]:
    if isinstance(block, Paragraph):
        return {"type": "paragraph", "text": block.text, "tone": block.tone}
    if isinstance(block, KeyValues):
        return {"type": "fields", "rows": [{"field": k, "value": v} for k, v in block.rows]}
    if isinstance(block, Table):
        return {"type": "table", "headers": block.headers, "note": block.note,
                "rows": [[{"text": cell_text(c), "tone": cell_tone(c)} if isinstance(c, Cell) else c
                          for c in row] for row in block.rows]}
    if isinstance(block, Findings):
        return {"type": "findings", "items": [f.__dict__ for f in block.items]}
    if isinstance(block, Picture):
        return {"type": "image", "caption": block.caption, "width": block.width, "height": block.height,
                "png_base64": base64.b64encode(block.png).decode("ascii")}
    if isinstance(block, Preformatted):
        return {"type": "preformatted", "text": block.text}
    if isinstance(block, Links):
        return {"type": "links", "items": [{"label": l, "url": u} for l, u in block.items]}
    return {"type": "unknown"}


def render_json(doc: ReportDocument) -> str:
    payload = {
        "generator": {"name": APP_NAME, "version": APP_VERSION},
        "title": doc.title,
        "subject": doc.subject,
        "kind": doc.kind,
        "created": doc.created.isoformat(timespec="seconds"),
        "verdict": doc.verdict.__dict__ if doc.verdict else None,
        "integrity": [{"field": k, "value": v} for k, v in doc.integrity],
        "sections": [{"title": s.title, "tab": s.tab, "description": s.description,
                      "blocks": [_json_block(b) for b in s.blocks]} for s in doc.sections],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


RENDERERS = {"html": render_html, "md": render_markdown, "json": render_json}


def render(doc: ReportDocument, extension: str) -> str:
    try:
        return RENDERERS[extension.lower().lstrip(".")](doc)
    except KeyError:
        raise ValueError(f"Unsupported report format: {extension}") from None
