"""Memory-conscious pixel access shared by the image analyses.

Images are decoded from the in-memory copy of the original bytes. Large images
are reduced for previews and statistics, while LSB-sensitive analyses use a
full-resolution centre crop so they never see resampled pixels.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from typing import Optional

from AIMarkLens.core.errors import LabError

MAX_PIXELS = 220_000_000  # hard ceiling; larger images are refused rather than exhausting memory


@dataclass
class DecodedImage:
    mode: str
    width: int
    height: int
    has_alpha: bool
    frames: int
    image: object  # PIL.Image.Image (first frame, fully loaded)


def open_image(data: bytes) -> DecodedImage:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            frames = getattr(img, "n_frames", 1)
            img.load()
    except Image.DecompressionBombError as exc:
        raise LabError("The image is too large to decode safely.", str(exc)) from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise LabError("The image data could not be decoded.",
                       f"The file may be truncated or damaged ({exc}). Metadata results are still shown.") from exc
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    return DecodedImage(img.mode, img.width, img.height, has_alpha, frames, img)


def to_rgb_array(img, with_alpha: bool = False):
    """Convert to an 8-bit RGB(A) numpy array without changing the pixel grid."""
    import numpy as np

    mode = "RGBA" if with_alpha else "RGB"
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        arr = np.asarray(img, dtype=np.uint32)
        arr = (arr >> 8).clip(0, 255).astype(np.uint8)
        arr = np.stack([arr] * 3, axis=-1)
        if with_alpha:
            arr = np.dstack([arr, np.full(arr.shape[:2], 255, np.uint8)])
        return arr
    if img.mode != mode:
        img = img.convert(mode)
    return np.asarray(img, dtype=np.uint8)


def centre_crop_box(width: int, height: int, size: int) -> tuple[int, int, int, int]:
    cw, ch = min(width, size), min(height, size)
    left, top = (width - cw) // 2, (height - ch) // 2
    return left, top, left + cw, top + ch


def preview_rgba(img, max_side: int = 1600):
    """Return a downscaled RGBA copy for display (thumbnail keeps memory low)."""
    from PIL import Image

    copy = img.copy() if img.mode in ("RGB", "RGBA") else img.convert("RGBA")
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return copy.convert("RGBA")


def reduced(img, max_side: int):
    from PIL import Image

    if max(img.width, img.height) <= max_side:
        return img
    copy = img.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return copy


def perceptual_hash(img) -> str:
    """64-bit DCT perceptual hash: unchanged by metadata removal and barely changed by re-encoding or resizing."""
    import cv2
    import numpy as np

    grey = np.asarray(img.convert("L"), dtype=np.float32)
    small = cv2.resize(grey, (32, 32), interpolation=cv2.INTER_AREA)
    low = cv2.dct(small)[:8, :8]
    median = np.median(low.flatten()[1:])
    bits = (low.flatten() > median).astype(np.uint8)
    return f"{int(''.join(map(str, bits)), 2):016x}"


def encode_png(arr) -> Optional[bytes]:
    import cv2

    ok, buf = cv2.imencode(".png", arr)
    return buf.tobytes() if ok else None
