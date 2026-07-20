from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from skimage import morphology


WINDOWS_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = WINDOWS_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from native_engine import ASSIGNMENT_OPTIMIZER_SEARCH_SPECS  # noqa: E402
from src.spatialscope_analysis.celltype_assignment import (  # noqa: E402
    CelltypeAssignmentParams,
    MAX_ASSIGNMENT_SMOOTHING_RADIUS_PX,
    MAX_ASSIGNMENT_TOPHAT_RADIUS_PX,
    MAX_NATIVE_VOTING_RADIUS_PX,
    _assignment_disk_offsets,
    _euclidean_disk_dilation,
    _marker_threshold,
    _preprocess_assignment_marker,
    _truncated_box_blur,
)


class AssignmentParameterParityTests(unittest.TestCase):
    @staticmethod
    def _reference_box_blur(image: np.ndarray, radius: int) -> np.ndarray:
        values = np.asarray(image, dtype=np.float64)
        output = np.zeros_like(values)
        for y in range(values.shape[0]):
            y0 = max(0, y - radius)
            y1 = min(values.shape[0], y + radius + 1)
            for x in range(values.shape[1]):
                x0 = max(0, x - radius)
                x1 = min(values.shape[1], x + radius + 1)
                output[y, x] = float(np.mean(values[y0:y1, x0:x1]))
        return output

    @staticmethod
    def _wpf_assignment_specs() -> dict[str, tuple[float, float, float]]:
        source = (
            WINDOWS_DIR
            / "native"
            / "src"
            / "SpatialScope.App"
            / "Models"
            / "ParameterDefinition.cs"
        ).read_text(encoding="utf-8")
        pattern = re.compile(
            r'new\("(?P<key>[^"]+)",\s*"[^"]+",\s*"[^"]+",\s*'
            r'[-+]?\d+(?:\.\d+)?,\s*'
            r'(?P<minimum>[-+]?\d+(?:\.\d+)?),\s*'
            r'(?P<maximum>[-+]?\d+(?:\.\d+)?),\s*'
            r'(?P<increment>[-+]?\d+(?:\.\d+)?),'
        )
        return {
            match.group("key"): (
                float(match.group("minimum")),
                float(match.group("maximum")),
                float(match.group("increment")),
            )
            for match in pattern.finditer(source)
        }

    def test_wpf_ranges_match_macos_and_keep_reference_values_editable(self) -> None:
        specs = self._wpf_assignment_specs()
        expected = {
            "r_voronoi_um": (0.0, 300.0, 1.0),
            "r_buffer_um": (0.0, 300.0, 1.0),
            "r_vote_um": (0.0, 300.0, 1.0),
            "tophat_r_um": (0.0, 150.0, 1.0),
            "gauss_sigma_um": (0.0, 75.0, 0.5),
            "ambiguous_min_probability": (0.0, 1.0, 0.01),
            "ambiguous_min_gap": (0.0, 1.0, 0.01),
        }
        for key, expected_spec in expected.items():
            self.assertEqual(specs[key], expected_spec)

        restored_reference_values = {
            "r_vote_um": 201.33,
            "tophat_r_um": 37.5,
            "gauss_sigma_um": 56.25,
        }
        for key, value in restored_reference_values.items():
            minimum, maximum, _ = specs[key]
            self.assertLessEqual(minimum, value)
            self.assertLessEqual(value, maximum)

    def test_wpf_threshold_picker_displays_local_and_retains_triangle(self) -> None:
        source = (
            WINDOWS_DIR / "native" / "src" / "SpatialScope.App" / "MainWindow.xaml.cs"
        ).read_text(encoding="utf-8")
        self.assertIn('Content = "Local", Tag = "local"', source)
        self.assertIn('"local" => 1', source)
        self.assertIn('Content = "Triangle", Tag = "triangle"', source)

    def test_optimizer_defaults_match_macos_domains(self) -> None:
        expected = {
            "r_voronoi_um": (0.0, 300.0, 1.0),
            "r_buffer_um": (0.0, 300.0, 1.0),
            "r_vote_um": (0.0, 300.0, 1.0),
            "tophat_r_um": (0.0, 150.0, 1.0),
            "gauss_sigma_um": (0.0, 75.0, 0.5),
        }
        for key, (minimum, maximum, step) in expected.items():
            spec = ASSIGNMENT_OPTIMIZER_SEARCH_SPECS[key]
            self.assertEqual((spec["min"], spec["max"], spec["step"]), (minimum, maximum, step))

        self.assertEqual(
            ASSIGNMENT_OPTIMIZER_SEARCH_SPECS["thresh_mode"]["options"],
            ["global_otsu", "local", "yen", "triangle"],
        )
        self.assertEqual(
            (
                ASSIGNMENT_OPTIMIZER_SEARCH_SPECS["ambiguous_min_probability"]["min"],
                ASSIGNMENT_OPTIMIZER_SEARCH_SPECS["ambiguous_min_probability"]["max"],
            ),
            (0.01, 1.0),
        )
        self.assertEqual(
            (
                ASSIGNMENT_OPTIMIZER_SEARCH_SPECS["ambiguous_min_gap"]["min"],
                ASSIGNMENT_OPTIMIZER_SEARCH_SPECS["ambiguous_min_gap"]["max"],
            ),
            (0.0, 1.0),
        )

    def test_local_mode_is_preserved_and_matches_macos_otsu_behavior(self) -> None:
        params = CelltypeAssignmentParams(thresh_mode="local")
        self.assertEqual(params.to_dict()["thresh_mode"], "local")

        image = np.array(
            [[0.0, 0.02, 0.05, 0.1], [0.2, 0.3, 0.75, 1.0]],
            dtype=np.float32,
        )
        self.assertEqual(
            _marker_threshold(image, "local"),
            _marker_threshold(image, "global_otsu"),
        )
        self.assertTrue(np.isfinite(_marker_threshold(image, "triangle")))

    def test_edge_normalized_box_blur_matches_macos_window_definition(self) -> None:
        image = np.array(
            [[0.0, 1.0, 4.0, 2.0], [3.0, 8.0, 5.0, 7.0], [6.0, 9.0, 2.0, 1.0]],
            dtype=np.float32,
        )
        for radius in (1, 2, 8):
            np.testing.assert_allclose(
                _truncated_box_blur(image, radius),
                self._reference_box_blur(image, radius),
                rtol=0.0,
                atol=1e-12,
            )

    def test_assignment_preprocessing_matches_macos_box_filters_and_caps(self) -> None:
        image = np.array(
            [[0.0, 0.1, 0.6], [0.3, 1.0, 0.2], [0.4, 0.8, 0.05]],
            dtype=np.float32,
        )
        background = self._reference_box_blur(image, 1)
        expected = self._reference_box_blur(np.maximum(0.0, image - background), 2)
        np.testing.assert_allclose(
            _preprocess_assignment_marker(image, 1, 2),
            expected,
            rtol=0.0,
            atol=1e-12,
        )

        recorded_radii: list[int] = []

        def record_blur(values: np.ndarray, radius: int) -> np.ndarray:
            recorded_radii.append(radius)
            return np.asarray(values, dtype=np.float64)

        with patch(
            "src.spatialscope_analysis.celltype_assignment._truncated_box_blur",
            side_effect=record_blur,
        ):
            _preprocess_assignment_marker(image, 10_000, 10_000)
        self.assertEqual(
            recorded_radii,
            [MAX_ASSIGNMENT_TOPHAT_RADIUS_PX, MAX_ASSIGNMENT_SMOOTHING_RADIUS_PX],
        )

    def test_edt_buffer_dilation_matches_exact_disk_geometry(self) -> None:
        masks = []
        center = np.zeros((13, 15), dtype=bool)
        center[6, 7] = True
        masks.append(center)
        corner = np.zeros((13, 15), dtype=bool)
        corner[0, 0] = True
        corner[9, 12] = True
        masks.append(corner)
        for mask in masks:
            for radius in (1, 2, 5):
                np.testing.assert_array_equal(
                    _euclidean_disk_dilation(mask, radius),
                    morphology.binary_dilation(mask, morphology.disk(radius)),
                )

    def test_assignment_vote_and_candidate_offsets_use_macos_radius_cap(self) -> None:
        expected_dy, expected_dx = _assignment_disk_offsets(MAX_NATIVE_VOTING_RADIUS_PX)
        self.assertEqual(len(expected_dy), 1793)
        for requested_radius in (197, 232, 10_000):
            actual_dy, actual_dx = _assignment_disk_offsets(requested_radius)
            np.testing.assert_array_equal(actual_dy, expected_dy)
            np.testing.assert_array_equal(actual_dx, expected_dx)


if __name__ == "__main__":
    unittest.main()
