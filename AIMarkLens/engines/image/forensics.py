"""Pixel-level research analyses: LSB statistics, noise-residual spectrum and JPEG quality.

These measurements help you study invisible watermarking. They are descriptive
statistics, not detectors for any specific commercial watermark: SynthID and
similar schemes are designed to be statistically invisible to tests like these.
"""

from __future__ import annotations

import gzip
import math
from dataclasses import dataclass, field
from typing import Optional

from AIMarkLens.engines.image.pixels import centre_crop_box, encode_png, to_rgb_array

_VIS_SIDE = 512

STD_LUMA = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
            14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
            49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]


@dataclass
class LsbChannel:
    channel: str
    ones_ratio: float
    pair_balance_p: float
    neighbour_corr: float


@dataclass
class SpectralPeak:
    fx: float
    fy: float
    strength: float
    note: str


@dataclass
class ForensicsResult:
    crop_box: tuple[int, int, int, int]
    lsb: list[LsbChannel] = field(default_factory=list)
    residual_std: float = 0.0
    peaks: list[SpectralPeak] = field(default_factory=list)
    alpha_note: str = ""
    lsb_png: Optional[bytes] = None
    spectrum_png: Optional[bytes] = None
    residual_png: Optional[bytes] = None
    vis_size: tuple[int, int] = (0, 0)


# ---------------------------------------------------------------- statistics helpers

def _gammaincc(a: float, x: float) -> float:
    """Regularised upper incomplete gamma Q(a, x) (Numerical Recipes)."""
    if x <= 0:
        return 1.0
    if x < a + 1:
        term = total = 1.0 / a
        ap = a
        for _ in range(500):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-12:
                break
        return max(0.0, 1.0 - total * math.exp(-x + a * math.log(x) - math.lgamma(a)))
    b = x + 1 - a
    c = 1 / 1e-300
    d = 1 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-12:
            break
    return min(1.0, math.exp(-x + a * math.log(x) - math.lgamma(a)) * h)


def chi_square_sf(stat: float, dof: int) -> float:
    return _gammaincc(dof / 2.0, stat / 2.0) if dof > 0 else 1.0


def pair_balance_p(channel) -> float:
    """Westfeld-Pfitzmann chi-square attack: p close to 1 suggests full-capacity LSB embedding."""
    import numpy as np

    hist = np.bincount(channel.reshape(-1), minlength=256).astype(np.float64)
    even, odd = hist[0::2], hist[1::2]
    expected = (even + odd) / 2.0
    mask = expected > 4
    if mask.sum() < 2:
        return 0.0
    stat = float((((even - expected) ** 2)[mask] / expected[mask]).sum())
    return chi_square_sf(stat, int(mask.sum()) - 1)


def estimate_jpeg_quality(tables: dict[int, list[int]]) -> Optional[int]:
    luma = tables.get(0)
    if not luma or len(luma) != 64:
        return None
    scale = sum(luma) * 100.0 / sum(STD_LUMA)
    quality = (200 - scale) / 2 if scale <= 100 else 5000 / scale
    return int(round(max(1, min(100, quality))))


# ---------------------------------------------------------------- main analysis

def analyse_pixels(img, crop_size: int, ctx) -> ForensicsResult:
    import cv2
    import numpy as np

    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    box = centre_crop_box(img.width, img.height, crop_size)
    crop = img.crop(box)
    rgba = to_rgb_array(crop, with_alpha=has_alpha)
    result = ForensicsResult(crop_box=box)
    ctx.progress(0.1, "Measuring least-significant bits")

    names = ["Red", "Green", "Blue"] + (["Alpha"] if has_alpha else [])
    for index, name in enumerate(names):
        ctx.check()
        channel = np.ascontiguousarray(rgba[:, :, index])
        bits = (channel & 1).astype(np.int8)
        ones = float(bits.mean())
        signed = bits * 2 - 1
        corr = 0.0
        if signed.shape[1] > 1:
            a, b = signed[:, :-1].astype(np.float32), signed[:, 1:].astype(np.float32)
            denom = float(a.std() * b.std())
            corr = float(((a - a.mean()) * (b - b.mean())).mean() / denom) if denom > 1e-9 else 1.0
        p = pair_balance_p(channel) if name != "Alpha" else float("nan")
        result.lsb.append(LsbChannel(name, ones, p, corr))
    if has_alpha:
        alpha = rgba[:, :, 3]
        lo, hi = int(alpha.min()), int(alpha.max())
        if lo >= 254:
            result.alpha_note = ("The alpha channel is effectively opaque (254-255), a pattern used to hide "
                                 "data in alpha LSBs." if lo == 254 else "The alpha channel is fully opaque.")
        else:
            result.alpha_note = f"Alpha ranges from {lo} to {hi}."

    ctx.progress(0.35, "Building LSB plane")
    lsb_vis = ((rgba[:, :, 1] & 1) * 255).astype(np.uint8)
    result.lsb_png = encode_png(_fit(lsb_vis, cv2.INTER_NEAREST))

    ctx.progress(0.5, "Computing noise residual")
    grey = cv2.cvtColor(np.ascontiguousarray(rgba[:, :, :3]), cv2.COLOR_RGB2GRAY).astype(np.float32)
    residual = grey - cv2.GaussianBlur(grey, (0, 0), 1.2)
    result.residual_std = float(residual.std())
    res_vis = np.clip(np.abs(residual) * 10, 0, 255).astype(np.uint8)
    result.residual_png = encode_png(_fit(res_vis, cv2.INTER_AREA))

    ctx.progress(0.7, "Analysing frequency spectrum")
    h, w = residual.shape
    if h >= 32 and w >= 32:
        window = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
        spectrum = np.fft.fftshift(np.fft.fft2(residual * window))
        mag = np.log1p(np.abs(spectrum)).astype(np.float32)
        result.peaks = _find_peaks(mag, cv2, np)
        norm = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        coloured = cv2.applyColorMap(_fit(norm, cv2.INTER_AREA), cv2.COLORMAP_INFERNO)
        result.spectrum_png = encode_png(coloured)
        result.vis_size = (coloured.shape[1], coloured.shape[0])
    ctx.progress(1.0, "Pixel analysis complete")
    return result


