from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np


WINDOWS_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = WINDOWS_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from smoke_pipeline import PIXEL_SIZE_UM, _celltype_config, _make_synthetic_channels  # noqa: E402
from src.spatialscope_analysis.celltype_assignment import (  # noqa: E402
    CelltypeAssignmentParams,
    _run_celltype_assignment_impl,
    prepare_celltype_assignment_optimizer_data,
)
from src.spatialscope_analysis.io import files_to_long_df  # noqa: E402
from src.spatialscope_analysis.models import NucleiParams  # noqa: E402
from src.spatialscope_analysis.nuclei_segmentation import run_nuclei_segmentation  # noqa: E402


def _sorted_counts(result: dict[str, Any]) -> dict[str, int]:
    counts = result["counts"]
    return {
        str(row.celltype): int(row.count)
        for row in counts.sort_values("celltype").itertuples(index=False)
    }


def _assert_optimizer_summary_parity(
    full_result: dict[str, Any],
    optimized_result: dict[str, Any],
) -> None:
    if _sorted_counts(full_result) != _sorted_counts(optimized_result):
        raise AssertionError(
            f"Optimizer count mismatch: full={_sorted_counts(full_result)}, "
            f"optimized={_sorted_counts(optimized_result)}"
        )

    full_cells = full_result["df_cells"].sort_values("label").reset_index(drop=True)
    optimized_cells = optimized_result["df_cells"].sort_values("label").reset_index(drop=True)
    exact_columns = [
        column
        for column in optimized_cells.columns
        if column in full_cells.columns
        and (
            column in {
                "label",
                "celltype_id",
                "celltype",
                "matched_celltypes",
                "n_matched_celltypes",
                "ambiguous_best_type",
                "ambiguous_best_probability",
                "ambiguous_second_probability",
                "ambiguous_probability_gap",
                "ambiguous_candidate_probabilities",
            }
            or column.endswith("_pos_pix")
            or column.endswith("_sum_intensity")
            or column.endswith("_pos")
        )
    ]
    for column in exact_columns:
        full_values = full_cells[column].to_numpy()
        optimized_values = optimized_cells[column].to_numpy()
        if np.issubdtype(full_values.dtype, np.floating):
            equal = np.array_equal(full_values, optimized_values, equal_nan=True)
        else:
            equal = np.array_equal(full_values, optimized_values)
        if not equal:
            raise AssertionError(f"Optimizer summary changed exact assignment column {column!r}.")


