from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image


WINDOWS_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = WINDOWS_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from cell_distribution_export import NativePipelineConfig, run_cell_density_analysis
from native_engine import (
    _distribution_config_for_output,
    _distribution_restore_preview_paths,
    _distribution_result_preview_contract,
)


def _assert_windows_preview_invalidation_contract() -> list[str]:
    app_dir = WINDOWS_DIR / "native" / "src" / "SpatialScope.App"
    main_source = (app_dir / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    region_source = (app_dir / "MainWindow.Region.cs").read_text(encoding="utf-8")
    distribution_source = (app_dir / "MainWindow.Distribution.cs").read_text(encoding="utf-8")

    distribution_entry = re.search(
        r'\["distribution"\]\s*=\s*\[(?P<keys>.*?)\]\s*,',
        main_source,
        flags=re.DOTALL,
    )
    if distribution_entry is None:
        raise AssertionError("The Windows workflow preview catalog has no distribution entry.")

    required_keys = ["distribution", "distributionBandMap", "distributionDensity"]
    catalog_keys = distribution_entry.group("keys")
    missing_keys = [key for key in required_keys if f'"{key}"' not in catalog_keys]
    if missing_keys:
        raise AssertionError(
            "The Windows distribution preview invalidation catalog is incomplete: "
            f"{missing_keys}"
        )

    required_main_contracts = [
        "private void RemoveWorkflowPreviews(string sectionKey)",
        "_previewPaths.Remove(key);\n            HideTaggedDetailElement($\"preview:{key}\");",
        "RemoveWorkflowPreviews(section.Key);",
        'RemoveWorkflowPreviews("distribution");',
        "if (_sections[index].Status == WorkflowStatus.Complete) _sections[index].Status = WorkflowStatus.Ready;",
        "refreshInvalidatedViewAfterFailure = completesSection;",
        "if (refreshInvalidatedViewAfterFailure) RefreshSectionViewIfSelected(sectionKey);",
    ]
    for contract in required_main_contracts:
        if contract not in main_source:
            raise AssertionError(f"Missing Windows preview invalidation contract: {contract}")

    workflow_start = main_source.index("private async Task<JsonElement?> RunWorkflowAsync")
    workflow_end = main_source.index("\n    private void MarkNextReady", workflow_start)
    workflow_body = main_source[workflow_start:workflow_end]
    ordered_failure_contract = [
        "catch (Exception exception)",
        "refreshInvalidatedViewAfterFailure = completesSection;",
        "finally",
        "SetInteractionBusy(false);",
        "if (refreshInvalidatedViewAfterFailure) RefreshSectionViewIfSelected(sectionKey);",
    ]
    positions = [workflow_body.find(contract) for contract in ordered_failure_contract]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise AssertionError(
            "The workflow failure path must refresh invalidated controls after interaction is restored."
        )

    if 'RemoveWorkflowPreviews("distribution");' not in region_source:
        raise AssertionError("A saved Region catalog change does not clear distribution previews.")
    if '_previewPaths.Remove("distribution' in main_source + region_source + distribution_source:
        raise AssertionError("Distribution preview cleanup bypasses the centralized workflow catalog.")

    for preview_key in ("distributionBandMap", "distributionDensity"):
        if f'previewKey: "{preview_key}"' not in distribution_source:
            raise AssertionError(f"The {preview_key} panel is not tagged for immediate invalidation.")
    return required_keys


def _region_band_fixture(output_folder: Path) -> dict[str, Any]:
    height = 48
    width = 48
    inside_band_index = np.full((height, width), -1, dtype=np.int16)
    outside_band_index = np.full((height, width), -1, dtype=np.int16)
    inside_band_index[:, :24] = ((23 - np.arange(24)) // 6)[None, :]
    outside_band_index[:, 24:] = ((np.arange(24, 48) - 24) // 6)[None, :]

    region_dir = output_folder / "10_cell_distribution_analysis" / "01_region_masks"
    region_dir.mkdir(parents=True)
    arrays_path = region_dir / "region_bands__Smoke_ROI__5um__arrays.npz"
    inputs_path = region_dir / "region_bands__Smoke_ROI__5um__inputs.json"
    band_map_path = region_dir / "region_bands__Smoke_ROI__5um__band_map.png"
    np.savez_compressed(
        arrays_path,
        inside_band_index=inside_band_index,
        outside_band_index=outside_band_index,
    )
    inputs_path.write_text(
        json.dumps(
            {
                "boundary_label": "Smoke ROI",
                "band_width_um": 5.0,
                "pixel_size_um": [1.0, 1.0],
            }
        ),
        encoding="utf-8",
    )
    Image.new("RGB", (width, height), color=(32, 80, 92)).save(band_map_path)
    return {
        "boundary_label": "Smoke ROI",
        "band_width_um": 5.0,
        "saved_paths": {
            "arrays_npz": str(arrays_path),
            "inputs_json": str(inputs_path),
            "png": str(band_map_path),
        },
    }


def run_smoke() -> dict[str, Any]:
    invalidation_keys = _assert_windows_preview_invalidation_contract()
    with tempfile.TemporaryDirectory(prefix="spatialscope-distribution-preview-") as temp_dir:
        output_folder = Path(temp_dir)
        config_dir = output_folder / "00_config"
        config_dir.mkdir(parents=True)
        (config_dir / "pipeline_config.json").write_text(
            json.dumps(
                {
                    "folder": str(output_folder),
                    "save_dir": str(output_folder / "obsolete-original-location"),
                    "pixel_size_um": [1.0, 1.0],
                    "image_id": "Distribution preview smoke",
                    "channels": [],
                    "overlay_channels": [],
                    "white_channel": None,
                    "white_weight": 0.0,
                }
            ),
            encoding="utf-8",
        )
        relocated_config = _distribution_config_for_output(output_folder)
        if relocated_config.save_dir != output_folder.resolve():
            raise AssertionError(
                "A restored/copied project retained its obsolete serialized output root: "
                f"{relocated_config.save_dir}"
            )
        region_result = _region_band_fixture(output_folder)
        config = NativePipelineConfig(
            folder=output_folder,
            save_dir=output_folder,
            pixel_size_um=(1.0, 1.0),
            image_id="Distribution preview smoke",
            channels=[],
            overlay_channels=[],
            white_channel=None,
            white_weight=0.0,
        )
        assignment_df = pd.DataFrame(
            [
                {"label": 1, "celltype": "Type A", "centroid_x_px": 22.0, "centroid_y_px": 8.0},
                {"label": 2, "celltype": "Type A", "centroid_x_px": 15.0, "centroid_y_px": 16.0},
                {"label": 3, "celltype": "Type A", "centroid_x_px": 27.0, "centroid_y_px": 24.0},
                {"label": 4, "celltype": "Type B", "centroid_x_px": 9.0, "centroid_y_px": 32.0},
                {"label": 5, "celltype": "Type B", "centroid_x_px": 34.0, "centroid_y_px": 40.0},
            ]
        )
        celltype_config = [
            {"name": "Type A", "color_hex": "#2F9ED8"},
            {"name": "Type B", "color_hex": "#E83E75"},
        ]
        density_result = run_cell_density_analysis(
            config=config,
            assignment_df=assignment_df,
            celltype_cfg=celltype_config,
            region_masks_result=region_result,
            selected_celltypes=["Type A", "Type B"],
        )

        contract = _distribution_result_preview_contract(region_result, density_result)
        band_map_path = Path(str(contract["bandMapPreviewPath"]))
        density_plot_path = Path(str(contract["densityPlotPreviewPath"]))
        if Path(str(contract["previewPath"])) != band_map_path:
            raise AssertionError("The backward-compatible distribution preview must remain the band map.")
        if not band_map_path.is_file() or not density_plot_path.is_file():
            raise AssertionError(f"The distribution preview contract contains a missing file: {contract}")

        with Image.open(density_plot_path) as image:
            plot_width, plot_height = image.size
            if plot_width <= plot_height or plot_width < 600 or plot_height < 250:
                raise AssertionError(f"The density line plot has unexpected dimensions: {image.size}")

        long_csv_path = Path(density_result["saved_paths"]["csv_band_long"])
        density_rows = pd.read_csv(long_csv_path)
        plotted_types = set(density_rows["celltype"].astype(str))
        if plotted_types != {"Type A", "Type B"}:
            raise AssertionError(f"The plotted cell types do not match the selection: {plotted_types}")
        if int((density_rows["cell_count"] > 0).sum()) < 4:
            raise AssertionError("The density plot was not backed by the fixture's real cell counts.")

        restored = _distribution_restore_preview_paths(
            output_folder / "10_cell_distribution_analysis"
        )
        if restored.get("distribution") != band_map_path:
            raise AssertionError(f"Restore did not select the boundary band map: {restored}")
        if restored.get("distributionBandMap") != band_map_path:
            raise AssertionError(f"Restore did not expose the named boundary map: {restored}")
        if restored.get("distributionDensity") != density_plot_path:
            raise AssertionError(f"Restore did not expose the density line plot: {restored}")

        return {
            "status": "passed",
            "selectedCellTypes": sorted(plotted_types),
            "positiveMetricRows": int((density_rows["cell_count"] > 0).sum()),
            "plotSize": [plot_width, plot_height],
            "previewKeys": sorted(restored),
            "windowsInvalidationKeys": invalidation_keys,
            "windowsFailureRefreshContract": True,
        }


def main() -> int:
    print(json.dumps(run_smoke(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
