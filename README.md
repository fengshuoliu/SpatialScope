# SpatialScope

SpatialScope is a desktop application for spatial image analysis from aligned, per-marker numeric CSV matrices. It provides a guided workflow for composite visualization, nuclei segmentation, rule-based cell typing, neighborhood analysis, regions, cell distribution, distance analysis, and publication-ready exports on macOS and Windows.

> **Project history:** SpatialScope is the renamed and substantially redesigned successor to the published [TME Spatial prototype](https://github.com/fengshuoliu/TME_spatial). The original repository and [legacy website](https://fengshuoliu.github.io/TME_spatial/) remain online so links in articles continue to work.

## Download

[Download SpatialScope for macOS](https://github.com/fengshuoliu/SpatialScope/releases/latest/download/SpatialScope-macOS-universal.dmg)

[Download SpatialScope for Windows](https://github.com/fengshuoliu/SpatialScope/releases/latest/download/SpatialScope-Windows-x64-Setup-1.2.0.exe)

The current release supports macOS 13 or later on Apple Silicon and Intel Macs, plus 64-bit Windows 10 and 11. These independently distributed builds are not notarized by Apple or signed with a commercial Windows certificate, so follow the one-time approval steps in the [installation guide](docs/INSTALLATION.md).

## Documentation

- [SpatialScope User Manual](docs/SpatialScope_User_Manual.md)
- [SpatialScope and QuPath: Functional Comparison](docs/SpatialScope_vs_QuPath.md)
- [Installation and update instructions](docs/INSTALLATION.md)
- [Maintainer release guide](docs/RELEASING.md)
- [Project website](https://fengshuoliu.github.io/SpatialScope/)

## Platform roadmap

| Platform | Status | Repository location |
| --- | --- | --- |
| macOS | Available in version 1.2 | Current Xcode project |
| Windows x64 | Available in version 1.2 | [`windows/`](windows/) |

Both platforms use the same `SpatialScope` product identity, release history, analysis definitions, and output contracts.

The language selector follows the operating system by default and can be set explicitly to English or Simplified Chinese from the sidebar. This changes UI text only; analysis methods, exported data, filenames, schemas, and the `SpatialScope` product name remain unchanged.

## Build from source

macOS requirements:

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

Windows release builds are produced on a Windows x64 host:

```powershell
./windows/build_release.ps1
```

The Windows build freezes the scientific Python runtime, runs a deterministic end-to-end analysis smoke test, and creates both an NSIS installer and portable executable under `windows/desktop/dist/`. End users do not need Python or Node.js.

## Updates

On macOS, SpatialScope uses [Sparkle](https://sparkle-project.org/) with EdDSA-signed archives. On Windows, the NSIS installation checks GitHub Releases through `electron-updater`; portable builds require manual replacement. Release binaries are hosted by GitHub Releases.

## Citation

If SpatialScope supports your work, please cite:

> Xu Z, Liu F, Ding Y, et al. Unbiased niche labeling maps immune-excluded niche in bone metastasis. *Cell*. 2026. [https://doi.org/10.1016/j.cell.2026.04.009](https://doi.org/10.1016/j.cell.2026.04.009)

Machine-readable citation metadata and the complete author list are provided in [`CITATION.cff`](CITATION.cff). The original TME Spatial repository remains the historical software artifact associated with earlier publications; cite the repository and article version that match the software used in your analysis.

## License

SpatialScope is released under the [MIT License](LICENSE).
