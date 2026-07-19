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
        for preview_path in overlay["previewPaths"].values():
            if not Path(preview_path).is_file():
                raise AssertionError(f"Missing overlay preview: {preview_path}")

        nuclei_optimizer = engine.request(
            "nuclei_optimizer",
            {
                "parameters": {"nucleus_channel": "DAPI", "min_diam_um": 6.0, "max_diam_um": 16.0},
                "maxEvaluations": 2,
                "parallelWorkers": 1,
                "parallelBackend": "threading",
                "useFixedRoiSubset": True,
            },
        )
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
                "nativeThreads": 1,
            },
        )
        n_nuclei = int(nuclei["summary"]["nNuclei"])
        if not 40 <= n_nuclei <= 100:
            raise AssertionError(f"Unexpected native-engine nuclei count: {n_nuclei}")

        assignment_optimizer = engine.request(
            "celltype_optimizer",
            {
                "cellTypes": cell_types(),
                "parameters": {"r_voronoi_um": 3.0, "r_buffer_um": 2.0, "r_vote_um": 3.0},
                "maxEvaluations": 2,
                "parallelWorkers": 1,
                "parallelBackend": "threading",
                "useFixedRoiSubset": True,
            },
        )
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
                "nativeThreads": 1,
                "supportWorkers": 1,
            },
        )
        resolved_types = [name for name in assignment["summary"]["cellCounts"] if name not in {"Unassigned", "Ambiguous"}]
        if len(resolved_types) < 3:
            raise AssertionError(f"Expected at least three assigned types, got {resolved_types}")

        neighborhood = engine.request("neighborhood", {"gridSizeUm": 24.0})
        if int(neighborhood["summary"]["clusterCount"]) < 1:
            raise AssertionError("Neighborhood analysis did not produce clusters.")

        region = engine.request(
            "region",
            {"selectedTypes": [resolved_types[0]], "closeUm": 9.0, "dilateUm": 6.0, "minAreaUm2": 0.0, "minCells": 1},
        )
        if not region["boundaries"]:
            raise AssertionError("Region analysis did not produce a boundary.")

        distribution = engine.request(
            "cell_distribution",
            {"boundaryLabel": region["boundaries"][0]["label"], "bandWidthUm": 10.0, "selectedCellTypes": resolved_types},
        )
        if not distribution["artifacts"]:
            raise AssertionError("Cell distribution did not produce artifacts.")

        distance = engine.request(
            "distance",
            {"mode": "nearest", "targetType": resolved_types[0], "queryTypes": resolved_types[1:]},
        )
        if not distance["artifacts"]:
            raise AssertionError("Distance analysis did not produce artifacts.")

        outputs = engine.request("outputs", {})
        if len(outputs["files"]) < 12:
            raise AssertionError("Native output manifest is unexpectedly small.")

        if engine.max_protocol_line_bytes >= 2_000_000:
            raise AssertionError(f"Protocol line too large: {engine.max_protocol_line_bytes} bytes")
        serialized_outputs = json.dumps(outputs)
        if "data:image" in serialized_outputs or "base64" in serialized_outputs.lower():
            raise AssertionError("Native engine protocol contains an embedded data payload.")

        report = {
            "status": "passed",
            "n_nuclei": n_nuclei,
            "resolved_cell_types": resolved_types,
            "neighborhood_clusters": neighborhood["summary"]["clusterCount"],
            "optimizer_checks": 2,
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
