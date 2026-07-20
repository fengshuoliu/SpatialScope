from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image


WINDOWS_DIR = Path(__file__).resolve().parents[1]
BACKEND_SRC = WINDOWS_DIR / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from spatialscope_analysis.models import RegionParams
from spatialscope_analysis.region_analysis import run_region_boundary_analysis


REGISTRY_NAME = "boundary_mask_registry.json"
COMPUTATIONAL_SOURCE = "computational_roi_identification"


def _load_registry(output_folder: Path) -> list[dict[str, Any]]:
    payload = json.loads((output_folder / REGISTRY_NAME).read_text(encoding="utf-8"))
    entries = payload.get("entries", []) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise AssertionError("The Region registry does not contain an entry list.")
    return [entry for entry in entries if isinstance(entry, dict)]


def _save_test_mask(path: Path, row_slice: slice, column_slice: slice) -> None:
    mask = np.zeros((24, 24), dtype=np.uint8)
    mask[row_slice, column_slice] = 255
    Image.fromarray(mask).save(path, format="TIFF")


def _run_analysis(
    output_folder: Path,
    df_cells: pd.DataFrame,
    celltype_mask: np.ndarray,
    celltype_config: list[dict[str, str]],
    selected_types: list[str],
) -> None:
    result = run_region_boundary_analysis(
        df_cells=df_cells,
        celltype_mask=celltype_mask,
        celltype_cfg=celltype_config,
        save_dir=output_folder,
        pixel_size_um=(1.0, 1.0),
        params=RegionParams(
            selected_types=selected_types,
            close_um=0,
            dilate_um=0,
            min_area_um2=0,
            min_cells=1,
            contour_downsample=1,
        ),
        save_outputs=True,
    )
    plt.close(result["figure"])


def run_smoke() -> dict[str, Any]:
    celltype_mask = np.zeros((24, 24), dtype=np.uint16)
    celltype_mask[3:10, 3:10] = 1
    celltype_mask[14:21, 14:21] = 2
    celltype_config = [
        {"name": "Type A", "color_hex": "#3366CC"},
        {"name": "Type B", "color_hex": "#DC3912"},
    ]
    df_cells = pd.DataFrame(
        [
            {"label": 1, "celltype": "Type A", "centroid_x_px": 6.0, "centroid_y_px": 6.0},
            {"label": 2, "celltype": "Type B", "centroid_x_px": 17.0, "centroid_y_px": 17.0},
        ]
    )

    with tempfile.TemporaryDirectory(prefix="spatialscope-region-registry-") as temp_dir:
        output_folder = Path(temp_dir)
        _run_analysis(output_folder, df_cells, celltype_mask, celltype_config, ["Type A", "Type B"])

        initial_entries = _load_registry(output_folder)
        initial_computational = [
            entry for entry in initial_entries if entry.get("source") == COMPUTATIONAL_SOURCE
        ]
        if {entry.get("mask_key") for entry in initial_computational} != {"Type A", "Type B"}:
            raise AssertionError(f"Unexpected initial computational entries: {initial_computational}")

        manual_path = output_folder / "manual_keep_region_mask_uint8.tiff"
        adjusted_path = output_folder / "adjusted_keep_region_mask_uint8.tiff"
        _save_test_mask(manual_path, slice(1, 5), slice(15, 19))
        _save_test_mask(adjusted_path, slice(8, 13), slice(8, 13))

        manual_entry = {
            "mask_path": manual_path.name,
            "display_name": "Manual ROI to preserve",
            "source": "manual_roi_selection",
            "group_name": "manual_keep",
            "mask_key": "manual_keep",
            "id": "manual-preserved-id",
            "source_type": "polygon",
            "seed_cell_types": ["Type A"],
        }
        adjusted_entry = {
            "mask_path": adjusted_path.name,
            "display_name": "Adjusted ROI to preserve",
            "source": "manual_boundary_adjustment",
            "group_name": "adjusted_keep",
            "mask_key": "adjusted_keep",
            "id": "adjusted-preserved-id",
            "source_type": "boundary_edit",
            "seed_cell_types": ["Type B"],
        }
        registry_path = output_folder / REGISTRY_NAME
        registry_path.write_text(
            json.dumps({"entries": [*initial_entries, manual_entry, adjusted_entry]}, indent=2),
            encoding="utf-8",
        )

        _run_analysis(output_folder, df_cells, celltype_mask, celltype_config, ["Type B"])
        final_entries = _load_registry(output_folder)

        computational_entries = [
            entry for entry in final_entries if entry.get("source") == COMPUTATIONAL_SOURCE
        ]
        if len(computational_entries) != 1 or computational_entries[0].get("mask_key") != "Type B":
            raise AssertionError(
                "A computational rerun must replace every stale computational entry; "
                f"found {computational_entries}"
            )

        preserved_by_source = {
            str(entry.get("source")): entry
            for entry in final_entries
            if entry.get("source") in {"manual_roi_selection", "manual_boundary_adjustment"}
        }
        if preserved_by_source.get("manual_roi_selection") != manual_entry:
            raise AssertionError("The manual ROI registry entry changed during the computational rerun.")
        if preserved_by_source.get("manual_boundary_adjustment") != adjusted_entry:
            raise AssertionError("The adjusted ROI registry entry changed during the computational rerun.")
        if not manual_path.is_file() or not adjusted_path.is_file():
            raise AssertionError("A preserved manual or adjusted ROI mask file was removed.")

        return {
            "status": "passed",
            "initialComputationalMaskKeys": sorted(
                str(entry.get("mask_key")) for entry in initial_computational
            ),
            "finalComputationalMaskKeys": [str(computational_entries[0].get("mask_key"))],
            "preservedSources": sorted(preserved_by_source),
            "finalRegistryEntryCount": len(final_entries),
        }


def main() -> int:
    print(json.dumps(run_smoke(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
