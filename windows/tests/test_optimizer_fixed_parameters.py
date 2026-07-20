from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.spatialscope_analysis.celltype_assignment import (  # noqa: E402
    CELLTYPE_OPTIMIZER_PARAM_ORDER,
    _build_celltype_assignment_search_space_specs,
    _sample_global_celltype_assignment_candidates,
)
from src.spatialscope_analysis.nuclei_segmentation import (  # noqa: E402
    SWEEP_PARAM_ORDER,
    _build_search_space_specs,
    _sample_global_search_candidates,
)
from src.spatialscope_analysis.optimizer_fixed_parameters import (  # noqa: E402
    apply_assignment_fixed_parameter_keys,
    apply_nuclei_fixed_parameter_keys,
)


def nuclei_search_specs() -> dict[str, dict[str, float]]:
    return {
        "min_diam_um": {"min": 0.0, "max": 30.0, "step": 0.5},
        "max_diam_um": {"min": 0.0, "max": 180.0, "step": 1.0},
        "tophat_radius_um": {"min": 0.0, "max": 12.0, "step": 0.5},
        "gauss_sigma_um": {"min": 0.0, "max": 5.0, "step": 0.1},
        "local_win_um": {"min": 5.0, "max": 120.0, "step": 1.0},
        "local_offset": {"min": -0.40, "max": 0.40, "step": 0.01},
        "h_maxima_um": {"min": 0.0, "max": 8.0, "step": 0.05},
        "seed_min_dist_um": {"min": 0.0, "max": 20.0, "step": 0.1},
        "watershed_compactness": {"min": 0.0, "max": 4.0, "step": 0.05},
        "post_resplit_mult": {"min": 0.0, "max": 5.0, "step": 0.05},
    }


def assignment_search_specs() -> dict[str, dict[str, object]]:
    return {
        "r_voronoi_um": {"kind": "float", "min": 0.0, "max": 300.0, "step": 1.0},
        "r_buffer_um": {"kind": "float", "min": 0.0, "max": 300.0, "step": 1.0},
        "r_vote_um": {"kind": "float", "min": 0.0, "max": 300.0, "step": 1.0},
        "tophat_r_um": {"kind": "float", "min": 0.0, "max": 150.0, "step": 1.0},
        "gauss_sigma_um": {"kind": "float", "min": 0.0, "max": 75.0, "step": 0.5},
        "thresh_mode": {
            "kind": "choice",
            "options": ["global_otsu", "local", "yen", "triangle"],
        },
        "min_pos_object_size_px": {"kind": "int", "min": 0, "max": 80, "step": 1},
        "min_pos_pix": {"kind": "int", "min": 0, "max": 40, "step": 1},
        "resolve_ambiguous": {"kind": "bool", "options": [True]},
        "ambiguous_min_probability": {"kind": "float", "min": 0.01, "max": 1.0, "step": 0.01},
        "ambiguous_min_gap": {"kind": "float", "min": 0.0, "max": 1.0, "step": 0.01},
    }


