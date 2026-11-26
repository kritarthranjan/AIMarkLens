import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

XMP_AI = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
          b'<rdf:Description xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
          b'xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" '
          b'Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia" '
          b'photoshop:Credit="Made with Google AI"/></rdf:RDF></x:xmpmeta>')


def natural_rgb(height=480, width=640, seed=3):
    """A smooth, photo-like synthetic image (gradients, shapes, mild noise)."""
    import cv2
    import numpy as np

    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    img = np.zeros((height, width, 3), np.float32)
    for c in range(3):
        img[..., c] = 90 + 60 * np.sin(xx / (97 + c * 13)) * np.cos(yy / (71 + c * 7)) + 40 * (yy / height)
    for _ in range(25):
        colour = rng.integers(0, 255, 3).tolist()
        cv2.circle(img, (int(rng.integers(0, width)), int(rng.integers(0, height))), int(rng.integers(8, 90)), colour, -1)
    img = cv2.GaussianBlur(img, (0, 0), 2.0) + rng.normal(0, 3, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


@pytest.fixture
def rgb():
    return natural_rgb()


@pytest.fixture
def jpeg_with_metadata(tmp_path, rgb):
    from PIL import Image

    exif = Image.Exif()
    exif[0x0131] = "Adobe Firefly"
    exif[0x010F] = "Canon"
    path = tmp_path / "sample.jpg"
    Image.fromarray(rgb).save(path, quality=90, exif=exif, xmp=XMP_AI)
    return str(path)


@pytest.fixture
def png_with_parameters(tmp_path, rgb):
    from PIL import Image, PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "a lighthouse\nNegative prompt: blur\nSteps: 20, Sampler: Euler a, CFG scale: 7")
    path = tmp_path / "sample.png"
    Image.fromarray(rgb).save(path, pnginfo=info)
    return str(path)


@pytest.fixture
def webp_with_exif(tmp_path, rgb):
    from PIL import Image

    exif = Image.Exif()
    exif[0x0131] = "Midjourney"
    path = tmp_path / "sample.webp"
    Image.fromarray(rgb).save(path, quality=85, exif=exif, xmp=XMP_AI)
    return str(path)


def read(path):
    with open(path, "rb") as fh:
        return fh.read()
