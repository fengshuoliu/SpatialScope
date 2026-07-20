from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


BACKEND_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from spatialscope_analysis.models import RegionParams  # noqa: E402
from spatialscope_analysis.region_analysis import (  # noqa: E402
    _load_boundary_registry,
    run_region_boundary_analysis,
    save_adjusted_region_analysis,
    save_manual_roi_analysis,
)


HASH_TOKEN_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{12,16}(?![0-9a-f])")


class _PreviewFigure:
    def savefig(self, path: Path, **_kwargs: object) -> None:
        Path(path).write_bytes(b"preview")


class RegionOutputFilenameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.celltype_mask = np.zeros((18, 18), dtype=np.uint16)
        self.celltype_mask[2:7, 2:7] = 1
        self.celltype_mask[7:12, 7:12] = 2
        self.celltype_mask[12:17, 12:17] = 3
        self.celltype_config = [
            {"name": "CD3 T cells", "color_hex": "#3366CC"},
            {"name": "CD8 T cells", "color_hex": "#DC3912"},
            {"name": "PanCK tumor cells", "color_hex": "#109618"},
        ]
        self.df_cells = pd.DataFrame(
            [
                {"label": 1, "celltype": "CD3 T cells", "centroid_x_px": 4.0, "centroid_y_px": 4.0},
                {"label": 2, "celltype": "CD8 T cells", "centroid_x_px": 9.0, "centroid_y_px": 9.0},
                {"label": 3, "celltype": "PanCK tumor cells", "centroid_x_px": 14.0, "centroid_y_px": 14.0},
            ]
        )

    def assert_public_paths_are_readable(self, result: dict[str, object]) -> None:
        saved_paths = result["saved_paths"]
        self.assertIsInstance(saved_paths, dict)
        for value in saved_paths.values():
            paths = value.values() if isinstance(value, dict) else [value]
            for raw_path in paths:
                basename = Path(raw_path).name
                self.assertNotRegex(basename, HASH_TOKEN_RE)

    @patch("spatialscope_analysis.region_analysis.make_region_overlay_figure", return_value=_PreviewFigure())
    def test_computational_outputs_use_stage_and_individual_cell_type_names(self, _figure: object) -> None:
        selected_types = [item["name"] for item in self.celltype_config]
        with tempfile.TemporaryDirectory(prefix="spatialscope-region-names-") as temp_value:
            result = run_region_boundary_analysis(
                df_cells=self.df_cells,
                celltype_mask=self.celltype_mask,
                celltype_cfg=self.celltype_config,
                save_dir=Path(temp_value),
                pixel_size_um=(1.0, 1.0),
                params=RegionParams(
                    selected_types=selected_types,
                    close_um=0.0,
                    dilate_um=0.0,
                    min_area_um2=0.0,
                    min_cells=1,
                ),
                save_outputs=True,
            )

            self.assert_public_paths_are_readable(result)
            self.assertEqual(
                Path(result["saved_paths"]["overlay_png"]).name,
                "celltypes_with_boundaries__computed_regions.png",
            )
            self.assertEqual(
                {
                    name: Path(path).name
                    for name, path in result["saved_paths"]["mask_paths"].items()
                },
                {
                    name: f"computed__{name}_region_mask_uint8.tiff"
                    for name in selected_types
                },
            )
            concatenated = "__".join(selected_types)
            for path in result["saved_paths"]["mask_paths"].values():
                self.assertNotIn(concatenated, Path(path).name)

    @patch("spatialscope_analysis.region_analysis.make_region_overlay_figure", return_value=_PreviewFigure())
    def test_adjusted_output_uses_display_label_but_keeps_hashed_registry_identity(self, _figure: object) -> None:
        mask_key = "manual_0123456789abcdef"
        display_name = "Tumor island (2)"
        adjusted_mask = np.zeros_like(self.celltype_mask, dtype=bool)
        adjusted_mask[3:15, 3:15] = True
        with tempfile.TemporaryDirectory(prefix="spatialscope-adjusted-names-") as temp_value:
            result = save_adjusted_region_analysis(
                df_cells=self.df_cells,
                celltype_mask=self.celltype_mask,
                celltype_cfg=self.celltype_config,
                save_dir=Path(temp_value),
                pixel_size_um=(1.0, 1.0),
                adjusted_masks={mask_key: adjusted_mask},
                selected_types=["PanCK tumor cells", "CD8 T cells"],
                edit_meta={"target_type": mask_key, "display_name": display_name},
                edited_boundary_types=[mask_key],
                boundary_display_names={mask_key: display_name},
                replace_registry_source=None,
                registry_entry_metadata={mask_key: {"id": "roi_0123456789abcdef"}},
                save_outputs=True,
            )

            self.assert_public_paths_are_readable(result)
            self.assertEqual(
                Path(result["saved_paths"]["mask_paths"][mask_key]).name,
                "adjusted__Tumor island (2)_region_mask_uint8.tiff",
            )
            self.assertEqual(
                Path(result["saved_paths"]["counts_csv"]).name,
                "celltype_counts_by_region__adjusted_region__Tumor island (2).csv",
            )
            [entry] = _load_boundary_registry(Path(temp_value))
            self.assertEqual(entry["id"], "roi_0123456789abcdef")
            self.assertEqual(entry["mask_key"], mask_key)
            self.assertEqual(entry["display_name"], display_name)
            self.assertEqual(entry["mask_path"], "adjusted__Tumor island (2)_region_mask_uint8.tiff")

    @patch("spatialscope_analysis.region_analysis.make_roi_comparison_figure", return_value=_PreviewFigure())
    def test_manual_duplicate_labels_keep_visible_parenthesized_suffixes(self, _figure: object) -> None:
        roi_label_mask = np.zeros_like(self.celltype_mask, dtype=np.uint16)
        roi_label_mask[1:6, 1:6] = 1
        roi_label_mask[11:16, 11:16] = 2
        with tempfile.TemporaryDirectory(prefix="spatialscope-manual-names-") as temp_value:
            result = save_manual_roi_analysis(
                df_cells=self.df_cells,
                celltype_mask=self.celltype_mask,
                celltype_cfg=self.celltype_config,
                overlay_rgb=np.zeros((*self.celltype_mask.shape, 3), dtype=np.uint8),
                save_dir=Path(temp_value),
                pixel_size_um=(1.0, 1.0),
                roi_label_mask=roi_label_mask,
                selected_types=[item["name"] for item in self.celltype_config],
                roi_source_panel="celltypes",
                roi_custom_names=["Immune focus", "Immune focus"],
                save_outputs=True,
            )

            self.assertEqual(list(result["roi_masks"]), ["Immune focus", "Immune focus (2)"])
            self.assertEqual(
                {
                    name: Path(path).name
                    for name, path in result["saved_paths"]["mask_paths"].items()
                },
                {
                    "Immune focus": "manual__Immune focus_region_mask_uint8.tiff",
                    "Immune focus (2)": "manual__Immune focus (2)_region_mask_uint8.tiff",
                },
            )
            self.assertEqual(
                Path(result["saved_paths"]["roi_mask_tiff"]).name,
                "manual_roi_mask_uint16__manual_roi_selection.tiff",
            )
            self.assert_public_paths_are_readable(result)

    def test_legacy_hashed_registry_path_still_loads(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spatialscope-region-legacy-") as temp_value:
            save_dir = Path(temp_value)
            legacy_mask = save_dir / "manual_deadbeefcafe_region_mask_uint8.tiff"
            legacy_mask.write_bytes(b"legacy mask placeholder")
            legacy_entry = {
                "mask_path": legacy_mask.name,
                "display_name": "Legacy boundary",
                "source": "manual_boundary_adjustment",
                "group_name": "adjusted__manual_deadbeefcafe",
                "mask_key": "manual_deadbeefcafe",
                "id": "roi_deadbeefcafe",
            }
            (save_dir / "boundary_mask_registry.json").write_text(
                json.dumps({"entries": [legacy_entry]}, indent=2),
                encoding="utf-8",
            )

            self.assertEqual(_load_boundary_registry(save_dir), [legacy_entry])


if __name__ == "__main__":
    unittest.main()
