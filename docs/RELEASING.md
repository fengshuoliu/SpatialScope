# Release SpatialScope Through GitHub

SpatialScope distributes ad-hoc-signed macOS builds and unsigned Windows x64 builds through GitHub Releases. The platforms release independently: macOS 1.2.1 uses Sparkle for updates, while Windows 1.2.6 is a native WPF application with a verified GitHub release updater. Apple Developer Program and commercial Windows code-signing memberships are not required. Users approve the app once through macOS Privacy & Security or Windows SmartScreen.

## Platform versions and tags

- macOS uses `v<version>` tags, the Xcode marketing version and build number, and the Sparkle feed in `docs/appcast.xml`.
- Windows uses `windows-v<version>` tags and the `<Version>` value in `windows/native/src/SpatialScope.App/SpatialScope.App.csproj`.
- A Windows-only release must not change the Xcode version, macOS artifacts, or Sparkle appcast.
- Public platform download links use `docs/download/<platform>/`, which selects the newest published release for that platform. GitHub's repository-level Latest badge may point to either platform without breaking the other platform's download.

The stable public download routes are:

```text
https://fengshuoliu.github.io/SpatialScope/download/macos/
https://fengshuoliu.github.io/SpatialScope/download/windows/
```

The router ignores drafts and prereleases, selects the most recently published matching platform tag, and requires the stable asset name listed below. Keep version-pinned GitHub asset URLs in release records and the Sparkle feed; use the router for public “latest” download links.

For Windows 1.2.6, use the tag `windows-v1.2.6`. Its stable download URL is:

```text
https://github.com/fengshuoliu/SpatialScope/releases/download/windows-v1.2.6/SpatialScope-Windows-x64-Setup.exe
```

The macOS download URL remains pinned to its platform release:

```text
https://github.com/fengshuoliu/SpatialScope/releases/download/v1.2.1/SpatialScope-macOS-universal.dmg
```

## One-time setup

1. Keep the Sparkle private EdDSA key in the macOS login Keychain. Do not commit, export, or share it. The matching public key is stored as `SUPublicEDKey` in `SpatialScope/Info.plist`.
2. Enable GitHub Pages for the repository with `main` and `/docs` as the source.
3. Confirm that the feed URL in `SpatialScope/Info.plist` is:

   ```text
   https://fengshuoliu.github.io/SpatialScope/appcast.xml
   ```

4. Keep the platform asset names stable.

   macOS:

   ```text
   SpatialScope-macOS-universal.dmg
   SpatialScope-macOS-universal.zip
   SHA256SUMS.txt
   ```

   Windows:

   ```text
   SpatialScope-Windows-x64-Setup.exe
   SHA256SUMS-Windows.txt
   ```

## Prepare a macOS release

1. Increase the Xcode marketing version and build number. Every Sparkle build number must be greater than the previous release.
2. Add the release date and user-facing macOS changes to `CHANGELOG.md`.
3. Build the self-contained universal artifacts:

   ```bash
   ./script/package_release.sh
   ```

4. Confirm that the DMG, ZIP, and checksum file were written to `build/release/v<version>/`.
5. Test the DMG on a second Mac or a clean macOS user account. Verify first-launch approval, one representative analysis, export creation, and **Check for Updates...**.

## Prepare a Windows release

1. Set the intended Windows version in `windows/native/src/SpatialScope.App/SpatialScope.App.csproj` without changing the macOS version.
2. Add the release date and user-facing Windows changes to `CHANGELOG.md`.
3. On a Windows x64 host, prepare and test the source application:

   ```powershell
   .\windows\run_native.ps1 setup
   .\windows\run_native.ps1 test
   ```

4. Build and validate the self-contained package:

   ```powershell
   .\windows\build_native.ps1 -FullSmoke
   ```

5. Confirm that the build succeeds and produces:

   ```text
   windows/native/dist/SpatialScope-Windows-x64-Setup.exe
   windows/native/dist/SHA256SUMS-Windows.txt
   ```

6. Run the setup program, launch SpatialScope from its installed shortcut, and verify native input/output folder selection, the sidebar **Check for updates** action, Step 2 SVG and PNG generation, one representative complete workflow, exported files, reopening the output folder, and uninstalling from Windows Settings.
7. Push the release branch and wait for the Windows workflow to pass. Download the `SpatialScope-Windows-x64` workflow artifact and confirm that it contains only the setup executable and Windows checksum file. The workflow runs the updater contract tests, frozen renderer, Step 2, and the complete synthetic nine-stage analysis before packaging.

### Windows update channel contract

The native updater queries `https://api.github.com/repos/fengshuoliu/SpatialScope/releases?per_page=100`; it never uses the repository-wide `/releases/latest` route because that route can point to either platform. A Windows update is eligible only when it is a published, non-prerelease `windows-v<version>` release newer than the installed version and contains exactly one uploaded, nonempty asset with each stable name:

