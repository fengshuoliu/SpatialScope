from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import gaussian_filter
from skimage.draw import disk


WINDOWS_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = WINDOWS_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from cell_distribution_export import (  # noqa: E402
    _read_config,
    run_cell_cluster_distribution_analysis,
    run_cell_density_analysis,
    run_region_mask_band_analysis,
)
from src.spatialscope_analysis.celltype_assignment import (  # noqa: E402
    CelltypeAssignmentParams,
    run_celltype_assignment,
    run_celltype_assignment_parameter_sweep,
)
from src.spatialscope_analysis.distance_analysis import (  # noqa: E402
    run_boundary_distance_analysis,
    run_nearest_neighbor_analysis,
)
from src.spatialscope_analysis.io import files_to_long_df, write_json, zip_directory_bytes  # noqa: E402
from src.spatialscope_analysis.models import ChannelConfig, NucleiParams, PipelineConfig, RegionParams  # noqa: E402
from src.spatialscope_analysis.neighborhood_analysis import (  # noqa: E402
    run_neighborhood_analysis,
    save_neighborhood_analysis_outputs,
)
from src.spatialscope_analysis.nuclei_segmentation import (  # noqa: E402
    run_nuclei_parameter_sweep,
    run_nuclei_segmentation,
)
from src.spatialscope_analysis.region_analysis import (  # noqa: E402
    run_region_boundary_analysis,
    save_manual_roi_analysis,
)
from src.spatialscope_analysis.visualization import overlay_multi_channels, plot_split_channels  # noqa: E402


IMAGE_SIZE = 128
PIXEL_SIZE_UM = (1.0, 1.0)


