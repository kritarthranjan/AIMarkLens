"""Generate the AIMarkLens logo set. Usage: python make_logos.py [font_dir] [out_dir]
Needs: PySide6, Pillow, fontTools, and Inter-SemiBold.otf / Inter-Regular.otf (SIL OFL)."""
from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image
from PySide6.QtCore import QByteArray
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

FONT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/usr/share/fonts/opentype/inter")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent
NAME, TAGLINE = "AIMarkLens", "AI watermark and provenance analysis"

DARK = dict(bg="#14161a", surface="#1b1e24", line="#2b3039", text="#e3e6ea", muted="#8a919c",
            ring="#3b82f6", grid="#5d6470", dot="#ffffff")
LIGHT = dict(DARK, text="#14161a", muted="#5d6470", ring="#2563eb", grid="#b4bac4", dot="#14161a")
FAINT = dict(DARK, ring="#1c212a", grid="#1f242d", dot="#2a3140")  # watermark-style backdrop mark


class Face:
    def __init__(self, path: Path) -> None:
        self.font = TTFont(path)
        self.glyphs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.upm = self.font["head"].unitsPerEm
        self.cap = self.font["OS/2"].sCapHeight / self.upm

    def width(self, text: str, size: float, tracking: float = 0) -> float:
        return sum(self.glyphs[self.cmap[ord(c)]].width * size / self.upm + tracking for c in text) - tracking

    def run(self, text: str, size: float, x: float, y: float, fill: str, tracking: float = 0) -> str:
        pen, cursor, s = SVGPathPen(self.glyphs), 0.0, size / self.upm
        for ch in text:
            glyph = self.glyphs[self.cmap[ord(ch)]]
            glyph.draw(TransformPen(pen, (s, 0, 0, -s, x + cursor, y)))
            cursor += glyph.width * s + tracking
        return f'<path d="{pen.getCommands()}" fill="{fill}"/>'


BOLD, REG = Face(FONT_DIR / "Inter-SemiBold.otf"), Face(FONT_DIR / "Inter-Regular.otf")


def mark(level: int, t: dict) -> str:
    """Lens containing a watermark grid with one highlighted cell. level 0 = tiny sizes, 2 = full."""
    ring = f'stroke="{t["ring"]}" fill="none"'
    if level == 0:
        return (f'<circle cx="108" cy="108" r="64" {ring} stroke-width="28"/>'
                f'<line x1="158" y1="158" x2="212" y2="212" stroke="{t["ring"]}" stroke-width="34" stroke-linecap="round"/>'
                f'<circle cx="108" cy="108" r="18" fill="{t["dot"]}"/>')
    body = (f'<circle cx="110" cy="110" r="68" {ring} stroke-width="18"/>'
            f'<line x1="160" y1="160" x2="214" y2="214" stroke="{t["ring"]}" stroke-width="24" stroke-linecap="round"/>')
    if level == 1:
        return body + f'<circle cx="110" cy="110" r="15" fill="{t["dot"]}"/>'
    for r in (-1, 0, 1):
        for c in (-1, 0, 1):
            if (r, c) != (-1, 1):
                body += f'<circle cx="{110 + c * 26}" cy="{110 + r * 26}" r="6" fill="{t["grid"]}"/>'
    return body + f'<circle cx="136" cy="84" r="9.5" fill="{t["dot"]}"/>'


def place(inner: str, cx: float, cy: float, size: float) -> str:
    s = size / 193
    return f'<g transform="translate({cx} {cy}) scale({s:.4f}) translate(-129.5 -129.5)">{inner}</g>'


def svg(w: int, h: int, body: str, bg: str | None = None, rx: int = 0) -> str:
    back = f'<rect width="{w}" height="{h}" rx="{rx}" fill="{bg}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'{back}{body}</svg>')


def wordmark(size: float, x: float, y: float, t: dict, center: bool = False) -> str:
    w = BOLD.width(NAME, size)
    x = x - w / 2 if center else x
    ai = BOLD.width("AI", size)
    return BOLD.run("AI", size, x, y, t["ring"]) + BOLD.run("MarkLens", size, x + ai, y, t["text"])


def text(face: Face, s: str, size: float, x: float, y: float, fill: str, center=False, tracking=0.0) -> str:
    if center:
        x -= face.width(s, size, tracking) / 2
    return face.run(s, size, x, y, fill, tracking)


def tile(size: int, level: int, t: dict) -> str:
    inner = place(mark(level, t), 128, 128, 150 if level == 2 else 168)
    body = (f'<rect x="1" y="1" width="254" height="254" rx="56" fill="{t["surface"]}" '
            f'stroke="{t["line"]}" stroke-width="2"/>{inner}')
    return svg(256, 256, body).replace('width="256" height="256"', f'width="{size}" height="{size}"', 1)


