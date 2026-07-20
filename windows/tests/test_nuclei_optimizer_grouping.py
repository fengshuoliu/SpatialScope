from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.spatialscope_analysis.compute_runtime import ComputeRuntime, set_compute_runtime  # noqa: E402
from src.spatialscope_analysis.models import NucleiParams  # noqa: E402
from src.spatialscope_analysis.nuclei_segmentation import (  # noqa: E402
    SWEEP_PARAM_ORDER,
    _evaluate_explicit_combo_records,
    _evaluate_sweep_combo_chunk,
    normalize_nucleus_image,
)


class NucleiOptimizerGroupingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = ComputeRuntime(cpu_workers=4, enable_gpu=False, parity_mode="full")
        set_compute_runtime(self.runtime)

    def tearDown(self) -> None:
        set_compute_runtime(None)
        self.runtime.close()

    @staticmethod
    def _fixture() -> tuple[np.ndarray, np.ndarray, NucleiParams, list[tuple[float, ...]]]:
        rng = np.random.default_rng(20260719)
        dapi = rng.normal(0.015, 0.004, (160, 192)).clip(0, None).astype(np.float32)
        dapi[12::24, 14::28] += np.float32(0.9)
        dapi_norm = normalize_nucleus_image(dapi)
        base = NucleiParams(nucleus_channel="DAPI", min_diam_um=2.0, max_diam_um=40.0)
        variants = [
            {},
            {"local_offset": 0.08},
            {"tophat_radius_um": 2.2, "gauss_sigma_um": 0.6},
            {"tophat_radius_um": 2.4, "gauss_sigma_um": 0.7, "h_maxima_um": 0.8},
            {"tophat_radius_um": 5.1, "gauss_sigma_um": 1.2, "min_diam_um": 3.0},
            {"tophat_radius_um": 5.4, "gauss_sigma_um": 1.4, "max_diam_um": 28.0},
        ]
        combos = [
            tuple(float(variant.get(field, getattr(base, field))) for field in SWEEP_PARAM_ORDER)
            for variant in variants
        ]
        return dapi, dapi_norm, base, combos

    def test_grouped_metrics_match_full_label_evaluation_exactly(self) -> None:
        dapi, dapi_norm, base, combos = self._fixture()
        records = [(index, combo) for index, combo in enumerate(combos, start=1)]
        with self.runtime.request_scope("legacy"):
            expected = _evaluate_sweep_combo_chunk(
                records,
                base,
                dapi,
                dapi_norm,
                (1.0, 1.0),
                native_threads=1,
            )

        telemetry_state = {"recorded": False}
        with self.runtime.request_scope("grouped") as request:
            actual = _evaluate_explicit_combo_records(
                combos,
                1,
                base,
                dapi,
                dapi_norm,
                (1.0, 1.0),
                parallel_workers=4,
                parallel_backend="threading",
                native_threads_per_worker=1,
                compute_telemetry_state=telemetry_state,
            )
            # A later adaptive-search batch shares the same request state and
            # must not multiply full-image CPU/OpenCL telemetry work.
            _evaluate_explicit_combo_records(
                combos[:2],
                len(combos) + 1,
                base,
                dapi,
                dapi_norm,
                (1.0, 1.0),
                parallel_workers=4,
                parallel_backend="threading",
                native_threads_per_worker=1,
                compute_telemetry_state=telemetry_state,
            )
            telemetry = request.telemetry()

        self.assertEqual(expected, actual)
        self.assertTrue(telemetry_state["recorded"])
        self.assertEqual(telemetry["operations"]["labelsInSet"]["calls"], 1)
        self.assertNotIn("lookupLabels", telemetry["operations"])
        self.assertEqual(telemetry["cpuWorkersUsed"], 4)


if __name__ == "__main__":
    unittest.main()