```text
SpatialScope-Windows-x64-Setup.exe
SHA256SUMS-Windows.txt
```

Before installation, the updater requires the exact GitHub download path, a valid GitHub `sha256:` asset digest, the declared asset size, and a matching exact filename entry in `SHA256SUMS-Windows.txt`. Do not rename these assets, attach duplicates, omit the checksum, or publish the release before both uploads finish. Existing 1.2.4 and earlier installations need one manual installation of 1.2.5; 1.2.5 and later check this channel automatically once per day.

## Generate the Sparkle feed for macOS

Do this only for a macOS release. Create a temporary directory containing only the release ZIP and a Markdown file with the same base name for release notes. Then run Sparkle's `generate_appcast` tool from Xcode's resolved package artifacts:

```bash
SPARKLE_BIN="build/DerivedData-Release/SourcePackages/artifacts/sparkle/Sparkle/bin"
MAC_VERSION="1.2.1"
RELEASE_DIR="build/release/v${MAC_VERSION}"
APPCAST_WORK="$(mktemp -d /tmp/spatialscope-appcast.XXXXXX)"

ditto --noextattr --norsrc \
  "$RELEASE_DIR/SpatialScope-macOS-universal.zip" \
  "$APPCAST_WORK/SpatialScope-macOS-universal.zip"
ditto --noextattr --norsrc CHANGELOG.md \
  "$APPCAST_WORK/SpatialScope-macOS-universal.md"

"$SPARKLE_BIN/generate_appcast" \
  --download-url-prefix "https://github.com/fengshuoliu/SpatialScope/releases/download/v${MAC_VERSION}/" \
  --link "https://fengshuoliu.github.io/SpatialScope/" \
  --embed-release-notes \
  -o "$APPCAST_WORK/appcast.xml" \
  "$APPCAST_WORK"

ditto --noextattr --norsrc "$APPCAST_WORK/appcast.xml" docs/appcast.xml
```

Change `MAC_VERSION` for the new macOS release. Approve Keychain access when macOS asks. Never use `generate_keys` again unless intentionally rotating the update key; replacing the key would prevent existing installations from trusting new releases.

Inspect `docs/appcast.xml` before publishing. It must contain the new version, build number, GitHub asset URL, minimum macOS version, file length, and `sparkle:edSignature`.

## Publish a macOS release

1. Commit and push the macOS version, changelog, source changes, and release documentation.
2. Create a GitHub release tagged `v<version>` and make it the Latest release.
3. Upload the DMG, ZIP, and `SHA256SUMS.txt` from `build/release/v<version>/`.
4. Publish the release and verify that the DMG, ZIP, and automatic macOS download route work successfully.
5. Commit and push `docs/appcast.xml` only after the release assets are live.
6. Wait for GitHub Pages to deploy, then open:

   ```text
   https://fengshuoliu.github.io/SpatialScope/appcast.xml
   ```

7. In the previous public macOS version, select **SpatialScope > Check for Updates...** and complete the update.

## Publish a Windows release

1. Commit and push the Windows version, changelog, source changes, and release documentation.
2. Create the tag `windows-v<version>` from the tested commit.
3. Create a normal GitHub release named `SpatialScope <version> for Windows`. Upload `SpatialScope-Windows-x64-Setup.exe` and `SHA256SUMS-Windows.txt`.
4. Set the GitHub Releases API field `make_latest` deliberately. Windows 1.2.6 is published as the repository's Latest release; the platform-aware download routes keep both platforms available independently.
5. Publish the Windows release and verify the pinned setup URL, automatic Windows download route, GitHub asset digest, checksum, installation, application launch, manual in-app update check, and uninstall on Windows 10 or 11.
6. Confirm that `https://github.com/fengshuoliu/SpatialScope/releases/latest` resolves to the intended repository-wide release and that both platform-aware download routes select the newest published release for their platform.
7. Do not regenerate or commit `docs/appcast.xml` for this Windows-only release.
8. Keep public Windows download links pointed at `download/windows/`. Do not use `/releases/latest/download/` for a platform-specific asset because GitHub has only one repository-wide Latest release.

Keep the existing macOS release notes, `v1.2.1` tag, assets, Xcode version, and Sparkle appcast unchanged when publishing Windows 1.2.6.

## Rollback

For a defective macOS release, remove its item from `docs/appcast.xml` and push that change immediately so Sparkle stops offering it. Keep the GitHub release available long enough for investigation unless it presents a security or data-loss risk. Publish a corrected release with a higher build number; never reuse a build number that users may already have installed.

For a defective Windows release, remove its public download links, mark the release as a prerelease or draft while investigating when appropriate, and publish a corrected `windows-v<version>` release. Never replace a previously downloaded installer with different bytes under the same filename and tag.
