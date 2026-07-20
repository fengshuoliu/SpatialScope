from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np


BACKEND_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from spatialscope_analysis.compute_runtime import (  # noqa: E402
    ComputeRuntime,
    _LABEL_SET_LOOKUP_MAX_BYTES,
    _default_runtime_from_environment,
    _prepare_label_membership,
    enumerate_opencl_gpus,
    get_compute_runtime,
    select_workflow_parallel_backend,
    set_compute_runtime,
)


class ComputeRuntimeCpuFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = ComputeRuntime(cpu_workers=4, enable_gpu=False, parity_mode="full")

    def tearDown(self) -> None:
        set_compute_runtime(None)
        self.runtime.close()

    def test_exact_integer_operations_and_shapes(self) -> None:
        labels = np.array(
            [[0, 1, 2, 3], [4, 5, 1, -1], [2, 0, 7, 3]],
            dtype=np.int32,
        )

        np.testing.assert_array_equal(self.runtime.equal_scalar(labels, 3), labels == 3)

        lookup = np.array([10, 11, 12, 13, 14, 15], dtype=np.int32)
        expected_lookup = np.full(labels.shape, -9, dtype=np.int32)
        valid = (labels >= 0) & (labels < lookup.size)
        expected_lookup[valid] = lookup[labels[valid]]
        np.testing.assert_array_equal(
            self.runtime.lookup_labels(labels, lookup, default=-9),
            expected_lookup,
        )

        np.testing.assert_array_equal(
            self.runtime.labels_in_set(labels, [1, 3, 7]),
            np.isin(labels, [1, 3, 7]),
        )
        np.testing.assert_array_equal(
            self.runtime.labels_in_set(labels, []),
            np.zeros(labels.shape, dtype=bool),
        )

    def test_joblib_backend_is_only_clamped_for_an_active_shared_gpu_runtime(self) -> None:
        self.assertEqual(
            select_workflow_parallel_backend(
                "loky",
                shared_gpu_runtime_enabled=True,
            ),
            "threading",
        )
        self.assertEqual(
            select_workflow_parallel_backend(
                "loky",
                shared_gpu_runtime_enabled=False,
            ),
            "loky",
        )
        self.assertEqual(
            select_workflow_parallel_backend(
                None,
                shared_gpu_runtime_enabled=False,
            ),
            "threading",
        )

    def test_process_local_default_runtime_honors_gpu_off_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "SPATIALSCOPE_GPU_MODE": "off",
                "SPATIALSCOPE_GPU_PARITY_MODE": "off",
            },
        ):
            runtime = _default_runtime_from_environment()
        try:
            self.assertFalse(runtime.enable_gpu)
            self.assertEqual(runtime.capabilities()["compatibleGpuCount"], 0)
            self.assertTrue(
                any(
                    "disabled" in reason.lower()
                    for reason in runtime.capabilities()["fallbackReasons"]
                )
            )
        finally:
            runtime.close()

    def test_label_membership_uses_bounded_lookup_or_sorted_search(self) -> None:
        dense_ids = np.arange(-512, 513, dtype=np.int32)
        mode, membership, minimum = _prepare_label_membership(dense_ids)
        self.assertEqual(mode, "lookup")
        self.assertEqual(minimum, -512)
        self.assertEqual(membership.nbytes, dense_ids.size)
        self.assertLessEqual(membership.nbytes, _LABEL_SET_LOOKUP_MAX_BYTES)

        sparse_ids = [np.iinfo(np.int32).max, -7, np.iinfo(np.int32).min, -7]
        mode, membership, minimum = _prepare_label_membership(sparse_ids)
        self.assertEqual(mode, "sorted")
        self.assertEqual(minimum, 0)
        self.assertEqual(membership.nbytes, 3 * np.dtype(np.int32).itemsize)
        np.testing.assert_array_equal(
            membership,
            np.array(
                [np.iinfo(np.int32).min, -7, np.iinfo(np.int32).max],
                dtype=np.int32,
            ),
        )

        labels = np.array(
            [
                np.iinfo(np.int32).min,
                np.iinfo(np.int32).min + 1,
                -7,
                0,
                np.iinfo(np.int32).max - 1,
                np.iinfo(np.int32).max,
            ],
            dtype=np.int32,
        )
        np.testing.assert_array_equal(
            self.runtime.labels_in_set(labels, sparse_ids),
            np.isin(labels, sparse_ids),
        )

    def test_assignment_base_matches_current_numpy_semantics(self) -> None:
        labels = np.array([[1, 0, 0, 2], [0, 0, 3, 0]], dtype=np.int32)
        positive = np.array([[1, 1, 1, 0], [1, 1, 1, 1]], dtype=bool)
        voronoi = np.array([[0, 1, 1, 0], [1, 0, 0, 1]], dtype=bool)
        buffer_zone = np.array([[0, 0, 1, 0], [0, 0, 0, 1]], dtype=bool)
        nearest = np.array([[1, 1, 2, 2], [1, 2, 3, 3]], dtype=np.int32)

        expected = np.zeros(labels.shape, dtype=np.uint16)
        inside = positive & (labels > 0)
        expected[inside] = labels[inside].astype(np.uint16)
        nonbuffer = positive & (labels <= 0) & voronoi & ~buffer_zone
        expected[nonbuffer] = nearest[nonbuffer].astype(np.uint16)

        actual = self.runtime.assignment_base(
            labels, positive, voronoi, buffer_zone, nearest
        )
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(actual.dtype, np.uint16)

    def test_expand_tile_grid_handles_partial_edge_tiles(self) -> None:
        tiles = np.array([[11, 12, 13], [21, 22, 23]], dtype=np.int32)
        actual = self.runtime.expand_tile_grid(
            tiles, height=5, width=8, tile_h=3, tile_w=3
        )
        expected = np.empty((5, 8), dtype=np.int32)
        for y in range(5):
            for x in range(8):
                expected[y, x] = tiles[y // 3, x // 3]
        np.testing.assert_array_equal(actual, expected)

    def test_float_and_band_cpu_definitions(self) -> None:
        values = np.array([np.nan, -2.0, 0.0, 1.0, 3.0, 9.0], dtype=np.float32)
        expected_norm = np.clip(
            (values - np.float32(0.0)) / np.float32(3.0), 0.0, 1.0
        ).astype(np.float32)
        np.testing.assert_allclose(
            self.runtime.normalize_clip(values, 0.0, 3.0),
            expected_norm,
            rtol=0,
            atol=0,
            equal_nan=True,
        )
        np.testing.assert_array_equal(
            self.runtime.threshold_ge(values, 1.0), values >= np.float32(1.0)
        )

        base = np.zeros((2, 3, 3), dtype=np.float32)
        intensity = np.linspace(0, 1, 6, dtype=np.float32).reshape(2, 3)
        color = np.array([1.0, 0.5, 0.25], dtype=np.float32)
        layer = np.clip(intensity[..., None] * color, 0.0, 1.0)
        expected_rgb = 1.0 - (1.0 - base) * (1.0 - layer)
        np.testing.assert_allclose(
            self.runtime.composite_rgb(base, intensity, color),
            expected_rgb,
            rtol=0,
            atol=0,
        )

        distance = np.array([[0.0, 4.9, 5.0], [10.1, np.nan, -2.0]], dtype=np.float32)
        mask = np.array([[1, 1, 1], [1, 1, 0]], dtype=bool)
        expected_band = np.array([[0, 0, 1], [2, -1, -1]], dtype=np.int32)
        np.testing.assert_array_equal(
            self.runtime.band_index(distance, mask, 5.0), expected_band
        )

    def test_active_request_aggregates_calls_from_worker_threads(self) -> None:
        set_compute_runtime(self.runtime)
        self.assertIs(get_compute_runtime(), self.runtime)
        labels = np.arange(4096, dtype=np.int32).reshape(64, 64) % 17

        with self.runtime.request_scope("threaded-optimizer") as request:
            with ThreadPoolExecutor(max_workers=4) as executor:
                outputs = list(
                    executor.map(
                        lambda scalar: get_compute_runtime().equal_scalar(labels, scalar),
                        [1, 2, 3, 4, 5, 6, 7, 8],
                    )
                )
            telemetry = request.telemetry()

        for scalar, output in zip([1, 2, 3, 4, 5, 6, 7, 8], outputs):
            np.testing.assert_array_equal(output, labels == scalar)
        self.assertEqual(telemetry["operations"]["equalScalar"]["calls"], 8)
        self.assertEqual(
            telemetry["operations"]["equalScalar"]["elements"],
            8 * labels.size,
        )
        self.assertIsInstance(telemetry["operations"], dict)
        self.assertEqual(telemetry["gpuDevicesUsed"], [])
        self.assertTrue(
            any("disabled" in reason.lower() for reason in telemetry["fallbackReasons"])
        )

    def test_every_configured_cpu_lane_completes_returned_work(self) -> None:
        values = np.arange(16384, dtype=np.float32).reshape(128, 128)

        with self.runtime.request_scope("all-cpu-lanes") as request:
            actual = request.threshold_ge(values, 8192.0)
            telemetry = request.telemetry()

        np.testing.assert_array_equal(actual, values >= np.float32(8192.0))
        self.assertEqual(telemetry["cpuWorkersConfigured"], 4)
        self.assertEqual(telemetry["cpuWorkersUsed"], 4)
        details = telemetry["cpuWorkerDetails"]
        self.assertEqual({detail["laneId"] for detail in details}, {0, 1, 2, 3})
        self.assertEqual(len({detail["threadId"] for detail in details}), 4)
        self.assertTrue(all(detail["workUnits"] > 0 for detail in details))
        self.assertTrue(all(detail["elements"] > 0 for detail in details))

        operation = telemetry["operations"]["thresholdGe"]
        self.assertEqual(operation["cpuWorkersUsed"], 4)
        self.assertEqual(
            {detail["laneId"] for detail in operation["cpuWorkerDetails"]},
            {0, 1, 2, 3},
        )


class ComputeRuntimeOpenCLParityTests(unittest.TestCase):
    def test_discovery_and_every_compatible_gpu_produce_returned_output(self) -> None:
        discovered = enumerate_opencl_gpus()
        for descriptor in discovered:
            self.assertEqual(descriptor["backend"], "OpenCL")
            self.assertEqual(descriptor["deviceType"], "GPU")
            self.assertTrue(descriptor["platformName"])
            self.assertTrue(descriptor["name"])

        runtime = ComputeRuntime(cpu_workers=2, enable_gpu=True, parity_mode="full")
        try:
            compatible = [
                descriptor
                for descriptor in runtime.device_descriptors()
                if descriptor["compatible"]
            ]
            if not compatible:
                self.skipTest("No compatible OpenCL GPU is available on this host.")

            size = max(4096, (len(compatible) + runtime.cpu_workers) * 128)
            labels = (np.arange(size, dtype=np.int32) % 23).reshape(64, -1)
            lookup = np.arange(32, dtype=np.int32) * 7
            positive = labels % 2 == 0
            voronoi = labels % 3 != 0
            buffer_zone = labels % 5 == 0
            nearest = labels + 1
            values = np.linspace(-1.0, 2.0, labels.size, dtype=np.float32).reshape(labels.shape)

            with runtime.request_scope("gpu-parity", parity_mode="full") as request:
                np.testing.assert_array_equal(request.equal_scalar(labels, 7), labels == 7)
                np.testing.assert_array_equal(
                    request.lookup_labels(labels, lookup), lookup[labels]
                )
                np.testing.assert_array_equal(
                    request.labels_in_set(labels, [1, 4, 9, 16]),
                    np.isin(labels, [1, 4, 9, 16]),
                )
                sparse_labels = np.resize(
                    np.array(
                        [
                            np.iinfo(np.int32).min,
                            np.iinfo(np.int32).min + 1,
                            -7,
                            0,
                            11,
                            np.iinfo(np.int32).max - 1,
                            np.iinfo(np.int32).max,
                        ],
                        dtype=np.int32,
                    ),
                    labels.shape,
                )
                sparse_ids = [np.iinfo(np.int32).min, -7, np.iinfo(np.int32).max]
                np.testing.assert_array_equal(
                    request.labels_in_set(sparse_labels, sparse_ids),
                    np.isin(sparse_labels, sparse_ids),
                )
                assignment = request.assignment_base(
                    labels, positive, voronoi, buffer_zone, nearest
                )
                expected_assignment = np.zeros(labels.shape, dtype=np.uint16)
                inside = positive & (labels > 0)
                expected_assignment[inside] = labels[inside].astype(np.uint16)
                nonbuffer = positive & (labels <= 0) & voronoi & ~buffer_zone
                expected_assignment[nonbuffer] = nearest[nonbuffer].astype(np.uint16)
                np.testing.assert_array_equal(assignment, expected_assignment)

                norm = request.normalize_clip(values, 0.0, 1.0)
                np.testing.assert_allclose(
                    norm,
                    np.clip(values, 0.0, 1.0),
                    rtol=2e-6,
                    atol=2e-6,
                )
                np.testing.assert_array_equal(
                    request.threshold_ge(values, 0.25),
                    values >= np.float32(0.25),
                )

                base = np.zeros(values.shape + (3,), dtype=np.float32)
                color = np.array([0.2, 0.6, 1.0], dtype=np.float32)
                composite = request.composite_rgb(base, np.clip(values, 0, 1), color)
                expected_composite = np.clip(values, 0, 1)[..., None] * color
                np.testing.assert_allclose(
                    composite,
                    expected_composite,
                    rtol=3e-6,
                    atol=3e-6,
                )

                distance = np.abs(values) * np.float32(10.0)
                mask = labels % 4 != 0
                expected_band = np.full(labels.shape, -1, dtype=np.int32)
                expected_band[mask] = np.floor(distance[mask] / np.float32(2.5)).astype(np.int32)
                np.testing.assert_array_equal(
                    request.band_index(distance, mask, 2.5), expected_band
                )

                tiles = np.arange(64, dtype=np.int32).reshape(8, 8)
                expanded = request.expand_tile_grid(
                    tiles, height=64, width=64, tile_h=8, tile_w=8
                )
                np.testing.assert_array_equal(
                    expanded,
                    np.repeat(np.repeat(tiles, 8, axis=0), 8, axis=1),
                )
                telemetry = request.telemetry()

            used = telemetry["gpuDevicesUsed"]
            self.assertEqual(
                {descriptor["id"] for descriptor in compatible},
                {descriptor["id"] for descriptor in used},
            )
            self.assertTrue(all(device["workUnits"] > 0 for device in used))
            self.assertTrue(all(device["elements"] > 0 for device in used))
            self.assertTrue(all(device["outputElements"] > 0 for device in used))
            self.assertGreater(telemetry["cpuWorkUnits"], 0)
            self.assertGreater(telemetry["cpuElements"], 0)
            self.assertGreater(telemetry["gpuOutputElements"], 0)
            self.assertEqual(telemetry["backend"], "OpenCL+CPU")
            for operation in telemetry["operations"].values():
                if operation["gpuElements"]:
                    self.assertEqual(operation["parityFailures"], 0)
                    self.assertGreater(operation["gpuOutputElements"], 0)
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
