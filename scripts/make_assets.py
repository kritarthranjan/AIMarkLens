"""Derive the build and README assets from the AIMarkLens brand artwork in assets/.

The brand originals in assets/ are never modified (assets/make_logos.py creates them). This script writes:

  AIMarkLens/resources/app-icon.ico       window and taskbar icon (copied from AIMarkLens_App_Icon.ico)
  AIMarkLens/resources/app-icon-256.png   in-app logo
  assets/generated/portable.ico           icon for the portable builds (from Portable_EXE_Thumbnail.png)
  assets/generated/splash-public.png      portable-build splash screens (from Installer_Background.png)
  assets/generated/splash-personal.png
  assets/generated/social-preview.png     1280x640 GitHub social preview (from Software_Download_Thumbnail.png)

The Setup wizard uses Installer_Welcome_Banner.bmp, Installer_Header.bmp and Uninstaller_Banner.bmp directly.

Run:  .venv\\Scripts\\python.exe scripts\\make_assets.py
"""

import os
import shutil
import sys

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
OUT = os.path.join(ASSETS, "generated")
RESOURCES = os.path.join(ROOT, "AIMarkLens", "resources")
sys.path.insert(0, ROOT)

SOURCES = {
    "icon": "AIMarkLens_App_Icon.ico",
    "portable": "Portable_EXE_Thumbnail.png",
    "background": "Installer_Background.png",
    "thumbnail": "Software_Download_Thumbnail.png",
    "welcome": "Installer_Welcome_Banner.bmp",
    "header": "Installer_Header.bmp",
    "uninstall": "Uninstaller_Banner.bmp",
}
ICON_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
SPLASH_SIZE = (600, 320)
MUTED = (138, 145, 156)  # brand "muted" text colour, #8a919c


def src(key: str) -> str:
    path = os.path.join(ASSETS, SOURCES[key])
    if not os.path.isfile(path):
        raise SystemExit(f"Missing brand artwork: {path}")
    return path


def ui_font(size: int) -> ImageFont.ImageFont:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for name in ("Inter-Regular.otf", "segoeui.ttf", "arial.ttf"):
        path = os.path.join(windir, "Fonts", name)
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def largest_icon_frame() -> Image.Image:
    icon = Image.open(src("icon"))
    icon.size = max(icon.info.get("sizes", {(256, 256)}))
    return icon.convert("RGBA")


def make_app_icon() -> None:
    os.makedirs(RESOURCES, exist_ok=True)
    shutil.copyfile(src("icon"), os.path.join(RESOURCES, "app-icon.ico"))
    largest_icon_frame().resize((256, 256), Image.Resampling.LANCZOS).save(os.path.join(RESOURCES, "app-icon-256.png"))


def make_portable_icon() -> None:
    art = Image.open(src("portable")).convert("RGBA")  # already has transparent rounded corners
    side = max(art.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(art, ((side - art.width) // 2, (side - art.height) // 2), art)
    square.resize((256, 256), Image.Resampling.LANCZOS).save(os.path.join(OUT, "portable.ico"), sizes=ICON_SIZES)


def make_splashes() -> None:
    # The background already carries the logo and tagline on its left; crop to the splash size around it.
    background = Image.open(src("background")).convert("RGB")
    scaled = ImageOps.fit(background, SPLASH_SIZE, Image.Resampling.LANCZOS, centering=(0.0, 0.5))
    for edition, label in (("public", "Public Edition"), ("personal", "Personal Edition")):
        splash = scaled.copy()
        draw = ImageDraw.Draw(splash)
        draw.text((SPLASH_SIZE[0] - 20, 22), label, font=ui_font(15), fill=MUTED, anchor="ra")
        draw.rectangle((0, 0, SPLASH_SIZE[0] - 1, SPLASH_SIZE[1] - 1), outline=(43, 48, 57))
        splash.save(os.path.join(OUT, f"splash-{edition}.png"))


def make_social_preview() -> None:
    thumbnail = Image.open(src("thumbnail")).convert("RGB")
    ImageOps.fit(thumbnail, (1280, 640), Image.Resampling.LANCZOS).save(os.path.join(OUT, "social-preview.png"))


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    for key in ("welcome", "header", "uninstall"):
        src(key)  # fail early if the installer artwork is missing
    for stale in ("wizard-small-100.bmp", "wizard-small-150.bmp", "wizard-small-200.bmp"):
        path = os.path.join(OUT, stale)
        if os.path.exists(path):
            os.remove(path)  # replaced by Installer_Header.bmp
    make_app_icon()
    make_portable_icon()
    make_splashes()
    make_social_preview()
    print(f"Assets derived from the brand artwork into {OUT} and {RESOURCES}")


if __name__ == "__main__":
    main()
