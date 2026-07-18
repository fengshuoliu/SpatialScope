# Release SpatialScope Through GitHub

This project distributes ad-hoc-signed macOS builds through GitHub Releases and
uses Sparkle for in-app updates. An Apple Developer Program membership is not
required for this workflow. Users must approve the app once through macOS
Privacy & Security because the build is not Apple-notarized.

## One-time setup

1. Keep the Sparkle private EdDSA key in the macOS login Keychain. Do not commit,
   export, or share it. The matching public key is stored as `SUPublicEDKey` in
   `SpatialScope/Info.plist`.
2. Enable GitHub Pages for the repository with `main` and `/docs` as the source.
3. Confirm that the feed URL in `SpatialScope/Info.plist` is:

   ```text
   https://fengshuoliu.github.io/SpatialScope/appcast.xml
   ```

4. Keep the GitHub release asset names stable:

   ```text
   SpatialScope-macOS-universal.dmg
   SpatialScope-macOS-universal.zip
   SHA256SUMS.txt
   ```

## Prepare a release

1. In Xcode, increase both the marketing version and build number. For example,
   release 1.1 can use marketing version `1.1` and build number `2`. Every Sparkle
   release must have a build number greater than the previous release.
2. Add the release date and user-facing changes to `CHANGELOG.md`.
3. Build the self-contained universal artifacts:

   ```bash
   ./script/package_release.sh
   ```

4. Confirm that the command succeeds. The DMG, ZIP, and checksum file will be
   written to `build/release/v<version>/`.
5. Test the DMG on a second Mac or a clean macOS user account. Verify first-launch
   approval, one representative analysis, export creation, and the Check for
   Updates menu item.

## Generate the Sparkle feed

Create a temporary directory containing only the release ZIP and a Markdown file
with the same base name for release notes. Then run Sparkle's `generate_appcast`
tool from Xcode's resolved package artifacts:

```bash
SPARKLE_BIN="build/DerivedData-Release/SourcePackages/artifacts/sparkle/Sparkle/bin"
RELEASE_DIR="build/release/v1.1"
APPCAST_WORK="$(mktemp -d /tmp/spatialscope-appcast.XXXXXX)"

ditto --noextattr --norsrc \
  "$RELEASE_DIR/SpatialScope-macOS-universal.zip" \
  "$APPCAST_WORK/SpatialScope-macOS-universal.zip"
ditto --noextattr --norsrc CHANGELOG.md \
  "$APPCAST_WORK/SpatialScope-macOS-universal.md"

"$SPARKLE_BIN/generate_appcast" \
  --download-url-prefix "https://github.com/fengshuoliu/SpatialScope/releases/download/v1.1/" \
  --link "https://fengshuoliu.github.io/SpatialScope/" \
  --embed-release-notes \
  -o "$APPCAST_WORK/appcast.xml" \
  "$APPCAST_WORK"

ditto --noextattr --norsrc "$APPCAST_WORK/appcast.xml" docs/appcast.xml
```

Replace `1.1` with the new version in both places. Approve Keychain access when
macOS asks. Never use `generate_keys` again unless intentionally rotating the
update key; replacing the key would prevent existing installations from trusting
new releases.

Inspect `docs/appcast.xml` before publishing. It must contain the new version,
build number, GitHub asset URL, minimum macOS version, file length, and
`sparkle:edSignature`.

## Publish on GitHub

1. Commit and push the version, changelog, and source changes.
2. Create a GitHub release tagged `v<version>` and upload all three files from
   `build/release/v<version>/`.
3. Publish the release and verify that the DMG and ZIP download successfully.
4. Commit and push `docs/appcast.xml` only after the release assets are live.
5. Wait for GitHub Pages to deploy, then open:

   ```text
   https://fengshuoliu.github.io/SpatialScope/appcast.xml
   ```

6. In the previous public version of SpatialScope, select **SpatialScope > Check
   for Updates...** and complete the update.

## Rollback

If a release is defective, remove its item from `docs/appcast.xml` and push that
change immediately so Sparkle stops offering it. Keep the GitHub release available
long enough for investigation unless it presents a security or data-loss risk.
Publish a corrected release with a higher build number; never reuse a build number
that users may already have installed.
