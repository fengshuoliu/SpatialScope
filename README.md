# SpatialScope

SpatialScope is a native macOS application for spatial image analysis from aligned, per-marker numeric CSV matrices. It provides a guided workflow for composite visualization, nuclei segmentation, rule-based cell typing, neighborhood analysis, regions, cell distribution, distance analysis, and publication-ready exports.

> **Project history:** SpatialScope is the renamed and substantially redesigned successor to the published [TME Spatial prototype](https://github.com/fengshuoliu/TME_spatial). The original repository and [legacy website](https://fengshuoliu.github.io/TME_spatial/) remain online so links in articles continue to work.

## Download

[Download SpatialScope for macOS](https://github.com/fengshuoliu/SpatialScope/releases/latest/download/SpatialScope-macOS-universal.dmg)

The current release supports macOS 13 or later on Apple Silicon and Intel Macs. Because this independently distributed build is not notarized by Apple, follow the one-time Gatekeeper instructions in the [installation guide](docs/INSTALLATION.md).

## Documentation

- [SpatialScope User Manual](docs/SpatialScope_User_Manual.md)
- [SpatialScope and QuPath: Functional Comparison](docs/SpatialScope_vs_QuPath.md)
- [Installation and update instructions](docs/INSTALLATION.md)
- [Maintainer release guide](docs/RELEASING.md)
- [Project website](https://fengshuoliu.github.io/SpatialScope/)

## Platform roadmap

| Platform | Status | Repository location |
| --- | --- | --- |
| macOS | Available in version 1.1 | Current Xcode project |
| Windows | Planned | [`windows/`](windows/) |

Windows will use the same `SpatialScope` product identity and release history.

The macOS interface follows the system language by default and can be set explicitly to English or Simplified Chinese from the sidebar. This changes UI text only; analysis methods, exported data, filenames, schemas, and the `SpatialScope` product name remain unchanged.

## Build from source

Requirements:

- macOS 13 or later
- Xcode with the macOS SDK
- Python 3.9 only when rebuilding the bundled Cell Distribution helper

Build and run a development copy:

```bash
./script/build_and_run.sh
```

Build the self-contained universal release artifacts:

```bash
./script/package_release.sh
```

The package script creates ad-hoc-signed DMG and ZIP files under `build/release/`. It bundles architecture-specific Cell Distribution helpers so end users do not need Python, Conda, Streamlit, or the legacy repository.

## Updates

SpatialScope uses [Sparkle](https://sparkle-project.org/) with EdDSA-signed archives. Users can select **SpatialScope > Check for Updates...**. Release binaries are hosted by GitHub Releases and update metadata is hosted by GitHub Pages.

## Citation

If SpatialScope supports your work, please cite:

> Xu Z, Liu F, Ding Y, et al. Unbiased niche labeling maps immune-excluded niche in bone metastasis. *Cell*. 2026. [https://doi.org/10.1016/j.cell.2026.04.009](https://doi.org/10.1016/j.cell.2026.04.009)

Machine-readable citation metadata and the complete author list are provided in [`CITATION.cff`](CITATION.cff). The original TME Spatial repository remains the historical software artifact associated with earlier publications; cite the repository and article version that match the software used in your analysis.

## License

SpatialScope is released under the [MIT License](LICENSE).
