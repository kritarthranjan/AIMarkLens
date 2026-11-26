# Changelog

All notable changes to AIMarkLens are recorded here. Dates use the ISO format (YYYY-MM-DD).

## 1.1.0 (2026-11-26)

### Changed
- Renamed from AETHERVEXA to **AIMarkLens**, with new brand artwork, icons and installer images. Settings, encrypted
  API keys, analysis history and downloaded tools from AETHERVEXA (and TraceLens) are migrated automatically.
- Installing 1.1.0 upgrades an existing AETHERVEXA 1.0.0 installation in place and removes its old shortcuts.
- The image export (**Metadata-free copy…**) is now in both editions, alongside the text export (**Cleaned copy…**).
- Interface accent colour matches the new brand blue.

## 1.0.0 (2026-11-24)

Released as AETHERVEXA.

First public release.

### Editions
- **Public Edition**: the stable analysis tool, as a standard installer and a portable file. Experimental features
  are not included in the build.
- **Personal Edition**: adds the Experiments lab (robustness experiments, lab watermark, synthetic text watermark),
  metadata-free image copies, the statistical text test, provenance-artefact text cleaning and advanced settings.

### Analysis
- Image Lab for PNG, JPEG and WebP: EXIF, XMP, IPTC, ICC, PNG text chunks, C2PA Content Credentials (claims,
  actions, ingredients, signer certificate, content-hash binding), AI generation parameters, hidden LSB metadata,
  file structure and pixel statistics.
- Text Lab: exact clipboard capture, invisible and look-alike characters, decoding of known hiding schemes,
  assistant artefacts and formatting statistics, with a reveal view.
- Compare for images and text, including online detector results for both files.
- Local analysis history recognises copies and visually identical versions of images with known AI signals, so a
  file with its metadata removed is not reported as having no known origin.
- Honest verdicts: "no embedded provenance" is reported as "origin not determined", never as "clean".

### Online
- Google SynthID verification through Vertex AI, plus Hive (V3 and V2 keys), Sightengine and AI or Not.
- Signer certificates checked against the official C2PA trust list when c2patool is installed.
- One-click download of ExifTool and c2patool, verified against published SHA-256 checksums.
- API keys stored encrypted with Windows DPAPI.

### Distribution
- Inno Setup installer with per-user or all-users install, Start menu and desktop shortcuts, and an uninstaller that
  offers to remove settings, keys, history and downloaded tools.
- Portable single-file builds with a splash screen.
- Settings, keys, history and tools from the earlier development name (TraceLens) are migrated automatically.
