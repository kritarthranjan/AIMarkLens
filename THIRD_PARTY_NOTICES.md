# Third-party notices

AIMarkLens's release builds bundle the following open-source components. Each remains under its own licence;
the full licence texts are available from the linked projects and are included in each package's distribution.

| Component | Use in AIMarkLens | Licence |
|---|---|---|
| [Qt 6](https://www.qt.io/) and [PySide6](https://doc.qt.io/qtforpython-6/) | User interface | LGPL-3.0 (dynamically linked; you may replace the Qt libraries in the application folder) |
| [Python](https://www.python.org/) | Runtime | Python Software Foundation License |
| [Pillow](https://python-pillow.org/) | Image decoding, EXIF access | MIT-CMU (HPND) |
| [OpenCV](https://opencv.org/) (`opencv-python-headless`) | Pixel analysis, colour conversion, resampling | Apache-2.0 |
| [NumPy](https://numpy.org/) | Numerical analysis | BSD-3-Clause |
| [Requests](https://requests.readthedocs.io/), [urllib3](https://urllib3.readthedocs.io/), [certifi](https://github.com/certifi/python-certifi), [idna](https://github.com/kjd/idna), [charset-normalizer](https://github.com/jawah/charset_normalizer), [PySocks](https://github.com/Anorov/PySocks) | HTTPS for online verification and downloads | Apache-2.0, MIT, MPL-2.0, BSD-3-Clause, MIT, BSD-3-Clause |
| [google-auth](https://github.com/googleapis/google-auth-library-python) and its dependencies (cachetools, pyasn1, pyasn1-modules, rsa) | Google Cloud sign-in for SynthID verification | Apache-2.0, MIT, BSD-2-Clause, Apache-2.0 |
| [PyInstaller](https://pyinstaller.org/) bootloader | Packaging | GPL-2.0 with a bootloader exception that permits distribution of the built application |
| [Inno Setup](https://jrsoftware.org/isinfo.php) | Windows installer and uninstaller | Inno Setup License |

## Tools downloaded at the user's request

These are **not** included in the release files. AIMarkLens downloads them from their official sources, after verifying
the published SHA-256 checksums, only when the user presses **Install** in Settings.

| Tool | Source | Licence |
|---|---|---|
| [ExifTool](https://exiftool.org/) by Phil Harvey | exiftool.org (via SourceForge) | Perl Artistic License or GPL |
| [c2patool](https://github.com/contentauth/c2pa-rs) by the Content Authenticity Initiative | GitHub releases | MIT or Apache-2.0 |

## Online services

Google Vertex AI, Hive, Sightengine and AI or Not are third-party services that the user signs up to separately.
Their own terms and privacy policies apply to any image the user chooses to upload.
