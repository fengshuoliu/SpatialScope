# Changelog

All notable changes to SpatialScope are documented here.

## 1.2.4 - 2026-07-20 (Windows)

- Made Distance Analysis mode-specific: nearest-neighbor keeps its Target selector, while cell-to-boundary runs directly from a boundary and one or more compact query-cell selections.
- Added optimizer switches that keep nuclei minimum/maximum diameters or assignment Voronoi/buffer radii fixed while other parameters are screened; nuclei diameter locks default on and assignment locks default off.
- Separated the Neighborhood field map from its numbered cluster color key and added a CSV mapping cluster numbers to cluster labels, counts, fractions, and colors.
- Replaced opaque hashes in user-facing Region and analysis filenames with stable, readable stage and Region names while retaining legacy restore compatibility.
- Unified section typography and spacing across the native workflow, preserved multi-marker rule pickers with Nucleus selected by default for every all-positive rule, and expanded release smoke coverage.
- Kept the macOS application, Sparkle appcast, v1.2.1 release, and stable platform-specific download routes unchanged.

## 1.2.3 - 2026-07-20 (Windows)

- Rebuilt Region Analysis around a saved ROI catalog, explicit computational and manual workflows, adjustable display styling, and custom exports.
- Added a high-contrast white drawing cursor and boundary feedback for polygon and free-draw Region editing.
- Kept scientific previews fit to the complete image field and reserved Ctrl+wheel for deliberate zoom, preventing ordinary page scrolling from cropping plots.
- Split Cell Distribution into boundary-banded regions and cell density by boundary distance, with persistent multi-cell-type selection and the Apple-style density line plot.
- Preserved distribution artifacts inside the output folder selected during restore, including when a saved project is copied or moved.
- Expanded release gates for Region filtering/registry behavior, distribution preview/restore contracts, frozen SVG rendering, and the complete nine-stage workflow.

## 1.2.2 - 2026-07-20 (Windows)

- Reorganized the native Windows interface with a consistent typography scale, clearer section hierarchy, and more readable control spacing.
- Improved cell-type rule editing with persistent multi-marker selection, clearer marker choices, and the segmented nucleus selected by default for each cell type's all-positive rule.
- Improved automatic nucleus-channel selection for datasets that use nuclear marker names such as `Ir191_nuclei` instead of DAPI.
- Matched the macOS cell-assignment parameter ranges, Local threshold option, and bounded fast-filter execution so saved Apple-compatible settings remain editable and practical to optimize on Windows.
- Preserved and restored completed downstream analysis settings, including neighborhood, region, distribution, and both distance-analysis modes.
- Expanded Windows workflow validation with packaged-engine, restore-state, and real-dataset coverage while leaving the macOS application unchanged.

## 1.2.1 - 2026-07-19 (Windows)

- Replaced the legacy Electron/Streamlit Windows shell with a native WPF/.NET desktop application and a private frozen analysis engine.
- Distributed Windows as a self-contained setup executable; users do not need a browser, Node.js, Python, or the .NET SDK.
- Fixed packaged Step 2 SVG generation by explicitly bundling and testing the Matplotlib Agg and SVG backends.
- Added native input and output folder selection, bilingual interface text, live app CPU usage, parameter guidance, and sequential workflow status colors.
- Verified the packaged engine with Step 2 and complete nine-stage smoke tests.
- Replaced the earlier Electron-based Windows package with the native 1.2.1 installer.

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
