from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import tifffile
from PIL import Image


WINDOWS_DIR = Path(__file__).resolve().parents[1]
BUILD_ROOT = WINDOWS_DIR / "build"
SMOKE_SCRIPT = Path(__file__).with_name("native_engine_smoke.py")


MASK_PATTERNS = (
    "02_nuclei_segmentation/nuclei_labels_uint16.tiff",
    "05_cell_type_assignment/celltypes_mask_uint16.tiff",
    "05_cell_type_assignment/marker_assign_*_uint16.tiff",
    "06_neighborhood_analysis/neighborhood_cluster_mask_uint16.tiff",
    "07_region_analysis/*_region_mask_uint8.tiff",
)

CSV_PATTERNS = (
    "02_nuclei_segmentation/nuclei_native_optimizer_results.csv",
    "02_nuclei_segmentation/nuclei_summary.csv",
    "04_cell_type_assignment_parameters/celltype_assignment_native_optimizer_results.csv",
    "05_cell_type_assignment/cells_summary.csv",
    "05_cell_type_assignment/celltype_counts.csv",
    "05_cell_type_assignment/marker_assignment_thresholds.csv",
    "06_neighborhood_analysis/neighborhood_cluster_summary.csv",
    "06_neighborhood_analysis/neighborhood_tile_assignments.csv",
    "07_region_analysis/cell_region_assignments__*.csv",
    "07_region_analysis/celltype_counts_by_region__*.csv",
    "07_region_analysis/region_area_summary__*.csv",
    "09_distance_analysis/*.csv",
    "10_cell_distribution_analysis/01_region_masks/*__summary.csv",
    "10_cell_distribution_analysis/02_cell_density/*__long.csv",
    "10_cell_distribution_analysis/02_cell_density/*__region.csv",
    "10_cell_distribution_analysis/02_cell_density/*__wide.csv",
)

OVERLAY_PREVIEWS = (
    Path("01_overlay_preview/overlay_preview.png"),
    Path("01_overlay_preview/split_channels_preview.png"),
)


def _build_child(path: Path, label: str) -> Path:
    resolved = path.resolve()
    build_root = BUILD_ROOT.resolve()
    try:
        relative = resolved.relative_to(build_root)
    except ValueError as exc:
        raise ValueError(f"{label} must be inside the Windows build folder: {build_root}") from exc
    if not relative.parts:
        raise ValueError(f"{label} cannot be the Windows build folder itself: {build_root}")
    return resolved