def run_smoke(work_root: Path, minimum_speedup: float) -> dict[str, Any]:
    input_dir = work_root / "input"
    output_dir = work_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    channels, _ = _make_synthetic_channels(input_dir)
    channel_payload = [channel.to_dict() for channel in channels]
    df_pixels, shapes = files_to_long_df(
        input_dir,
        channel_payload,
        image_id="OptimizerSmoke",
        pixel_size_um=PIXEL_SIZE_UM,
    )
    nuclei_result = run_nuclei_segmentation(
        df_pixels=df_pixels,
        shapes=shapes,
        image_id="OptimizerSmoke",
        save_dir=output_dir / "nuclei",
        pixel_size_um=PIXEL_SIZE_UM,
        params=NucleiParams(
            nucleus_channel="DAPI",
            min_diam_um=6.0,
            max_diam_um=16.0,
            tophat_radius_um=6.0,
            gauss_sigma_um=0.6,
            local_win_um=15.0,
            local_offset=-0.03,
            h_maxima_um=0.1,
            seed_min_dist_um=2.0,
            watershed_compactness=0.2,
            post_resplit_mult=0.6,
        ),
        save_outputs=False,
        native_threads=1,
    )
    labels = nuclei_result["labels"]
    celltype_cfg = _celltype_config()

    preparation_started = time.perf_counter()
    prepared_data = prepare_celltype_assignment_optimizer_data(
        folder=input_dir,
        image_id="OptimizerSmoke",
        channels_cfg=channel_payload,
        celltype_cfg=celltype_cfg,
        labels=labels,
        df_pixels=df_pixels,
        shapes=shapes,
    )
    preparation_seconds = time.perf_counter() - preparation_started

    base_params = CelltypeAssignmentParams(
        r_voronoi_um=3.0,
        r_buffer_um=2.0,
        r_vote_um=3.0,
        tophat_r_um=0.0,
        gauss_sigma_um=0.5,
        thresh_mode="global_otsu",
        min_pos_object_size_px=5,
        min_pos_pix=3,
        resolve_ambiguous=True,
        ambiguous_min_probability=0.55,
        ambiguous_min_gap=0.05,
    )
    parameter_sets = [
        base_params,
        replace(
            base_params,
            r_voronoi_um=5.0,
            r_buffer_um=3.0,
            r_vote_um=2.0,
            tophat_r_um=1.0,
            gauss_sigma_um=0.2,
            thresh_mode="yen",
            min_pos_object_size_px=1,
            min_pos_pix=2,
            ambiguous_min_probability=0.45,
            ambiguous_min_gap=0.02,
        ),
        replace(
            base_params,
            r_voronoi_um=2.0,
            r_buffer_um=1.0,
            r_vote_um=4.0,
            tophat_r_um=2.0,
            gauss_sigma_um=0.8,
            thresh_mode="triangle",
            min_pos_object_size_px=12,
            min_pos_pix=6,
            resolve_ambiguous=False,
        ),
        replace(
            base_params,
            r_voronoi_um=2.0,
            r_buffer_um=232.0,
            r_vote_um=197.0,
            tophat_r_um=66.0,
            gauss_sigma_um=32.5,
            thresh_mode="local",
            ambiguous_min_probability=0.01,
            ambiguous_min_gap=0.0,
        ),
    ]

    common = {
        "folder": input_dir,
        "save_dir": output_dir / "assignment",
        "pixel_size_um": PIXEL_SIZE_UM,
        "image_id": "OptimizerSmoke",
        "channels_cfg": channel_payload,
        "celltype_cfg": celltype_cfg,
        "labels": labels,
        "df_pixels": df_pixels,
        "shapes": shapes,
        "save_outputs": False,
        "make_figures": False,
        "native_threads": 1,
        "support_workers": 1,
    }

    full_seconds = 0.0
    optimized_seconds = 0.0
    for params in parameter_sets:
        started = time.perf_counter()
        full_result = _run_celltype_assignment_impl(**common, params=params)
        full_seconds += time.perf_counter() - started

        started = time.perf_counter()
        optimized_result = _run_celltype_assignment_impl(
            **common,
            params=params,
            summary_only=True,
            prepared_data=prepared_data,
        )
        optimized_seconds += time.perf_counter() - started
        _assert_optimizer_summary_parity(full_result, optimized_result)

    optimized_total_seconds = preparation_seconds + optimized_seconds
    speedup = full_seconds / max(optimized_total_seconds, 1e-9)
    if minimum_speedup > 0 and speedup < minimum_speedup:
        raise AssertionError(
            f"Optimizer acceleration was only {speedup:.2f}x; expected at least {minimum_speedup:.2f}x."
        )
    return {
        "status": "passed",
        "parameter_sets": len(parameter_sets),
        "exact_assignment_parity": True,
        "full_seconds": full_seconds,
        "preparation_seconds": preparation_seconds,
        "optimized_evaluation_seconds": optimized_seconds,
        "optimized_total_seconds": optimized_total_seconds,
        "speedup": speedup,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify exact optimizer summary parity and report the Windows acceleration."
    )
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--minimum-speedup", type=float, default=2.0)
    args = parser.parse_args()

    if args.work_root is not None:
        work_root = args.work_root.resolve()
        build_root = (WINDOWS_DIR / "build").resolve()
        try:
            work_root.relative_to(build_root)
        except ValueError as exc:
            raise ValueError(f"--work-root must stay inside {build_root}: {work_root}") from exc
        if work_root == build_root:
            raise ValueError(f"Refusing to replace the Windows build root: {build_root}")
        if work_root.exists():
            shutil.rmtree(work_root)
        report = run_smoke(work_root, max(0.0, float(args.minimum_speedup)))
    else:
        with tempfile.TemporaryDirectory(prefix="spatialscope-optimizer-smoke-") as temp_dir:
            report = run_smoke(Path(temp_dir), max(0.0, float(args.minimum_speedup)))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
