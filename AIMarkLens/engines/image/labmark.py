"""AIMarkLens lab watermark: a transparent, documented reference watermark for experiments.

It exists so you can study invisible watermarking end to end on your own
synthetic or authorised samples: embed into a copy, transform it, and measure
whether the payload survives.

Scheme: the luminance channel is resampled to a fixed 512×512 working grid
(which makes detection independent of image size), split into 8×8 blocks and
transformed with a DCT. Each block carries one payload bit as the sign of the
difference between two mid-frequency coefficients, with a key-derived block
order and sign pattern. The embedding difference is resampled back to full
resolution and added to the original luminance. Detection soft-votes every
block and tests the recovered bits against the key's payload with an exact
binomial tail, giving a false-positive probability.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from AIMarkLens.engines.image.metrics import psnr  # noqa: F401 - re-exported for experiment callers

WORK = 512
BLOCK = 8
PAYLOAD_BITS = 48
_POS_A = (2, 3)
_POS_B = (3, 2)
DEFAULT_STRENGTH = 10.0
P_THRESHOLD = 1e-6


@dataclass
class LabmarkResult:
    matches: int
    bits: int
    p_value: float
    confidence: float

    @property
    def accuracy(self) -> float:
        return self.matches / self.bits

    @property
    def detected(self) -> bool:
        return self.p_value < P_THRESHOLD

    def describe(self) -> str:
        verdict = "detected" if self.detected else "not detected"
        return f"{verdict}: {self.matches}/{self.bits} bits ({self.accuracy:.0%}), p = {self.p_value:.1e}"


def _dct_matrix():
    import numpy as np

    c = np.zeros((BLOCK, BLOCK), dtype=np.float64)
    for k in range(BLOCK):
        scale = math.sqrt(1 / BLOCK) if k == 0 else math.sqrt(2 / BLOCK)
        for n in range(BLOCK):
            c[k, n] = scale * math.cos(math.pi * (2 * n + 1) * k / (2 * BLOCK))
    return c


def _key_material(key: str):
    import numpy as np

    seed = int.from_bytes(hashlib.sha256(f"AIMarkLens-labmark-order:{key}".encode()).digest()[:8], "big")
    payload_bytes = hashlib.sha256(f"AIMarkLens-labmark-payload:{key}".encode()).digest()[:PAYLOAD_BITS // 8]
    payload = np.unpackbits(np.frombuffer(payload_bytes, dtype=np.uint8))
    rng = np.random.default_rng(seed)
    blocks = (WORK // BLOCK) ** 2
    assignment = rng.permutation(blocks) % PAYLOAD_BITS
    signs = rng.choice(np.array([-1.0, 1.0]), size=blocks)
    return payload, assignment, signs


def _blocks(small):
    n = WORK // BLOCK
    return small.reshape(n, BLOCK, n, BLOCK).transpose(0, 2, 1, 3).reshape(-1, BLOCK, BLOCK)


def _unblocks(blocks):
    n = WORK // BLOCK
    return blocks.reshape(n, n, BLOCK, BLOCK).transpose(0, 2, 1, 3).reshape(WORK, WORK)


def _luma(rgb):
    import cv2
    import numpy as np

    return cv2.cvtColor(np.ascontiguousarray(rgb[:, :, :3]), cv2.COLOR_RGB2YCrCb).astype(np.float32)


def embed(rgb, key: str, strength: float = DEFAULT_STRENGTH):
    """Return a watermarked copy of an RGB uint8 array (the input is not modified)."""
    import cv2
    import numpy as np

    if not key:
        raise ValueError("A key is required.")
    h, w = rgb.shape[:2]
    if min(h, w) < 64:
        raise ValueError("The image is too small for the lab watermark (minimum 64×64).")
    ycc = _luma(rgb)
    y = ycc[:, :, 0].astype(np.float64)
    payload, assignment, signs = _key_material(key)
    c = _dct_matrix()
    target = (payload[assignment] * 2.0 - 1.0) * signs
    marked = y
    for _ in range(3):  # a few passes compensate for losses from resampling and rounding
        small = cv2.resize(marked, (WORK, WORK), interpolation=cv2.INTER_AREA)
        coeffs = np.einsum("ij,njk,lk->nil", c, _blocks(small), c)
        diff = coeffs[:, _POS_A[0], _POS_A[1]] - coeffs[:, _POS_B[0], _POS_B[1]]
        shortfall = np.where(target * diff < strength, (target * strength - diff) / 2.0, 0.0)
        if not np.any(shortfall):
            break
        delta = np.zeros_like(coeffs)
        delta[:, _POS_A[0], _POS_A[1]] = shortfall
        delta[:, _POS_B[0], _POS_B[1]] = -shortfall
        spatial = _unblocks(np.einsum("ji,njk,kl->nil", c, delta, c))
        marked = marked + cv2.resize(spatial, (w, h), interpolation=cv2.INTER_CUBIC) * 1.15
    ycc[:, :, 0] = np.clip(marked, 0, 255)
    out = cv2.cvtColor(np.clip(np.rint(ycc), 0, 255).astype(np.uint8), cv2.COLOR_YCrCb2RGB)
    if rgb.shape[2] == 4:
        out = np.dstack([out, rgb[:, :, 3]])
    return out


def _binomial_tail(k: int, n: int) -> float:
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def detect(rgb, key: str) -> LabmarkResult:
    import cv2
    import numpy as np

    y = _luma(rgb)[:, :, 0].astype(np.float64)
    small = cv2.resize(y, (WORK, WORK), interpolation=cv2.INTER_AREA)
    payload, assignment, signs = _key_material(key)
    c = _dct_matrix()
    coeffs = np.einsum("ij,njk,lk->nil", c, _blocks(small), c)
    diff = (coeffs[:, _POS_A[0], _POS_A[1]] - coeffs[:, _POS_B[0], _POS_B[1]]) * signs
    votes = np.bincount(assignment, weights=diff, minlength=PAYLOAD_BITS)
    bits = (votes > 0).astype(np.uint8)
    matches = int((bits == payload).sum())
    spread = float(np.abs(diff).mean()) or 1.0
    confidence = float(np.mean(votes * (payload * 2.0 - 1.0)) / (spread * len(diff) / PAYLOAD_BITS))
    return LabmarkResult(matches, PAYLOAD_BITS, _binomial_tail(matches, PAYLOAD_BITS), confidence)
