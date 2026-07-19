# Install SpatialScope

SpatialScope is distributed directly through [GitHub Releases](https://github.com/fengshuoliu/SpatialScope/releases). It does not require an App Store or Microsoft Store account. macOS and Windows release independently, so use the platform-specific download links below.

## Windows 10 and 11

### Requirements

- 64-bit Windows 10 or Windows 11
- Write access to the selected output folder
- About 2 GB of free disk space for the application and temporary analysis files

### Install

1. Download [`SpatialScope-Windows-x64-Portable-2.0.0.zip`](https://github.com/fengshuoliu/SpatialScope/releases/download/windows-v2.0.0/SpatialScope-Windows-x64-Portable-2.0.0.zip).
2. Verify the ZIP against `SHA256SUMS-Windows.txt` from the same Windows release.
3. Extract the complete ZIP to a writable folder.
4. Open the extracted folder and run `SpatialScope.exe`.
5. If Microsoft Defender SmartScreen appears, select **More info**, confirm that the app name is SpatialScope, and select **Run anyway**.

Keep the extracted `engine` folder beside `SpatialScope.exe`; moving only the executable makes the scientific workflows unavailable. SpatialScope is not signed with a commercial code-signing certificate. A SmartScreen prompt is expected for an independently distributed build and does not require disabling Windows Security. Organization-managed computers may require administrator approval.

The native Windows package does not run an installer or create Start-menu and desktop shortcuts. Create a shortcut to `SpatialScope.exe` manually if desired.

### Windows updates

Windows updates are manual. Download the newer Windows portable ZIP from GitHub Releases, close SpatialScope, extract the complete package into a new folder, and run its `SpatialScope.exe`. Keep the previous folder until you have confirmed that the new version opens your data and outputs correctly.

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

The macOS and Windows platform releases include their corresponding SHA-256 checksum files. On macOS:

```bash
shasum -a 256 ~/Downloads/SpatialScope-macOS-universal.dmg
```

On Windows PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 "$HOME\Downloads\SpatialScope-Windows-x64-Portable-2.0.0.zip"
```

Compare the result with `SHA256SUMS.txt` for macOS or `SHA256SUMS-Windows.txt` for Windows on the corresponding platform release.
