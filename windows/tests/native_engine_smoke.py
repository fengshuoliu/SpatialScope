from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from PIL import Image


WINDOWS_DIR = Path(__file__).resolve().parents[1]
ENGINE_SCRIPT = WINDOWS_DIR / "backend" / "native_engine.py"
BACKEND_SRC = WINDOWS_DIR / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from spatialscope_analysis.models import RegionParams  # noqa: E402


def _assert_png_backed_svg(png_path: Path, svg_path: Path) -> None:
    if not png_path.is_file() or not svg_path.is_file():
        raise AssertionError(f"Missing Region PNG/SVG export pair: {png_path}, {svg_path}")
    svg_text = svg_path.read_text(encoding="utf-8")
    match = re.search(r'href="data:image/png;base64,([^"]+)"', svg_text)
    if match is None or "<svg" not in svg_text:
        raise AssertionError(f"Region SVG does not contain an embedded PNG image: {svg_path}")
    try:
        embedded_png = base64.b64decode(match.group(1), validate=True)
    except ValueError as error:
        raise AssertionError(f"Region SVG contains invalid base64 image data: {svg_path}") from error
    if embedded_png != png_path.read_bytes():
        raise AssertionError(f"Region SVG does not preserve the exact PNG rendering: {svg_path}")


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

    if RegionParams(selected_types=[]).line_style != "-":
        raise AssertionError("The shared Region parameter model does not default to a solid boundary line.")

    engine = EngineProcess(engine_command)
    try:
        hello = engine.request("hello", {})
        if hello["protocolVersion"] != 1:
            raise AssertionError(f"Unexpected engine protocol: {hello}")
        required_region_capabilities = {
            "region",
            "region_preview",
            "region_manual_preview",
            "region_manual_save",
            "region_custom_export",
        }
        missing_region_capabilities = required_region_capabilities.difference(hello.get("capabilities", []))
        if missing_region_capabilities:
            raise AssertionError(f"Engine is missing Region capabilities: {sorted(missing_region_capabilities)}")
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
                "fixedParameterKeys": ["min_diam_um", "max_diam_um"],
                "maxEvaluations": 2,
                "parallelBackend": "threading",
                "useFixedRoiSubset": True,
            },
        )
        assert_default_compute_usage(nuclei_optimizer, "nuclei_optimizer")
        if not nuclei_optimizer["recommendedParameters"]:
            raise AssertionError("Nuclei optimizer did not return a recommendation.")
        if nuclei_optimizer.get("fixedParameters") != {"min_diam_um": 6.0, "max_diam_um": 16.0}:
            raise AssertionError(f"Nuclei optimizer did not preserve its fixed diameters: {nuclei_optimizer}")
        if any(
            float(nuclei_optimizer["recommendedParameters"].get(key, -1)) != expected
            for key, expected in (("min_diam_um", 6.0), ("max_diam_um", 16.0))
        ):
            raise AssertionError(f"Nuclei recommendation changed a fixed diameter: {nuclei_optimizer}")

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
                "fixedParameterKeys": ["r_voronoi_um"],
                "maxEvaluations": 2,
                "parallelBackend": "threading",
                "useFixedRoiSubset": True,
            },
        )
        assert_default_compute_usage(assignment_optimizer, "celltype_optimizer")
        if not assignment_optimizer["recommendedParameters"]:
            raise AssertionError("Assignment optimizer did not return a recommendation.")
        if assignment_optimizer.get("fixedParameters") != {"r_voronoi_um": 3.0}:
            raise AssertionError(f"Assignment optimizer did not preserve its fixed radius: {assignment_optimizer}")
        if float(assignment_optimizer["recommendedParameters"].get("r_voronoi_um", -1)) != 3.0:
            raise AssertionError(f"Assignment recommendation changed a fixed radius: {assignment_optimizer}")

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
        neighborhood_preview_path = Path(str(neighborhood.get("previewPath") or ""))
        neighborhood_legend_path = Path(str(neighborhood.get("legendPreviewPath") or ""))
        if neighborhood_preview_path.name != "neighborhood_map.png" or not neighborhood_preview_path.is_file():
            raise AssertionError(f"Neighborhood response omitted the separate map preview: {neighborhood}")
        if neighborhood_legend_path.name != "neighborhood_cluster_key.png" or not neighborhood_legend_path.is_file():
            raise AssertionError(f"Neighborhood response omitted the separate cluster key preview: {neighborhood}")
        if neighborhood_preview_path == neighborhood_legend_path:
            raise AssertionError("Neighborhood map and cluster key previews resolved to the same file.")
        neighborhood_key_path = neighborhood_preview_path.parent / "neighborhood_cluster_key.csv"
        with neighborhood_key_path.open(newline="", encoding="utf-8") as key_file:
            neighborhood_key_rows = list(csv.DictReader(key_file))
        expected_key_columns = [
            "number",
            "cluster_id",
            "cluster_key",
            "cluster_label",
            "tile_count",
            "cell_count",
            "tile_fraction",
            "color_hex",
        ]
        if not neighborhood_key_rows or list(neighborhood_key_rows[0]) != expected_key_columns:
            raise AssertionError(f"Neighborhood cluster key has the wrong schema: {neighborhood_key_rows}")
        if any(int(row["number"]) != int(row["cluster_id"]) for row in neighborhood_key_rows):
            raise AssertionError("Neighborhood cluster numbers do not map directly to cluster IDs.")
        if {row["cluster_label"] for row in neighborhood_key_rows} != set(neighborhood_labels):
            raise AssertionError("Neighborhood cluster key omitted a displayed cluster label.")
        if any(
            row["color_hex"].lower() != neighborhood_colors[row["cluster_label"]].lower()
            for row in neighborhood_key_rows
        ):
            raise AssertionError("Neighborhood cluster key colors do not match the requested map colors.")
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
            "lineStyle": "-",
            "boundaryColor": "#A1D99B",
            "useTypeColors": False,
        }
        if region.get("parameters") != expected_region_parameters:
            raise AssertionError(f"Region response did not normalize all ten parameters: {region.get('parameters')}")
        if int(region.get("width", 0)) <= 0 or int(region.get("height", 0)) <= 0:
            raise AssertionError(f"Region response did not include source dimensions: {region}")
        source_width = int(region["width"])
        source_height = int(region["height"])
        for preview_key in ("overlayPreviewPath", "maskPreviewPath"):
            preview_path = Path(str(region.get(preview_key) or ""))
            if not preview_path.is_file():
                raise AssertionError(f"Region response omitted {preview_key}: {region}")
            with Image.open(preview_path) as preview_image:
                if preview_image.size != (source_width, source_height):
                    raise AssertionError(f"{preview_key} is not source-pixel sized: {preview_image.size}")
        with Image.open(Path(region["comparisonPreviewPath"])) as comparison_image:
            if comparison_image.size != (source_width * 2 + 24, source_height):
                raise AssertionError(f"Region comparison preview has the wrong panel geometry: {comparison_image.size}")
        with Image.open(Path(region["overlayPreviewPath"])) as overlay_panel, Image.open(
            Path(region["maskPreviewPath"])
        ) as mask_panel:
            if overlay_panel.convert("RGB").tobytes() == mask_panel.convert("RGB").tobytes():
                raise AssertionError("Region comparison reused the cell-type mask instead of a multiplex overlay.")
        if region.get("previewPath") != region.get("comparisonPreviewPath"):
            raise AssertionError("The main Region preview is not the overlay/mask comparison.")
        expected_region_row_keys = {
            "id",
            "label",
            "sourceType",
            "dominantType",
            "cellCount",
            "areaUm2",
            "colorHex",
            "countsByType",
        }
        if len(region.get("regions", [])) != len(region["boundaries"]):
            raise AssertionError("Region rows and selectable boundaries are out of sync.")
        for row in region["regions"]:
            if set(row) != expected_region_row_keys:
                raise AssertionError(f"Region row has an unexpected schema: {row}")
            if not str(row["id"]).startswith("roi_") or len(str(row["id"])) != 20:
                raise AssertionError(f"Region row has no stable ROI id: {row}")
            if int(row["cellCount"]) <= 0 or float(row["areaUm2"]) <= 0:
                raise AssertionError(f"Region response exposed an empty boundary: {row}")
            if not set(row["countsByType"]).issubset(resolved_types):
                raise AssertionError(f"Region row contains unavailable cell types: {row}")
        for item in region.get("dominantCounts", []):
            if set(item) != {"name", "count", "colorHex"}:
                raise AssertionError(f"Dominant-count row has an unexpected schema: {item}")
        for boundary in region["boundaries"]:
            if set(boundary) != {"id", "label", "path", "sourceType"}:
                raise AssertionError(f"Region boundary has an unexpected schema: {boundary}")
            boundary_path = Path(boundary["path"])
            if not boundary_path.is_file():
                raise AssertionError(f"Region boundary mask is missing: {boundary_path}")
            with Image.open(boundary_path) as boundary_image:
                if boundary_image.getbbox() is None:
                    raise AssertionError(f"Region response exposed an empty boundary mask: {boundary_path}")
        region_state = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        if region_state.get("analysisParameters") != {
            "neighborhood": expected_neighborhood_parameters,
            "region": expected_region_parameters,
        }:
            raise AssertionError(
                "Region settings did not preserve neighborhood settings: "
                f"{region_state.get('analysisParameters')}"
            )

        base_region_ids = {str(row["id"]) for row in region["regions"]}
        filtered_preview_payload = {
            "selectedBoundaryIds": [region["regions"][0]["id"]],
            "selectedCellTypes": resolved_types[:2],
            "previewKey": "display-panel",
            "lineWidth": 3.0,
            "lineStyle": "-.",
            "boundaryColor": "#FF8800",
            "useTypeColors": False,
        }
        state_before_region_preview = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        filtered_preview = engine.request("region_preview", filtered_preview_payload)
        if (int(filtered_preview["width"]), int(filtered_preview["height"])) != (
            int(region["width"]),
            int(region["height"]),
        ):
            raise AssertionError(f"Filtered Region preview changed the source aspect: {filtered_preview}")
        if filtered_preview.get("boundaryCellTypeMode") != "content" or int(filtered_preview.get("boundaryCount", 0)) != 1:
            raise AssertionError(f"Filtered Region preview did not return its filtered boundary rows: {filtered_preview}")
        global_selected_cell_count = sum(
            int(assignment["summary"]["cellCounts"].get(cell_type, 0))
            for cell_type in resolved_types[:2]
        )
        expected_filtered_cell_count = sum(
            int(row.get("countsByType", {}).get(cell_type, 0))
            for row in filtered_preview.get("regions", [])
            for cell_type in resolved_types[:2]
        )
        if expected_filtered_cell_count >= global_selected_cell_count:
            raise AssertionError(
                "Synthetic Region preview no longer distinguishes in-ROI cells from the whole-image selected-cell count."
            )
        if int(filtered_preview.get("cellCount", -1)) != expected_filtered_cell_count or int(
            filtered_preview.get("summary", {}).get("cellCount", -1)
        ) != expected_filtered_cell_count:
            raise AssertionError(
                "Filtered Region preview did not count selected cell types within the displayed ROI: "
                f"expected {expected_filtered_cell_count}, global {global_selected_cell_count}, "
                f"response {filtered_preview}"
            )
        for preview_key in ("overlayPreviewPath", "maskPreviewPath"):
            with Image.open(Path(filtered_preview[preview_key])) as preview_image:
                if preview_image.size != (source_width, source_height):
                    raise AssertionError(f"Filtered {preview_key} is not source-pixel sized: {preview_image.size}")
        with Image.open(Path(filtered_preview["comparisonPreviewPath"])) as comparison_image:
            if comparison_image.size != (source_width * 2 + 24, source_height):
                raise AssertionError(f"Filtered Region comparison has the wrong geometry: {comparison_image.size}")
        second_filtered_preview = engine.request(
            "region_preview",
            {**filtered_preview_payload, "previewKey": "customize-panel"},
        )
        if second_filtered_preview["previewPath"] == filtered_preview["previewPath"]:
            raise AssertionError("Distinct Region preview keys reused the same cache path.")
        manual_editor_payload = {
            **filtered_preview_payload,
            "selectedCellTypes": [resolved_types[0]],
            "previewKey": "manual_editor",
            "boundaryCellTypeMode": "source",
        }
        first_manual_editor_preview = engine.request("region_preview", manual_editor_payload)
        manual_editor_path = Path(first_manual_editor_preview["previewPath"])
        first_manual_editor_bytes = manual_editor_path.read_bytes()
        second_manual_editor_preview = engine.request(
            "region_preview",
            {**manual_editor_payload, "selectedCellTypes": [resolved_types[1]]},
        )
        second_manual_editor_path = Path(second_manual_editor_preview["previewPath"])
        if second_manual_editor_path != manual_editor_path:
            raise AssertionError("Manual editor selections did not reuse their stable preview path.")
        if second_manual_editor_path.read_bytes() == first_manual_editor_bytes:
            raise AssertionError(
                "Manual editor preview pixels did not change after selecting a different displayed cell type."
            )
        if json.loads(workflow_state_path.read_text(encoding="utf-8")) != state_before_region_preview:
            raise AssertionError("Read-only Region preview changed persisted workflow state.")

        cells_summary_path = output_folder / "05_cell_type_assignment" / "cells_summary.csv"
        with cells_summary_path.open("r", encoding="utf-8", newline="") as handle:
            cell_rows = list(csv.DictReader(handle))
        rows_by_type = {
            cell_type: [row for row in cell_rows if str(row.get("celltype")) == cell_type]
            for cell_type in resolved_types
        }
        manual_seed_type = max(resolved_types, key=lambda cell_type: len(rows_by_type[cell_type]))
        manual_secondary_type = next(cell_type for cell_type in resolved_types if cell_type != manual_seed_type)
        if len(rows_by_type[manual_seed_type]) < 2:
            raise AssertionError("Synthetic assignment does not contain enough cells for manual Region editing.")
        full_source_polygon = [
            {"x": 0.0, "y": 0.0},
            {"x": float(source_width - 1), "y": 0.0},
            {"x": float(source_width - 1), "y": float(source_height - 1)},
            {"x": 0.0, "y": float(source_height - 1)},
        ]
        manual_create_payload = {
            "mode": "create",
            "displayName": "Smoke manual ROI",
            "polygons": [full_source_polygon],
            "seedCellTypes": [manual_seed_type, manual_secondary_type],
            "selectedCellTypes": resolved_types,
            "closeUm": 0.0,
            "dilateUm": 0.0,
            "minAreaUm2": 0.0,
            "minCells": 1,
            "contourDownsample": 1,
            "lineWidth": 3.0,
            "lineStyle": "-.",
            "boundaryColor": "#FF8800",
            "useTypeColors": False,
            "previewKey": "manual-create",
        }
        state_before_manual_preview = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        manual_preview = engine.request("region_manual_preview", manual_create_payload)
        if manual_preview.get("summary", {}).get("region", {}).get("sourceType") != "manual":
            raise AssertionError(f"Manual create preview did not return a manual Region row: {manual_preview}")
        with Image.open(Path(manual_preview["previewPath"])) as preview_image:
            if preview_image.size != (source_width, source_height):
                raise AssertionError(f"Manual Region preview is not source-pixel sized: {preview_image.size}")
        if json.loads(workflow_state_path.read_text(encoding="utf-8")) != state_before_manual_preview:
            raise AssertionError("Manual Region preview changed persisted workflow state.")

        manual_save = engine.request("region_manual_save", manual_create_payload)
        manual_region_id = str(manual_save["summary"]["savedRegionId"])
        manual_region_label = str(manual_save["summary"]["savedRegionLabel"])
        saved_region_ids = {str(row["id"]) for row in manual_save["regions"]}
        if not base_region_ids.issubset(saved_region_ids) or manual_region_id not in saved_region_ids:
            raise AssertionError(f"Manual save did not preserve base boundaries and add the new ROI: {manual_save}")
        saved_manual_row = next(row for row in manual_save["regions"] if str(row["id"]) == manual_region_id)
        if saved_manual_row["sourceType"] != "manual" or saved_manual_row["label"] != manual_region_label:
            raise AssertionError(f"Manual save returned the wrong Region metadata: {saved_manual_row}")
        if int(saved_manual_row["countsByType"].get(manual_secondary_type, 0)) <= 0:
            raise AssertionError(f"Manual multi-type ROI did not retain its secondary content: {saved_manual_row}")
        if not manual_save.get("artifacts") or not all(
            Path(record["absolutePath"]).is_file() for record in manual_save["artifacts"]
        ):
            raise AssertionError("Manual Region save did not return durable artifacts.")
        manual_saved_state = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        expected_completed_through_region = {"inputs", "overlay", "nuclei", "cellTypes", "neighborhood", "region"}
        if any(
            bool(value) != (stage in expected_completed_through_region)
            for stage, value in manual_saved_state["stages"].items()
        ):
            raise AssertionError(f"Manual Region save did not invalidate only downstream stages: {manual_saved_state['stages']}")
        if manual_saved_state.get("analysisParameters") != {
            "neighborhood": expected_neighborhood_parameters,
            "region": expected_region_parameters,
        }:
            raise AssertionError("Manual Region save changed the applied computational Region parameters.")

        manual_type_color_preview = engine.request(
            "region_preview",
            {
                "selectedBoundaryIds": [manual_region_id],
                "selectedCellTypes": [manual_seed_type],
                "boundaryCellTypeMode": "source",
                "previewKey": "manual-type-color",
                "lineWidth": 1.0,
                "lineStyle": "-",
                "boundaryColor": "#010203",
                "useTypeColors": True,
            },
        )
        expected_manual_color_hex = next(
            item["color_hex"] for item in cell_types() if item["name"] == manual_seed_type
        )
        expected_manual_rgb = tuple(
            int(expected_manual_color_hex[index : index + 2], 16)
            for index in (1, 3, 5)
        )
        manual_boundary_path = Path(
            next(item["path"] for item in manual_save["boundaries"] if str(item["id"]) == manual_region_id)
        )
        with Image.open(manual_boundary_path) as boundary_image, Image.open(
            Path(manual_type_color_preview["overlayPreviewPath"])
        ) as overlay_image:
            boundary_pixels = list(boundary_image.convert("L").getdata())
            overlay_pixels = list(overlay_image.convert("RGB").getdata())
            boundary_width, boundary_height = boundary_image.size
            found_preferred_color = False
            for y in range(1, boundary_height - 1):
                for x in range(1, boundary_width - 1):
                    offset = y * boundary_width + x
                    if boundary_pixels[offset] <= 0:
                        continue
                    if all(
                        boundary_pixels[neighbor] > 0
                        for neighbor in (offset - 1, offset + 1, offset - boundary_width, offset + boundary_width)
                    ):
                        continue
                    if overlay_pixels[offset] == expected_manual_rgb:
                        found_preferred_color = True
                        break
                if found_preferred_color:
                    break
            if not found_preferred_color:
                raise AssertionError(
                    "useTypeColors did not color the manual ROI boundary from its preferred seed cell type."
                )

        content_filtered_preview = engine.request(
            "region_preview",
            {
                "selectedBoundaryIds": [manual_region_id],
                "selectedCellTypes": [manual_secondary_type],
                "boundaryCellTypeMode": "content",
                "previewKey": "manual-content-filter",
            },
        )
        source_filtered_preview = engine.request(
            "region_preview",
            {
                "selectedBoundaryIds": [manual_region_id],
                "selectedCellTypes": [manual_secondary_type],
                "boundaryCellTypeMode": "source",
                "previewKey": "manual-source-filter",
            },
        )
        if int(content_filtered_preview.get("boundaryCount", 0)) != 1:
            raise AssertionError(f"Content-mode filtering ignored positive Region composition: {content_filtered_preview}")
        if int(source_filtered_preview.get("boundaryCount", -1)) != 0:
            raise AssertionError(f"Source-mode filtering did not use the preferred defining type: {source_filtered_preview}")
        if source_filtered_preview.get("previewPath") != source_filtered_preview.get("maskPreviewPath"):
            raise AssertionError("Source-mode Region preview is not the exact single-panel manual-editor raster.")
        with Image.open(Path(source_filtered_preview["previewPath"])) as preview_image:
            if preview_image.size != (source_width, source_height):
                raise AssertionError(f"Source-mode manual editor preview changed source coordinates: {preview_image.size}")

        include_preview = engine.request(
            "region_manual_preview",
            {
                **manual_create_payload,
                "mode": "include",
                "displayName": "Smoke adjusted include",
                "targetBoundaryLabel": region["boundaries"][0]["label"],
                "seedCellTypes": [resolved_types[1]],
                "selectedCellTypes": resolved_types[:2],
                "previewKey": "manual-include",
            },
        )
        if include_preview.get("summary", {}).get("mode") != "include":
            raise AssertionError(f"Manual include preview did not run include mode: {include_preview}")

        excluded_row = rows_by_type[manual_seed_type][0]
        excluded_x = int(round(float(excluded_row["centroid_x_px"])))
        excluded_y = int(round(float(excluded_row["centroid_y_px"])))
        local_polygon = [
            {"x": float(excluded_x - 2), "y": float(excluded_y - 2)},
            {"x": float(excluded_x + 2), "y": float(excluded_y - 2)},
            {"x": float(excluded_x + 2), "y": float(excluded_y + 2)},
            {"x": float(excluded_x - 2), "y": float(excluded_y + 2)},
        ]
        exclude_preview = engine.request(
            "region_manual_preview",
            {
                **manual_create_payload,
                "mode": "exclude",
                "displayName": "Smoke adjusted exclude",
                "targetBoundaryLabel": manual_region_label,
                "polygons": [local_polygon],
                "seedCellTypes": [manual_seed_type],
                "selectedCellTypes": [manual_seed_type],
                "previewKey": "manual-exclude",
            },
        )
        exclude_summary = exclude_preview.get("summary", {})
        if exclude_summary.get("mode") != "exclude" or not (
            0 < int(exclude_summary.get("resultSeedCellCount", 0)) < int(exclude_summary.get("baseCellCount", 0))
        ):
            raise AssertionError(f"Manual exclude preview did not remove a target seed cell: {exclude_preview}")
        if json.loads(workflow_state_path.read_text(encoding="utf-8")) != manual_saved_state:
            raise AssertionError("Manual include/exclude previews changed persisted workflow state.")

        custom_export = engine.request(
            "region_custom_export",
            {
                "selectedBoundaryIds": [manual_region_id],
                "selectedCellTypes": [manual_secondary_type],
                "boundaryCellTypeMode": "content",
                "lineWidth": 4.0,
                "lineStyle": ":",
                "boundaryColor": "#22CCFF",
                "useTypeColors": False,
                "contourDownsample": 1,
            },
        )
        if int(custom_export.get("boundaryCount", 0)) != 1 or custom_export.get("boundaryCellTypeMode") != "content":
            raise AssertionError(f"Customized export did not apply content-mode boundary filtering: {custom_export}")
        for preview_key in (
            "overlayPreviewPath",
            "maskPreviewPath",
            "originalOverlayPreviewPath",
            "originalMaskPreviewPath",
        ):
            with Image.open(Path(custom_export[preview_key])) as preview_image:
                if preview_image.size != (source_width, source_height):
                    raise AssertionError(f"Customized {preview_key} is not source-pixel sized: {preview_image.size}")
        for preview_key in ("comparisonPreviewPath", "originalComparisonPreviewPath"):
            with Image.open(Path(custom_export[preview_key])) as preview_image:
                if preview_image.size != (source_width * 2 + 24, source_height):
                    raise AssertionError(f"Customized {preview_key} has the wrong comparison geometry: {preview_image.size}")
        custom_export_paths = {
            str(record.get("relativePath") or "").replace("\\", "/")
            for record in custom_export.get("artifacts", [])
        }
        if not any("/01_original_unmodified/" in f"/{path}" for path in custom_export_paths) or not any(
            "/02_customized_display/" in f"/{path}" for path in custom_export_paths
        ):
            raise AssertionError(f"Custom Region export omitted original or customized artifacts: {custom_export_paths}")
        for comparison_key in ("comparisonPreviewPath", "originalComparisonPreviewPath"):
            primary_png = Path(custom_export[comparison_key])
            for suffix in (".png", ".tiff", ".json"):
                required = primary_png.with_suffix(suffix)
                if not required.is_file():
                    raise AssertionError(f"Custom Region export omitted {required.name}")
            _assert_png_backed_svg(primary_png, primary_png.with_suffix(".svg"))
        if any(Path(path).suffix.lower() == ".ai" for path in custom_export_paths):
            raise AssertionError("Windows Region export claimed an unsupported Adobe Illustrator artifact.")
        if json.loads(workflow_state_path.read_text(encoding="utf-8")) != manual_saved_state:
            raise AssertionError("Custom Region export changed persisted workflow state.")

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
            "queryTypes": [*resolved_types[1:], resolved_types[1]],
            "boundaryLabel": region["boundaries"][0]["label"],
            "regionFilter": "all",
        }
        boundary_distance = engine.request("distance", boundary_distance_payload)
        assert_default_compute_usage(boundary_distance, "distance boundary")
        if "targetType" in boundary_distance.get("summary", {}):
            raise AssertionError("Boundary-distance response still exposes an unused targetType.")
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

        visible_paths = [
            str(item.get("relative_path") or item.get("relativePath") or "")
            for item in outputs["files"]
        ]
        opaque_names = [
            path
            for path in visible_paths
            if re.search(r"(?i)(?:^|[_-])[0-9a-f]{12,16}(?:[_\-.]|$)", Path(path).name)
        ]
        if opaque_names:
            raise AssertionError(f"Results exposed opaque identity hashes: {opaque_names}")
        if any("previews/" in path.replace("\\", "/") for path in visible_paths):
            raise AssertionError("Results exposed transient Region preview-cache files.")
        expected_distance_names = {
            "nearest_neighbor_distances.csv",
            "nearest_neighbor_distances.png",
            "nearest_neighbor_distances.svg",
            "cell_to_boundary_distances.csv",
            "cell_to_boundary_distances.png",
            "cell_to_boundary_distances.svg",
        }
        visible_names = {Path(path).name for path in visible_paths}
        missing_distance_names = sorted(expected_distance_names - visible_names)
        if missing_distance_names:
            raise AssertionError(f"Readable distance filenames are missing: {missing_distance_names}")

        state_before_invalid_requests = json.loads(workflow_state_path.read_text(encoding="utf-8"))
        invalid_requests = (
            ("distance", {"mode": "invalid", "targetType": resolved_types[0], "queryTypes": resolved_types[1:]}),
            (
                "distance",
                {
                    "mode": "boundary",
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
            ("region", {**region_payload, "closeUm": 80.01}),
            ("region", {**region_payload, "dilateUm": 80.01}),
            ("region", {**region_payload, "contourDownsample": 3}),
            (
                "region_preview",
                {
                    "selectedBoundaryLabels": ["Unavailable boundary"],
                    "selectedCellTypes": resolved_types[:1],
                },
            ),
            (
                "region_preview",
                {
                    "selectedBoundaryIds": [region["regions"][0]["id"]],
                    "selectedCellTypes": resolved_types[:1],
                    "lineStyle": "not-a-line-style",
                },
            ),
            (
                "region_preview",
                {
                    "selectedBoundaryIds": [region["regions"][0]["id"]],
                    "selectedCellTypes": resolved_types[:1],
                    "lineWidth": 10.01,
                },
            ),
            (
                "region_preview",
                {
                    "selectedBoundaryIds": [region["regions"][0]["id"]],
                    "selectedCellTypes": resolved_types[:1],
                    "lineWidth": 0.49,
                },
            ),
            (
                "region_preview",
                {
                    "selectedBoundaryIds": [manual_region_id],
                    "selectedCellTypes": [manual_secondary_type],
                    "boundaryCellTypeMode": "hybrid",
                },
            ),
            (
                "region_manual_preview",
                {
                    **manual_create_payload,
                    "polygons": [[{"x": 0, "y": 0}, {"x": 1, "y": 1}]],
                },
            ),
            (
                "region_manual_preview",
                {
                    **manual_create_payload,
                    "mode": "include",
                },
            ),
            (
                "region_manual_preview",
                {
                    **manual_create_payload,
                    "targetBoundaryLabel": manual_region_label,
                },
            ),
            (
                "region_manual_save",
                {
                    **manual_create_payload,
                    "displayName": "",
                },
            ),
            (
                "region_custom_export",
                {
                    "selectedBoundaryIds": ["roi_0000000000000000"],
                    "selectedCellTypes": resolved_types[:1],
                },
            ),
            (
                "region_custom_export",
                {
                    "selectedBoundaryIds": [manual_region_id],
                    "selectedCellTypes": [manual_secondary_type],
                    "boundaryCellTypeMode": "source",
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
        if any(int(nuclei_grid["search_space"][label]["n_values"]) != 1 for label in ("MIN_DIAM_UM", "MAX_DIAM_UM")):
            raise AssertionError("Nuclei optimizer grid did not record fixed diameter singleton axes.")
        if int(assignment_grid["search_space"]["R_VORONOI_UM"]["n_values"]) != 1:
            raise AssertionError("Assignment optimizer grid did not record the fixed Voronoi radius axis.")

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
            if restored.get("regionParameters") != expected_region_parameters:
                raise AssertionError(f"Restore did not expose all applied Region parameters: {restored}")
            if (int(restored.get("width", 0)), int(restored.get("height", 0))) != (
                source_width,
                source_height,
            ):
                raise AssertionError(f"Restore did not expose the source image dimensions: {restored}")
            restored_neighborhood_previews = restored.get("previewPaths", {})
            for preview_key, expected_name in (
                ("neighborhood", "neighborhood_map.png"),
                ("neighborhoodLegend", "neighborhood_cluster_key.png"),
            ):
                restored_preview_path = Path(str(restored_neighborhood_previews.get(preview_key) or ""))
                if restored_preview_path.name != expected_name or not restored_preview_path.is_file():
                    raise AssertionError(
                        f"Restore omitted the new {preview_key} output: {restored_neighborhood_previews}"
                    )
            for preview_key in ("regionOverlay", "regionMask"):
                restored_preview_path = Path(str(restored.get("previewPaths", {}).get(preview_key) or ""))
                if not restored_preview_path.is_file():
                    raise AssertionError(f"Restore omitted {preview_key}: {restored.get('previewPaths')}")
                with Image.open(restored_preview_path) as restored_preview_image:
                    if restored_preview_image.size != (source_width, source_height):
                        raise AssertionError(
                            f"Restored {preview_key} changed the source panel dimensions: {restored_preview_image.size}"
                        )
            restored_region_ids = {str(row.get("id")) for row in restored.get("regions", [])}
            if not base_region_ids.issubset(restored_region_ids) or manual_region_id not in restored_region_ids:
                raise AssertionError(f"Restore lost stable computational or manual Region ids: {restored_region_ids}")
            restored_manual_row = next(
                (row for row in restored.get("regions", []) if str(row.get("id")) == manual_region_id),
                None,
            )
            if restored_manual_row is None or restored_manual_row.get("sourceType") != "manual":
                raise AssertionError(f"Restore lost manual Region metadata: {restored_manual_row}")
            if any(set(row) != expected_region_row_keys for row in restored.get("regions", [])):
                raise AssertionError(f"Restore returned an inconsistent Region row schema: {restored.get('regions')}")
            if not isinstance(restored.get("dominantCounts"), list):
                raise AssertionError("Restore did not return dominant Region counts.")
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

            legacy_neighborhood_output = guard_root / "legacy-neighborhood-preview"
            shutil.copytree(output_folder, legacy_neighborhood_output)
            legacy_neighborhood_dir = legacy_neighborhood_output / "06_neighborhood_analysis"
            (legacy_neighborhood_dir / "neighborhood_map.png").replace(
                legacy_neighborhood_dir / "neighborhood_preview.png"
            )
            (legacy_neighborhood_dir / "neighborhood_cluster_key.png").unlink()
            legacy_neighborhood_engine = EngineProcess(engine_command)
            try:
                legacy_neighborhood_restore = legacy_neighborhood_engine.request(
                    "restore",
                    {"outputFolder": str(legacy_neighborhood_output)},
                )
                if not legacy_neighborhood_restore.get("workflow", {}).get("neighborhood"):
                    raise AssertionError("Restore rejected a valid legacy neighborhood preview.")
                legacy_previews = legacy_neighborhood_restore.get("previewPaths", {})
                legacy_preview_path = Path(str(legacy_previews.get("neighborhood") or ""))
                if legacy_preview_path.name != "neighborhood_preview.png" or not legacy_preview_path.is_file():
                    raise AssertionError(f"Restore did not fall back to the legacy neighborhood preview: {legacy_previews}")
                if "neighborhoodLegend" in legacy_previews:
                    raise AssertionError("Restore exposed a missing legacy neighborhood cluster key.")
            finally:
                legacy_neighborhood_engine.close()

            zero_region_output = guard_root / "zero-region-run"
            shutil.copytree(output_folder, zero_region_output)
            zero_region_engine = EngineProcess(engine_command)
            try:
                zero_restore = zero_region_engine.request("restore", {"outputFolder": str(zero_region_output)})
                if not zero_restore.get("workflow", {}).get("region"):
                    raise AssertionError("Zero-Region guard could not restore the completed source history.")
                try:
                    zero_region_engine.request(
                        "region",
                        {
                            "selectedTypes": [resolved_types[0]],
                            "closeUm": 0.0,
                            "dilateUm": 0.0,
                            "minAreaUm2": 0.0,
                            "minCells": 1_000_000,
                        },
                    )
                except RuntimeError as error:
                    if "produced no nonempty boundary" not in str(error):
                        raise AssertionError(f"Empty Region run failed for the wrong reason: {error}") from error
                else:
                    raise AssertionError("A Region run with zero nonempty boundaries reported success.")
                zero_state = json.loads(
                    (zero_region_output / "00_config" / "windows_session_state.json").read_text(encoding="utf-8")
                )
                expected_after_zero_region = {"inputs", "overlay", "nuclei", "cellTypes", "neighborhood"}
                if any(
                    bool(value) != (stage in expected_after_zero_region)
                    for stage, value in zero_state["stages"].items()
                ):
                    raise AssertionError(f"Empty Region run unlocked downstream workflow: {zero_state['stages']}")
                if set(zero_state.get("analysisParameters", {})) != {"neighborhood"}:
                    raise AssertionError("Empty Region run retained invalid Region or downstream parameters.")
            finally:
                zero_region_engine.close()
            zero_region_restore_engine = EngineProcess(engine_command)
            try:
                zero_region_restored = zero_region_restore_engine.request(
                    "restore",
                    {"outputFolder": str(zero_region_output)},
                )
                if zero_region_restored.get("workflow", {}).get("region") or zero_region_restored.get("boundaries"):
                    raise AssertionError("Restart exposed Region results after a zero-boundary run.")
            finally:
                zero_region_restore_engine.close()

            empty_boundary_output = guard_root / "empty-boundary-filter"
            shutil.copytree(output_folder, empty_boundary_output)
            empty_boundary_dir = empty_boundary_output / "07_region_analysis"
            empty_boundary_path = empty_boundary_dir / "empty_smoke_region_mask_uint8.tiff"
            Image.new("L", (source_width, source_height), 0).save(empty_boundary_path, format="TIFF")
            empty_registry_path = empty_boundary_dir / "boundary_mask_registry.json"
            empty_registry = json.loads(empty_registry_path.read_text(encoding="utf-8"))
            empty_registry.setdefault("entries", []).append(
                {
                    "mask_path": empty_boundary_path.name,
                    "display_name": "Empty smoke boundary",
                    "source": "manual_roi_selection",
                    "group_name": "empty-smoke",
                    "mask_key": "empty-smoke",
                }
            )
            empty_registry_path.write_text(json.dumps(empty_registry, indent=2) + "\n", encoding="utf-8")
            empty_boundary_engine = EngineProcess(engine_command)
            try:
                empty_boundary_restore = empty_boundary_engine.request(
                    "restore",
                    {"outputFolder": str(empty_boundary_output)},
                )
                if any(
                    str(row.get("label")) == "Empty smoke boundary"
                    for row in empty_boundary_restore.get("regions", [])
                ):
                    raise AssertionError("Restore exposed an empty Region boundary.")
                if not any("empty Region boundary mask" in str(message) for message in empty_boundary_restore.get("warnings", [])):
                    raise AssertionError(
                        f"Restore filtered an empty Region boundary without a warning: {empty_boundary_restore.get('warnings')}"
                    )
                if not empty_boundary_restore.get("workflow", {}).get("region"):
                    raise AssertionError("An ignored empty Region boundary invalidated otherwise valid Region results.")
            finally:
                empty_boundary_engine.close()

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
            valid_state = json.loads(invalid_state_path.read_text(encoding="utf-8"))
            invalid_region_cases = (
                ("unavailable cell type", {"selectedTypes": ["Unavailable cell type"]}),
                ("closing radius above 80", {"closeUm": 80.01}),
                ("dilation radius above 80", {"dilateUm": 80.01}),
                ("unsupported contour downsample", {"contourDownsample": 3}),
                ("line width above 10", {"lineWidth": 10.01}),
                ("line width below 0.5", {"lineWidth": 0.49}),
            )
            for case_name, invalid_values in invalid_region_cases:
                invalid_state = json.loads(json.dumps(valid_state))
                invalid_state["analysisParameters"]["region"].update(invalid_values)
                invalid_state_path.write_text(json.dumps(invalid_state, indent=2) + "\n", encoding="utf-8")
                invalid_parameters_engine = EngineProcess(engine_command)
                try:
                    invalid_restore = invalid_parameters_engine.request(
                        "restore",
                        {"outputFolder": str(invalid_parameters_output)},
                    )
                    invalid_analysis = invalid_restore.get("analysisParameters", {})
                    if "region" in invalid_analysis:
                        raise AssertionError(f"Restore accepted invalid Region settings: {case_name}.")
                    if invalid_analysis.get("neighborhood") != expected_neighborhood_parameters:
                        raise AssertionError("One invalid analysis entry discarded an unrelated valid entry.")
                    if not any(
                        "region analysis parameters were ignored" in str(value)
                        for value in invalid_restore.get("warnings", [])
                    ):
                        raise AssertionError(f"Restore did not report rejected Region settings: {case_name}.")
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
                stale_preview_keys = {
                    "cellTypes",
                    "neighborhood",
                    "neighborhoodLegend",
                    "region",
                    "distribution",
                    "distance_nearest",
                    "distance_boundary",
                }
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
                mismatched_parameters = dict(applied_assignment_parameters)
                mismatched_parameters["min_pos_pix"] = int(mismatched_parameters["min_pos_pix"]) + 1
                try:
                    assignment_apply_engine.request(
                        "apply_recommendation",
                        {
                            "kind": "assignment",
                            "parameters": mismatched_parameters,
                            "recommendationId": assignment_apply_restore.get("assignmentRecommendationId"),
                        },
                    )
                except RuntimeError as error:
                    if "do not match" not in str(error):
                        raise AssertionError(f"Mismatched recommendation failed for the wrong reason: {error}") from error
                else:
                    raise AssertionError("The engine accepted assignment parameters that differed from the recommendation.")
                try:
                    assignment_apply_engine.request(
                        "apply_recommendation",
                        {
                            "kind": "assignment",
                            "parameters": applied_assignment_parameters,
                            "recommendationId": "0" * 64,
                        },
                    )
                except RuntimeError as error:
                    if "recommendation changed" not in str(error).lower():
                        raise AssertionError(f"Stale recommendation ID failed for the wrong reason: {error}") from error
                else:
                    raise AssertionError("The engine accepted a stale assignment recommendation ID.")
                applied_assignment = assignment_apply_engine.request(
                    "apply_recommendation",
                    {
                        "kind": "assignment",
                        "parameters": applied_assignment_parameters,
                        "recommendationId": assignment_apply_restore.get("assignmentRecommendationId"),
                    },
                )
                if applied_assignment.get("parameters") != applied_assignment_parameters:
                    raise AssertionError(
                        "The engine did not return the canonical assignment recommendation: "
                        f"{applied_assignment}"
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

                final_from_applied = assignment_apply_restore_engine.request(
                    "celltype_assignment",
                    {
                        "nucleusChannel": "DAPI",
                        "cellTypes": assignment_applied_restore["cellTypes"],
                        # Mirror the WPF final-run request: after Apply, the UI
                        # sends the canonical values currently shown in its
                        # editable final-parameter controls.
                        "parameters": assignment_applied_restore["assignmentParameters"],
                    },
                )
                if final_from_applied.get("parameters") != applied_assignment_parameters:
                    raise AssertionError(
                        "Final assignment did not consume the applied recommendation: "
                        f"actual={final_from_applied.get('parameters')}, expected={applied_assignment_parameters}"
                    )
                assignment_output = assignment_apply_output / "05_cell_type_assignment"
                for filename in (
                    "celltype_assignment_params.json",
                    "celltype_assignment_parameters.json",
                ):
                    saved_parameters = json.loads((assignment_output / filename).read_text(encoding="utf-8"))
                    if saved_parameters != applied_assignment_parameters:
                        raise AssertionError(
                            f"{filename} did not save the applied recommendation: "
                            f"actual={saved_parameters}, expected={applied_assignment_parameters}"
                        )
                final_state = json.loads(
                    (assignment_apply_output / "00_config" / "windows_session_state.json").read_text(encoding="utf-8")
                )
                if final_state.get("pendingParameters", {}).get("assignment"):
                    raise AssertionError("Final assignment did not clear the consumed applied recommendation.")
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
            "region_protocol_checks": 12,
            "restore_checks": 16,
            "apply_checks": 5,
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
