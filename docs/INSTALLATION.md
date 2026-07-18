# Install SpatialScope on macOS

## Requirements

- macOS 13 or later
- Apple Silicon or Intel processor
- Write access to the selected output folder

## Install

1. Download `SpatialScope-macOS-universal.dmg` from the [latest GitHub release](https://github.com/fengshuoliu/SpatialScope/releases/latest).
2. Open the disk image.
3. Drag `SpatialScope.app` to the Applications shortcut.
4. Eject the SpatialScope disk image.
5. Open SpatialScope from Applications.

## First-launch approval

SpatialScope is distributed independently and is not notarized by Apple. If macOS blocks the first launch:

1. Try to open SpatialScope once, then dismiss the warning.
2. Open **System Settings > Privacy & Security**.
3. Scroll to the Security section and click **Open Anyway** for SpatialScope.
4. Authenticate and click **Open**.

This creates an exception for SpatialScope without disabling Gatekeeper globally. Organization-managed Macs may require approval from an administrator.

## Updates

Use **SpatialScope > Check for Updates...** from the macOS menu bar. SpatialScope verifies update archives with its embedded EdDSA public key before installation.

## Verify a download

Every release includes `SHA256SUMS.txt`. In Terminal, run:

```bash
shasum -a 256 ~/Downloads/SpatialScope-macOS-universal.dmg
```

Compare the result with the checksum published on the same GitHub release.
