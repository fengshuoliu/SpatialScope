from __future__ import annotations

import argparse
import contextlib
import io
import json
import mimetypes
import os
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from cell_distribution_export import (  # noqa: E402
    _read_config as read_distribution_config,
    run_cell_cluster_distribution_analysis,
    run_cell_density_analysis,
    run_region_mask_band_analysis,
)
from src.spatialscope_analysis.celltype_assignment import (  # noqa: E402
    CELLTYPE_OPTIMIZER_PARAM_LABELS,
    COLOR_HEX_LIST,
    CelltypeAssignmentParams,
    recommend_celltype_assignment_optimizer_result,
    run_celltype_assignment,
    run_celltype_assignment_parameter_optimizer,
    save_celltype_config,
)
from src.spatialscope_analysis.distance_analysis import (  # noqa: E402
    run_boundary_distance_analysis,
    run_nearest_neighbor_analysis,
)
from src.spatialscope_analysis.io import (  # noqa: E402
    discover_text_image_files,
    files_to_long_df,
    list_output_files,
    write_json,
)
from src.spatialscope_analysis.models import (  # noqa: E402
    ChannelConfig,
    NucleiParams,
    PipelineConfig,
    RegionParams,
)
from src.spatialscope_analysis.neighborhood_analysis import (  # noqa: E402
    run_neighborhood_analysis,
    save_neighborhood_analysis_outputs,
)
from src.spatialscope_analysis.nuclei_segmentation import (  # noqa: E402
    SWEEP_PARAM_LABELS,
    recommend_nuclei_parameter_sweep_result,
    run_nuclei_parameter_optimizer,
    run_nuclei_segmentation,
)
from src.spatialscope_analysis.region_analysis import run_region_boundary_analysis  # noqa: E402
from src.spatialscope_analysis.visualization import (  # noqa: E402
    generate_distinct_hex,
    overlay_multi_channels,
    plot_split_channels,
)


PROTOCOL_VERSION = 1
ENGINE_VERSION = "2.0.0"
PROTOCOL_STDOUT = sys.stdout

SECTION_OUTPUT_SUBDIRS = {
    "config": "00_config",
    "overlay": "01_overlay_preview",
    "nuclei": "02_nuclei_segmentation",
    "celltype_definition": "03_cell_type_definition",
    "celltype_assignment_parameters": "04_cell_type_assignment_parameters",
    "celltype_assignment": "05_cell_type_assignment",
    "neighborhood_analysis": "06_neighborhood_analysis",
    "region_analysis": "07_region_analysis",
    "integrated_region_analysis": "08_adjusted_region_analysis",
    "distance_analysis": "09_distance_analysis",
    "cell_distribution_analysis": "10_cell_distribution_analysis",
}


def _emit(payload: Dict[str, Any]) -> None:
    PROTOCOL_STDOUT.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    PROTOCOL_STDOUT.flush()


def _progress(request_id: str, value: float, message: str) -> None:
    _emit(
        {
            "type": "progress",
            "id": request_id,
            "value": float(min(max(value, 0.0), 1.0)),
            "message": str(message),
        }
    )


def _section_dir(config: PipelineConfig, key: str) -> Path:
    path = Path(config.save_dir) / SECTION_OUTPUT_SUBDIRS[key]
    path.mkdir(parents=True, exist_ok=True)
    return path