def _run_smoke(
    python: Path,
    engine_executable: Path | None,
    input_folder: Path,
    output_folder: Path,
    *,
    gpu_mode: str,
    parity_mode: str,
) -> dict[str, Any]:
    command = [
        str(python),
        str(SMOKE_SCRIPT),
        "--input-folder",
        str(input_folder),
        "--output-folder",
        str(output_folder),
    ]
    if engine_executable is None:
        command.extend(("--python", str(python)))
    else:
        command.extend(("--engine-executable", str(engine_executable)))

    environment = os.environ.copy()
    environment["SPATIALSCOPE_GPU_MODE"] = gpu_mode
    environment["SPATIALSCOPE_GPU_PARITY_MODE"] = parity_mode
    completed = subprocess.run(
        command,
        cwd=str(WINDOWS_DIR),
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        details = "\n".join(
            part.strip()
            for part in (completed.stdout[-3000:], completed.stderr[-3000:])
            if part.strip()
        )
        raise RuntimeError(
            f"native_engine_smoke.py failed in GPU mode {gpu_mode!r} "
            f"with exit code {completed.returncode}: {details}"
        )

    report_path = output_folder / "native_engine_smoke_report.json"
    if not report_path.is_file():
        raise AssertionError(f"Smoke run did not create its report: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "passed":
        raise AssertionError(f"Smoke run did not pass in GPU mode {gpu_mode!r}: {report}")
    return report


def _matched_relative_paths(root: Path, patterns: Iterable[str], kind: str) -> set[Path]:
    matches: set[Path] = set()
    for pattern in patterns:
        pattern_matches = {path.relative_to(root) for path in root.glob(pattern) if path.is_file()}
        if not pattern_matches:
            raise AssertionError(f"Missing {kind} matching {pattern!r} under {root}")
        matches.update(pattern_matches)
    return matches


def _assert_same_file_set(cpu_paths: set[Path], gpu_paths: set[Path], kind: str) -> None:
    if cpu_paths == gpu_paths:
        return
    cpu_only = sorted(str(path) for path in cpu_paths - gpu_paths)
    gpu_only = sorted(str(path) for path in gpu_paths - cpu_paths)
    raise AssertionError(f"{kind} file sets differ; CPU-only={cpu_only}, GPU-only={gpu_only}")


def _array_equal(left: np.ndarray, right: np.ndarray) -> bool:
    try:
        return bool(np.array_equal(left, right, equal_nan=True))
    except TypeError:
        return bool(np.array_equal(left, right))


def _assert_array_equal(cpu_array: np.ndarray, gpu_array: np.ndarray, label: str) -> None:
    if _array_equal(cpu_array, gpu_array):
        return
    raise AssertionError(
        f"Array mismatch for {label}: "
        f"CPU shape/dtype={cpu_array.shape}/{cpu_array.dtype}, "
        f"GPU shape/dtype={gpu_array.shape}/{gpu_array.dtype}"
    )


def _compare_masks(cpu_root: Path, gpu_root: Path) -> int:
    cpu_paths = _matched_relative_paths(cpu_root, MASK_PATTERNS, "workflow TIFF mask")
    gpu_paths = _matched_relative_paths(gpu_root, MASK_PATTERNS, "workflow TIFF mask")
    _assert_same_file_set(cpu_paths, gpu_paths, "Workflow TIFF mask")
    for relative_path in sorted(cpu_paths):
        cpu_mask = np.asarray(tifffile.imread(cpu_root / relative_path))
        gpu_mask = np.asarray(tifffile.imread(gpu_root / relative_path))
        _assert_array_equal(cpu_mask, gpu_mask, str(relative_path))
    return len(cpu_paths)


def _compare_distribution_arrays(cpu_root: Path, gpu_root: Path) -> tuple[int, int]:
    pattern = "10_cell_distribution_analysis/**/*.npz"
    cpu_paths = _matched_relative_paths(cpu_root, (pattern,), "cell-distribution NPZ")
    gpu_paths = _matched_relative_paths(gpu_root, (pattern,), "cell-distribution NPZ")
    _assert_same_file_set(cpu_paths, gpu_paths, "Cell-distribution NPZ")

    array_count = 0
    for relative_path in sorted(cpu_paths):
        with np.load(cpu_root / relative_path, allow_pickle=False) as cpu_archive, np.load(
            gpu_root / relative_path, allow_pickle=False
        ) as gpu_archive:
            cpu_names = set(cpu_archive.files)
            gpu_names = set(gpu_archive.files)
            if cpu_names != gpu_names:
                raise AssertionError(
                    f"NPZ array names differ for {relative_path}: "
                    f"CPU-only={sorted(cpu_names - gpu_names)}, GPU-only={sorted(gpu_names - cpu_names)}"
                )
            for name in sorted(cpu_names):
                _assert_array_equal(
                    np.asarray(cpu_archive[name]),
                    np.asarray(gpu_archive[name]),
                    f"{relative_path}::{name}",
                )
                array_count += 1
    return len(cpu_paths), array_count


def _compare_csv_frames(cpu_root: Path, gpu_root: Path) -> int:
    cpu_paths = _matched_relative_paths(cpu_root, CSV_PATTERNS, "key workflow CSV")
    gpu_paths = _matched_relative_paths(gpu_root, CSV_PATTERNS, "key workflow CSV")
    _assert_same_file_set(cpu_paths, gpu_paths, "Key workflow CSV")
    for relative_path in sorted(cpu_paths):
        cpu_frame = pd.read_csv(cpu_root / relative_path)
        gpu_frame = pd.read_csv(gpu_root / relative_path)
        try:
            pd.testing.assert_frame_equal(
                cpu_frame,
                gpu_frame,
                check_dtype=True,
                check_exact=True,
                check_like=False,
            )
        except AssertionError as exc:
            raise AssertionError(f"CSV frame mismatch for {relative_path}: {exc}") from exc
    return len(cpu_paths)


def _compare_overlay_previews(cpu_root: Path, gpu_root: Path) -> int:
    for relative_path in OVERLAY_PREVIEWS:
        cpu_path = cpu_root / relative_path
        gpu_path = gpu_root / relative_path
        if not cpu_path.is_file() or not gpu_path.is_file():
            raise AssertionError(f"Missing overlay preview pair for {relative_path}")
        with Image.open(cpu_path) as cpu_image, Image.open(gpu_path) as gpu_image:
            if cpu_image.mode != gpu_image.mode or cpu_image.size != gpu_image.size:
                raise AssertionError(
                    f"Overlay preview metadata mismatch for {relative_path}: "
                    f"CPU={cpu_image.mode}/{cpu_image.size}, GPU={gpu_image.mode}/{gpu_image.size}"
                )
            _assert_array_equal(
                np.asarray(cpu_image),
                np.asarray(gpu_image),
                str(relative_path),
            )
    return len(OVERLAY_PREVIEWS)


def compare_outputs(cpu_root: Path, gpu_root: Path) -> dict[str, int]:
    mask_count = _compare_masks(cpu_root, gpu_root)
    npz_count, distribution_array_count = _compare_distribution_arrays(cpu_root, gpu_root)
    csv_count = _compare_csv_frames(cpu_root, gpu_root)
    overlay_count = _compare_overlay_previews(cpu_root, gpu_root)
    return {
        "tiffMasks": mask_count,
        "distributionNpzFiles": npz_count,
        "distributionArrays": distribution_array_count,
        "csvFrames": csv_count,
        "overlayPreviews": overlay_count,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the native engine smoke on CPU and required GPU paths, then prove exact output parity."
    )
    parser.add_argument("--python", type=Path, default=WINDOWS_DIR / ".venv" / "Scripts" / "python.exe")
    parser.add_argument(
        "--engine-executable",
        type=Path,
        help="Run a frozen SpatialScopeEngine executable instead of native_engine.py.",
    )
    parser.add_argument(
        "--input-folder",
        type=Path,
        default=BUILD_ROOT / "gpu-smoke-fixture" / "synthetic_input",
    )
    parser.add_argument("--output-root", type=Path, default=BUILD_ROOT / "native-gpu-parity")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        python = args.python.resolve()
        if not python.is_file():
            raise FileNotFoundError(f"Python executable not found: {python}")
        engine_executable = args.engine_executable.resolve() if args.engine_executable is not None else None
        if engine_executable is not None and not engine_executable.is_file():
            raise FileNotFoundError(f"Engine executable not found: {engine_executable}")
        input_folder = args.input_folder.resolve()
        if not input_folder.is_dir():
            raise FileNotFoundError(f"Input folder not found: {input_folder}")
        output_root = _build_child(args.output_root, "--output-root")
        if output_root.exists() and not output_root.is_dir():
            raise NotADirectoryError(f"Output root is not a folder: {output_root}")

        cpu_output = output_root / "cpu"
        gpu_output = output_root / "gpu-required-full-parity"
        cpu_report = _run_smoke(
            python,
            engine_executable,
            input_folder,
            cpu_output,
            gpu_mode="off",
            parity_mode="off",
        )
        gpu_report = _run_smoke(
            python,
            engine_executable,
            input_folder,
            gpu_output,
            gpu_mode="require",
            parity_mode="full",
        )
        checks = compare_outputs(cpu_output, gpu_output)
        report: dict[str, Any] = {
            "status": "passed",
            "checks": checks,
            "nuclei": {
                "cpu": int(cpu_report["n_nuclei"]),
                "gpu": int(gpu_report["n_nuclei"]),
            },
            "outputs": {"cpu": str(cpu_output), "gpu": str(gpu_output)},
        }
        print(json.dumps(report, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