class OptimizerFixedParameterTests(unittest.TestCase):
    def test_omitted_or_empty_keys_preserve_legacy_specs_without_aliasing(self) -> None:
        for apply, specs, parameters in (
            (
                apply_nuclei_fixed_parameter_keys,
                nuclei_search_specs(),
                {"min_diam_um": 6.0, "max_diam_um": 60.0},
            ),
            (
                apply_assignment_fixed_parameter_keys,
                assignment_search_specs(),
                {"r_voronoi_um": 3.0, "r_buffer_um": 2.0},
            ),
        ):
            with self.subTest(apply=apply.__name__):
                expected = copy.deepcopy(specs)
                for raw_keys in (None, []):
                    result = apply(specs, parameters, raw_keys)
                    self.assertEqual(result.search_specs, expected)
                    self.assertEqual(result.fixed_parameters, {})
                    self.assertEqual(result.fixed_parameter_keys, ())
                    self.assertIsNot(result.search_specs, specs)

    def test_nuclei_minimum_only_stays_exact_and_constrains_maximum(self) -> None:
        result = apply_nuclei_fixed_parameter_keys(
            nuclei_search_specs(),
            SimpleNamespace(min_diam_um=25.25, max_diam_um=60.0),
            ["min_diam_um"],
        )
        self.assertEqual(result.fixed_parameters, {"min_diam_um": 25.25})
        self.assertEqual(
            (result.search_specs["min_diam_um"]["min"], result.search_specs["min_diam_um"]["max"]),
            (25.25, 25.25),
        )
        self.assertGreaterEqual(result.search_specs["max_diam_um"]["min"], 25.25)

        built = _build_search_space_specs(result.search_specs)
        combos = _sample_global_search_candidates(built, 64, np.random.default_rng(7))
        min_index = SWEEP_PARAM_ORDER.index("min_diam_um")
        max_index = SWEEP_PARAM_ORDER.index("max_diam_um")
        self.assertTrue(combos)
        self.assertTrue(all(combo[min_index] == 25.25 for combo in combos))
        self.assertTrue(all(combo[max_index] >= 25.25 for combo in combos))

    def test_nuclei_maximum_only_stays_exact_and_constrains_minimum(self) -> None:
        result = apply_nuclei_fixed_parameter_keys(
            nuclei_search_specs(),
            {"min_diam_um": 6.0, "max_diam_um": 9.75},
            ["max_diam_um"],
        )
        self.assertEqual(result.fixed_parameters, {"max_diam_um": 9.75})
        self.assertEqual(
            (result.search_specs["max_diam_um"]["min"], result.search_specs["max_diam_um"]["max"]),
            (9.75, 9.75),
        )
        self.assertLessEqual(result.search_specs["min_diam_um"]["max"], 9.75)

        built = _build_search_space_specs(result.search_specs)
        combos = _sample_global_search_candidates(built, 64, np.random.default_rng(11))
        min_index = SWEEP_PARAM_ORDER.index("min_diam_um")
        max_index = SWEEP_PARAM_ORDER.index("max_diam_um")
        self.assertTrue(combos)
        self.assertTrue(all(combo[max_index] == 9.75 for combo in combos))
        self.assertTrue(all(combo[min_index] <= 9.75 for combo in combos))

    def test_nuclei_both_diameters_are_exact_singletons(self) -> None:
        result = apply_nuclei_fixed_parameter_keys(
            nuclei_search_specs(),
            {"min_diam_um": 6.3, "max_diam_um": 60.2},
            ["min_diam_um", "max_diam_um", "min_diam_um"],
        )
        self.assertEqual(result.fixed_parameter_keys, ("min_diam_um", "max_diam_um"))
        self.assertEqual(
            result.fixed_parameters,
            {"min_diam_um": 6.3, "max_diam_um": 60.2},
        )
        built = _build_search_space_specs(result.search_specs)
        self.assertEqual(built["min_diam_um"]["n_values"], 1)
        self.assertEqual(built["max_diam_um"]["n_values"], 1)
        self.assertEqual(built["min_diam_um"]["min"], 6.3)
        self.assertEqual(built["max_diam_um"]["min"], 60.2)

    def test_nuclei_rejects_inverted_fixed_pair(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be larger"):
            apply_nuclei_fixed_parameter_keys(
                nuclei_search_specs(),
                {"min_diam_um": 20.0, "max_diam_um": 10.0},
                ["min_diam_um", "max_diam_um"],
            )

    def test_assignment_supports_every_independent_lock_combination(self) -> None:
        parameters = {"r_voronoi_um": 3.25, "r_buffer_um": 2.75}
        fields = ("r_voronoi_um", "r_buffer_um")
        field_indices = {field: CELLTYPE_OPTIMIZER_PARAM_ORDER.index(field) for field in fields}
        for lock_voronoi in (False, True):
            for lock_buffer in (False, True):
                keys = [
                    field
                    for field, locked in zip(fields, (lock_voronoi, lock_buffer))
                    if locked
                ]
                with self.subTest(keys=keys):
                    result = apply_assignment_fixed_parameter_keys(
                        assignment_search_specs(),
                        parameters,
                        keys,
                    )
                    self.assertEqual(set(result.fixed_parameters), set(keys))
                    built = _build_celltype_assignment_search_space_specs(result.search_specs)
                    for field in fields:
                        if field in keys:
                            self.assertEqual(built[field]["n_values"], 1)
                            self.assertEqual(built[field]["values"], [parameters[field]])
                        else:
                            self.assertGreater(built[field]["n_values"], 1)

                    combos = _sample_global_celltype_assignment_candidates(
                        built,
                        32,
                        np.random.default_rng(19),
                    )
                    self.assertTrue(combos)
                    for field in keys:
                        self.assertTrue(
                            all(combo[field_indices[field]] == parameters[field] for combo in combos)
                        )

    def test_unknown_and_nonfinite_fixed_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown parameter"):
            apply_nuclei_fixed_parameter_keys(
                nuclei_search_specs(),
                {"min_diam_um": 6.0},
                ["local_offset"],
            )
        with self.assertRaisesRegex(ValueError, "finite number"):
            apply_assignment_fixed_parameter_keys(
                assignment_search_specs(),
                {"r_voronoi_um": float("nan")},
                ["r_voronoi_um"],
            )
        with self.assertRaisesRegex(ValueError, "must be an array"):
            apply_assignment_fixed_parameter_keys(
                assignment_search_specs(),
                {"r_voronoi_um": 3.0},
                "r_voronoi_um",  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
