from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


WINDOWS_DIR = Path(__file__).resolve().parents[1]
ENGINE_SCRIPT = WINDOWS_DIR / "backend" / "native_engine.py"


class EngineProcess:
    def __init__(self, command: Sequence[str]) -> None:
        self._runtime = tempfile.TemporaryDirectory(prefix="spatialscope-engine-smoke-")
        runtime_path = Path(self._runtime.name)
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["MPLBACKEND"] = "Agg"
        environment["MPLCONFIGDIR"] = str(runtime_path / "matplotlib")
        self.process = subprocess.Popen(
            [*command, "--json-lines"],
            cwd=str(runtime_path),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.counter = 0
        self.max_protocol_line_bytes = 0

    def request(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.counter += 1
        request_id = f"smoke-{self.counter}"
        request = {"id": request_id, "command": command, "payload": payload}
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

        while True:
            line = self.process.stdout.readline()
            if line == "":
                raise RuntimeError(f"Engine exited while running {command}; exit={self.process.poll()}")
            self.max_protocol_line_bytes = max(self.max_protocol_line_bytes, len(line.encode("utf-8")))
            response = json.loads(line)
            if response.get("id") != request_id:
                continue
            if response.get("type") == "progress":
                continue
            if response.get("type") == "error":
                raise RuntimeError(f"{command} failed: {response.get('message')}")
            if response.get("type") == "result":
                return response["data"]

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                try:
                    self.request("shutdown", {})
                except (OSError, RuntimeError):
                    pass
        finally:
            if self.process.stdin and not self.process.stdin.closed:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            if self.process.poll() is None:
                self.process.wait(timeout=10)
            self._runtime.cleanup()


def cell_types() -> list[dict[str, Any]]:
    return [
        {"name": "CD4 T", "color_hex": "#ff4fb3", "all_pos": ["nucleus", "CD4"], "all_neg": ["CD8", "B220", "Tumor"], "any_pos_groups": []},
        {"name": "CD8 T", "color_hex": "#33c96f", "all_pos": ["nucleus", "CD8"], "all_neg": ["CD4", "B220", "Tumor"], "any_pos_groups": []},
        {"name": "B cell", "color_hex": "#32b8d8", "all_pos": ["nucleus", "B220"], "all_neg": ["CD4", "CD8", "Tumor"], "any_pos_groups": []},
        {"name": "Tumor", "color_hex": "#f05b49", "all_pos": ["nucleus", "Tumor"], "all_neg": ["CD4", "CD8", "B220"], "any_pos_groups": []},
    ]


def prepare_smoke_output_folder(output_folder: Path) -> Path:
    resolved = output_folder.resolve()
    build_root = (WINDOWS_DIR / "build").resolve()
    if resolved.exists():
        try:
            relative = resolved.relative_to(build_root)
        except ValueError as exc:
            raise FileExistsError(
                "Refusing to delete an existing smoke-test output folder outside "
                f"{build_root}: {resolved}"
            ) from exc
        if not relative.parts:
            raise FileExistsError(f"Refusing to delete the Windows build root: {resolved}")
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)
    return resolved


def run_smoke(engine_command: Sequence[str], input_folder: Path, output_folder: Path) -> dict[str, Any]:
    output_folder = prepare_smoke_output_folder(output_folder)

    engine = EngineProcess(engine_command)
    try:
        hello = engine.request("hello", {})
        if hello["protocolVersion"] != 1:
            raise AssertionError(f"Unexpected engine protocol: {hello}")
        expected_cpu_count = max(1, os.cpu_count() or 1)
        compute = hello.get("compute", {})
        if int(compute.get("defaultCpuWorkers", 0)) != expected_cpu_count:
            raise AssertionError(f"Engine did not default to all logical CPUs: {compute}")
        expected_gpu_names = set(str(value) for value in compute.get("detectedGpus", []))

        def assert_default_compute_usage(result: dict[str, Any], command: str) -> None:
            usage = result.get("compute", {})
            if int(usage.get("cpuWorkersConfigured", 0)) != expected_cpu_count:
                raise AssertionError(f"{command} did not retain the all-CPU default: {usage}")
            if int(usage.get("cpuWorkersUsed", 0)) != expected_cpu_count:
                raise AssertionError(f"{command} did not execute returned work on all logical CPU lanes: {usage}")
            worker_details = usage.get("cpuWorkerDetails", [])
            if len({int(worker.get("threadId", -1)) for worker in worker_details}) != expected_cpu_count:
                raise AssertionError(f"{command} did not confirm distinct CPU worker threads: {usage}")
            if any(
                int(worker.get("workUnits", 0)) <= 0 or int(worker.get("elements", 0)) <= 0
                for worker in worker_details
            ):
                raise AssertionError(f"{command} reported a CPU lane without completed output work: {usage}")
            if not expected_gpu_names:
                return
            used_gpu_names = {
                str(device.get("name"))
                for device in usage.get("gpuDevicesUsed", [])
                if int(device.get("outputElements", 0)) > 0
            }
            if used_gpu_names != expected_gpu_names:
                raise AssertionError(
                    f"{command} did not return output from every compatible GPU; "
                    f"expected={sorted(expected_gpu_names)}, used={sorted(used_gpu_names)}, usage={usage}"
                )
            if int(usage.get("gpuOutputElements", 0)) <= 0:
                raise AssertionError(f"{command} reported GPUs without using their output: {usage}")

        configure = engine.request(
            "configure",
            {
                "inputFolder": str(input_folder),
                "outputFolder": str(output_folder),
                "pixelSizeUm": [1.0, 1.0],
                "imageId": "NativeSmoke",
                "whiteChannel": "DAPI",
                "whiteWeight": 0.25,
            },
        )
        if len(configure["channels"]) != 5:
            raise AssertionError("Native configuration did not discover all five channels.")

        overlay = engine.request("overlay", {"clipHighPercentile": 99.8})
        assert_default_compute_usage(overlay, "overlay")
        for preview_path in overlay["previewPaths"].values():
            if not Path(preview_path).is_file():
                raise AssertionError(f"Missing overlay preview: {preview_path}")

        nuclei_optimizer = engine.request(
            "nuclei_optimizer",
            {
                "parameters": {"nucleus_channel": "DAPI", "min_diam_um": 6.0, "max_diam_um": 16.0},
                "maxEvaluations": 2,
                "parallelBackend": "threading",
                "useFixedRoiSubset": True,
            },
        )
        assert_default_compute_usage(nuclei_optimizer, "nuclei_optimizer")
        if not nuclei_optimizer["recommendedParameters"]:
            raise AssertionError("Nuclei optimizer did not return a recommendation.")

        nuclei = engine.request(
            "nuclei",
            {
                "parameters": {
                    "nucleus_channel": "DAPI",
                    "min_diam_um": 6.0,
                    "max_diam_um": 16.0,
                    "tophat_radius_um": 6.0,
                    "gauss_sigma_um": 0.6,
                    "local_win_um": 15.0,
                    "local_offset": -0.03,
                    "h_maxima_um": 0.1,
                    "seed_min_dist_um": 2.0,
                    "watershed_compactness": 0.2,
                    "post_resplit_mult": 0.6,
                },
            },
        )
        assert_default_compute_usage(nuclei, "nuclei")
        n_nuclei = int(nuclei["summary"]["nNuclei"])
        if not 40 <= n_nuclei <= 100:
            raise AssertionError(f"Unexpected native-engine nuclei count: {n_nuclei}")

        assignment_optimizer = engine.request(
            "celltype_optimizer",
            {
                "cellTypes": cell_types(),
                "parameters": {"r_voronoi_um": 3.0, "r_buffer_um": 2.0, "r_vote_um": 3.0},
                "maxEvaluations": 2,
                "parallelBackend": "threading",
                "useFixedRoiSubset": True,
            },
        )
        assert_default_compute_usage(assignment_optimizer, "celltype_optimizer")
        if not assignment_optimizer["recommendedParameters"]:
            raise AssertionError("Assignment optimizer did not return a recommendation.")

        assignment = engine.request(
            "celltype_assignment",
            {
                "nucleusChannel": "DAPI",
                "cellTypes": cell_types(),
                "parameters": {
                    "r_voronoi_um": 3.0,
                    "r_buffer_um": 2.0,
                    "r_vote_um": 3.0,
                    "tophat_r_um": 0.0,
                    "gauss_sigma_um": 0.5,
                    "thresh_mode": "global_otsu",
                    "min_pos_object_size_px": 5,
                    "min_pos_pix": 3,
                    "resolve_ambiguous": True,
                    "ambiguous_min_probability": 0.55,
                    "ambiguous_min_gap": 0.05,
                },
            },
        )
        assert_default_compute_usage(assignment, "celltype_assignment")
        resolved_types = [name for name in assignment["summary"]["cellCounts"] if name not in {"Unassigned", "Ambiguous"}]
        if len(resolved_types) < 3:
            raise AssertionError(f"Expected at least three assigned types, got {resolved_types}")

        workflow_state_path = output_folder / "00_config" / "windows_session_state.json"
        neighborhood_probe = engine.request("neighborhood", {"gridSizeUm": 24.0})
        assert_default_compute_usage(neighborhood_probe, "neighborhood")
        if int(neighborhood_probe["summary"]["clusterCount"]) < 1:
            raise AssertionError("Neighborhood analysis did not produce clusters.")
        neighborhood_labels = [str(value) for value in neighborhood_probe["clusterLabels"]]
        neighborhood_colors = {
            label: f"#{((index + 1) * 0x234567) % 0xFFFFFF:06X}"
            for index, label in enumerate(neighborhood_labels)
        }
        neighborhood_payload = {
            "gridSizeUm": 24.0,
            "clusterColors": neighborhood_colors,
            "displayClusters": [*reversed(neighborhood_labels), neighborhood_labels[-1]],
        }
        neighborhood = engine.request("neighborhood", neighborhood_payload)
        assert_default_compute_usage(neighborhood, "neighborhood")
        expected_neighborhood_parameters = {
            "gridSizeUm": 24.0,
            "clusterColors": neighborhood_colors,
            "displayClusters": list(reversed(neighborhood_labels)),
        }
        neighborhood_state = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        if neighborhood_state.get("analysisParameters") != {
            "neighborhood": expected_neighborhood_parameters,
        }:
            raise AssertionError(
                "Neighborhood settings were not persisted exactly: "
                f"{neighborhood_state.get('analysisParameters')}"
            )

        region_payload = {
            "selectedTypes": [resolved_types[0], resolved_types[0]],
            "closeUm": 9.0,
            "dilateUm": 6.0,
            "minAreaUm2": 0.0,
            "minCells": 1,
        }
        region = engine.request(
            "region",
            region_payload,
        )
        assert_default_compute_usage(region, "region")
        if not region["boundaries"]:
            raise AssertionError("Region analysis did not produce a boundary.")
        expected_region_parameters = {
            **region_payload,
            "selectedTypes": [resolved_types[0]],
            "contourDownsample": 2,
            "lineWidth": 2.0,
            "lineStyle": "--",
            "boundaryColor": "#A1D99B",
            "useTypeColors": False,
        }
        region_state = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        if region_state.get("analysisParameters") != {
            "neighborhood": expected_neighborhood_parameters,
            "region": expected_region_parameters,
        }:
            raise AssertionError(
                "Region settings did not preserve neighborhood settings: "
                f"{region_state.get('analysisParameters')}"
            )

        distribution_payload = {
            "boundaryLabel": region["boundaries"][0]["label"],
            "bandWidthUm": 10.0,
            "overlayChannels": ["DAPI", "DAPI"],
            "selectedCellTypes": [*resolved_types, resolved_types[0]],
        }
        distribution = engine.request(
            "cell_distribution",
            distribution_payload,
        )
        assert_default_compute_usage(distribution, "cell_distribution")
        if not distribution["artifacts"]:
            raise AssertionError("Cell distribution did not produce artifacts.")
        expected_distribution_parameters = {
            **distribution_payload,
            "overlayChannels": ["DAPI"],
            "selectedCellTypes": resolved_types,
        }
        distribution_state = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        if distribution_state.get("analysisParameters") != {
            "neighborhood": expected_neighborhood_parameters,
            "region": expected_region_parameters,
            "distribution": expected_distribution_parameters,
        }:
            raise AssertionError(
                "Distribution settings did not preserve upstream settings: "
                f"{distribution_state.get('analysisParameters')}"
            )

        nearest_distance_payload = {
            "mode": "nearest",
            "targetType": resolved_types[0],
            "queryTypes": [*resolved_types[1:], resolved_types[1]],
        }
        distance = engine.request(
            "distance",
            nearest_distance_payload,
        )
        assert_default_compute_usage(distance, "distance")
        if not distance["artifacts"]:
            raise AssertionError("Distance analysis did not produce artifacts.")

        nearest_state = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        expected_analysis_parameters = {
            "neighborhood": expected_neighborhood_parameters,
            "region": expected_region_parameters,
            "distribution": expected_distribution_parameters,
            "distance": {
                "nearest": {
                    **nearest_distance_payload,
                    "queryTypes": resolved_types[1:],
                },
                "lastMode": "nearest",
            },
        }
        if nearest_state.get("analysisParameters") != expected_analysis_parameters:
            raise AssertionError(
                "Nearest-distance settings were not persisted exactly: "
                f"{nearest_state.get('analysisParameters')}"
            )

        boundary_distance_payload = {
            "mode": "boundary",
            "targetType": resolved_types[0],
            "queryTypes": [*resolved_types[1:], resolved_types[1]],
            "boundaryLabel": region["boundaries"][0]["label"],
            "regionFilter": "all",
        }
        boundary_distance = engine.request("distance", boundary_distance_payload)
        assert_default_compute_usage(boundary_distance, "distance boundary")
        if not boundary_distance["artifacts"]:
            raise AssertionError("Boundary-distance analysis did not produce artifacts.")
        expected_analysis_parameters["distance"] = {
            "nearest": {
                **nearest_distance_payload,
                "queryTypes": resolved_types[1:],
            },
            "boundary": {
                **boundary_distance_payload,
                "queryTypes": resolved_types[1:],
            },
            "lastMode": "boundary",
        }
        completed_state = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        if completed_state.get("analysisParameters") != expected_analysis_parameters:
            raise AssertionError(
                "Boundary-distance settings did not preserve the nearest-distance settings: "
                f"{completed_state.get('analysisParameters')}"
            )

        outputs = engine.request("outputs", {})
        if len(outputs["files"]) < 12:
            raise AssertionError("Native output manifest is unexpectedly small.")

        state_before_invalid_requests = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        invalid_requests = (
            ("distance", {"mode": "invalid", "targetType": resolved_types[0], "queryTypes": resolved_types[1:]}),
            (
                "distance",
                {
                    "mode": "boundary",
                    "targetType": resolved_types[0],
                    "queryTypes": resolved_types[1:],
                    "boundaryLabel": "Unavailable boundary",
                },
            ),
            (
                "cell_distribution",
                {
                    "boundaryLabel": "Unavailable boundary",
                    "bandWidthUm": 10.0,
                    "selectedCellTypes": resolved_types,
                },
            ),
            (
                "region",
                {
                    **region_payload,
                    "minCells": 0,
                },
            ),
        )
        for command, invalid_payload in invalid_requests:
            try:
                engine.request(command, invalid_payload)
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"{command} accepted an invalid analysis payload: {invalid_payload}")
            state_after_invalid_request = json.loads(workflow_state_path.read_text(encoding="utf-8"))
            if state_after_invalid_request != state_before_invalid_requests:
                raise AssertionError(
                    f"Rejected {command} payload mutated persisted workflow state: {invalid_payload}"
                )

        if engine.max_protocol_line_bytes >= 2_000_000:
            raise AssertionError(f"Protocol line too large: {engine.max_protocol_line_bytes} bytes")
        serialized_outputs = json.dumps(outputs)
        if "data:image" in serialized_outputs or "base64" in serialized_outputs.lower():
            raise AssertionError("Native engine protocol contains an embedded data payload.")

        nuclei_grid = json.loads((output_folder / "02_nuclei_segmentation" / "nuclei_native_optimizer_grid.json").read_text(encoding="utf-8"))
        assignment_grid = json.loads((output_folder / "04_cell_type_assignment_parameters" / "celltype_assignment_native_optimizer_grid.json").read_text(encoding="utf-8"))
        expected_optimizer_workers = min(expected_cpu_count, 2)
        if int(nuclei_grid["parallel_config"]["parallel_workers"]) != expected_optimizer_workers:
            raise AssertionError("Nuclei optimizer did not default to the all-core worker budget.")
        if int(assignment_grid["parallel_config"]["parallel_workers"]) != expected_optimizer_workers:
            raise AssertionError("Assignment optimizer did not default to the all-core worker budget.")

        state_before_screening = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        workflow_before_screening = state_before_screening["stages"]
        if state_before_screening.get("analysisParameters") != expected_analysis_parameters:
            raise AssertionError("Completed analysis settings changed before parameter screening.")
        engine.request(
            "nuclei_optimizer",
            {
                "parameters": {"nucleus_channel": "DAPI", "min_diam_um": 6.0, "max_diam_um": 16.0},
                "maxEvaluations": 1,
                "parallelBackend": "threading",
                "useFixedRoiSubset": True,
            },
        )
        engine.request(
            "celltype_optimizer",
            {
                "cellTypes": cell_types(),
                "parameters": {"r_voronoi_um": 3.0, "r_buffer_um": 2.0, "r_vote_um": 3.0},
                "maxEvaluations": 1,
                "parallelBackend": "threading",
                "useFixedRoiSubset": True,
            },
        )
        state_after_screening = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        workflow_after_screening = state_after_screening["stages"]
        if workflow_after_screening != workflow_before_screening or not all(workflow_after_screening.values()):
            raise AssertionError(
                "Parameter screening changed completed workflow history before explicit Apply: "
                f"before={workflow_before_screening}, after={workflow_after_screening}"
            )
        if state_after_screening.get("analysisParameters") != expected_analysis_parameters:
            raise AssertionError("Parameter screening changed persisted downstream analysis settings.")

        for required in (
            output_folder / "02_nuclei_segmentation" / "nuclei_native_optimizer_recommendation.json",
            output_folder / "04_cell_type_assignment_parameters" / "celltype_assignment_native_optimizer_recommendation.json",
            output_folder / "05_cell_type_assignment" / "celltype_assignment_params.json",
        ):
            if not required.is_file():
                raise AssertionError(f"Missing resumable session state: {required}")

        restored_engine = EngineProcess(engine_command)
        try:
            restored = restored_engine.request("restore", {"outputFolder": str(output_folder)})
            if not restored.get("restored"):
                raise AssertionError("A fresh engine did not recognize the completed output folder.")
            if not all(bool(restored["workflow"].get(key)) for key in (
                "inputs", "overlay", "nuclei", "cellTypes", "neighborhood", "region", "distribution", "distance", "outputs"
            )):
                raise AssertionError(f"Restored workflow is incomplete: {restored['workflow']}")
            if not restored.get("nucleiRecommendation") or not restored.get("assignmentRecommendation"):
                raise AssertionError("Optimizer recommendations were not restored.")
            if restored.get("analysisParameters") != expected_analysis_parameters:
                raise AssertionError(
                    "Downstream analysis settings were not restored exactly: "
                    f"{restored.get('analysisParameters')}"
                )
            if len(restored.get("files", [])) < 12:
                raise AssertionError("Restored output manifest is unexpectedly small.")
            restored_distance = restored_engine.request(
                "distance",
                boundary_distance_payload,
            )
            if not restored_distance.get("artifacts"):
                raise AssertionError("A downstream workflow could not run from restored state.")
            restored_outputs = restored_engine.request("outputs", {})
            if len(restored_outputs.get("files", [])) < 12:
                raise AssertionError("Restored workflow could not refresh its final output manifest.")
        finally:
            restored_engine.close()

        with tempfile.TemporaryDirectory(prefix="spatialscope-history-guards-") as guard_value:
            guard_root = Path(guard_value)

            legacy_parameters_output = guard_root / "legacy-analysis-parameters"
            shutil.copytree(output_folder, legacy_parameters_output)
            legacy_state_path = legacy_parameters_output / "00_config" / "windows_session_state.json"
            legacy_state = json.loads(legacy_state_path.read_text(encoding="utf-8"))
            legacy_state.pop("analysisParameters", None)
            legacy_state_path.write_text(json.dumps(legacy_state, indent=2) + "\n", encoding="utf-8")
            legacy_engine = EngineProcess(engine_command)
            try:
                for attempt in range(2):
                    legacy_restore = legacy_engine.request(
                        "restore",
                        {"outputFolder": str(legacy_parameters_output)},
                    )
                    if not all(legacy_restore["workflow"].values()):
                        raise AssertionError(
                            f"Legacy analysis-parameter restore {attempt + 1} lost valid workflow history."
                        )
                    if legacy_restore.get("analysisParameters"):
                        raise AssertionError(
                            "Legacy restore manufactured downstream settings that were never persisted."
                        )
            finally:
                legacy_engine.close()

            invalid_parameters_output = guard_root / "invalid-analysis-parameters"
            shutil.copytree(output_folder, invalid_parameters_output)
            invalid_state_path = invalid_parameters_output / "00_config" / "windows_session_state.json"
            invalid_state = json.loads(invalid_state_path.read_text(encoding="utf-8"))
            invalid_state["analysisParameters"]["region"]["selectedTypes"] = ["Unavailable cell type"]
            invalid_state_path.write_text(json.dumps(invalid_state, indent=2) + "\n", encoding="utf-8")
            invalid_parameters_engine = EngineProcess(engine_command)
            try:
                invalid_restore = invalid_parameters_engine.request(
                    "restore",
                    {"outputFolder": str(invalid_parameters_output)},
                )
                invalid_analysis = invalid_restore.get("analysisParameters", {})
                if "region" in invalid_analysis:
                    raise AssertionError("Restore accepted unavailable region cell types from session state.")
                if invalid_analysis.get("neighborhood") != expected_neighborhood_parameters:
                    raise AssertionError("One invalid analysis entry discarded an unrelated valid entry.")
                if not any("region analysis parameters were ignored" in str(value) for value in invalid_restore.get("warnings", [])):
                    raise AssertionError("Restore did not report the rejected region analysis settings.")
            finally:
                invalid_parameters_engine.close()

            changed_definition_output = guard_root / "changed-definition"
            shutil.copytree(output_folder, changed_definition_output)
            changed_definitions = cell_types()
            changed_definitions[0] = {**changed_definitions[0], "color_hex": "#112233"}
            changed_engine = EngineProcess(engine_command)
            try:
                changed_restore = changed_engine.request(
                    "restore",
                    {"outputFolder": str(changed_definition_output)},
                )
                if not all(changed_restore["workflow"].values()):
                    raise AssertionError("Definition-change guard could not restore the completed source history.")
                changed_optimizer = changed_engine.request(
                    "celltype_optimizer",
                    {
                        "cellTypes": changed_definitions,
                        "parameters": {"r_voronoi_um": 3.0, "r_buffer_um": 2.0, "r_vote_um": 3.0},
                        "maxEvaluations": 1,
                        "parallelBackend": "threading",
                        "useFixedRoiSubset": True,
                    },
                )
                if not changed_optimizer["recommendedParameters"]:
                    raise AssertionError("Changed-definition optimizer did not return a recommendation.")
                changed_state = json.loads(
                    (changed_definition_output / "00_config" / "windows_session_state.json").read_text(encoding="utf-8")
                )
                expected_preserved = {"inputs", "overlay", "nuclei"}
                if any(
                    bool(value) != (stage in expected_preserved)
                    for stage, value in changed_state["stages"].items()
                ):
                    raise AssertionError(
                        "Changed cell type definitions did not invalidate the old assignment history: "
                        f"{changed_state['stages']}"
                    )
                if not changed_state.get("recommendations", {}).get("assignment"):
                    raise AssertionError("Changed-definition recommendation was not persisted.")
                if changed_state.get("analysisParameters"):
                    raise AssertionError("Changed cell type definitions retained downstream analysis settings.")
                try:
                    changed_engine.request("neighborhood", {"gridSizeUm": 24.0})
                except RuntimeError as error:
                    if "Run cell type assignment" not in str(error):
                        raise AssertionError(f"Changed-definition guard failed for the wrong reason: {error}") from error
                else:
                    raise AssertionError("Changed definitions left stale assignment data usable in engine memory.")
            finally:
                changed_engine.close()

            if not (changed_definition_output / "05_cell_type_assignment" / "cells_summary.csv").is_file():
                raise AssertionError("Changed-definition guard did not retain a stale assignment artifact to test filtering.")
            changed_restore_engine = EngineProcess(engine_command)
            try:
                changed_restored = changed_restore_engine.request(
                    "restore",
                    {"outputFolder": str(changed_definition_output)},
                )
                if any(
                    bool(changed_restored["workflow"].get(stage)) != (stage in {"inputs", "overlay", "nuclei"})
                    for stage in changed_restored["workflow"]
                ):
                    raise AssertionError(
                        "Changed-definition restore exposed invalidated workflow stages: "
                        f"{changed_restored['workflow']}"
                    )
                if changed_restored.get("resolvedCellTypes") or changed_restored.get("boundaries"):
                    raise AssertionError("Changed-definition restore exposed stale assignment-derived results.")
                if changed_restored.get("assignmentParameters"):
                    raise AssertionError("Changed-definition restore exposed stale final assignment parameters.")
                if changed_restored.get("analysisParameters"):
                    raise AssertionError("Changed-definition restore exposed stale downstream analysis settings.")
                stale_preview_keys = {"cellTypes", "neighborhood", "region", "distribution", "distance_nearest", "distance_boundary"}
                if stale_preview_keys.intersection(changed_restored.get("previewPaths", {})):
                    raise AssertionError("Changed-definition restore exposed stale final previews.")
                stale_subdirs = {
                    "05_cell_type_assignment",
                    "06_neighborhood_analysis",
                    "07_region_analysis",
                    "08_integrated_region_analysis",
                    "09_cell_distribution_analysis",
                    "10_distance_analysis",
                }
                returned_tops = {
                    str(record.get("relative_path") or "").replace("\\", "/").split("/", 1)[0]
                    for record in changed_restored.get("files", [])
                }
                if returned_tops.intersection(stale_subdirs):
                    raise AssertionError(f"Changed-definition restore listed stale final artifacts: {returned_tops}")
            finally:
                changed_restore_engine.close()

            nuclei_apply_output = guard_root / "nuclei-apply"
            shutil.copytree(output_folder, nuclei_apply_output)
            nuclei_apply_engine = EngineProcess(engine_command)
            try:
                nuclei_apply_restore = nuclei_apply_engine.request(
                    "restore",
                    {"outputFolder": str(nuclei_apply_output)},
                )
                nuclei_recommendation = dict(nuclei_apply_restore["nucleiRecommendation"])
                applied_nuclei_parameters = {"nucleus_channel": "DAPI", **nuclei_recommendation}
                applied_nuclei = nuclei_apply_engine.request(
                    "apply_recommendation",
                    {"kind": "nuclei", "parameters": applied_nuclei_parameters},
                )
                if any(
                    bool(applied_nuclei["workflow"].get(stage)) != (stage in {"inputs", "overlay"})
                    for stage in applied_nuclei["workflow"]
                ):
                    raise AssertionError(f"Applying nuclei parameters did not invalidate downstream stages: {applied_nuclei['workflow']}")
                nuclei_applied_state = json.loads(
                    (nuclei_apply_output / "00_config" / "windows_session_state.json").read_text(encoding="utf-8")
                )
                if nuclei_applied_state.get("analysisParameters"):
                    raise AssertionError("Applied nuclei parameters retained downstream analysis settings.")
                try:
                    nuclei_apply_engine.request("outputs", {})
                except RuntimeError as error:
                    if "Complete all analysis steps" not in str(error):
                        raise AssertionError(f"Incomplete outputs guard failed for the wrong reason: {error}") from error
                else:
                    raise AssertionError("Outputs accepted a workflow invalidated by applied nuclei parameters.")
            finally:
                nuclei_apply_engine.close()

            nuclei_apply_restore_engine = EngineProcess(engine_command)
            try:
                nuclei_applied_restore = nuclei_apply_restore_engine.request(
                    "restore",
                    {"outputFolder": str(nuclei_apply_output)},
                )
                if nuclei_applied_restore.get("nucleiRecommendation") or nuclei_applied_restore.get("assignmentRecommendation"):
                    raise AssertionError("Applied nuclei recommendation remained pending after restart.")
                for key, expected in applied_nuclei_parameters.items():
                    actual = nuclei_applied_restore["nucleiParameters"].get(key)
                    if actual != expected:
                        raise AssertionError(f"Applied nuclei parameter was not restored: {key}={actual!r}, expected {expected!r}")
                if set(nuclei_applied_restore.get("previewPaths", {})) - {"overlay", "split"}:
                    raise AssertionError("Applied nuclei parameters exposed stale downstream previews after restart.")
            finally:
                nuclei_apply_restore_engine.close()

            assignment_apply_output = guard_root / "assignment-apply"
            shutil.copytree(output_folder, assignment_apply_output)
            assignment_apply_engine = EngineProcess(engine_command)
            try:
                assignment_apply_restore = assignment_apply_engine.request(
                    "restore",
                    {"outputFolder": str(assignment_apply_output)},
                )
                assignment_recommendation = dict(assignment_apply_restore["assignmentRecommendation"])
                applied_assignment_parameters = dict(assignment_apply_restore["assignmentParameters"])
                applied_assignment_parameters.update(assignment_recommendation)
                applied_assignment = assignment_apply_engine.request(
                    "apply_recommendation",
                    {"kind": "assignment", "parameters": applied_assignment_parameters},
                )
                if any(
                    bool(applied_assignment["workflow"].get(stage)) != (stage in {"inputs", "overlay", "nuclei"})
                    for stage in applied_assignment["workflow"]
                ):
                    raise AssertionError(
                        "Applying assignment parameters did not invalidate downstream stages: "
                        f"{applied_assignment['workflow']}"
                    )
                assignment_applied_state = json.loads(
                    (assignment_apply_output / "00_config" / "windows_session_state.json").read_text(encoding="utf-8")
                )
                if assignment_applied_state.get("analysisParameters"):
                    raise AssertionError("Applied assignment parameters retained downstream analysis settings.")
            finally:
                assignment_apply_engine.close()

            assignment_apply_restore_engine = EngineProcess(engine_command)
            try:
                assignment_applied_restore = assignment_apply_restore_engine.request(
                    "restore",
                    {"outputFolder": str(assignment_apply_output)},
                )
                if assignment_applied_restore.get("assignmentRecommendation"):
                    raise AssertionError("Applied assignment recommendation remained pending after restart.")
                for key, expected in applied_assignment_parameters.items():
                    actual = assignment_applied_restore["assignmentParameters"].get(key)
                    if actual != expected:
                        raise AssertionError(f"Applied assignment parameter was not restored: {key}={actual!r}, expected {expected!r}")
                if not assignment_applied_restore.get("cellTypes"):
                    raise AssertionError("Applied assignment parameters lost the saved cell type definitions.")
                if assignment_applied_restore.get("resolvedCellTypes") or assignment_applied_restore.get("boundaries"):
                    raise AssertionError("Applied assignment parameters exposed stale assignment-derived results.")
            finally:
                assignment_apply_restore_engine.close()

            reconfigured_output = guard_root / "reconfigured"
            shutil.copytree(output_folder, reconfigured_output)
            if not (reconfigured_output / "05_cell_type_assignment" / "cells_summary.csv").is_file():
                raise AssertionError("Reconfigure guard did not retain a stale assignment artifact to test filtering.")
            configure_engine = EngineProcess(engine_command)
            try:
                configure_engine.request(
                    "configure",
                    {
                        "inputFolder": str(input_folder),
                        "outputFolder": str(reconfigured_output),
                        "pixelSizeUm": [1.25, 1.25],
                        "imageId": "NativeSmokeReconfigured",
                        "whiteChannel": "DAPI",
                        "whiteWeight": 0.25,
                    },
                )
                try:
                    configure_engine.request("outputs", {})
                except RuntimeError as error:
                    if "Complete all analysis steps" not in str(error):
                        raise AssertionError(f"Reconfigured outputs guard failed for the wrong reason: {error}") from error
                else:
                    raise AssertionError("Outputs accepted an incomplete reconfigured workflow.")
                configured_state = json.loads(
                    (reconfigured_output / "00_config" / "windows_session_state.json").read_text(encoding="utf-8")
                )
                expected_configured_state = {
                    stage: stage == "inputs"
                    for stage in configured_state["stages"]
                }
                if configured_state["stages"] != expected_configured_state:
                    raise AssertionError(f"Rejected outputs corrupted workflow history: {configured_state['stages']}")
            finally:
                configure_engine.close()

            reconfigured_engine = EngineProcess(engine_command)
            try:
                reconfigured = reconfigured_engine.request(
                    "restore",
                    {"outputFolder": str(reconfigured_output)},
                )
                expected_reconfigured = {stage: stage == "inputs" for stage in reconfigured["workflow"]}
                if reconfigured["workflow"] != expected_reconfigured:
                    raise AssertionError(
                        "Reconfigured output mixed stale workflow history into the new session: "
                        f"{reconfigured['workflow']}"
                    )
                for key in (
                    "previewPaths",
                    "nucleiParameters",
                    "assignmentParameters",
                    "nucleiRecommendation",
                    "assignmentRecommendation",
                    "analysisParameters",
                    "cellTypes",
                    "resolvedCellTypes",
                    "boundaries",
                ):
                    if reconfigured.get(key):
                        raise AssertionError(f"Reconfigured output exposed stale {key}: {reconfigured[key]}")
                returned_file_tops = {
                    str(record.get("relative_path") or "").replace("\\", "/").split("/", 1)[0]
                    for record in reconfigured.get("files", [])
                }
                returned_artifact_tops = {
                    str(record.get("relativePath") or "").replace("\\", "/").split("/", 1)[0]
                    for record in reconfigured.get("artifacts", [])
                }
                if returned_file_tops != {"00_config"} or returned_artifact_tops != {"00_config"}:
                    raise AssertionError(
                        "Reconfigured output listed stale analysis files: "
                        f"files={returned_file_tops}, artifacts={returned_artifact_tops}"
                    )
            finally:
                reconfigured_engine.close()

        report = {
            "status": "passed",
            "n_nuclei": n_nuclei,
            "resolved_cell_types": resolved_types,
            "neighborhood_clusters": neighborhood["summary"]["clusterCount"],
            "optimizer_checks": 5,
            "restore_checks": 9,
            "apply_checks": 2,
            "output_guard_checks": 2,
            "output_files": len(outputs["files"]),
            "max_protocol_line_bytes": engine.max_protocol_line_bytes,
            "output_folder": str(output_folder),
        }
        (output_folder / "native_engine_smoke_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        engine.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise every native SpatialScope engine workflow through JSON Lines.")
    parser.add_argument("--python", type=Path, default=WINDOWS_DIR / ".venv" / "Scripts" / "python.exe")
    parser.add_argument(
        "--engine-executable",
        type=Path,
        help="Run a frozen SpatialScopeEngine executable instead of native_engine.py.",
    )
    parser.add_argument("--input-folder", type=Path, default=WINDOWS_DIR / "build" / "smoke-output" / "synthetic_input")
    parser.add_argument("--output-folder", type=Path, default=WINDOWS_DIR / "build" / "native-engine-smoke")
    args = parser.parse_args()
    engine_command = (
        [str(args.engine_executable.resolve())]
        if args.engine_executable is not None
        else [str(args.python.resolve()), str(ENGINE_SCRIPT)]
    )
    report = run_smoke(engine_command, args.input_folder.resolve(), args.output_folder.resolve())
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
