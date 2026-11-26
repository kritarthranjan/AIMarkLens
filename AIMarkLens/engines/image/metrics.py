"""Image fidelity metrics shared by Compare and the experiments lab."""

from __future__ import annotations

import math
from typing import Optional


def psnr(a, b) -> float:
    import numpy as np

    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return float("inf") if mse == 0 else 10 * math.log10(255.0 ** 2 / mse)


def ssim(a, b) -> float:
    """Mean structural similarity of two RGB arrays, on luminance with an 11×11 Gaussian window."""
    import cv2
    import numpy as np

    ga = cv2.cvtColor(a[:, :, :3], cv2.COLOR_RGB2GRAY).astype(np.float32)
    gb = cv2.cvtColor(b[:, :, :3], cv2.COLOR_RGB2GRAY).astype(np.float32)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)  # noqa: E731
    mu_a, mu_b = blur(ga), blur(gb)
    sa = blur(ga * ga) - mu_a ** 2
    sb = blur(gb * gb) - mu_b ** 2
    sab = blur(ga * gb) - mu_a * mu_b
    ssim_map = ((2 * mu_a * mu_b + c1) * (2 * sab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (sa + sb + c2))
    return float(ssim_map.mean())


def fidelity(a, b) -> tuple[Optional[float], Optional[float]]:
    """(PSNR, SSIM), or (None, None) when the images differ in size."""
    if a.shape[:2] != b.shape[:2]:
        return None, None
    return psnr(a[:, :, :3], b[:, :, :3]), ssim(a, b)