def _close_figures(value: Any) -> None:
    if isinstance(value, plt.Figure):
        plt.close(value)
    elif isinstance(value, dict):
        for child in value.values():
            _close_figures(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _close_figures(child)


def _json_scalar(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _artifact_manifest(output_root: Path, within: Path | None = None) -> list[Dict[str, Any]]:
    output_root = output_root.resolve()
    search_root = (within or output_root).resolve()
    if not search_root.exists():
        return []

    records: list[Dict[str, Any]] = []
    for path in sorted(search_root.rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        suffix = path.suffix.lower()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        role = "preview" if path.stem.endswith("_preview") and suffix in {".png", ".jpg", ".jpeg"} else "export"
        record: Dict[str, Any] = {
            "id": str(path.relative_to(output_root)).replace("\\", "/"),
            "name": path.name,
            "role": role,
            "relativePath": str(path.relative_to(output_root)).replace("\\", "/"),
            "absolutePath": str(path),
            "mimeType": mime_type,
            "sizeBytes": int(stat.st_size),
            "modifiedUtc": float(stat.st_mtime),
        }
        if role == "preview":
            try:
                with Image.open(path) as image:
                    record["width"] = int(image.width)
                    record["height"] = int(image.height)
            except Exception:
                pass
        records.append(record)
    return records


def _save_figure_preview(figure: plt.Figure, path: Path, *, dpi: int = 110) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0, facecolor=figure.get_facecolor())
    return path


class NativeEngine:
    def __init__(self) -> None:
        self.config: PipelineConfig | None = None
        self.data_result: Dict[str, Any] | None = None
        self.nuclei_result: Dict[str, Any] | None = None
        self.celltype_config: list[Dict[str, Any]] = []
        self.assignment_result: Dict[str, Any] | None = None
        self.neighborhood_result: Dict[str, Any] | None = None
        self.region_result: Dict[str, Any] | None = None
        self.distribution_result: Dict[str, Any] | None = None

    def dispatch(self, request_id: str, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        handlers: Dict[str, Callable[[str, Dict[str, Any]], Dict[str, Any]]] = {
            "hello": self.hello,
            "configure": self.configure,
            "overlay": self.overlay,
            "nuclei": self.nuclei,
            "nuclei_optimizer": self.nuclei_optimizer,
            "celltype_assignment": self.celltype_assignment,
            "celltype_optimizer": self.celltype_optimizer,
            "neighborhood": self.neighborhood,
            "region": self.region,
            "cell_distribution": self.cell_distribution,
            "distance": self.distance,
            "outputs": self.outputs,
            "reset": self.reset,
        }
        if command not in handlers:
            raise KeyError(f"Unknown command: {command}")
        return handlers[command](request_id, payload)

    def hello(self, _request_id: str, _payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "engineVersion": ENGINE_VERSION,
            "transport": "json-lines/file-manifests",
            "capabilities": [
                "configure",
                "overlay",
                "nuclei",
                "nuclei_optimizer",
                "celltype_assignment",
                "celltype_optimizer",
                "neighborhood",
                "region",
                "cell_distribution",
                "distance",
                "outputs",
            ],
        }

    def reset(self, _request_id: str, _payload: Dict[str, Any]) -> Dict[str, Any]:
        self.__init__()
        return {"reset": True}

    def configure(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        _progress(request_id, 0.05, "Validating folders")
        input_folder = Path(str(payload.get("inputFolder", ""))).expanduser().resolve()
        output_folder = Path(str(payload.get("outputFolder", ""))).expanduser().resolve()
        if not input_folder.is_dir():
            raise FileNotFoundError(f"Input folder was not found: {input_folder}")
        if not str(payload.get("outputFolder", "")).strip():
            raise ValueError("Output folder is required.")
        output_folder.mkdir(parents=True, exist_ok=True)

        available_files = discover_text_image_files(input_folder)
        if not available_files:
            raise RuntimeError("No CSV or text image files were found in the input folder.")

        _progress(request_id, 0.25, "Building the channel registry")
        requested_channels = payload.get("channels") or []
        configured_by_file = {
            str(item.get("file") or item.get("fileName")): item
            for item in requested_channels
            if isinstance(item, dict)
        }
        palette = generate_distinct_hex(max(1, len(available_files)))
        channels: list[ChannelConfig] = []
        overlay_channels: list[str] = []
        for index, file_name in enumerate(available_files):
            requested = configured_by_file.get(file_name, {})
            channel_name = str(requested.get("channel") or requested.get("marker") or Path(file_name).stem)
            color_hex = str(requested.get("color_hex") or requested.get("colorHex") or palette[index])
            channels.append(ChannelConfig(file=file_name, channel=channel_name, color_hex=color_hex))
            if bool(requested.get("includeOverlay", requested.get("include_in_overlay", True))):
                overlay_channels.append(channel_name)

        pixel_size = payload.get("pixelSizeUm") or [payload.get("pixelSizeX", 1.0), payload.get("pixelSizeY", 1.0)]
        if len(pixel_size) < 2:
            raise ValueError("pixelSizeUm must contain X and Y values.")
        pixel_size_um = (float(pixel_size[0]), float(pixel_size[1]))
        if pixel_size_um[0] <= 0 or pixel_size_um[1] <= 0:
            raise ValueError("Pixel size values must be greater than zero.")

        self.config = PipelineConfig(
            folder=input_folder,
            save_dir=output_folder,
            pixel_size_um=pixel_size_um,
            image_id=str(payload.get("imageId") or "FieldA"),
            channels=channels,
            overlay_channels=overlay_channels or [channel.channel for channel in channels],
            white_channel=str(payload.get("whiteChannel") or "") or None,
            white_weight=float(payload.get("whiteWeight") or 0.0),
            input_mode="local",
        )
        for key in SECTION_OUTPUT_SUBDIRS:
            _section_dir(self.config, key)
        config_path = _section_dir(self.config, "config") / "pipeline_config.json"
        write_json(config_path, self.config.to_json_dict())
        # Keep the legacy Windows name readable by older auxiliary tools.
        write_json(_section_dir(self.config, "config") / "config.json", self.config.to_json_dict())

        self.data_result = None
        self.nuclei_result = None
        self.celltype_config = []
        self.assignment_result = None
        self.neighborhood_result = None
        self.region_result = None
        self.distribution_result = None
        _progress(request_id, 1.0, "Configuration saved")
        return {
            "inputFolder": str(input_folder),
            "outputFolder": str(output_folder),
            "configPath": str(config_path),
            "pixelSizeUm": list(pixel_size_um),
            "imageId": self.config.image_id,
            "channels": [
                {
                    "file": channel.file,
                    "channel": channel.channel,
                    "colorHex": channel.color_hex,
                    "includeOverlay": channel.channel in self.config.overlay_channels,
                }
                for channel in channels
            ],
        }

    def overlay(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        _progress(request_id, 0.05, "Loading channel matrices")
        data = self._ensure_pixels()
        output_dir = _section_dir(config, "overlay")
        channels_payload = [channel.to_dict() for channel in config.channels]

        _progress(request_id, 0.40, "Rendering the multiplex overlay")
        overlay_figure, overlay_rgb = overlay_multi_channels(
            data["df_pixels"],
            data["shapes"],
            config.image_id,
            channels_payload,
            config.overlay_channels,
            white_channel=config.white_channel,
            white_weight=config.white_weight,
            clip_hi=float(payload.get("clipHighPercentile", 99.8)),
            pixel_size_um=config.pixel_size_um,
            save_path=output_dir / "overlay.png",
        )
        _save_figure_preview(overlay_figure, output_dir / "overlay_preview.png", dpi=110)

        _progress(request_id, 0.70, "Rendering split channels")
        split_figure = plot_split_channels(
            data["df_pixels"],
            data["shapes"],
            config.image_id,
            channels_payload,
            pixel_size_um=config.pixel_size_um,
            clip_hi=float(payload.get("clipHighPercentile", 99.8)),
            save_path=output_dir / "split_channels.png",
        )
        _save_figure_preview(split_figure, output_dir / "split_channels_preview.png", dpi=85)
        plt.close(overlay_figure)
        plt.close(split_figure)

        _progress(request_id, 1.0, "Overlay and split channels saved")
        return {
            "summary": {"channelCount": len(config.channels)},
            "previewPaths": {
                "overlay": str(output_dir / "overlay_preview.png"),
                "splitChannels": str(output_dir / "split_channels_preview.png"),
            },
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def nuclei(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        data = self._ensure_pixels()
        raw = payload.get("parameters") or payload
        nucleus_channel = str(raw.get("nucleus_channel") or raw.get("nucleusChannel") or config.channels[0].channel)
        params = NucleiParams(
            nucleus_channel=nucleus_channel,
            min_diam_um=float(raw.get("min_diam_um", 6.0)),
            max_diam_um=float(raw.get("max_diam_um", 60.0)),
            tophat_radius_um=float(raw.get("tophat_radius_um", 2.0)),
            gauss_sigma_um=float(raw.get("gauss_sigma_um", 0.5)),
            local_win_um=float(raw.get("local_win_um", 25.0)),
            local_offset=float(raw.get("local_offset", -0.03)),
            h_maxima_um=float(raw.get("h_maxima_um", 0.25)),
            seed_min_dist_um=float(raw.get("seed_min_dist_um", 0.1)),
            watershed_compactness=float(raw.get("watershed_compactness", 0.5)),
            post_resplit_mult=float(raw.get("post_resplit_mult", 0.5)),
        )
        _progress(request_id, 0.10, "Preparing the nucleus channel")
        output_dir = _section_dir(config, "nuclei")
        self.nuclei_result = run_nuclei_segmentation(
            df_pixels=data["df_pixels"],
            shapes=data["shapes"],
            image_id=config.image_id,
            save_dir=output_dir,
            pixel_size_um=config.pixel_size_um,
            params=params,
            save_outputs=True,
            native_threads=max(1, int(payload.get("nativeThreads", 1))),
        )
        _progress(request_id, 0.90, "Saving segmentation previews")
        figure = self.nuclei_result.get("figure")
        if isinstance(figure, plt.Figure):
            _save_figure_preview(figure, output_dir / "nuclei_segmentation_preview.png", dpi=105)
            plt.close(figure)
            self.nuclei_result["figure"] = None
        self.assignment_result = None
        self.neighborhood_result = None
        self.region_result = None
        self.distribution_result = None
        _progress(request_id, 1.0, "Nuclei segmentation complete")
        return {
            "summary": {"nNuclei": int(self.nuclei_result["n_nuclei"])},
            "parameters": params.to_dict(),
            "previewPath": str(output_dir / "nuclei_segmentation_preview.png"),
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def nuclei_optimizer(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        data = self._ensure_pixels()
        raw = payload.get("parameters") or {}
        base_params = NucleiParams(
            nucleus_channel=str(raw.get("nucleus_channel") or raw.get("nucleusChannel") or config.channels[0].channel),
            min_diam_um=float(raw.get("min_diam_um", 6.0)),
            max_diam_um=float(raw.get("max_diam_um", 60.0)),
            tophat_radius_um=float(raw.get("tophat_radius_um", 2.0)),
            gauss_sigma_um=float(raw.get("gauss_sigma_um", 0.5)),
            local_win_um=float(raw.get("local_win_um", 25.0)),
            local_offset=float(raw.get("local_offset", -0.03)),
            h_maxima_um=float(raw.get("h_maxima_um", 0.25)),
            seed_min_dist_um=float(raw.get("seed_min_dist_um", 0.1)),
            watershed_compactness=float(raw.get("watershed_compactness", 0.5)),
            post_resplit_mult=float(raw.get("post_resplit_mult", 0.5)),
        )
        search_specs = payload.get("searchSpecs") or {
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
        max_evaluations = max(1, min(int(payload.get("maxEvaluations", 64)), 4096))
        _progress(request_id, 0.05, f"Screening up to {max_evaluations} nuclei parameter combinations")
        output_dir = _section_dir(config, "nuclei")
        result = run_nuclei_parameter_optimizer(
            df_pixels=data["df_pixels"],
            shapes=data["shapes"],
            image_id=config.image_id,
            save_dir=output_dir,
            pixel_size_um=config.pixel_size_um,
            base_params=base_params,
            search_specs=search_specs,
            save_outputs=True,
            max_evaluations=max_evaluations,
            exhaustive_limit=4096,
            parallel_workers=max(1, int(payload.get("parallelWorkers", 1))),
            parallel_backend=str(payload.get("parallelBackend") or "threading"),
            native_threads_per_worker=1,
            random_seed=int(payload.get("randomSeed", 42)),
            output_prefix="nuclei_native_optimizer",
            use_fixed_roi_subset=bool(payload.get("useFixedRoiSubset", True)),
            roi_area_fraction_per_roi=float(payload.get("roiAreaFractionPerRoi", 0.02)),
        )
        recommended_row = recommend_nuclei_parameter_sweep_result(result["results"])
        recommended: Dict[str, Any] = {}
        if recommended_row is not None:
            for key in [
                "min_diam_um", "max_diam_um", "tophat_radius_um", "gauss_sigma_um", "local_win_um",
                "local_offset", "h_maxima_um", "seed_min_dist_um", "watershed_compactness", "post_resplit_mult",
            ]:
                column = SWEEP_PARAM_LABELS.get(key, key)
                if column in recommended_row:
                    recommended[key] = _json_scalar(recommended_row[column])
        preview_path = self._save_first_result_figure_preview(result, output_dir / "nuclei_optimizer_preview.png")
        evaluated = int(result.get("evaluated_unique_combinations", len(result.get("results", []))))
        _close_figures(result)
        _progress(request_id, 1.0, "Nuclei parameter optimization complete")
        return {
            "summary": {"evaluatedCombinations": evaluated},
            "recommendedParameters": recommended,
            "previewPath": str(preview_path) if preview_path else None,
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def celltype_assignment(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.nuclei_result is None:
            raise RuntimeError("Run final nuclei segmentation before cell type assignment.")
        data = self._ensure_pixels()

        definitions = payload.get("cellTypes") or payload.get("celltypeConfig") or []
        if not definitions:
            marker_names = [channel.channel for channel in config.channels]
            definitions = [
                {
                    "name": marker,
                    "color_hex": config.channels[index].color_hex,
                    "mode": "simple",
                    "all_pos": ["nucleus", marker],
                    "all_neg": [],
                    "any_pos_groups": [],
                }
                for index, marker in enumerate(marker_names)
                if marker != str(payload.get("nucleusChannel") or self.nuclei_result["params"]["NUCLEUS_CHANNEL"])
            ]
        self.celltype_config = [self._normalize_celltype(item, index) for index, item in enumerate(definitions)]
        definition_dir = _section_dir(config, "celltype_definition")
        definition_path = save_celltype_config(self.celltype_config, definition_dir)
        # Canonical wrapper used by native and smoke workflows.
        write_json(definition_dir / "celltype_config.json", {"cell_types": self.celltype_config})

        raw = payload.get("parameters") or {}
        params = CelltypeAssignmentParams(
            r_voronoi_um=float(raw.get("r_voronoi_um", 3.0)),
            r_buffer_um=float(raw.get("r_buffer_um", 2.0)),
            r_vote_um=float(raw.get("r_vote_um", 3.0)),
            tophat_r_um=float(raw.get("tophat_r_um", 1.0)),
            gauss_sigma_um=float(raw.get("gauss_sigma_um", 0.5)),
            thresh_mode=str(raw.get("thresh_mode", "global_otsu")),
            min_pos_object_size_px=int(raw.get("min_pos_object_size_px", 9)),
            min_pos_pix=int(raw.get("min_pos_pix", 5)),
            resolve_ambiguous=bool(raw.get("resolve_ambiguous", True)),
            ambiguous_min_probability=float(raw.get("ambiguous_min_probability", 0.60)),
            ambiguous_min_gap=float(raw.get("ambiguous_min_gap", 0.10)),
        )
        _progress(request_id, 0.12, "Saving cell type definitions")
        output_dir = _section_dir(config, "celltype_assignment")
        self.assignment_result = run_celltype_assignment(
            folder=Path(config.folder),
            save_dir=output_dir,
            pixel_size_um=config.pixel_size_um,
            image_id=config.image_id,
            channels_cfg=[channel.to_dict() for channel in config.channels],
            celltype_cfg=self.celltype_config,
            labels=self.nuclei_result["labels"],
            df_pixels=data["df_pixels"],
            shapes=data["shapes"],
            params=params,
            save_outputs=True,
            make_figures=True,
            native_threads=max(1, int(payload.get("nativeThreads", 1))),
            support_workers=max(1, int(payload.get("supportWorkers", 1))),
        )
        _progress(request_id, 0.90, "Saving assignment previews")
        preview_path = self._save_first_result_figure_preview(
            self.assignment_result,
            output_dir / "celltype_assignment_preview.png",
        )
        counts = {
            str(key): int(value)
            for key, value in self.assignment_result["df_cells"]["celltype"].astype(str).value_counts().items()
        }
        self.neighborhood_result = None
        self.region_result = None
        self.distribution_result = None
        _progress(request_id, 1.0, "Cell type assignment complete")
        return {
            "summary": {"cellCounts": counts, "totalCells": int(len(self.assignment_result["df_cells"]))},
            "parameters": params.to_dict(),
            "celltypeConfigPath": str(definition_path),
            "previewPath": str(preview_path) if preview_path else None,
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def celltype_optimizer(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.nuclei_result is None:
            raise RuntimeError("Run final nuclei segmentation before assignment parameter optimization.")
        data = self._ensure_pixels()
        definitions = payload.get("cellTypes") or payload.get("celltypeConfig") or []
        if not definitions:
            raise ValueError("Define at least one cell type before assignment parameter optimization.")
        self.celltype_config = [self._normalize_celltype(item, index) for index, item in enumerate(definitions)]
        definition_dir = _section_dir(config, "celltype_definition")
        write_json(definition_dir / "celltype_config.json", {"cell_types": self.celltype_config})

        raw = payload.get("parameters") or {}
        base_params = CelltypeAssignmentParams(
            r_voronoi_um=float(raw.get("r_voronoi_um", 3.0)),
            r_buffer_um=float(raw.get("r_buffer_um", 2.0)),
            r_vote_um=float(raw.get("r_vote_um", 3.0)),
            tophat_r_um=float(raw.get("tophat_r_um", 1.0)),
            gauss_sigma_um=float(raw.get("gauss_sigma_um", 0.5)),
            thresh_mode=str(raw.get("thresh_mode", "global_otsu")),
            min_pos_object_size_px=int(raw.get("min_pos_object_size_px", 9)),
            min_pos_pix=int(raw.get("min_pos_pix", 5)),
            resolve_ambiguous=bool(raw.get("resolve_ambiguous", True)),
            ambiguous_min_probability=float(raw.get("ambiguous_min_probability", 0.60)),
            ambiguous_min_gap=float(raw.get("ambiguous_min_gap", 0.10)),
        )
        search_specs = payload.get("searchSpecs") or {
            "r_voronoi_um": {"kind": "float", "min": 0.0, "max": 10.0, "step": 0.5},
            "r_buffer_um": {"kind": "float", "min": 0.0, "max": 8.0, "step": 0.5},
            "r_vote_um": {"kind": "float", "min": 0.0, "max": 10.0, "step": 0.5},
            "tophat_r_um": {"kind": "float", "min": 0.0, "max": 4.0, "step": 0.5},
            "gauss_sigma_um": {"kind": "float", "min": 0.0, "max": 1.5, "step": 0.1},
            "thresh_mode": {"kind": "choice", "options": ["global_otsu", "yen", "triangle"]},
            "min_pos_object_size_px": {"kind": "int", "min": 0, "max": 80, "step": 1},
            "min_pos_pix": {"kind": "int", "min": 0, "max": 40, "step": 1},
            "resolve_ambiguous": {"kind": "bool", "options": [True]},
            "ambiguous_min_probability": {"kind": "float", "min": 0.0, "max": 0.80, "step": 0.01},
            "ambiguous_min_gap": {"kind": "float", "min": 0.0, "max": 0.20, "step": 0.01},
        }
        max_evaluations = max(1, min(int(payload.get("maxEvaluations", 64)), 4096))
        _progress(request_id, 0.05, f"Screening up to {max_evaluations} assignment parameter combinations")
        output_dir = _section_dir(config, "celltype_assignment_parameters")
        result = run_celltype_assignment_parameter_optimizer(
            folder=Path(config.folder),
            save_dir=output_dir,
            pixel_size_um=config.pixel_size_um,
            image_id=config.image_id,
            channels_cfg=[channel.to_dict() for channel in config.channels],
            celltype_cfg=self.celltype_config,
            labels=self.nuclei_result["labels"],
            df_pixels=data["df_pixels"],
            shapes=data["shapes"],
            base_params=base_params,
            search_specs=search_specs,
            save_outputs=True,
            max_evaluations=max_evaluations,
            exhaustive_limit=4096,
            parallel_workers=max(1, int(payload.get("parallelWorkers", 1))),
            parallel_backend=str(payload.get("parallelBackend") or "threading"),
            native_threads_per_worker=1,
            support_workers_per_worker=1,
            random_seed=int(payload.get("randomSeed", 42)),
            output_prefix="celltype_assignment_native_optimizer",
            use_fixed_roi_subset=bool(payload.get("useFixedRoiSubset", True)),
            roi_area_fraction_per_roi=float(payload.get("roiAreaFractionPerRoi", 0.02)),
        )
        recommended_row = recommend_celltype_assignment_optimizer_result(
            result["results"],
            defined_celltype_names=[str(item["name"]) for item in self.celltype_config],
        )
        recommended: Dict[str, Any] = {}
        if recommended_row is not None:
            for key in [
                "r_voronoi_um", "r_buffer_um", "r_vote_um", "tophat_r_um", "gauss_sigma_um",
                "thresh_mode", "min_pos_object_size_px", "min_pos_pix", "resolve_ambiguous",
                "ambiguous_min_probability", "ambiguous_min_gap",
            ]:
                column = CELLTYPE_OPTIMIZER_PARAM_LABELS.get(key, key)
                if column in recommended_row:
                    recommended[key] = _json_scalar(recommended_row[column])
        preview_path = self._save_first_result_figure_preview(result, output_dir / "celltype_assignment_optimizer_preview.png")
        evaluated = int(result.get("evaluated_unique_combinations", len(result.get("results", []))))
        _close_figures(result)
        _progress(request_id, 1.0, "Assignment parameter optimization complete")
        return {
            "summary": {"evaluatedCombinations": evaluated},
            "recommendedParameters": recommended,
            "previewPath": str(preview_path) if preview_path else None,
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def neighborhood(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.assignment_result is None or self.nuclei_result is None:
            raise RuntimeError("Run cell type assignment before neighborhood analysis.")
        _progress(request_id, 0.12, "Building the neighborhood grid")
        self.neighborhood_result = run_neighborhood_analysis(
            df_cells=self.assignment_result["df_cells"],
            image_shape=tuple(self.nuclei_result["labels"].shape),
            pixel_size_um=config.pixel_size_um,
            grid_size_um=float(payload.get("gridSizeUm", 20.0)),
        )
        labels = [str(value) for value in self.neighborhood_result.get("cluster_labels", [])]
        palette = generate_distinct_hex(max(1, len(labels)))
        colors = {
            label: str((payload.get("clusterColors") or {}).get(label) or palette[index])
            for index, label in enumerate(labels)
        }
        _progress(request_id, 0.72, "Saving neighborhood outputs")
        output_dir = _section_dir(config, "neighborhood_analysis")
        saved = save_neighborhood_analysis_outputs(
            self.neighborhood_result,
            output_dir,
            config.pixel_size_um,
            colors,
            display_cluster_labels=payload.get("displayClusters") or labels,
            save_outputs=True,
        )
        preview_path = self._save_first_result_figure_preview(saved, output_dir / "neighborhood_preview.png")
        _close_figures(saved)
        self.region_result = None
        self.distribution_result = None
        _progress(request_id, 1.0, "Neighborhood analysis complete")
        return {
            "summary": {
                "clusterCount": len(labels),
                "occupiedTiles": int(len(self.neighborhood_result.get("tile_assignments", []))),
            },
            "clusterLabels": labels,
            "previewPath": str(preview_path) if preview_path else None,
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def region(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.assignment_result is None:
            raise RuntimeError("Run cell type assignment before region analysis.")
        present_types = [
            str(value)
            for value in self.assignment_result["df_cells"]["celltype"].astype(str).unique().tolist()
            if str(value) not in {"Unassigned", "Ambiguous"}
        ]
        selected_types = [str(value) for value in (payload.get("selectedTypes") or present_types[:1])]
        if not selected_types:
            raise ValueError("Select at least one cell type for region analysis.")
        params = RegionParams(
            selected_types=selected_types,
            close_um=float(payload.get("closeUm", 15.0)),
            dilate_um=float(payload.get("dilateUm", 10.0)),
            min_area_um2=float(payload.get("minAreaUm2", 20000.0)),
            min_cells=int(payload.get("minCells", 5)),
            contour_downsample=int(payload.get("contourDownsample", 2)),
            line_width=float(payload.get("lineWidth", 2.0)),
            line_style=str(payload.get("lineStyle", "--")),
            boundary_color=str(payload.get("boundaryColor", "#A1D99B")),
            use_type_colors=bool(payload.get("useTypeColors", False)),
        )
        _progress(request_id, 0.15, "Computing region masks")
        output_dir = _section_dir(config, "region_analysis")
        self.region_result = run_region_boundary_analysis(
            df_cells=self.assignment_result["df_cells"],
            celltype_mask=self.assignment_result["celltype_mask"],
            celltype_cfg=self.celltype_config,
            save_dir=output_dir,
            pixel_size_um=config.pixel_size_um,
            params=params,
            save_outputs=True,
        )
        preview_path = self._save_first_result_figure_preview(self.region_result, output_dir / "region_analysis_preview.png")
        _close_figures(self.region_result)
        boundaries = []
        for label, path in (self.region_result.get("saved_paths", {}).get("mask_paths", {}) or {}).items():
            boundaries.append({"label": str(label), "path": str(path)})
        self.distribution_result = None
        _progress(request_id, 1.0, "Region analysis complete")
        return {
            "summary": {"boundaryCount": len(boundaries)},
            "boundaries": boundaries,
            "previewPath": str(preview_path) if preview_path else None,
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def cell_distribution(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.assignment_result is None or self.region_result is None:
            raise RuntimeError("Run cell type assignment and region analysis before cell distribution analysis.")
        boundaries = list((self.region_result.get("saved_paths", {}).get("mask_paths", {}) or {}).items())
        if not boundaries:
            raise RuntimeError("Region analysis did not produce a boundary mask.")
        requested_label = str(payload.get("boundaryLabel") or boundaries[0][0])
        boundary_path = next((Path(value) for key, value in boundaries if str(key) == requested_label), Path(boundaries[0][1]))
        _progress(request_id, 0.08, "Building distance bands")
        distribution_config = read_distribution_config(Path(config.save_dir))
        region_masks = run_region_mask_band_analysis(
            config=distribution_config,
            data_result=self._ensure_pixels(),
            boundary_label=requested_label,
            boundary_mask_path=boundary_path,
            arrays_npz_path=None,
            band_width_um=float(payload.get("bandWidthUm", 10.0)),
            overlay_channels=[str(value) for value in payload.get("overlayChannels", [])],
        )
        _progress(request_id, 0.58, "Calculating cell density")
        selected_types = [str(value) for value in payload.get("selectedCellTypes", [])]
        if not selected_types:
            selected_types = [
                str(value)
                for value in self.assignment_result["df_cells"]["celltype"].astype(str).unique().tolist()
                if str(value) not in {"Unassigned", "Ambiguous"}
            ]
        density = run_cell_density_analysis(
            config=distribution_config,
            assignment_df=self.assignment_result["df_cells"],
            celltype_cfg=self.celltype_config,
            region_masks_result=region_masks,
            selected_celltypes=selected_types,
        )
        self.distribution_result = {"region_masks": region_masks, "density": density}
        output_dir = _section_dir(config, "cell_distribution_analysis")
        preview_candidates = sorted(output_dir.rglob("*.png"))
        _progress(request_id, 1.0, "Cell distribution analysis complete")
        return {
            "summary": {"selectedCellTypes": selected_types, "boundaryLabel": requested_label},
            "previewPath": str(preview_candidates[0]) if preview_candidates else None,
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def distance(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.assignment_result is None:
            raise RuntimeError("Run cell type assignment before distance analysis.")
        mode = str(payload.get("mode") or "nearest")
        present_types = [
            str(value)
            for value in self.assignment_result["df_cells"]["celltype"].astype(str).unique().tolist()
            if str(value) not in {"Unassigned", "Ambiguous"}
        ]
        target = str(payload.get("targetType") or (present_types[0] if present_types else ""))
        queries = [str(value) for value in (payload.get("queryTypes") or present_types[1:])]
        if not target or not queries:
            raise ValueError("Select a target cell type and at least one query cell type.")
        output_dir = _section_dir(config, "distance_analysis")
        _progress(request_id, 0.12, "Computing distances")
        if mode == "boundary":
            if self.region_result is None:
                raise RuntimeError("Run region analysis before cell-to-boundary distance analysis.")
            boundaries = list((self.region_result.get("saved_paths", {}).get("mask_paths", {}) or {}).items())
            if not boundaries:
                raise RuntimeError("No boundary mask is available.")
            boundary_label = str(payload.get("boundaryLabel") or boundaries[0][0])
            boundary_path = next((Path(value) for key, value in boundaries if str(key) == boundary_label), Path(boundaries[0][1]))
            result = run_boundary_distance_analysis(
                df_cells=self.assignment_result["df_cells"],
                celltype_cfg=self.celltype_config,
                celltype_mask=self.assignment_result["celltype_mask"],
                save_dir=output_dir,
                pixel_size_um=config.pixel_size_um,
                boundary_mask_path=boundary_path,
                boundary_name=boundary_label,
                query_types=queries,
                region_filter=str(payload.get("regionFilter") or "all"),
                save_outputs=True,
            )
        else:
            result = run_nearest_neighbor_analysis(
                df_cells=self.assignment_result["df_cells"],
                celltype_cfg=self.celltype_config,
                save_dir=output_dir,
                pixel_size_um=config.pixel_size_um,
                target_type=target,
                query_types=queries,
                save_outputs=True,
            )
        preview_path = self._save_first_result_figure_preview(result, output_dir / f"{mode}_distance_preview.png")
        _close_figures(result)
        _progress(request_id, 1.0, "Distance analysis complete")
        return {
            "summary": {"mode": mode, "targetType": target, "queryTypes": queries},
            "previewPath": str(preview_path) if preview_path else None,
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def outputs(self, _request_id: str, _payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        return {
            "outputFolder": str(config.save_dir),
            "files": list_output_files(Path(config.save_dir)),
            "artifacts": _artifact_manifest(Path(config.save_dir)),
        }

    def _require_config(self) -> PipelineConfig:
        if self.config is None:
            raise RuntimeError("Save the input configuration first.")
        return self.config

    def _ensure_pixels(self) -> Dict[str, Any]:
        config = self._require_config()
        if self.data_result is None:
            df_pixels, shapes = files_to_long_df(
                Path(config.folder),
                [channel.to_dict() for channel in config.channels],
                image_id=config.image_id,
                pixel_size_um=config.pixel_size_um,
            )
            self.data_result = {"df_pixels": df_pixels, "shapes": shapes}
        return self.data_result

    @staticmethod
    def _normalize_celltype(item: Dict[str, Any], index: int) -> Dict[str, Any]:
        return {
            "name": str(item.get("name") or f"Cell type {index + 1}"),
            "color_hex": str(item.get("color_hex") or item.get("colorHex") or COLOR_HEX_LIST[index % len(COLOR_HEX_LIST)]),
            "mode": str(item.get("mode") or "simple"),
            "all_pos": [str(value) for value in (item.get("all_pos") or item.get("allPositive") or [])],
            "all_neg": [str(value) for value in (item.get("all_neg") or item.get("allNegative") or [])],
            "any_pos_groups": [
                [str(value) for value in group]
                for group in (item.get("any_pos_groups") or item.get("anyPositiveGroups") or [])
                if isinstance(group, (list, tuple))
            ],
        }

    @staticmethod
    def _save_first_result_figure_preview(result: Dict[str, Any], path: Path) -> Path | None:
        figures: list[plt.Figure] = []

        def collect(value: Any) -> None:
            if isinstance(value, plt.Figure):
                figures.append(value)
            elif isinstance(value, dict):
                for child in value.values():
                    collect(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    collect(child)

        collect(result)
        if not figures:
            return None
        return _save_figure_preview(figures[0], path, dpi=105)


def run_json_lines() -> int:
    engine = NativeEngine()
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        request_id = ""
        try:
            request = json.loads(raw_line)
            request_id = str(request.get("id") or "")
            command = str(request.get("command") or "")
            payload = request.get("payload") or {}
            if command == "shutdown":
                _emit({"type": "result", "id": request_id, "data": {"shutdown": True}})
                return 0
            with contextlib.redirect_stdout(sys.stderr):
                data = engine.dispatch(request_id, command, payload)
            _emit({"type": "result", "id": request_id, "data": data})
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            _emit(
                {
                    "type": "error",
                    "id": request_id,
                    "message": str(error) or error.__class__.__name__,
                    "errorType": error.__class__.__name__,
                }
            )
    return 0


def run_backend_smoke_test() -> Dict[str, Any]:
    """Exercise every Matplotlib renderer required by the packaged engine.

    Matplotlib selects its SVG renderer dynamically from a file extension, so
    PyInstaller cannot discover it by following normal imports.  Keeping this
    check in the executable makes a missing ``backend_svg`` fail during the
    build instead of when a user reaches Composite Preview.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.backends.backend_svg import FigureCanvasSVG
    from matplotlib.figure import Figure

    figure = Figure(figsize=(1.0, 1.0))
    axis = figure.subplots()
    axis.plot([0.0, 1.0], [0.0, 1.0])

    png_buffer = io.BytesIO()
    FigureCanvasAgg(figure).print_png(png_buffer)
    png_payload = png_buffer.getvalue()
    if not png_payload.startswith(b"\x89PNG"):
        raise RuntimeError("Matplotlib Agg backend did not produce a PNG payload.")

    svg_buffer = io.BytesIO()
    FigureCanvasSVG(figure).print_svg(svg_buffer)
    svg_payload = svg_buffer.getvalue()
    if b"<svg" not in svg_payload:
        raise RuntimeError("Matplotlib SVG backend did not produce an SVG payload.")

    return {
        **NativeEngine().hello("smoke", {}),
        "matplotlibVersion": matplotlib.__version__,
        "aggPngBytes": len(png_payload),
        "svgBytes": len(svg_payload),
        "renderers": ["Agg", "SVG"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SpatialScope native Windows analysis engine")
    parser.add_argument("--json-lines", action="store_true", help="Read JSON requests from stdin and write JSON events to stdout.")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("MPLBACKEND", "Agg")
    if args.smoke_test:
        print(json.dumps(run_backend_smoke_test(), indent=2))
        return 0
    if args.json_lines:
        return run_json_lines()
    parser.error("Choose --json-lines or --smoke-test")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
