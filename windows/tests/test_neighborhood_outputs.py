from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


BACKEND_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from spatialscope_analysis.neighborhood_analysis import (
    make_neighborhood_figure,
    save_neighborhood_analysis_outputs,
    run_neighborhood_analysis,
)


class NeighborhoodOutputTests(unittest.TestCase):
    @staticmethod
    def _analysis_result():
        cells = pd.DataFrame(
            [
                {"label": 1, "celltype": "A", "centroid_x_px": 2.0, "centroid_y_px": 2.0},
                {"label": 2, "celltype": "B", "centroid_x_px": 12.0, "centroid_y_px": 2.0},
                {"label": 3, "celltype": "A", "centroid_x_px": 2.0, "centroid_y_px": 12.0},
                {"label": 4, "celltype": "B", "centroid_x_px": 3.0, "centroid_y_px": 13.0},
                {"label": 5, "celltype": "A", "centroid_x_px": 12.0, "centroid_y_px": 12.0},
                {"label": 6, "celltype": "B", "centroid_x_px": 13.0, "centroid_y_px": 13.0},
            ]
        )
        return run_neighborhood_analysis(
            df_cells=cells,
            image_shape=(30, 30),
            pixel_size_um=(1.0, 1.0),
            grid_size_um=10.0,
        )

    def test_map_figure_has_no_embedded_cluster_legend(self) -> None:
        result = self._analysis_result()
        figure = make_neighborhood_figure(
            result["cluster_mask"],
            result["cluster_summary"],
            (1.0, 1.0),
            {"A": "#112233", "B": "#445566", "A + B": "#778899"},
        )
        try:
            self.assertIsNone(figure.axes[0].get_legend())
        finally:
            plt.close(figure)

    def test_save_writes_separate_map_and_number_key_contract(self) -> None:
        result = self._analysis_result()
        colors = {"A": "#112233", "B": "#445566", "A + B": "#778899"}

        with tempfile.TemporaryDirectory(prefix="spatialscope-neighborhood-") as value:
            output_dir = Path(value)

            def create_render_placeholder(_figure, filename, *args, **kwargs):
                Path(filename).write_bytes(b"rendered")

            with patch(
                "matplotlib.figure.Figure.savefig",
                autospec=True,
                side_effect=create_render_placeholder,
            ):
                saved = save_neighborhood_analysis_outputs(
                    result,
                    output_dir,
                    (1.0, 1.0),
                    colors,
                    display_cluster_labels=["A + B", "B"],
                    save_outputs=True,
                )

            try:
                expected_names = {
                    "neighborhood_map.svg",
                    "neighborhood_map.png",
                    "neighborhood_map.tiff",
                    "neighborhood_cluster_key.svg",
                    "neighborhood_cluster_key.png",
                    "neighborhood_cluster_key.csv",
                    "neighborhood_cluster_mask_uint16.tiff",
                    "neighborhood_cluster_summary.csv",
                    "neighborhood_tile_assignments.csv",
                    "neighborhood_params.json",
                }
                self.assertTrue(expected_names.issubset({path.name for path in output_dir.iterdir()}))
                self.assertFalse((output_dir / "neighborhood_clusters.png").exists())
                self.assertFalse((output_dir / "neighborhood_preview.png").exists())

                key = pd.read_csv(output_dir / "neighborhood_cluster_key.csv")
                summary = pd.read_csv(output_dir / "neighborhood_cluster_summary.csv")
                self.assertEqual(
                    list(key.columns),
                    [
                        "number",
                        "cluster_id",
                        "cluster_key",
                        "cluster_label",
                        "tile_count",
                        "cell_count",
                        "tile_fraction",
                        "color_hex",
                    ],
                )
                self.assertEqual(key["number"].tolist(), key["cluster_id"].tolist())
                self.assertEqual(key["cluster_label"].tolist(), summary["cluster_label"].tolist())
                self.assertEqual(key["tile_count"].tolist(), summary["n_tiles"].tolist())
                self.assertEqual(key["cell_count"].tolist(), summary["n_cells"].tolist())
                self.assertEqual(key["tile_fraction"].tolist(), summary["tile_fraction"].tolist())
                self.assertEqual(key["color_hex"].tolist(), ["#445566", "#778899"])
                self.assertEqual(key["cluster_id"].tolist(), [1, 2])
                self.assertEqual(key["cluster_label"].tolist(), ["B", "A + B"])

                self.assertEqual(Path(saved["saved_paths"]["png"]).name, "neighborhood_map.png")
                self.assertEqual(Path(saved["saved_paths"]["legend_png"]).name, "neighborhood_cluster_key.png")
                self.assertIsNone(saved["figure"].axes[0].get_legend())
                self.assertIsNot(saved["figure"], saved["legend_figure"])
            finally:
                plt.close(saved["figure"])
                plt.close(saved["legend_figure"])


if __name__ == "__main__":
    unittest.main()