def lockup(t: dict, mark_size: int = 112, text_size: int = 60) -> str:
    pad, gap = 12, 18
    tw = BOLD.width(NAME, text_size)
    w, h = int(pad * 2 + mark_size + gap + tw + 1), mark_size + pad * 2
    body = place(mark(2, t), pad + mark_size / 2, h / 2, mark_size)
    body += wordmark(text_size, pad + mark_size + gap, h / 2 + BOLD.cap * text_size / 2, t)
    return svg(w, h, body)


def master(t: dict) -> str:
    body = place(mark(2, t), 512, 400, 520)
    body += wordmark(128, 512, 830, t, center=True)
    body += text(REG, TAGLINE, 38, 512, 905, t["muted"], center=True)
    return svg(1024, 1024, body, bg=t["bg"])


def banner(headline: list[str], footer: str) -> str:
    t = DARK
    body = place(mark(2, t), 164, 190, 190) + wordmark(44, 164, 372, t, center=True)
    for i, line in enumerate(headline):
        body += text(REG, line, 21, 164, 430 + i * 30, t["muted"], center=True)
    body += f'<rect x="132" y="560" width="64" height="2" fill="{t["line"]}"/>'
    body += text(REG, footer, 17, 164, 596, t["muted"], center=True)
    return svg(328, 628, body, bg=t["bg"])


def installer_background() -> str:
    body = place(mark(2, FAINT), 1440, 540, 980)
    body += place(mark(2, DARK), 210, 470, 120)
    body += wordmark(92, 300, 470 + BOLD.cap * 92 / 2, DARK)
    body += text(REG, TAGLINE, 34, 160, 600, DARK["muted"])
    return svg(1920, 1080, body, bg=DARK["bg"])


def portable() -> str:
    body = place(mark(2, DARK), 256, 218, 270)
    body += text(BOLD, "PORTABLE", 46, 256, 438, DARK["muted"], center=True, tracking=8)
    return svg(512, 512, body, bg=DARK["surface"], rx=112)


def download_thumbnail() -> str:
    t = DARK
    body = place(mark(2, t), 250, 250, 210)
    body += wordmark(128, 380, 250 + BOLD.cap * 128 / 2, t)
    body += text(REG, "AI watermark and provenance analysis for Windows", 40, 140, 450, t["text"])
    body += text(REG, "Windows 10 and 11   \u00b7   Read-only analysis   \u00b7   Reports in HTML, JSON and Markdown",
                 27, 140, 520, t["muted"])
    return svg(1280, 640, body, bg=t["bg"])


def render(svg_text: str, w: int, h: int, bg_opaque: bool) -> Image.Image:
    renderer = QSvgRenderer(QByteArray(svg_text.encode()))
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
    buf = io.BytesIO(img.bits().tobytes())
    pil = Image.frombuffer("RGBA", (w, h), buf.getvalue(), "raw", "BGRA", 0, 1)
    return pil.convert("RGB") if bg_opaque else pil


def write_ico(path: Path) -> None:
    sizes = {16: 0, 24: 0, 32: 0, 48: 1, 64: 1, 128: 2, 256: 2}
    pngs = {}
    for size, level in sizes.items():
        out = io.BytesIO()
        render(tile(size, level, DARK), size, size, False).save(out, "PNG")
        pngs[size] = out.getvalue()
    entries, data, offset = b"", b"", 6 + 16 * len(pngs)
    for size, blob in pngs.items():
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    path.write_bytes(struct.pack("<HHH", 0, 1, len(pngs)) + entries + data)


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "AIMarkLens_Header_Logo.svg").write_text(lockup(DARK), encoding="utf-8")
    (OUT / "AIMarkLens_Header_Logo_Light.svg").write_text(lockup(LIGHT), encoding="utf-8")
    (OUT / "AIMarkLens_Master_Logo.svg").write_text(master(DARK), encoding="utf-8")
    write_ico(OUT / "AIMarkLens_App_Icon.ico")

    render(master(DARK), 2048, 2048, True).save(OUT / "AIMarkLens_Master_Logo.png")
    render(installer_background(), 1920, 1080, True).save(OUT / "Installer_Background.png")
    render(portable(), 512, 512, False).save(OUT / "Portable_EXE_Thumbnail.png")
    render(download_thumbnail(), 1280, 640, True).save(OUT / "Software_Download_Thumbnail.png")

    welcome = banner(["AI watermark and", "provenance analysis"], "Windows 10 and 11")
    remove = banner(["Remove the application", "from this computer"], "Your files are not touched")
    render(welcome, 328, 628, True).save(OUT / "Installer_Welcome_Banner.bmp")
    render(remove, 328, 628, True).save(OUT / "Uninstaller_Banner.bmp")
    # Header sits on a white wizard page, so it is the icon tile on white.
    header = svg(110, 110, f'<rect width="110" height="110" fill="#ffffff"/>'
                 f'<g transform="translate(7 7) scale({96 / 256})">'
                 f'{tile(256, 2, DARK).split(">", 1)[1].rsplit("</svg>", 1)[0]}</g>')
    render(header, 110, 110, True).save(OUT / "Installer_Header.bmp")


if __name__ == "__main__":
    main()
