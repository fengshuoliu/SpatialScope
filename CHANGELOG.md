# Changelog

All notable changes to SpatialScope are documented here.

## 2.0.0 - 2026-07-19 (Windows)

- Replaced the legacy Electron/Streamlit Windows shell with a native WPF/.NET desktop application and a private frozen analysis engine.
- Distributed Windows as a self-contained portable ZIP; users do not need an installer, browser, Node.js, Python, or the .NET SDK.
- Fixed packaged Step 2 SVG generation by explicitly bundling and testing the Matplotlib Agg and SVG backends.
- Added native input and output folder selection, bilingual interface text, live app CPU usage, parameter guidance, and sequential workflow status colors.
- Verified the packaged engine with Step 2 and complete nine-stage smoke tests.
- Retired the Windows 1.2.0 NSIS installer and portable executable. Use the native 2.0.0 portable ZIP instead.

## 1.2.1 - 2026-07-19 (macOS)

- Fixed the Simplified Chinese nuclei segmentation result message that could crash the app when a saved result was opened.
- Added a compact SpatialScope menu bar item for showing the app, checking for updates, and quitting.
- Kept the workflow sidebar anchored while switching between analysis sections.

## 1.2 - 2026-07-18

- Added the first Windows x64 release with both an NSIS installer and portable executable.
- Bundled the full scientific analysis runtime so Windows users do not need Python, Node.js, or the legacy project.
- Added GitHub-hosted automatic updates for installed Windows copies.
- Added native Windows folder pickers, application menus, system-language detection, and the bilingual `Language/语言` setting.
- Added a deterministic end-to-end smoke pipeline covering all nine stages, including computational and manual ROIs, distances, and Cell Distribution exports.
- Added Windows CI that freezes the backend, verifies the packaged runtime, builds both installers, and publishes checksums and update metadata as workflow artifacts.
- Improved narrow-window labels, section status styling, output completion state, and statistical figure readability.
- Added Windows installation, SmartScreen approval, update, and source-build documentation.

## 1.1 - 2026-07-18

- Added an in-app language selector that follows macOS by default, with explicit English and Simplified Chinese choices.
- Kept the language control label bilingual as `Language/语言` in every interface mode.
- Kept the `SpatialScope` product name unchanged in both interface languages.
- Kept analysis methods, exported data, filenames, schemas, and generated figure text language-independent from the UI preference.
- Fixed a launch-time crash caused by localized workflow status accessibility labels.
- Fixed Cell Distribution reloads so region-mask files always come from the same saved analysis run.
- Updated the project website and documentation for the new interface option.

## 1.0 - 2026-07-18

- First public release under the SpatialScope name.
- Native macOS workflow with nine analysis sections.
- Universal Apple Silicon and Intel application bundle.
- Self-contained Cell Distribution runtime.
- GitHub-hosted, EdDSA-verified application updates through Sparkle.
- User manual and QuPath comparison documentation.

## Historical prototype

Earlier Streamlit-based development is preserved in the [TME Spatial repository](https://github.com/fengshuoliu/TME_spatial).
