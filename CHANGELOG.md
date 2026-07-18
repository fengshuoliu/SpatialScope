# Changelog

All notable changes to SpatialScope are documented here.

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
