"""Launch AIMarkLens without a console window (also the PyInstaller entry point)."""

from AIMarkLens.app import main

if __name__ == "__main__":
    raise SystemExit(main())