def _make_synthetic_channels(input_dir: Path) -> tuple[list[ChannelConfig], dict[str, str]]:
    rng = np.random.default_rng(20260718)
    channel_names = ["DAPI", "CD4", "CD8", "B220", "Tumor"]
    channels = {
        name: rng.normal(0.012, 0.004, (IMAGE_SIZE, IMAGE_SIZE)).clip(0, None).astype(np.float32)
        for name in channel_names
    }

    type_markers = {
        "CD4 T": "CD4",
        "CD8 T": "CD8",
        "B cell": "B220",
        "Tumor": "Tumor",
    }
    type_by_quadrant = {
        (0, 0): "CD4 T",
        (0, 1): "CD8 T",
        (1, 0): "B cell",
        (1, 1): "Tumor",
    }

    for y in range(10, IMAGE_SIZE - 8, 16):
        for x in range(10, IMAGE_SIZE - 8, 16):
            celltype = type_by_quadrant[(int(y >= IMAGE_SIZE // 2), int(x >= IMAGE_SIZE // 2))]
            nucleus_rr, nucleus_cc = disk((y, x), 4.4, shape=channels["DAPI"].shape)
            marker_rr, marker_cc = disk((y, x), 5.8, shape=channels["DAPI"].shape)
            channels["DAPI"][nucleus_rr, nucleus_cc] += 1.0
            channels[type_markers[celltype]][marker_rr, marker_cc] += 0.95

    channels["DAPI"] = gaussian_filter(channels["DAPI"], sigma=0.55)
    for marker in channel_names[1:]:
        channels[marker] = gaussian_filter(channels[marker], sigma=0.75)

    colors = {
        "DAPI": "#ffffff",
        "CD4": "#ff4fb3",
        "CD8": "#33c96f",
        "B220": "#32b8d8",
        "Tumor": "#f05b49",
    }
    configs: list[ChannelConfig] = []
    for channel_name in channel_names:
        filename = f"{channel_name}.csv"
        np.savetxt(input_dir / filename, channels[channel_name], delimiter=",", fmt="%.6f")
        configs.append(ChannelConfig(file=filename, channel=channel_name, color_hex=colors[channel_name]))
    return configs, colors


def _celltype_config() -> list[dict[str, Any]]:
    return [
        {
            "name": "CD4 T",
            "color_hex": "#ff4fb3",
            "mode": "simple",
            "all_pos": ["nucleus", "CD4"],
            "all_neg": ["CD8", "B220", "Tumor"],
            "any_pos_groups": [],
        },
        {
            "name": "CD8 T",
            "color_hex": "#33c96f",
            "mode": "simple",
            "all_pos": ["nucleus", "CD8"],
            "all_neg": ["CD4", "B220", "Tumor"],
            "any_pos_groups": [],
        },
        {
            "name": "B cell",
            "color_hex": "#32b8d8",
            "mode": "simple",
            "all_pos": ["nucleus", "B220"],
            "all_neg": ["CD4", "CD8", "Tumor"],
            "any_pos_groups": [],
        },
        {
            "name": "Tumor",
            "color_hex": "#f05b49",
            "mode": "simple",
            "all_pos": ["nucleus", "Tumor"],
            "all_neg": ["CD4", "CD8", "B220"],
            "any_pos_groups": [],
        },
    ]


def _close_figures(*results: Any) -> None:
    for result in results:
        if isinstance(result, dict):
            values = result.values()
        elif isinstance(result, (tuple, list)):
            values = result
        else:
            values = [result]
        for value in values:
            if isinstance(value, plt.Figure):
                plt.close(value)
    plt.close("all")


def run_smoke(output_root: Path) -> dict[str, Any]:
    if output_root.exists():
        shutil.rmtree(output_root)
    input_dir = output_root / "synthetic_input"
    output_dir = output_root / "SpatialScope_outputs"
    input_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    channels, _ = _make_synthetic_channels(input_dir)
    config = PipelineConfig(
        folder=input_dir,
        save_dir=output_dir,
        pixel_size_um=PIXEL_SIZE_UM,
        image_id="SmokeField",
        channels=channels,
        overlay_channels=[channel.channel for channel in channels],
        white_channel="DAPI",
        white_weight=0.25,
        input_mode="local",
    )
    config_dir = output_dir / "00_config"
    write_json(config_dir / "pipeline_config.json", config.to_json_dict())

    channel_payload = [channel.to_dict() for channel in channels]
    df_pixels, shapes = files_to_long_df(
        input_dir,
        channel_payload,
        image_id=config.image_id,
        pixel_size_um=PIXEL_SIZE_UM,
    )
    data_result = {"df_pixels": df_pixels, "shapes": shapes}

    overlay_dir = output_dir / "01_overlay_preview"
    overlay_dir.mkdir(parents=True)
    overlay_figure, overlay_rgb = overlay_multi_channels(
        df_pixels,
        shapes,
        config.image_id,
        channel_payload,
        config.overlay_channels,
        white_channel=config.white_channel,
        white_weight=config.white_weight,
        pixel_size_um=PIXEL_SIZE_UM,
        save_path=overlay_dir / "overlay.png",
    )
    split_result = plot_split_channels(
        df_pixels,
        shapes,
        config.image_id,
        channel_payload,
        pixel_size_um=PIXEL_SIZE_UM,
        save_path=overlay_dir / "split_channels.png",
    )

    nuclei_params = NucleiParams(
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
    )
    nuclei_dir = output_dir / "02_nuclei_segmentation"
    nuclei_result = run_nuclei_segmentation(
        df_pixels=df_pixels,
        shapes=shapes,
        image_id=config.image_id,
        save_dir=nuclei_dir,
        pixel_size_um=PIXEL_SIZE_UM,
        params=nuclei_params,
        save_outputs=True,
        native_threads=1,
    )
    if not 40 <= int(nuclei_result["n_nuclei"]) <= 100:
        raise AssertionError(f"Unexpected nuclei count: {nuclei_result['n_nuclei']}")

    nuclei_sweep_result = run_nuclei_parameter_sweep(
        df_pixels=df_pixels,
        shapes=shapes,
        image_id=config.image_id,
        save_dir=nuclei_dir,
        pixel_size_um=PIXEL_SIZE_UM,
        base_params=nuclei_params,
        sweep_values={"local_offset": [-0.04, -0.03], "gauss_sigma_um": [0.6]},
        save_outputs=True,
        max_combinations=2,
        parallel_workers=1,
        parallel_backend="threading",
        native_threads_per_worker=1,
        output_prefix="nuclei_parameter_sweep_smoke",
    )
    if int(nuclei_sweep_result["n_combinations"]) != 2:
        raise AssertionError("Nuclei parameter sweep did not evaluate both combinations.")

    celltype_cfg = _celltype_config()
    celltype_definition_dir = output_dir / "03_cell_type_definition"
    write_json(celltype_definition_dir / "celltype_config.json", {"cell_types": celltype_cfg})

    assignment_params = CelltypeAssignmentParams(
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
    assignment_params_dir = output_dir / "04_cell_type_assignment_parameters"
    assignment_sweep_result = run_celltype_assignment_parameter_sweep(
        folder=input_dir,
        save_dir=assignment_params_dir,
        pixel_size_um=PIXEL_SIZE_UM,
        image_id=config.image_id,
        channels_cfg=channel_payload,
        celltype_cfg=celltype_cfg,
        labels=nuclei_result["labels"],
        df_pixels=df_pixels,
        shapes=shapes,
        base_params=assignment_params,
        sweep_values={"min_pos_pix": [2, 3]},
        save_outputs=True,
        parallel_workers=1,
    )
    if int(assignment_sweep_result["n_combinations"]) != 2:
        raise AssertionError("Cell type parameter sweep did not evaluate both combinations.")

    assignment_dir = output_dir / "05_cell_type_assignment"
    assignment_result = run_celltype_assignment(
        folder=input_dir,
        save_dir=assignment_dir,
        pixel_size_um=PIXEL_SIZE_UM,
        image_id=config.image_id,
        channels_cfg=channel_payload,
        celltype_cfg=celltype_cfg,
        labels=nuclei_result["labels"],
        df_pixels=df_pixels,
        shapes=shapes,
        params=assignment_params,
        save_outputs=True,
        make_figures=True,
        native_threads=1,
        support_workers=1,
    )
    df_cells = assignment_result["df_cells"]
    resolved_cells = df_cells[~df_cells["celltype"].isin(["Unassigned", "Ambiguous"])]
    resolved_types = sorted(resolved_cells["celltype"].astype(str).unique().tolist())
    if len(resolved_types) < 3:
        raise AssertionError(f"Expected at least three resolved cell types, got {resolved_types}")

    neighborhood_dir = output_dir / "06_neighborhood_analysis"
    neighborhood_result = run_neighborhood_analysis(
        df_cells=df_cells,
        image_shape=nuclei_result["labels"].shape,
        pixel_size_um=PIXEL_SIZE_UM,
        grid_size_um=24.0,
    )
    cluster_colors = {
        label: ["#0e7c86", "#f05b49", "#7a6cc7", "#e5a93d", "#4f8f5b", "#cc6f9e"][idx % 6]
        for idx, label in enumerate(neighborhood_result["cluster_labels"])
    }
    neighborhood_saved = save_neighborhood_analysis_outputs(
        neighborhood_result,
        neighborhood_dir,
        PIXEL_SIZE_UM,
        cluster_colors,
        save_outputs=True,
    )

    region_dir = output_dir / "07_region_analysis"
    target_type = resolved_types[0]
    region_result = run_region_boundary_analysis(
        df_cells=df_cells,
        celltype_mask=assignment_result["celltype_mask"],
        celltype_cfg=celltype_cfg,
        save_dir=region_dir,
        pixel_size_um=PIXEL_SIZE_UM,
        params=RegionParams(
            selected_types=[target_type],
            close_um=9.0,
            dilate_um=6.0,
            min_area_um2=0.0,
            min_cells=1,
            contour_downsample=1,
            line_width=2.0,
            line_style="--",
            boundary_color="#0e7c86",
            use_type_colors=False,
        ),
        save_outputs=True,
    )
    boundary_mask_path = Path(region_result["saved_paths"]["mask_paths"][target_type])
    if not boundary_mask_path.exists() or not np.any(region_result["masks"][target_type]):
        raise AssertionError("Computational ROI did not produce a non-empty boundary mask.")

    roi_label_mask = np.zeros((IMAGE_SIZE, IMAGE_SIZE), dtype=np.uint16)
    roi_label_mask[12:62, 12:62] = 1
    manual_roi_dir = output_dir / "08_adjusted_region_analysis"
    manual_roi_result = save_manual_roi_analysis(
        df_cells=df_cells,
        celltype_mask=assignment_result["celltype_mask"],
        celltype_cfg=celltype_cfg,
        overlay_rgb=np.asarray(overlay_rgb),
        save_dir=manual_roi_dir,
        pixel_size_um=PIXEL_SIZE_UM,
        roi_label_mask=roi_label_mask,
        selected_types=resolved_types,
        roi_source_panel="overlay",
        roi_custom_names=["Smoke ROI"],
        save_outputs=True,
    )

    distance_dir = output_dir / "09_distance_analysis"
    query_types = resolved_types[1:]
    nearest_result = run_nearest_neighbor_analysis(
        df_cells=df_cells,
        celltype_cfg=celltype_cfg,
        save_dir=distance_dir,
        pixel_size_um=PIXEL_SIZE_UM,
        target_type=target_type,
        query_types=query_types,
        save_outputs=True,
    )
    boundary_distance_result = run_boundary_distance_analysis(
        df_cells=df_cells,
        celltype_cfg=celltype_cfg,
        celltype_mask=assignment_result["celltype_mask"],
        save_dir=distance_dir,
        pixel_size_um=PIXEL_SIZE_UM,
        boundary_mask_path=boundary_mask_path,
        boundary_name=f"{target_type} boundary",
        query_types=query_types,
        region_filter="all",
        save_outputs=True,
    )

    native_config = _read_config(output_dir)
    distribution_region_result = run_region_mask_band_analysis(
        config=native_config,
        data_result=data_result,
        boundary_label=f"{target_type} boundary",
        boundary_mask_path=boundary_mask_path,
        arrays_npz_path=None,
        band_width_um=10.0,
        overlay_channels=["CD4", "CD8"],
    )
    density_result = run_cell_density_analysis(
        config=native_config,
        assignment_df=df_cells,
        celltype_cfg=celltype_cfg,
        region_masks_result=distribution_region_result,
        selected_celltypes=resolved_types,
    )
    boundary_candidates = [
        (f"{target_type} boundary", boundary_mask_path),
        ("Smoke ROI", Path(manual_roi_result["saved_paths"]["mask_paths"]["Smoke ROI"])),
    ]
    cluster_result = run_cell_cluster_distribution_analysis(
        config=native_config,
        neighborhood_result=neighborhood_result,
        boundary_candidates=boundary_candidates,
        selected_boundary_labels=[label for label, _ in boundary_candidates],
        selected_cluster_labels=neighborhood_result["cluster_labels"],
    )

    required_files = [
        config_dir / "pipeline_config.json",
        overlay_dir / "overlay.png",
        Path(nuclei_result["saved_paths"]["labels_tiff"]),
        Path(assignment_result["saved_paths"]["cells_summary_csv"]),
        Path(neighborhood_saved["saved_paths"]["mask_tiff"]),
        boundary_mask_path,
        Path(manual_roi_result["saved_paths"]["roi_mask_tiff"]),
        Path(nearest_result["saved_paths"]["csv"]),
        Path(boundary_distance_result["saved_paths"]["csv"]),
        Path(distribution_region_result["saved_paths"]["arrays_npz"]),
        Path(density_result["saved_paths"]["csv_band_long"]),
        Path(cluster_result["saved_paths"]["csv_cluster_region"]),
    ]
    missing = [str(path) for path in required_files if not path.exists() or path.stat().st_size == 0]
    if missing:
        raise AssertionError(f"Missing or empty smoke outputs: {missing}")

    archive_bytes = zip_directory_bytes(output_dir)
    if len(archive_bytes) < 1024:
        raise AssertionError("Output archive is unexpectedly small.")

    report = {
        "status": "passed",
        "n_nuclei": int(nuclei_result["n_nuclei"]),
        "resolved_cell_types": resolved_types,
        "n_resolved_cells": int(len(resolved_cells)),
        "n_neighborhood_clusters": int(len(neighborhood_result["cluster_labels"])),
        "n_required_outputs": int(len(required_files)),
        "archive_size_bytes": int(len(archive_bytes)),
        "output_dir": str(output_dir),
    }
    write_json(output_root / "smoke_report.json", report)

    _close_figures(
        overlay_figure,
        split_result,
        nuclei_result,
        nuclei_sweep_result,
        assignment_sweep_result,
        assignment_result,
        neighborhood_saved,
        region_result,
        manual_roi_result,
        nearest_result,
        boundary_distance_result,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run every SpatialScope analysis stage on synthetic data.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=WINDOWS_DIR / "build" / "smoke-output",
        help="Temporary folder for generated input and analysis outputs.",
    )
    args = parser.parse_args()
    report = run_smoke(args.output_root.expanduser().resolve())
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
