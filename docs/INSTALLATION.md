# Install SpatialScope

SpatialScope is distributed directly through [GitHub Releases](https://github.com/fengshuoliu/SpatialScope/releases/latest). It does not require an App Store or Microsoft Store account.

## Windows 10 and 11

### Requirements

- 64-bit Windows 10 or Windows 11
- Write access to the selected output folder
- About 2 GB of free disk space for the application and temporary analysis files

### Install

1. Download `SpatialScope-Windows-x64-Setup-1.2.0.exe` from the latest GitHub release.
2. Open the downloaded installer.
3. If Microsoft Defender SmartScreen appears, select **More info**, confirm that the app name is SpatialScope, and select **Run anyway**.
4. Choose the installation folder and finish setup.
5. Open SpatialScope from the Start menu or desktop shortcut.

The installer is not signed with a commercial code-signing certificate. The SmartScreen prompt is expected for the first independently distributed release; it does not require disabling Windows Security. Organization-managed computers may require administrator approval.

The release also includes `SpatialScope-Windows-x64-Portable-1.2.0.exe`. It can run without installation, but it does not install shortcuts or apply automatic updates. Replace the portable executable manually when a new version is released.

### Windows updates

Installed copies check GitHub Releases automatically. When a new NSIS release is available, SpatialScope downloads it and asks to restart. You can also choose **Help > Check for Updates...**. Update downloads are validated against the SHA-512 digest in GitHub's `latest.yml` metadata.

## macOS 13 or later

### Requirements

- Apple Silicon or Intel processor
- Write access to the selected output folder

### Install

1. Download `SpatialScope-macOS-universal.dmg` from the latest GitHub release.
2. Open the disk image.
3. Drag `SpatialScope.app` to the Applications shortcut.
4. Eject the SpatialScope disk image.
5. Open SpatialScope from Applications.

### First-launch approval

SpatialScope is distributed independently and is not notarized by Apple. If macOS blocks the first launch:

1. Try to open SpatialScope once, then dismiss the warning.
2. Open **System Settings > Privacy & Security**.
3. Scroll to Security and select **Open Anyway** for SpatialScope.
4. Authenticate and select **Open**.

This creates an exception for SpatialScope without disabling Gatekeeper globally. Organization-managed Macs may require administrator approval.

### macOS updates

Use **SpatialScope > Check for Updates...**. SpatialScope verifies update archives with its embedded EdDSA public key before installation.

## Verify a download

Each release contains separate SHA-256 checksum files for macOS and Windows. On macOS:

```bash
shasum -a 256 ~/Downloads/SpatialScope-macOS-universal.dmg
```

On Windows PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 "$HOME\Downloads\SpatialScope-Windows-x64-Setup-1.2.0.exe"
```

Compare the result with the corresponding checksum published on the same GitHub release.
