from __future__ import annotations

import sys
import unittest
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D


BACKEND_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from spatialscope_analysis.region_analysis import make_region_overlay_figure


class RegionOverlayFilteringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.celltype_mask = np.zeros((24, 24), dtype=np.uint16)
        self.celltype_config = [
            {"name": "A", "color_hex": "#ff0000"},
            {"name": "B", "color_hex": "#00ff00"},
        ]
        self.mask_a = np.zeros((24, 24), dtype=bool)
        self.mask_a[2:9, 2:9] = True
        self.mask_b = np.zeros((24, 24), dtype=bool)
        self.mask_b[14:21, 14:21] = True

    @staticmethod
    def _line_colors(figure: plt.Figure) -> list[str]:
        return [
            str(item.get_color()).lower()
            for item in figure.axes[0].get_children()
            if isinstance(item, Line2D)
        ]

    def test_selected_cell_type_hides_unselected_named_mask(self) -> None:
        figure = make_region_overlay_figure(
            self.celltype_mask,
            self.celltype_config,
            {"A": self.mask_a, "B": self.mask_b},
            ["A"],
            (1.0, 1.0),
        )
        try:
            colors = self._line_colors(figure)
            self.assertIn("#ff0000", colors)
            self.assertNotIn("#00ff00", colors)
        finally:
            plt.close(figure)

    def test_arbitrary_roi_labels_remain_visible_when_no_mask_name_matches(self) -> None:
        figure = make_region_overlay_figure(
            self.celltype_mask,
            self.celltype_config,
            {"manual_roi_1": self.mask_a},
            ["A"],
            (1.0, 1.0),
            boundary_color="#ff00ff",
            use_type_colors=False,
        )
        try:
            self.assertIn("#ff00ff", self._line_colors(figure))
        finally:
            plt.close(figure)


if __name__ == "__main__":
    unittest.main()
