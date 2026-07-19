# Release SpatialScope Through GitHub

SpatialScope distributes ad-hoc-signed macOS builds and unsigned Windows x64 builds through GitHub Releases. The platforms release independently: macOS 1.2.1 uses Sparkle for updates, while Windows 2.0.0 is a native WPF application distributed as a manually updated portable ZIP. Apple Developer Program and commercial Windows code-signing memberships are not required. Users approve the app once through macOS Privacy & Security or Windows SmartScreen.

## Platform versions and tags

- macOS uses `v<version>` tags, the Xcode marketing version and build number, and the Sparkle feed in `docs/appcast.xml`.
- Windows uses `windows-v<version>` tags and the `<Version>` value in `windows/native/src/SpatialScope.App/SpatialScope.App.csproj`.
- A Windows-only release must not change the Xcode version, macOS artifacts, or Sparkle appcast.
- A Windows-only release must set GitHub's `make_latest` value to `false`, so the current macOS release remains the repository's Latest release.

For Windows 2.0.0, use the tag `windows-v2.0.0`. Its stable download URL is:

```text
https://github.com/fengshuoliu/SpatialScope/releases/download/windows-v2.0.0/SpatialScope-Windows-x64-Portable-2.0.0.zip
```

The macOS download URL continues to use GitHub's Latest release:

```text
https://github.com/fengshuoliu/SpatialScope/releases/latest/download/SpatialScope-macOS-universal.dmg
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
   SpatialScope-Windows-x64-Portable-<version>.zip
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
   windows/native/dist/SpatialScope-Windows-x64-Portable-<version>.zip
   windows/native/dist/SHA256SUMS-Windows.txt
   ```

6. Extract the ZIP into a new folder and run `SpatialScope.exe`. Verify native input/output folder selection, Step 2 SVG and PNG generation, one representative complete workflow, exported files, and reopening the output folder.
7. Push the release branch and wait for the Windows workflow to pass. Download the `SpatialScope-Windows-x64` workflow artifact and confirm that it contains only the portable ZIP and Windows checksum file. The workflow runs the frozen renderer, Step 2, and the complete synthetic nine-stage analysis before packaging.

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
4. Publish the release and verify that the DMG and ZIP download successfully.
5. Commit and push `docs/appcast.xml` only after the release assets are live.
6. Wait for GitHub Pages to deploy, then open:

   ```text
   https://fengshuoliu.github.io/SpatialScope/appcast.xml
   ```

7. In the previous public macOS version, select **SpatialScope > Check for Updates...** and complete the update.

## Publish a Windows release

1. Commit and push the Windows version, changelog, source changes, and release documentation.
2. Create the tag `windows-v<version>` from the tested commit.
3. Create a normal GitHub release named `SpatialScope <version> for Windows`. Upload the portable ZIP and `SHA256SUMS-Windows.txt`.
4. Set the GitHub Releases API field `make_latest` to `false` when creating or publishing the release. Do not mark the release as a prerelease solely to preserve the macOS Latest designation.
5. Publish the Windows release and verify the pinned ZIP URL, archive extraction, checksum, and launch on Windows 10 or 11.
6. Confirm that `https://github.com/fengshuoliu/SpatialScope/releases/latest` still resolves to the current macOS release and that the macOS DMG latest-download URL still works.
7. Do not regenerate or commit `docs/appcast.xml` for this Windows-only release.
8. Update public Windows download links to the pinned `windows-v<version>` URL. Do not use `/releases/latest/download/` for a platform-specific Windows asset.

For the initial native Windows release, add a notice to the macOS 1.2.1 release notes stating that the Windows 1.2.0 NSIS and portable EXE assets are retired and linking to `windows-v2.0.0`. Preserve the macOS 1.2.1 tag and artifacts.

## Rollback

For a defective macOS release, remove its item from `docs/appcast.xml` and push that change immediately so Sparkle stops offering it. Keep the GitHub release available long enough for investigation unless it presents a security or data-loss risk. Publish a corrected release with a higher build number; never reuse a build number that users may already have installed.

For a defective Windows release, remove its public download links, mark the release as a prerelease or draft while investigating when appropriate, and publish a corrected `windows-v<version>` release. Never replace a previously downloaded ZIP with different bytes under the same filename and tag.