def _fit(arr, interpolation):
    import cv2

    h, w = arr.shape[:2]
    scale = min(1.0, _VIS_SIDE / max(h, w))
    if scale >= 1.0:
        return arr
    return cv2.resize(arr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=interpolation)


def _find_peaks(mag, cv2, np) -> list[SpectralPeak]:
    h, w = mag.shape
    cy, cx = h // 2, w // 2
    background = cv2.blur(mag, (15, 15))
    excess = mag - background
    yy, xx = np.mgrid[0:h, 0:w]
    radius = np.hypot((yy - cy) / h, (xx - cx) / w)
    mask = (radius > 0.04) & ((yy < cy) | ((yy == cy) & (xx > cx)))
    values = excess[mask]
    if values.size == 0:
        return []
    threshold = float(values.mean() + 6 * values.std())
    local_max = excess >= cv2.dilate(excess, np.ones((5, 5), np.uint8))
    candidates = np.argwhere(local_max & mask & (excess > threshold))
    peaks = []
    for y, x in candidates:
        fy, fx = (y - cy) / h, (x - cx) / w
        strength = float(excess[y, x] - threshold)
        note = ""
        if _near_multiple(fx, 0.125) and _near_multiple(fy, 0.125):
            note = "Aligned with an 8×8 block grid (typical of JPEG compression)"
        elif _near_multiple(fx, 0.25) and _near_multiple(fy, 0.25):
            note = "Aligned with a 4-pixel grid (typical of upsampling)"
        peaks.append(SpectralPeak(float(fx), float(fy), strength, note))
    peaks.sort(key=lambda p: p.strength, reverse=True)
    return peaks[:24]


def _near_multiple(value: float, step: float, tolerance: float = 0.006) -> bool:
    remainder = abs(value) % step
    return min(remainder, step - remainder) < tolerance


# ---------------------------------------------------------------- stealth LSB metadata

_STEALTH_MAGICS = {
    b"stealth_pnginfo": ("alpha", False), b"stealth_pngcomp": ("alpha", True),
    b"stealth_rgbinfo": ("rgb", False), b"stealth_rgbcomp": ("rgb", True),
}


@dataclass
class StealthResult:
    signature: str
    channel: str
    compressed: bool
    payload: str
    error: str = ""


def detect_stealth_metadata(img) -> Optional[StealthResult]:
    """Read 'stealth' generation metadata hidden in LSBs (AUTOMATIC1111 extension, NovelAI).

    Bits are stored column by column (x outer, y inner), starting with a 15-byte
    signature, then a 32-bit payload length in bits, then the payload.
    """
    import numpy as np

    width, height = img.width, img.height
    if width * height < 64:
        return None
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    for mode in (("alpha", "rgb") if has_alpha else ("rgb",)):
        per_pixel = 1 if mode == "alpha" else 3

        def bits_for(count: int):
            columns = min(width, -(-count // (height * per_pixel)) + 1)
            region = to_rgb_array(img.crop((0, 0, columns, height)), with_alpha=(mode == "alpha"))
            plane = region[:, :, 3] if mode == "alpha" else region[:, :, :3]
            ordered = plane.transpose(1, 0, 2).reshape(-1) if mode == "rgb" else plane.T.reshape(-1)
            return (ordered[:count] & 1).astype(np.uint8)

        header = bits_for(15 * 8 + 32)
        if header.size < 152:
            continue
        signature = np.packbits(header[:120]).tobytes()
        if signature not in _STEALTH_MAGICS:
            continue
        expected_mode, compressed = _STEALTH_MAGICS[signature]
        if expected_mode != mode:
            continue
        length = int("".join(str(b) for b in header[120:152]), 2)
        if length <= 0 or length > 8 * 2 * 1024 * 1024 or length > width * height * per_pixel:
            return StealthResult(signature.decode(), mode, compressed, "", "The declared payload length is invalid.")
        payload_bits = bits_for(152 + length)[152:]
        raw = np.packbits(payload_bits).tobytes()
        try:
            text = (gzip.decompress(raw) if compressed else raw).decode("utf-8", "replace")
            return StealthResult(signature.decode(), mode, compressed, text[:20000])
        except (OSError, EOFError) as exc:
            return StealthResult(signature.decode(), mode, compressed, "", f"The payload could not be decompressed: {exc}")
    return None
