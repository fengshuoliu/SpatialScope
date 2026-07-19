# SpatialScope for Windows

This directory contains the Windows x64 implementation within the canonical SpatialScope repository.

## Architecture

- `backend/` is the complete Streamlit/scientific analysis application, frozen with PyInstaller for distribution.
- `desktop/` is the Electron shell that owns the native window, folder pickers, application menu, lifecycle, and GitHub updater.
- `tests/smoke_pipeline.py` runs every analysis stage and validates representative output files on synthetic tissue data.
- `build_release.ps1` creates the frozen backend, validates it, and packages NSIS and portable releases.

## Build

Run on 64-bit Windows 10 or 11 with Python 3.11 and Node.js 22:

```powershell
./windows/build_release.ps1
```

Artifacts are written to `windows/desktop/dist/`:

- `SpatialScope-Windows-x64-Setup-<version>.exe`
- `SpatialScope-Windows-x64-Portable-<version>.exe`
- `latest.yml` and the NSIS blockmap used by automatic updates
- `SHA256SUMS-Windows.txt`

The Windows build is available beginning with SpatialScope 1.2. The installer is intentionally unsigned, so Windows SmartScreen may require the user to choose **More info > Run anyway** on first installation.
