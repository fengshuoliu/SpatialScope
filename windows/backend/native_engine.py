from __future__ import annotations

import argparse
import base64
import copy
import contextlib
import hashlib
import html
import io
import json
import math
import mimetypes
import os
import re
import subprocess
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Sequence


CPU_COUNT = max(1, os.cpu_count() or 1)
for _thread_variable in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "NUMBA_NUM_THREADS",
):
    os.environ.setdefault(_thread_variable, str(CPU_COUNT))
os.environ.setdefault("OMP_MAX_ACTIVE_LEVELS", "1")
os.environ.setdefault("NUMBA_THREADING_LAYER", "omp")
os.environ.setdefault("KMP_WARNINGS", "0")

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

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
from src.spatialscope_analysis.compute_runtime import (  # noqa: E402
    ComputeRuntime,
    select_workflow_parallel_backend,
    set_compute_runtime,
)
from src.spatialscope_analysis.distance_analysis import (  # noqa: E402
    run_boundary_distance_analysis,
    run_nearest_neighbor_analysis,
)
from src.spatialscope_analysis.io import (  # noqa: E402
    discover_text_image_files,
    files_to_long_df,
    list_output_files,
    load_any_tiff,
    safe_name,
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
from src.spatialscope_analysis.region_analysis import (  # noqa: E402
    build_region_mask_from_cell_labels,
    draw_region_boundaries_rgb,
    make_region_canvas_rgb,
    run_region_boundary_analysis,
    save_adjusted_region_analysis,
    um_to_px_iso,
)
from src.spatialscope_analysis.visualization import (  # noqa: E402
    generate_distinct_hex,
    overlay_multi_channels,
    plot_split_channels,
)


PROTOCOL_VERSION = 1
ENGINE_VERSION = "1.2.3"
PROTOCOL_STDOUT = sys.stdout

REGION_CONTOUR_DOWNSAMPLES = (1, 2, 4, 8)
REGION_MORPHOLOGY_MAX_UM = 80.0
REGION_LINE_WIDTH_MIN = 0.5
REGION_LINE_WIDTH_MAX = 10.0

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

WORKFLOW_STAGES = (
    "inputs",
    "overlay",
    "nuclei",
    "cellTypes",
    "neighborhood",
    "region",
    "distribution",
    "distance",
    "outputs",
)
WORKFLOW_STATE_FILE = "windows_session_state.json"

ASSIGNMENT_OPTIMIZER_SEARCH_SPECS: Dict[str, Dict[str, Any]] = {
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


def _cpu_budget(payload: Dict[str, Any], key: str = "cpuWorkers", maximum: int | None = None) -> int:
    try:
        requested = int(payload.get(key, CPU_COUNT))
    except (TypeError, ValueError):
        requested = CPU_COUNT
    budget = max(1, min(requested, CPU_COUNT))
    return min(budget, maximum) if maximum is not None else budget


def _detect_windows_gpus() -> list[str]:
    if os.name != "nt":
        return []
    command = (
        "Get-CimInstance Win32_VideoController | "
        "Where-Object { $_.Name } | Select-Object -ExpandProperty Name | ConvertTo-Json -Compress"
    )
    try:
        system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows").resolve()
        powershell = (system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe").resolve()
        try:
            powershell.relative_to(system_root)
        except ValueError:
            return []
        if not powershell.is_file():
            return []
        completed = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            check=False,
            text=True,
            timeout=4,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return []
        parsed = json.loads(completed.stdout)
        values = parsed if isinstance(parsed, list) else [parsed]
        return [str(value).strip() for value in values if str(value).strip()]
    except Exception:
        return []


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


def _normalized_number(
    value: Any,
    label: str,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
    strictly_positive: bool = False,
    integer: bool = False,
) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    if strictly_positive and numeric <= minimum:
        raise ValueError(f"{label} must be greater than {minimum:g}")
    if not strictly_positive and numeric < minimum:
        raise ValueError(f"{label} must be at least {minimum:g}")
    if maximum is not None and numeric > maximum:
        raise ValueError(f"{label} must be at most {maximum:g}")
    if integer:
        if not numeric.is_integer():
            raise ValueError(f"{label} must be an integer")
        return int(numeric)
    return numeric


def _normalized_string_list(
    value: Any,
    label: str,
    *,
    allowed: set[str] | None = None,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} items must be nonempty strings")
        text = item.strip()
        if allowed is not None and text not in allowed:
            raise ValueError(f"{label} contains an unavailable value: {text}")
        if text not in seen:
            normalized.append(text)
            seen.add(text)
    if not allow_empty and not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _config_fingerprint(config: PipelineConfig) -> str:
    value = dict(config.to_json_dict())
    # Output folders may be moved or copied; the selected folder remains
    # authoritative and therefore is intentionally not part of the identity.
    value.pop("save_dir", None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


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


def _latest_existing_file(paths: Iterable[Path]) -> Path | None:
    existing = [Path(path) for path in paths if Path(path).is_file()]
    if not existing:
        return None
    return max(existing, key=lambda path: (path.stat().st_mtime_ns, path.name))


def _distribution_restore_preview_paths(distribution_dir: Path) -> Dict[str, Path]:
    distribution_dir = Path(distribution_dir)
    band_map = _latest_existing_file(
        (distribution_dir / "01_region_masks").glob("region_bands__*__band_map.png")
    )
    density_plot = _latest_existing_file(
        (distribution_dir / "02_cell_density").glob("cell_density__*__plot.png")
    )
    previews: Dict[str, Path] = {}
    if band_map is not None:
        previews["distribution"] = band_map
        previews["distributionBandMap"] = band_map
    if density_plot is not None:
        previews["distributionDensity"] = density_plot
    return previews


def _distribution_result_preview_contract(
    region_masks: Dict[str, Any],
    density: Dict[str, Any],
) -> Dict[str, str | None]:
    region_paths = region_masks.get("saved_paths", {}) if isinstance(region_masks, dict) else {}
    density_paths = density.get("saved_paths", {}) if isinstance(density, dict) else {}
    band_map = str(region_paths.get("png") or "").strip() or None
    density_plot = str(density_paths.get("png") or "").strip() or None
    return {
        "previewPath": band_map or density_plot,
        "bandMapPreviewPath": band_map,
        "densityPlotPreviewPath": density_plot,
    }


def _distribution_config_for_output(output_folder: Path):
    """Load plotting settings while keeping writes inside the active output root."""
    resolved_output = Path(output_folder).expanduser().resolve()
    distribution_config = read_distribution_config(resolved_output)
    # Saved projects can be copied or moved. The serialized save_dir records the
    # old location, but a restore explicitly selects resolved_output as the
    # active project root and every new artifact must remain beneath it.
    distribution_config.save_dir = resolved_output
    return distribution_config


class NativeEngine:
    def __init__(self, compute_runtime: ComputeRuntime | None = None) -> None:
        gpu_mode = str(os.environ.get("SPATIALSCOPE_GPU_MODE", "auto")).strip().lower()
        gpu_enabled = gpu_mode not in {"0", "false", "off", "cpu", "disabled"}
        parity_mode = str(os.environ.get("SPATIALSCOPE_GPU_PARITY_MODE", "off")).strip().lower()
        existing_runtime = getattr(self, "compute_runtime", None)
        self.compute_runtime = compute_runtime or existing_runtime or ComputeRuntime(
            cpu_workers=CPU_COUNT,
            enable_gpu=gpu_enabled,
            parity_mode=parity_mode,
            probe_devices=True,
        )
        set_compute_runtime(self.compute_runtime)
        self.gpu_mode = gpu_mode
        self.compute_capabilities = self.compute_runtime.capabilities()
        compatible_gpus = [
            descriptor
            for descriptor in self.compute_capabilities.get("gpuDevices", [])
            if bool(descriptor.get("compatible"))
        ]
        self.gpu_names = [str(descriptor.get("name") or "OpenCL GPU") for descriptor in compatible_gpus]
        if gpu_mode in {"require", "required"} and not self.gpu_names:
            reasons = "; ".join(self.compute_capabilities.get("fallbackReasons", []))
            raise RuntimeError(f"OpenCL GPU execution was required but no compatible GPU was found. {reasons}".strip())
        self.config: PipelineConfig | None = None
        self.data_result: Dict[str, Any] | None = None
        self.nuclei_result: Dict[str, Any] | None = None
        self.celltype_config: list[Dict[str, Any]] = []
        self.assignment_result: Dict[str, Any] | None = None
        self.neighborhood_result: Dict[str, Any] | None = None
        self.region_result: Dict[str, Any] | None = None
        self.distribution_result: Dict[str, Any] | None = None
        self.workflow_state = {stage: False for stage in WORKFLOW_STAGES}
        self.recommendation_state = {"nuclei": False, "assignment": False}
        self.pending_parameters: Dict[str, Dict[str, Any]] = {"nuclei": {}, "assignment": {}}
        self.analysis_parameters: Dict[str, Any] = {}

    def dispatch(self, request_id: str, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        handlers: Dict[str, Callable[[str, Dict[str, Any]], Dict[str, Any]]] = {
            "hello": self.hello,
            "configure": self.configure,
            "restore": self.restore,
            "overlay": self.overlay,
            "nuclei": self.nuclei,
            "nuclei_optimizer": self.nuclei_optimizer,
            "celltype_assignment": self.celltype_assignment,
            "celltype_optimizer": self.celltype_optimizer,
            "apply_recommendation": self.apply_recommendation,
            "neighborhood": self.neighborhood,
            "region": self.region,
            "region_preview": self.region_preview,
            "region_manual_preview": self.region_manual_preview,
            "region_manual_save": self.region_manual_save,
            "region_custom_export": self.region_custom_export,
            "cell_distribution": self.cell_distribution,
            "distance": self.distance,
            "outputs": self.outputs,
            "reset": self.reset,
        }
        if command not in handlers:
            raise KeyError(f"Unknown command: {command}")
        if command == "hello":
            return handlers[command](request_id, payload)
        with self.compute_runtime.request_scope(f"{request_id}:{command}") as compute_request:
            result = handlers[command](request_id, payload)
            result = dict(result)
            result["compute"] = compute_request.telemetry()
            return result

    def hello(self, _request_id: str, _payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "engineVersion": ENGINE_VERSION,
            "transport": "json-lines/file-manifests",
            "capabilities": [
                "configure",
                "restore",
                "overlay",
                "nuclei",
                "nuclei_optimizer",
                "celltype_assignment",
                "celltype_optimizer",
                "apply_recommendation",
                "neighborhood",
                "region",
                "region_preview",
                "region_manual_preview",
                "region_manual_save",
                "region_custom_export",
                "cell_distribution",
                "distance",
                "outputs",
            ],
            "compute": {
                "logicalCpuCount": CPU_COUNT,
                "defaultCpuWorkers": CPU_COUNT,
                "gpuMode": self.gpu_mode,
                "detectedGpus": list(self.gpu_names),
                "analysisGpuBackend": "OpenCL" if self.gpu_names else None,
                "gpuDevices": self.compute_runtime.device_descriptors(),
                "compatibleGpuCount": len(self.gpu_names),
                "parityMode": self.compute_runtime.parity_mode,
                "fallbackReasons": list(self.compute_capabilities.get("fallbackReasons", [])),
            },
        }

    def reset(self, _request_id: str, _payload: Dict[str, Any]) -> Dict[str, Any]:
        self.__init__()
        return {"reset": True}

    def _workflow_state_path(self) -> Path:
        config = self._require_config()
        return Path(config.save_dir) / SECTION_OUTPUT_SUBDIRS["config"] / WORKFLOW_STATE_FILE

    def _persist_workflow_state(self) -> None:
        config = self._require_config()
        state: Dict[str, Any] = {
            "schemaVersion": 1,
            "configFingerprint": _config_fingerprint(config),
            "stages": {stage: bool(self.workflow_state.get(stage, False)) for stage in WORKFLOW_STAGES},
            "recommendations": {
                kind: bool(self.recommendation_state.get(kind, False))
                for kind in ("nuclei", "assignment")
            },
            "pendingParameters": {
                kind: dict(self.pending_parameters.get(kind, {}))
                for kind in ("nuclei", "assignment")
            },
        }
        if self.analysis_parameters:
            state["analysisParameters"] = copy.deepcopy(self.analysis_parameters)
        _atomic_write_json(self._workflow_state_path(), state)

    def _reset_workflow_state(self) -> None:
        self.workflow_state = {stage: stage == "inputs" for stage in WORKFLOW_STAGES}
        self.recommendation_state = {"nuclei": False, "assignment": False}
        self.pending_parameters = {"nuclei": {}, "assignment": {}}
        self.analysis_parameters = {}
        self._persist_workflow_state()

    def _clear_analysis_parameters_from(self, stage: str, *, include_current: bool) -> None:
        start = WORKFLOW_STAGES.index(stage) + (0 if include_current else 1)
        analysis_stages = {
            "neighborhood": "neighborhood",
            "region": "region",
            "distribution": "distribution",
            "distance": "distance",
        }
        for key, owning_stage in analysis_stages.items():
            if WORKFLOW_STAGES.index(owning_stage) >= start:
                self.analysis_parameters.pop(key, None)

    def _invalidate_recommendations_from(self, stage: str) -> None:
        start = WORKFLOW_STAGES.index(stage)
        if start <= WORKFLOW_STAGES.index("nuclei"):
            self.recommendation_state["nuclei"] = False
            self.recommendation_state["assignment"] = False
        elif start <= WORKFLOW_STAGES.index("cellTypes"):
            self.recommendation_state["assignment"] = False

    def _mark_workflow_stage(self, stage: str, *, invalidate_after: bool = False) -> None:
        if stage not in WORKFLOW_STAGES:
            raise KeyError(f"Unknown workflow stage: {stage}")
        if invalidate_after:
            start = WORKFLOW_STAGES.index(stage) + 1
            for later in WORKFLOW_STAGES[start:]:
                self.workflow_state[later] = False
            self._clear_analysis_parameters_from(stage, include_current=False)
            self._invalidate_recommendations_from(stage)
        self.workflow_state[stage] = True
        self._persist_workflow_state()

    def _invalidate_workflow_from(
        self,
        stage: str,
        *,
        preserve_current: str | None = None,
        preserve_distance_parameters: bool = False,
        persist: bool = True,
    ) -> None:
        if stage not in WORKFLOW_STAGES:
            raise KeyError(f"Unknown workflow stage: {stage}")
        preserved_pending = (
            dict(self.pending_parameters.get(preserve_current, {}))
            if preserve_current in {"nuclei", "assignment"}
            else {}
        )
        preserved_distance = (
            copy.deepcopy(self.analysis_parameters.get("distance"))
            if preserve_distance_parameters and isinstance(self.analysis_parameters.get("distance"), dict)
            else None
        )
        start = WORKFLOW_STAGES.index(stage)
        for affected in WORKFLOW_STAGES[start:]:
            self.workflow_state[affected] = False
        if start <= WORKFLOW_STAGES.index("inputs"):
            self.data_result = None
            self.celltype_config = []
        if start <= WORKFLOW_STAGES.index("nuclei"):
            self.nuclei_result = None
            self.pending_parameters["nuclei"] = {}
            self.pending_parameters["assignment"] = {}
        if start <= WORKFLOW_STAGES.index("cellTypes"):
            self.assignment_result = None
            self.pending_parameters["assignment"] = {}
        if start <= WORKFLOW_STAGES.index("neighborhood"):
            self.neighborhood_result = None
        if start <= WORKFLOW_STAGES.index("region"):
            self.region_result = None
        if start <= WORKFLOW_STAGES.index("distribution"):
            self.distribution_result = None
        self._clear_analysis_parameters_from(stage, include_current=True)
        self._invalidate_recommendations_from(stage)
        if preserve_current in {"nuclei", "assignment"}:
            self.pending_parameters[preserve_current] = preserved_pending
        if preserved_distance is not None:
            self.analysis_parameters["distance"] = preserved_distance
        if persist:
            self._persist_workflow_state()

    def _validate_restored_analysis_parameters(
        self,
        raw: Any,
        workflow: Dict[str, bool],
        resolved_cell_types: Sequence[str],
        boundaries: Sequence[Dict[str, str]],
        warnings: list[str],
    ) -> Dict[str, Any]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            warnings.append("Saved analysis parameters were ignored: expected an object.")
            return {}

        available_types = {str(value) for value in resolved_cell_types}
        available_boundaries = {
            str(item.get("label") or "")
            for item in boundaries
            if str(item.get("label") or "")
        }
        available_channels = {
            channel.channel
            for channel in self._require_config().channels
        }

        def record(value: Any, label: str) -> Dict[str, Any]:
            if not isinstance(value, dict):
                raise ValueError(f"{label} must be an object")
            return value

        def text(value: Any, label: str) -> str:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a nonempty string")
            return value

        def number(
            value: Any,
            label: str,
            *,
            minimum: float = 0.0,
            maximum: float | None = None,
            strictly_positive: bool = False,
            integer: bool = False,
        ) -> float | int:
            return _normalized_number(
                value,
                label,
                minimum=minimum,
                maximum=maximum,
                strictly_positive=strictly_positive,
                integer=integer,
            )

        def string_list(
            value: Any,
            label: str,
            *,
            allowed: set[str] | None = None,
            allow_empty: bool = False,
        ) -> list[str]:
            return _normalized_string_list(
                value,
                label,
                allowed=allowed,
                allow_empty=allow_empty,
            )

        validated: Dict[str, Any] = {}

        if workflow.get("neighborhood") and "neighborhood" in raw:
            try:
                source = record(raw["neighborhood"], "neighborhood")
                raw_colors = record(source.get("clusterColors"), "neighborhood.clusterColors")
                colors = {
                    text(key, "neighborhood.clusterColors key"): text(
                        value,
                        "neighborhood.clusterColors value",
                    )
                    for key, value in raw_colors.items()
                }
                validated["neighborhood"] = {
                    "gridSizeUm": number(
                        source.get("gridSizeUm"),
                        "neighborhood.gridSizeUm",
                        strictly_positive=True,
                    ),
                    "clusterColors": colors,
                    "displayClusters": string_list(
                        source.get("displayClusters"),
                        "neighborhood.displayClusters",
                        allow_empty=True,
                    ),
                }
            except ValueError as error:
                warnings.append(f"Saved neighborhood analysis parameters were ignored: {error}")

        if workflow.get("region") and "region" in raw:
            try:
                source = record(raw["region"], "region")
                use_type_colors = source.get("useTypeColors")
                if not isinstance(use_type_colors, bool):
                    raise ValueError("region.useTypeColors must be a boolean")
                line_style = text(source.get("lineStyle"), "region.lineStyle").strip()
                if line_style not in {"-", "--", "-.", ":"}:
                    raise ValueError("region.lineStyle must be one of '-', '--', '-.', or ':'")
                boundary_color = text(source.get("boundaryColor"), "region.boundaryColor").strip()
                if not mcolors.is_color_like(boundary_color):
                    raise ValueError("region.boundaryColor must be a valid color value")
                contour_downsample = number(
                    source.get("contourDownsample"),
                    "region.contourDownsample",
                    strictly_positive=True,
                    integer=True,
                )
                if contour_downsample not in REGION_CONTOUR_DOWNSAMPLES:
                    choices = ", ".join(str(value) for value in REGION_CONTOUR_DOWNSAMPLES)
                    raise ValueError(f"region.contourDownsample must be one of {choices}")
                validated["region"] = {
                    "selectedTypes": string_list(
                        source.get("selectedTypes"),
                        "region.selectedTypes",
                        allowed=available_types,
                    ),
                    "closeUm": number(
                        source.get("closeUm"),
                        "region.closeUm",
                        maximum=REGION_MORPHOLOGY_MAX_UM,
                    ),
                    "dilateUm": number(
                        source.get("dilateUm"),
                        "region.dilateUm",
                        maximum=REGION_MORPHOLOGY_MAX_UM,
                    ),
                    "minAreaUm2": number(source.get("minAreaUm2"), "region.minAreaUm2"),
                    "minCells": number(
                        source.get("minCells"),
                        "region.minCells",
                        minimum=1.0,
                        integer=True,
                    ),
                    "contourDownsample": contour_downsample,
                    "lineWidth": number(
                        source.get("lineWidth"),
                        "region.lineWidth",
                        minimum=REGION_LINE_WIDTH_MIN,
                        maximum=REGION_LINE_WIDTH_MAX,
                    ),
                    "lineStyle": line_style,
                    "boundaryColor": boundary_color,
                    "useTypeColors": use_type_colors,
                }
            except ValueError as error:
                warnings.append(f"Saved region analysis parameters were ignored: {error}")

        if workflow.get("distribution") and "distribution" in raw:
            try:
                source = record(raw["distribution"], "distribution")
                boundary_label = text(source.get("boundaryLabel"), "distribution.boundaryLabel")
                if boundary_label not in available_boundaries:
                    raise ValueError(f"distribution.boundaryLabel is unavailable: {boundary_label}")
                validated["distribution"] = {
                    "boundaryLabel": boundary_label,
                    "bandWidthUm": number(
                        source.get("bandWidthUm"),
                        "distribution.bandWidthUm",
                        strictly_positive=True,
                    ),
                    "overlayChannels": string_list(
                        source.get("overlayChannels"),
                        "distribution.overlayChannels",
                        allowed=available_channels,
                        allow_empty=True,
                    ),
                    "selectedCellTypes": string_list(
                        source.get("selectedCellTypes"),
                        "distribution.selectedCellTypes",
                        allowed=available_types,
                    ),
                }
            except ValueError as error:
                warnings.append(f"Saved distribution analysis parameters were ignored: {error}")

        if workflow.get("distance") and "distance" in raw:
            try:
                source = record(raw["distance"], "distance")
                distance_parameters: Dict[str, Any] = {}
                for mode in ("nearest", "boundary"):
                    if mode not in source:
                        continue
                    try:
                        mode_source = record(source[mode], f"distance.{mode}")
                        saved_mode = text(mode_source.get("mode"), f"distance.{mode}.mode")
                        if saved_mode != mode:
                            raise ValueError(f"distance.{mode}.mode must be '{mode}'")
                        target_type = text(mode_source.get("targetType"), f"distance.{mode}.targetType")
                        if target_type not in available_types:
                            raise ValueError(f"distance.{mode}.targetType is unavailable: {target_type}")
                        mode_parameters: Dict[str, Any] = {
                            "mode": mode,
                            "targetType": target_type,
                            "queryTypes": string_list(
                                mode_source.get("queryTypes"),
                                f"distance.{mode}.queryTypes",
                                allowed=available_types,
                            ),
                        }
                        if mode == "boundary":
                            boundary_label = text(
                                mode_source.get("boundaryLabel"),
                                "distance.boundary.boundaryLabel",
                            )
                            if boundary_label not in available_boundaries:
                                raise ValueError(
                                    f"distance.boundary.boundaryLabel is unavailable: {boundary_label}"
                                )
                            region_filter = text(
                                mode_source.get("regionFilter"),
                                "distance.boundary.regionFilter",
                            )
                            if region_filter not in {"all", "inside", "outside"}:
                                raise ValueError(
                                    "distance.boundary.regionFilter must be 'all', 'inside', or 'outside'"
                                )
                            mode_parameters["boundaryLabel"] = boundary_label
                            mode_parameters["regionFilter"] = region_filter
                        distance_parameters[mode] = mode_parameters
                    except ValueError as error:
                        warnings.append(f"Saved {mode} distance parameters were ignored: {error}")

                last_mode = text(source.get("lastMode"), "distance.lastMode")
                if last_mode not in distance_parameters:
                    raise ValueError("distance.lastMode does not identify a valid saved distance mode")
                distance_parameters["lastMode"] = last_mode
                validated["distance"] = distance_parameters
            except ValueError as error:
                warnings.append(f"Saved distance analysis parameters were ignored: {error}")

        return validated

    def _output_record_is_current(self, relative_path: str) -> bool:
        normalized = str(relative_path).replace("\\", "/")
        parts = normalized.split("/", 1)
        top = parts[0]
        name = parts[-1]
        if top == SECTION_OUTPUT_SUBDIRS["config"]:
            return True
        if top == SECTION_OUTPUT_SUBDIRS["overlay"]:
            return bool(self.workflow_state["overlay"])
        if top == SECTION_OUTPUT_SUBDIRS["nuclei"]:
            is_optimizer = name.startswith("nuclei_native_optimizer") or name == "nuclei_optimizer_preview.png"
            return self.recommendation_state["nuclei"] if is_optimizer else bool(self.workflow_state["nuclei"])
        if top == SECTION_OUTPUT_SUBDIRS["celltype_definition"]:
            return (
                bool(self.workflow_state["cellTypes"])
                or self.recommendation_state["assignment"]
                or bool(self.pending_parameters["assignment"])
            )
        if top == SECTION_OUTPUT_SUBDIRS["celltype_assignment_parameters"]:
            return self.recommendation_state["assignment"]
        stage_by_subdir = {
            SECTION_OUTPUT_SUBDIRS["celltype_assignment"]: "cellTypes",
            SECTION_OUTPUT_SUBDIRS["neighborhood_analysis"]: "neighborhood",
            SECTION_OUTPUT_SUBDIRS["region_analysis"]: "region",
            SECTION_OUTPUT_SUBDIRS["integrated_region_analysis"]: "region",
            SECTION_OUTPUT_SUBDIRS["cell_distribution_analysis"]: "distribution",
            SECTION_OUTPUT_SUBDIRS["distance_analysis"]: "distance",
        }
        stage = stage_by_subdir.get(top)
        return bool(self.workflow_state.get(stage, False)) if stage else False

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
        self._reset_workflow_state()
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

    def restore(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        output_value = str(payload.get("outputFolder", "")).strip()
        if not output_value:
            raise ValueError("Output folder is required.")
        output_folder = Path(output_value).expanduser().resolve()
        config_path = output_folder / SECTION_OUTPUT_SUBDIRS["config"] / "pipeline_config.json"
        if not config_path.is_file():
            return {"restored": False, "outputFolder": str(output_folder)}

        _progress(request_id, 0.08, "Reading saved SpatialScope configuration")
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("The saved pipeline configuration is invalid.")

        saved_input_value = str(saved.get("folder") or "").strip()
        if not saved_input_value:
            raise ValueError("The saved pipeline configuration has no input folder.")
        input_folder = Path(saved_input_value).expanduser()
        if not input_folder.is_absolute():
            input_folder = (config_path.parent / input_folder).resolve()
        else:
            input_folder = input_folder.resolve()
        input_root = input_folder.resolve()
        raw_channels = saved.get("channels") or []
        if not isinstance(raw_channels, list):
            raise ValueError("The saved channel registry is invalid.")
        channels: list[ChannelConfig] = []
        for item in raw_channels:
            if not isinstance(item, dict):
                continue
            raw_file = str(item.get("file") or "").strip()
            if not raw_file:
                continue
            file_path = Path(raw_file).expanduser()
            candidate = file_path.resolve() if file_path.is_absolute() else (input_root / file_path).resolve()
            try:
                relative_file = candidate.relative_to(input_root)
            except ValueError as error:
                raise ValueError(f"A saved channel path escapes the input folder: {raw_file}") from error
            channels.append(
                ChannelConfig(
                    file=str(relative_file).replace("\\", "/"),
                    channel=str(item.get("channel") or relative_file.stem),
                    color_hex=str(item.get("color_hex") or item.get("colorHex") or "#FFFFFF"),
                )
            )
        if not channels:
            raise ValueError("The saved pipeline configuration has no usable channels.")
        pixel_size = saved.get("pixel_size_um") or [1.0, 1.0]
        if not isinstance(pixel_size, (list, tuple)) or len(pixel_size) < 2:
            pixel_size = [1.0, 1.0]
        self.config = PipelineConfig(
            folder=input_folder,
            save_dir=output_folder,
            pixel_size_um=(float(pixel_size[0]), float(pixel_size[1])),
            image_id=str(saved.get("image_id") or "FieldA"),
            channels=channels,
            overlay_channels=[str(value) for value in (saved.get("overlay_channels") or [])],
            white_channel=str(saved.get("white_channel") or "") or None,
            white_weight=float(saved.get("white_weight") or 0.0),
            input_mode=str(saved.get("input_mode") or "local"),
        )
        if not self.config.overlay_channels:
            self.config.overlay_channels = [channel.channel for channel in channels]

        self.data_result = None
        self.nuclei_result = None
        self.celltype_config = []
        self.assignment_result = None
        self.neighborhood_result = None
        self.region_result = None
        self.distribution_result = None
        self.analysis_parameters = {}
        warnings: list[str] = []
        if not input_folder.is_dir():
            warnings.append(f"The saved input folder is not currently available: {input_folder}")

        manifest_stages: Dict[str, bool] | None = None
        manifest_recommendations: Dict[str, bool] | None = None
        manifest_pending_parameters: Dict[str, Dict[str, Any]] = {"nuclei": {}, "assignment": {}}
        manifest_analysis_parameters: Any = None
        manifest_path = output_folder / SECTION_OUTPUT_SUBDIRS["config"] / WORKFLOW_STATE_FILE
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict) or int(manifest.get("schemaVersion", 0)) != 1:
                    raise ValueError("unsupported session-state schema")
                if str(manifest.get("configFingerprint") or "") != _config_fingerprint(self.config):
                    raise ValueError("session state belongs to a different configuration")
                raw_stages = manifest.get("stages")
                if not isinstance(raw_stages, dict):
                    raise ValueError("session state has no stage registry")
                manifest_stages = {stage: bool(raw_stages.get(stage, False)) for stage in WORKFLOW_STAGES}
                raw_recommendations = manifest.get("recommendations")
                manifest_recommendations = {
                    kind: bool(raw_recommendations.get(kind, False)) if isinstance(raw_recommendations, dict) else False
                    for kind in ("nuclei", "assignment")
                }
                raw_pending_parameters = manifest.get("pendingParameters")
                if isinstance(raw_pending_parameters, dict):
                    for kind in ("nuclei", "assignment"):
                        raw_values = raw_pending_parameters.get(kind)
                        if isinstance(raw_values, dict):
                            manifest_pending_parameters[kind] = {
                                str(key): _json_scalar(value)
                                for key, value in raw_values.items()
                                if isinstance(value, (str, int, float, bool)) or value is None
                            }
                manifest_analysis_parameters = manifest.get("analysisParameters")
                if manifest_pending_parameters["nuclei"]:
                    for stage in WORKFLOW_STAGES[WORKFLOW_STAGES.index("nuclei"):]:
                        manifest_stages[stage] = False
                elif manifest_pending_parameters["assignment"]:
                    for stage in WORKFLOW_STAGES[WORKFLOW_STAGES.index("cellTypes"):]:
                        manifest_stages[stage] = False
            except Exception as error:
                warnings.append(f"Saved workflow state was ignored: {error}")
                manifest_stages = {stage: stage == "inputs" for stage in WORKFLOW_STAGES}
                manifest_recommendations = {"nuclei": False, "assignment": False}
                manifest_pending_parameters = {"nuclei": {}, "assignment": {}}
                manifest_analysis_parameters = None
        else:
            warnings.append("Loaded a legacy result folder; validated artifacts were used to reconstruct workflow history.")

        def stage_allowed(stage: str) -> bool:
            return manifest_stages is None or bool(manifest_stages.get(stage, False))

        def recommendation_allowed(kind: str) -> bool:
            return manifest_recommendations is None or bool(manifest_recommendations.get(kind, False))

        overlay_dir = output_folder / SECTION_OUTPUT_SUBDIRS["overlay"]
        nuclei_dir = output_folder / SECTION_OUTPUT_SUBDIRS["nuclei"]
        definition_dir = output_folder / SECTION_OUTPUT_SUBDIRS["celltype_definition"]
        assignment_optimizer_dir = output_folder / SECTION_OUTPUT_SUBDIRS["celltype_assignment_parameters"]
        assignment_dir = output_folder / SECTION_OUTPUT_SUBDIRS["celltype_assignment"]
        neighborhood_dir = output_folder / SECTION_OUTPUT_SUBDIRS["neighborhood_analysis"]
        region_dir = output_folder / SECTION_OUTPUT_SUBDIRS["region_analysis"]
        distribution_dir = output_folder / SECTION_OUTPUT_SUBDIRS["cell_distribution_analysis"]
        distance_dir = output_folder / SECTION_OUTPUT_SUBDIRS["distance_analysis"]

        preview_paths = {
            "overlay": overlay_dir / "overlay_preview.png",
            "split": overlay_dir / "split_channels_preview.png",
            "nucleiOptimizer": nuclei_dir / "nuclei_optimizer_preview.png",
            "nuclei": nuclei_dir / "nuclei_segmentation_preview.png",
            "assignmentOptimizer": assignment_optimizer_dir / "celltype_assignment_optimizer_preview.png",
            "cellTypes": assignment_dir / "celltype_assignment_preview.png",
            "neighborhood": neighborhood_dir / "neighborhood_preview.png",
            "region": region_dir / "region_analysis_preview.png",
            "regionOverlay": region_dir / "region_analysis_preview__overlay.png",
            "regionMask": region_dir / "region_analysis_preview__mask.png",
            "distance_nearest": distance_dir / "nearest_distance_preview.png",
            "distance_boundary": distance_dir / "boundary_distance_preview.png",
        }
        if distribution_dir.is_dir():
            preview_paths.update(_distribution_restore_preview_paths(distribution_dir))

        _progress(request_id, 0.30, "Restoring saved nuclei and cell results")
        nuclei_params: Dict[str, Any] = {}
        nuclei_labels_path = nuclei_dir / "nuclei_labels_uint16.tiff"
        nuclei_params_path = nuclei_dir / "nuclei_params.json"
        if stage_allowed("nuclei") and nuclei_params_path.is_file():
            try:
                value = json.loads(nuclei_params_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    nuclei_params = value
            except Exception as error:
                warnings.append(f"Could not read saved nuclei parameters: {error}")
        if stage_allowed("nuclei") and nuclei_labels_path.is_file():
            try:
                raw_labels = np.asarray(load_any_tiff(nuclei_labels_path))
                if raw_labels.ndim != 2 or raw_labels.size == 0:
                    raise ValueError(f"expected a nonempty 2-D label mask, got shape {raw_labels.shape}")
                if not np.issubdtype(raw_labels.dtype, np.integer):
                    if not np.isfinite(raw_labels).all() or not np.equal(raw_labels, np.floor(raw_labels)).all():
                        raise ValueError("label mask contains non-integer values")
                minimum_label = int(np.min(raw_labels))
                maximum_label = int(np.max(raw_labels))
                if minimum_label < 0 or maximum_label <= 0 or maximum_label > np.iinfo(np.int32).max:
                    raise ValueError("label mask contains an invalid nucleus-label range")
                labels = raw_labels.astype(np.int32)
                self.nuclei_result = {
                    "labels": labels,
                    "n_nuclei": maximum_label,
                    "params": nuclei_params,
                }
            except Exception as error:
                warnings.append(f"Could not restore saved nuclei labels: {error}")

        definition_candidates = [
            definition_dir / "celltype_config.json",
            assignment_dir / "celltype_config.json",
        ]
        if (
            stage_allowed("cellTypes")
            or recommendation_allowed("assignment")
            or bool(manifest_pending_parameters["assignment"])
        ):
            for definition_path in definition_candidates:
                if not definition_path.is_file():
                    continue
                try:
                    value = json.loads(definition_path.read_text(encoding="utf-8"))
                    records = value.get("cell_types", []) if isinstance(value, dict) else value
                    if isinstance(records, list):
                        self.celltype_config = [
                            self._normalize_celltype(item, index)
                            for index, item in enumerate(records)
                            if isinstance(item, dict)
                        ]
                        break
                except Exception as error:
                    warnings.append(f"Could not restore saved cell type definitions: {error}")

        assignment_params: Dict[str, Any] = {}
        assignment_params_path = next(
            (
                candidate
                for candidate in (
                    assignment_dir / "celltype_assignment_params.json",
                    assignment_dir / "celltype_assignment_parameters.json",
                )
                if candidate.is_file()
            ),
            None,
        )
        if stage_allowed("cellTypes") and assignment_params_path is not None:
            try:
                value = json.loads(assignment_params_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    assignment_params = value
            except Exception as error:
                warnings.append(f"Could not read saved assignment parameters: {error}")

        cells_path = assignment_dir / "cells_summary.csv"
        celltype_mask_path = assignment_dir / "celltypes_mask_uint16.tiff"
        if stage_allowed("cellTypes") and self.nuclei_result is not None and cells_path.is_file() and celltype_mask_path.is_file():
            try:
                cells = pd.read_csv(cells_path)
                required_columns = {"label", "celltype"}
                if not required_columns.issubset(cells.columns):
                    raise ValueError(f"cells_summary.csv is missing columns: {sorted(required_columns - set(cells.columns))}")
                if cells.empty or not self.celltype_config:
                    raise ValueError("saved cell assignment or cell type definitions are empty")
                celltype_mask = np.asarray(load_any_tiff(celltype_mask_path))
                if celltype_mask.ndim != 2 or celltype_mask.shape != self.nuclei_result["labels"].shape:
                    raise ValueError(
                        "cell type mask shape does not match the restored nucleus-label mask: "
                        f"{celltype_mask.shape} != {self.nuclei_result['labels'].shape}"
                    )
                if not np.issubdtype(celltype_mask.dtype, np.integer) or int(np.min(celltype_mask)) < 0:
                    raise ValueError("cell type mask contains invalid values")
                celltype_mask = celltype_mask.astype(np.uint16)
                self.assignment_result = {
                    "df_cells": cells,
                    "celltype_mask": celltype_mask,
                    "counts": cells["celltype"].astype(str).value_counts().rename_axis("celltype").reset_index(name="count"),
                }
            except Exception as error:
                warnings.append(f"Could not restore saved cell type assignment: {error}")

        boundaries: list[Dict[str, Any]] = []
        region_records: list[Dict[str, Any]] = []
        registry_path = region_dir / "boundary_mask_registry.json"
        if stage_allowed("region") and self.assignment_result is not None and registry_path.is_file():
            try:
                region_records = self._load_region_records(warnings)
                if region_records:
                    self._set_region_records(region_records)
                    _, _, boundaries = self._region_catalog(region_records)
            except Exception as error:
                warnings.append(f"Could not restore saved region boundaries: {error}")

        nuclei_recommendation = (
            self._load_optimizer_recommendation(
                nuclei_dir / "nuclei_native_optimizer_recommendation.json",
                nuclei_dir / "nuclei_native_optimizer_results.csv",
                "nuclei",
            )
            if recommendation_allowed("nuclei")
            else {}
        )
        assignment_recommendation = (
            self._load_optimizer_recommendation(
                assignment_optimizer_dir / "celltype_assignment_native_optimizer_recommendation.json",
                assignment_optimizer_dir / "celltype_assignment_native_optimizer_results.csv",
                "assignment",
            )
            if recommendation_allowed("assignment")
            else {}
        )

        overlay_complete = stage_allowed("overlay") and all(
            (overlay_dir / name).is_file() for name in ("overlay_preview.png", "split_channels_preview.png")
        )
        nuclei_complete = self.nuclei_result is not None
        assignment_complete = nuclei_complete and self.assignment_result is not None
        neighborhood_complete = stage_allowed("neighborhood") and assignment_complete and all(
            (neighborhood_dir / name).is_file()
            for name in ("neighborhood_tile_assignments.csv", "neighborhood_preview.png")
        )
        region_complete = stage_allowed("region") and assignment_complete and self.region_result is not None
        distribution_complete = (
            stage_allowed("distribution")
            and
            region_complete
            and distribution_dir.is_dir()
            and any(distribution_dir.rglob("*__summary.csv"))
            and any(distribution_dir.rglob("*.png"))
        )
        distance_complete = (
            stage_allowed("distance")
            and
            assignment_complete
            and distance_dir.is_dir()
            and any(distance_dir.rglob("*.csv"))
            and any(distance_dir.rglob("*.png"))
        )
        validated_workflow = {
            "inputs": True,
            "overlay": overlay_complete,
            "nuclei": nuclei_complete,
            "cellTypes": assignment_complete,
            "neighborhood": neighborhood_complete,
            "region": region_complete,
            "distribution": distribution_complete,
            "distance": distance_complete,
            "outputs": distance_complete,
        }
        workflow = (
            validated_workflow
            if manifest_stages is None
            else {
                stage: bool(manifest_stages.get(stage, False)) and bool(validated_workflow.get(stage, False))
                for stage in WORKFLOW_STAGES
            }
        )
        workflow["inputs"] = True

        resolved_cell_types: list[str] = []
        if self.assignment_result is not None:
            resolved_cell_types = [
                str(value)
                for value in self.assignment_result["df_cells"]["celltype"].astype(str).unique().tolist()
                if str(value) not in {"Unassigned", "Ambiguous"}
            ]

        self.workflow_state = dict(workflow)
        self.recommendation_state = {
            "nuclei": bool(nuclei_recommendation),
            "assignment": bool(assignment_recommendation),
        }
        self.pending_parameters = {
            "nuclei": dict(manifest_pending_parameters["nuclei"]),
            "assignment": dict(manifest_pending_parameters["assignment"]),
        }
        self.analysis_parameters = self._validate_restored_analysis_parameters(
            manifest_analysis_parameters,
            workflow,
            resolved_cell_types,
            boundaries,
            warnings,
        )
        regions: list[Dict[str, Any]] = []
        dominant_counts: list[Dict[str, Any]] = []
        if region_records:
            region_records = self._load_region_records([])
            self._set_region_records(region_records)
            regions, dominant_counts, boundaries = self._region_catalog(region_records)
        self._persist_workflow_state()
        if neighborhood_complete:
            self.neighborhood_result = {}
        if distribution_complete:
            self.distribution_result = {}

        preview_stage = {
            "overlay": "overlay",
            "split": "overlay",
            "nuclei": "nuclei",
            "cellTypes": "cellTypes",
            "neighborhood": "neighborhood",
            "region": "region",
            "regionOverlay": "region",
            "regionMask": "region",
            "distribution": "distribution",
            "distributionBandMap": "distribution",
            "distributionDensity": "distribution",
            "distance_nearest": "distance",
            "distance_boundary": "distance",
        }
        restored_preview_paths = {
            key: str(path)
            for key, path in preview_paths.items()
            if path.is_file()
            and (
                (key == "nucleiOptimizer" and self.recommendation_state["nuclei"])
                or (key == "assignmentOptimizer" and self.recommendation_state["assignment"])
                or (key in preview_stage and bool(workflow.get(preview_stage[key], False)))
            )
        }

        files = [
            record
            for record in list_output_files(output_folder)
            if self._output_record_is_current(str(record.get("relative_path") or ""))
        ]
        artifacts = [
            record
            for record in _artifact_manifest(output_folder)
            if self._output_record_is_current(str(record.get("relativePath") or ""))
        ]
        _progress(request_id, 1.0, "Saved SpatialScope results restored")
        if self.assignment_result is not None:
            restored_height, restored_width = (int(value) for value in self.assignment_result["celltype_mask"].shape)
        else:
            restored_width, restored_height = 0, 0
        return {
            "restored": True,
            "configuration": {
                "inputFolder": str(input_folder),
                "outputFolder": str(output_folder),
                "pixelSizeUm": list(self.config.pixel_size_um),
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
            },
            "workflow": workflow,
            "analysisParameters": copy.deepcopy(self.analysis_parameters),
            "regionParameters": copy.deepcopy(self.analysis_parameters.get("region", {})),
            "previewPaths": restored_preview_paths,
            "nucleiParameters": self.pending_parameters["nuclei"] or nuclei_params,
            "assignmentParameters": self.pending_parameters["assignment"] or assignment_params,
            "nucleiRecommendation": nuclei_recommendation,
            "assignmentRecommendation": assignment_recommendation,
            "cellTypes": self.celltype_config,
            "resolvedCellTypes": resolved_cell_types,
            "regions": regions,
            "dominantCounts": dominant_counts,
            "boundaries": boundaries,
            "width": restored_width,
            "height": restored_height,
            "files": files,
            "artifacts": artifacts,
            "warnings": warnings,
        }

    def overlay(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        self._invalidate_workflow_from("overlay")
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
        data["overlay_rgb"] = np.asarray(overlay_rgb, dtype=float)
        data["overlay_clip_high_percentile"] = float(payload.get("clipHighPercentile", 99.8))
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

        self._mark_workflow_stage("overlay")
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
        self._invalidate_workflow_from("nuclei", preserve_current="nuclei")
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
            native_threads=_cpu_budget(payload, "nativeThreads"),
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
        self.pending_parameters["nuclei"] = {}
        self._mark_workflow_stage("nuclei", invalidate_after=True)
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
            parallel_workers=_cpu_budget(payload, "parallelWorkers", max_evaluations),
            parallel_backend=select_workflow_parallel_backend(
                payload.get("parallelBackend"),
                shared_gpu_runtime_enabled=bool(self.gpu_names),
            ),
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
        write_json(output_dir / "nuclei_native_optimizer_recommendation.json", recommended)
        preview_path = self._save_first_result_figure_preview(result, output_dir / "nuclei_optimizer_preview.png")
        evaluated = int(result.get("evaluated_unique_combinations", len(result.get("results", []))))
        _close_figures(result)
        self.recommendation_state["nuclei"] = bool(recommended)
        self._persist_workflow_state()
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
        normalized_definitions = [self._normalize_celltype(item, index) for index, item in enumerate(definitions)]
        preserve_current = "assignment" if normalized_definitions == self.celltype_config else None
        self._invalidate_workflow_from("cellTypes", preserve_current=preserve_current)
        self.celltype_config = normalized_definitions
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
            native_threads=_cpu_budget(payload, "nativeThreads"),
            support_workers=_cpu_budget(payload, "supportWorkers"),
        )
        write_json(output_dir / "celltype_assignment_params.json", params.to_dict())
        write_json(output_dir / "celltype_assignment_parameters.json", params.to_dict())
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
        self.pending_parameters["assignment"] = {}
        self._mark_workflow_stage("cellTypes", invalidate_after=True)
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
        normalized_definitions = [
            self._normalize_celltype(item, index)
            for index, item in enumerate(definitions)
        ]
        if normalized_definitions != self.celltype_config:
            self._invalidate_workflow_from("cellTypes")
        self.celltype_config = normalized_definitions
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
        search_specs = payload.get("searchSpecs") or copy.deepcopy(ASSIGNMENT_OPTIMIZER_SEARCH_SPECS)
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
            parallel_workers=_cpu_budget(payload, "parallelWorkers", max_evaluations),
            parallel_backend=select_workflow_parallel_backend(
                payload.get("parallelBackend"),
                shared_gpu_runtime_enabled=bool(self.gpu_names),
            ),
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
        write_json(output_dir / "celltype_assignment_native_optimizer_recommendation.json", recommended)
        preview_path = self._save_first_result_figure_preview(result, output_dir / "celltype_assignment_optimizer_preview.png")
        evaluated = int(result.get("evaluated_unique_combinations", len(result.get("results", []))))
        _close_figures(result)
        self.recommendation_state["assignment"] = bool(recommended)
        self._persist_workflow_state()
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
        grid_size_um = float(
            _normalized_number(
                payload.get("gridSizeUm", 20.0),
                "Neighborhood gridSizeUm",
                strictly_positive=True,
            )
        )
        raw_cluster_colors = payload.get("clusterColors") or {}
        if not isinstance(raw_cluster_colors, dict):
            raise ValueError("Neighborhood clusterColors must be an object.")
        requested_colors: Dict[str, str] = {}
        for raw_label, raw_color in raw_cluster_colors.items():
            if not isinstance(raw_label, str) or not raw_label.strip():
                raise ValueError("Neighborhood clusterColors keys must be nonempty strings.")
            if not isinstance(raw_color, str) or not raw_color.strip():
                raise ValueError("Neighborhood clusterColors values must be nonempty strings.")
            requested_colors[raw_label.strip()] = raw_color.strip()
        raw_display_clusters = payload.get("displayClusters")
        requested_display_clusters = (
            _normalized_string_list(
                raw_display_clusters,
                "Neighborhood displayClusters",
                allow_empty=True,
            )
            if raw_display_clusters is not None
            else []
        )
        self._invalidate_workflow_from("neighborhood")
        _progress(request_id, 0.12, "Building the neighborhood grid")
        self.neighborhood_result = run_neighborhood_analysis(
            df_cells=self.assignment_result["df_cells"],
            image_shape=tuple(self.nuclei_result["labels"].shape),
            pixel_size_um=config.pixel_size_um,
            grid_size_um=grid_size_um,
        )
        labels = [str(value) for value in self.neighborhood_result.get("cluster_labels", [])]
        palette = generate_distinct_hex(max(1, len(labels)))
        colors = {
            label: str(requested_colors.get(label) or palette[index])
            for index, label in enumerate(labels)
        }
        display_clusters = requested_display_clusters or list(dict.fromkeys(labels))
        _progress(request_id, 0.72, "Saving neighborhood outputs")
        output_dir = _section_dir(config, "neighborhood_analysis")
        saved = save_neighborhood_analysis_outputs(
            self.neighborhood_result,
            output_dir,
            config.pixel_size_um,
            colors,
            display_cluster_labels=display_clusters,
            save_outputs=True,
        )
        preview_path = self._save_first_result_figure_preview(saved, output_dir / "neighborhood_preview.png")
        _close_figures(saved)
        self.region_result = None
        self.distribution_result = None
        self.analysis_parameters["neighborhood"] = {
            "gridSizeUm": grid_size_um,
            "clusterColors": dict(colors),
            "displayClusters": list(display_clusters),
        }
        self._mark_workflow_stage("neighborhood", invalidate_after=True)
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

    def _present_region_cell_types(self) -> list[str]:
        if self.assignment_result is None:
            return []
        present = {
            str(value)
            for value in self.assignment_result["df_cells"]["celltype"].astype(str).tolist()
            if str(value) not in {"Unassigned", "Ambiguous"}
        }
        configured = [str(item.get("name") or "") for item in self.celltype_config]
        ordered = [name for name in configured if name in present]
        ordered.extend(sorted(present.difference(ordered), key=str.casefold))
        return ordered

    def _normalize_region_parameters(
        self,
        payload: Dict[str, Any],
        selected_types: Sequence[str],
        *,
        inherit_saved: bool = False,
    ) -> tuple[RegionParams, Dict[str, Any]]:
        saved = self.analysis_parameters.get("region") if inherit_saved else None
        base = saved if isinstance(saved, dict) else {}

        def value(key: str, fallback: Any) -> Any:
            return payload[key] if key in payload else base.get(key, fallback)

        close_um = float(
            _normalized_number(
                value("closeUm", 15.0),
                "Region closeUm",
                maximum=REGION_MORPHOLOGY_MAX_UM,
            )
        )
        dilate_um = float(
            _normalized_number(
                value("dilateUm", 10.0),
                "Region dilateUm",
                maximum=REGION_MORPHOLOGY_MAX_UM,
            )
        )
        min_area_um2 = float(_normalized_number(value("minAreaUm2", 20000.0), "Region minAreaUm2"))
        min_cells = int(
            _normalized_number(
                value("minCells", 5),
                "Region minCells",
                minimum=1.0,
                integer=True,
            )
        )
        contour_downsample = int(
            _normalized_number(
                value("contourDownsample", 2),
                "Region contourDownsample",
                strictly_positive=True,
                integer=True,
            )
        )
        if contour_downsample not in REGION_CONTOUR_DOWNSAMPLES:
            choices = ", ".join(str(value) for value in REGION_CONTOUR_DOWNSAMPLES)
            raise ValueError(f"Region contourDownsample must be one of {choices}.")
        line_width = float(
            _normalized_number(
                value("lineWidth", 2.0),
                "Region lineWidth",
                minimum=REGION_LINE_WIDTH_MIN,
                maximum=REGION_LINE_WIDTH_MAX,
            )
        )
        line_style = value("lineStyle", "-")
        boundary_color = value("boundaryColor", "#A1D99B")
        use_type_colors = value("useTypeColors", False)
        if not isinstance(line_style, str) or line_style.strip() not in {"-", "--", "-.", ":"}:
            raise ValueError("Region lineStyle must be one of '-', '--', '-.', or ':'.")
        if not isinstance(boundary_color, str) or not boundary_color.strip() or not mcolors.is_color_like(boundary_color.strip()):
            raise ValueError("Region boundaryColor must be a valid color value.")
        if not isinstance(use_type_colors, bool):
            raise ValueError("Region useTypeColors must be a boolean.")

        params = RegionParams(
            selected_types=list(selected_types),
            close_um=close_um,
            dilate_um=dilate_um,
            min_area_um2=min_area_um2,
            min_cells=min_cells,
            contour_downsample=contour_downsample,
            line_width=line_width,
            line_style=line_style.strip(),
            boundary_color=boundary_color.strip(),
            use_type_colors=use_type_colors,
        )
        normalized = {
            "selectedTypes": list(params.selected_types),
            "closeUm": float(params.close_um),
            "dilateUm": float(params.dilate_um),
            "minAreaUm2": float(params.min_area_um2),
            "minCells": int(params.min_cells),
            "contourDownsample": int(params.contour_downsample),
            "lineWidth": float(params.line_width),
            "lineStyle": str(params.line_style),
            "boundaryColor": str(params.boundary_color),
            "useTypeColors": bool(params.use_type_colors),
        }
        return params, normalized

    @staticmethod
    def _region_source_type(record: Dict[str, Any]) -> str:
        explicit = str(record.get("source_type") or "").strip().lower()
        if explicit in {"computational", "manual", "adjusted"}:
            return explicit
        source = str(record.get("source") or "").strip().lower()
        if source == "manual_roi_selection":
            return "manual"
        if source == "manual_boundary_adjustment":
            return "adjusted"
        return "computational"

    @staticmethod
    def _stable_region_id(relative_path: str, record: Dict[str, Any]) -> str:
        explicit = str(record.get("id") or "").strip()
        if re.fullmatch(r"roi_[0-9a-fA-F]{16}", explicit):
            return explicit.lower()
        identity = "|".join(
            [
                str(record.get("source") or ""),
                str(record.get("group_name") or ""),
                str(record.get("mask_key") or ""),
                str(relative_path),
            ]
        )
        return f"roi_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"

    def _load_region_records(self, warnings: list[str] | None = None) -> list[Dict[str, Any]]:
        warnings = warnings if warnings is not None else []
        config = self._require_config()
        if self.assignment_result is None:
            return []
        shape = tuple(int(value) for value in self.assignment_result["celltype_mask"].shape)
        region_dir = Path(config.save_dir) / SECTION_OUTPUT_SUBDIRS["region_analysis"]
        registry_path = region_dir / "boundary_mask_registry.json"
        raw_records: list[Dict[str, Any]] = []
        if registry_path.is_file():
            try:
                payload = json.loads(registry_path.read_text(encoding="utf-8"))
                records = payload.get("entries", []) if isinstance(payload, dict) else payload
                if isinstance(records, list):
                    raw_records = [dict(item) for item in records if isinstance(item, dict)]
            except Exception as error:
                warnings.append(f"Could not read the Region boundary registry: {error}")
        if not raw_records and isinstance(self.region_result, dict):
            for label, path in (self.region_result.get("saved_paths", {}).get("mask_paths", {}) or {}).items():
                raw_records.append(
                    {
                        "mask_path": str(Path(path).name),
                        "display_name": str(label),
                        "mask_key": str(label),
                        "source": "computational_roi_identification",
                    }
                )

        region_root = region_dir.resolve()
        color_by_type = {
            str(item.get("name") or ""): str(item.get("color_hex") or "#A1D99B")
            for item in self.celltype_config
        }
        saved_parameters = self.analysis_parameters.get("region")
        saved_parameters = saved_parameters if isinstance(saved_parameters, dict) else {}
        default_color = str(saved_parameters.get("boundaryColor") or "#A1D99B")
        use_type_colors = bool(saved_parameters.get("useTypeColors", False))
        loaded: list[Dict[str, Any]] = []
        seen_paths: set[str] = set()
        for raw in raw_records:
            relative = str(raw.get("mask_path") or "").strip().replace("\\", "/")
            if not relative or relative in seen_paths:
                continue
            candidate = (region_dir / relative).resolve()
            try:
                candidate.relative_to(region_root)
            except ValueError:
                warnings.append(f"Ignored a Region boundary outside the Region output folder: {relative}")
                continue
            if not candidate.is_file():
                warnings.append(f"Ignored a missing Region boundary mask: {relative}")
                continue
            try:
                mask_array = np.asarray(load_any_tiff(candidate))
                if mask_array.ndim != 2 or tuple(mask_array.shape) != shape:
                    raise ValueError(f"expected shape {shape}, got {mask_array.shape}")
                mask = mask_array.astype(bool)
                if not np.any(mask):
                    warnings.append(f"Ignored empty Region boundary mask: {raw.get('display_name') or candidate.stem}")
                    continue
            except Exception as error:
                warnings.append(f"Ignored unreadable Region boundary mask {relative}: {error}")
                continue

            source_type = self._region_source_type(raw)
            mask_key = str(raw.get("mask_key") or raw.get("display_name") or candidate.stem)
            stored_color = str(raw.get("color_hex") or "").strip()
            if stored_color and not mcolors.is_color_like(stored_color):
                stored_color = ""
            color_hex = stored_color or (
                color_by_type.get(mask_key, default_color)
                if source_type == "computational" and use_type_colors
                else default_color
            )
            original_path: Path | None = None
            original_relative = str(raw.get("original_mask_path") or "").strip().replace("\\", "/")
            if original_relative:
                possible_original = (region_dir / original_relative).resolve()
                try:
                    possible_original.relative_to(region_root)
                    if possible_original.is_file():
                        original_path = possible_original
                except ValueError:
                    original_path = None
            loaded.append(
                {
                    "id": self._stable_region_id(relative, raw),
                    "label": str(raw.get("display_name") or mask_key or candidate.stem).strip() or candidate.stem,
                    "path": candidate,
                    "relativePath": relative,
                    "sourceType": source_type,
                    "maskKey": mask_key,
                    "colorHex": mcolors.to_hex(color_hex, keep_alpha=False),
                    "mask": mask,
                    "modifiedUtc": float(candidate.stat().st_mtime),
                    "originalPath": original_path,
                    "originalLabel": str(raw.get("original_label") or "").strip() or None,
                    "registry": raw,
                }
            )
            seen_paths.add(relative)

        latest_computational: dict[str, Dict[str, Any]] = {}
        retained: list[Dict[str, Any]] = []
        for record in loaded:
            if record["sourceType"] != "computational":
                retained.append(record)
                continue
            key = str(record["label"]).casefold()
            previous = latest_computational.get(key)
            if previous is None or (record["modifiedUtc"], record["relativePath"]) > (
                previous["modifiedUtc"],
                previous["relativePath"],
            ):
                latest_computational[key] = record
        if len(latest_computational) < sum(record["sourceType"] == "computational" for record in loaded):
            warnings.append("Older duplicate computational Region boundaries were hidden in favor of the newest saved mask.")
        retained.extend(latest_computational.values())
        retained.sort(key=lambda item: (str(item["sourceType"]), str(item["label"]).casefold(), str(item["id"])))

        used_labels: set[str] = set()
        for record in retained:
            base_label = str(record["label"])
            label = base_label
            suffix = 2
            while label.casefold() in used_labels:
                qualifier = str(record["sourceType"]).capitalize()
                label = f"{base_label} ({qualifier})" if suffix == 2 else f"{base_label} ({qualifier} {suffix})"
                suffix += 1
            record["label"] = label
            used_labels.add(label.casefold())
        return retained

    def _region_catalog(
        self,
        records: Sequence[Dict[str, Any]],
    ) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]]]:
        config = self._require_config()
        if self.assignment_result is None:
            return [], [], []
        df_cells = self.assignment_result["df_cells"]
        shape = self.assignment_result["celltype_mask"].shape
        height, width = int(shape[0]), int(shape[1])
        cy = np.clip(np.rint(df_cells["centroid_y_px"].to_numpy(float)).astype(int), 0, height - 1)
        cx = np.clip(np.rint(df_cells["centroid_x_px"].to_numpy(float)).astype(int), 0, width - 1)
        cell_types = df_cells["celltype"].astype(str).to_numpy()
        available_types = self._present_region_cell_types()
        type_order = {name: index for index, name in enumerate(available_types)}
        type_colors = {
            str(item.get("name") or ""): mcolors.to_hex(str(item.get("color_hex") or "#A1D99B"))
            for item in self.celltype_config
        }
        px_area_um2 = float(config.pixel_size_um[0]) * float(config.pixel_size_um[1])
        rows: list[Dict[str, Any]] = []
        dominant_region_counts: dict[str, int] = {}
        boundaries: list[Dict[str, Any]] = []
        for record in records:
            mask = np.asarray(record["mask"], dtype=bool)
            inside_types = cell_types[mask[cy, cx]]
            counts: Dict[str, int] = {}
            for name in available_types:
                count = int(np.count_nonzero(inside_types == name))
                if count > 0:
                    counts[name] = count
            dominant_type: str | None = None
            if counts:
                dominant_type = min(
                    counts,
                    key=lambda name: (-counts[name], type_order.get(name, len(type_order)), name.casefold()),
                )
                dominant_region_counts[dominant_type] = dominant_region_counts.get(dominant_type, 0) + 1
            rows.append(
                {
                    "id": str(record["id"]),
                    "label": str(record["label"]),
                    "sourceType": str(record["sourceType"]),
                    "dominantType": dominant_type,
                    "cellCount": int(sum(counts.values())),
                    "areaUm2": float(np.count_nonzero(mask) * px_area_um2),
                    "colorHex": str(record["colorHex"]),
                    "countsByType": counts,
                }
            )
            boundaries.append(
                {
                    "id": str(record["id"]),
                    "label": str(record["label"]),
                    "path": str(record["path"]),
                    "sourceType": str(record["sourceType"]),
                }
            )
        dominant_counts = [
            {
                "name": name,
                "count": int(count),
                "colorHex": type_colors.get(name, "#A1D99B"),
            }
            for name, count in sorted(
                dominant_region_counts.items(),
                key=lambda item: (-item[1], type_order.get(item[0], len(type_order)), item[0].casefold()),
            )
        ]
        return rows, dominant_counts, boundaries

    def _region_displayed_cell_count(
        self,
        records: Sequence[Dict[str, Any]],
        selected_cell_types: Sequence[str],
    ) -> int:
        """Count unique selected cells whose centroids fall in any displayed ROI."""
        if self.assignment_result is None or not records or not selected_cell_types:
            return 0
        shape = tuple(int(value) for value in self.assignment_result["celltype_mask"].shape)
        displayed_mask = np.zeros(shape, dtype=bool)
        for record in records:
            mask = np.asarray(record["mask"], dtype=bool)
            if mask.shape != shape:
                raise ValueError(
                    f"Region boundary {record.get('label') or record.get('id') or '<unknown>'} "
                    f"has shape {mask.shape}, expected {shape}."
                )
            displayed_mask |= mask

        df_cells = self.assignment_result["df_cells"]
        height, width = shape
        cy = np.clip(np.rint(df_cells["centroid_y_px"].to_numpy(float)).astype(int), 0, height - 1)
        cx = np.clip(np.rint(df_cells["centroid_x_px"].to_numpy(float)).astype(int), 0, width - 1)
        selected = df_cells["celltype"].astype(str).isin(selected_cell_types).to_numpy(dtype=bool)
        return int(np.count_nonzero(selected & displayed_mask[cy, cx]))

    @staticmethod
    def _boundary_cell_type_mode(payload: Dict[str, Any]) -> str:
        raw_mode = payload.get("boundaryCellTypeMode", "content")
        if not isinstance(raw_mode, str) or raw_mode.strip().lower() not in {"content", "source"}:
            raise ValueError("Region boundaryCellTypeMode must be 'content' or 'source'.")
        return raw_mode.strip().lower()

    def _preferred_region_cell_type(
        self,
        record: Dict[str, Any],
        row: Dict[str, Any],
        record_rows: Dict[str, tuple[Dict[str, Any], Dict[str, Any]]],
        seen_ids: set[str] | None = None,
    ) -> str | None:
        available = self._present_region_cell_types()
        available_by_fold = {name.casefold(): name for name in available}

        def canonical(value: Any) -> str | None:
            text = str(value or "").strip()
            if not text or text in {"Unassigned", "Ambiguous"}:
                return None
            direct = available_by_fold.get(text.casefold())
            if direct is not None:
                return direct
            for name in available:
                if text.casefold().startswith(f"{name} (".casefold()):
                    return name
            return None

        record_id = str(record.get("id") or "")
        visited = set(seen_ids or set())
        if record_id in visited:
            return canonical(row.get("dominantType"))
        visited.add(record_id)
        registry = record.get("registry") if isinstance(record.get("registry"), dict) else {}
        source_type = str(record.get("sourceType") or "")

        direct_candidates: list[Any] = []
        if source_type == "computational":
            direct_candidates.extend((record.get("maskKey"), record.get("label")))
        else:
            direct_candidates.extend(
                (
                    registry.get("target_cell_type"),
                    registry.get("original_source_type"),
                )
            )
        for candidate in direct_candidates:
            resolved = canonical(candidate)
            if resolved is not None:
                return resolved

        target_ids = [registry.get("target_boundary_id"), registry.get("original_region_id")]
        target_labels = [
            registry.get("target_boundary_label"),
            registry.get("original_label"),
            registry.get("target_type"),
        ]
        for target_id in target_ids:
            target_pair = record_rows.get(str(target_id or ""))
            if target_pair is not None and str(target_pair[0].get("id")) != record_id:
                resolved = self._preferred_region_cell_type(target_pair[0], target_pair[1], record_rows, visited)
                if resolved is not None:
                    return resolved
        for target_label in target_labels:
            label_text = str(target_label or "").strip()
            if not label_text:
                continue
            resolved = canonical(label_text)
            if resolved is not None:
                return resolved
            target_pair = next(
                (
                    pair
                    for pair in record_rows.values()
                    if str(pair[0].get("label") or "") == label_text
                    or str((pair[0].get("registry") or {}).get("display_name") or "") == label_text
                ),
                None,
            )
            if target_pair is not None and str(target_pair[0].get("id")) != record_id:
                resolved = self._preferred_region_cell_type(target_pair[0], target_pair[1], record_rows, visited)
                if resolved is not None:
                    return resolved

        raw_seed_types = registry.get("seed_cell_types")
        if isinstance(raw_seed_types, str):
            raw_seed_types = [raw_seed_types]
        if isinstance(raw_seed_types, list):
            for seed_type in raw_seed_types:
                resolved = canonical(seed_type)
                if resolved is not None:
                    return resolved
        for candidate in (record.get("maskKey"), record.get("label"), row.get("dominantType")):
            resolved = canonical(candidate)
            if resolved is not None:
                return resolved
        return None

    def _filter_region_records_by_cell_types(
        self,
        records: Sequence[Dict[str, Any]],
        selected_cell_types: Sequence[str],
        mode: str,
    ) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]]]:
        rows, _, _ = self._region_catalog(records)
        row_by_id = {str(row["id"]): row for row in rows}
        record_rows = {
            str(record["id"]): (record, row_by_id[str(record["id"])])
            for record in records
            if str(record["id"]) in row_by_id
        }
        selected = set(str(value) for value in selected_cell_types)
        filtered: list[Dict[str, Any]] = []
        for record in records:
            row = row_by_id.get(str(record["id"]))
            if row is None:
                continue
            preferred = self._preferred_region_cell_type(record, row, record_rows)
            source_match = preferred in selected if preferred is not None else False
            content_match = any(
                int(count) > 0 and str(cell_type) in selected
                for cell_type, count in row.get("countsByType", {}).items()
            )
            if source_match or (mode == "content" and content_match):
                filtered.append(record)
        filtered_rows, dominant_counts, boundaries = self._region_catalog(filtered)
        return filtered, filtered_rows, dominant_counts, boundaries

    def _set_region_records(self, records: Sequence[Dict[str, Any]]) -> None:
        result = self.region_result if isinstance(self.region_result, dict) else {}
        saved_paths = result.get("saved_paths") if isinstance(result.get("saved_paths"), dict) else {}
        saved_paths["mask_paths"] = {str(item["label"]): Path(item["path"]) for item in records}
        result["saved_paths"] = saved_paths
        result["boundary_records"] = list(records)
        self.region_result = result

    def _select_region_records(
        self,
        payload: Dict[str, Any],
        *,
        key: str = "selectedBoundaryLabels",
    ) -> tuple[list[Dict[str, Any]], list[str]]:
        warnings: list[str] = []
        records = self._load_region_records(warnings)
        if not records:
            raise RuntimeError("Run Region analysis or save a manual ROI before selecting Region boundaries.")
        by_label = {str(record["label"]): record for record in records}
        by_id = {str(record["id"]): record for record in records}
        raw_ids = payload.get("selectedBoundaryIds")
        if raw_ids is not None:
            selected_ids = _normalized_string_list(raw_ids, "Region selectedBoundaryIds", allowed=set(by_id))
            return [by_id[value] for value in selected_ids], warnings
        raw_labels = payload.get(key)
        labels = _normalized_string_list(
            list(by_label) if raw_labels is None else raw_labels,
            f"Region {key}",
            allowed=set(by_label),
        )
        return [by_label[value] for value in labels], warnings

    def _select_region_cell_types(self, payload: Dict[str, Any], key: str = "selectedCellTypes") -> list[str]:
        available = self._present_region_cell_types()
        if not available:
            raise RuntimeError("No assigned cell types are available for Region analysis.")
        raw = payload.get(key)
        return _normalized_string_list(
            list(available) if raw is None else raw,
            f"Region {key}",
            allowed=set(available),
        )

    @staticmethod
    def _preview_cache_path(output_dir: Path, prefix: str, preview_key: Any, identity: Dict[str, Any]) -> Path:
        key = safe_name(str(preview_key or "display"), "display").lower()
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:14]
        path = output_dir / "previews" / f"{safe_name(prefix, 'region_preview')}__{key}__{digest}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _write_exact_region_preview(
        self,
        records: Sequence[Dict[str, Any]],
        selected_cell_types: Sequence[str],
        params: RegionParams,
        path: Path,
    ) -> tuple[Path, int, int]:
        if self.assignment_result is None:
            raise RuntimeError("Run cell type assignment before creating a Region preview.")
        masks = {str(record["label"]): np.asarray(record["mask"], dtype=bool) for record in records}
        boundary_colors = self._region_boundary_colors(records, params)
        rgb = make_region_canvas_rgb(
            celltype_mask=self.assignment_result["celltype_mask"],
            celltype_cfg=self.celltype_config,
            masks=masks,
            selected_types=list(masks),
            display_celltypes=list(selected_cell_types),
            boundary_color=str(params.boundary_color),
            use_type_colors=bool(params.use_type_colors),
            thickness=max(1, int(round(float(params.line_width)))),
            boundary_colors=boundary_colors,
            line_style=str(params.line_style),
        )
        image = Image.fromarray(np.rint(rgb * 255.0).astype(np.uint8))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG", optimize=False)
        return path, int(image.width), int(image.height)

    def _region_boundary_colors(
        self,
        records: Sequence[Dict[str, Any]],
        params: RegionParams,
    ) -> Dict[str, str] | None:
        if not params.use_type_colors:
            return None
        type_colors = {
            str(item.get("name") or ""): str(item.get("color_hex") or params.boundary_color)
            for item in self.celltype_config
        }
        rows, _, _ = self._region_catalog(records)
        row_by_id = {str(row["id"]): row for row in rows}
        record_rows = {
            str(record["id"]): (record, row_by_id[str(record["id"])])
            for record in records
            if str(record["id"]) in row_by_id
        }
        return {
            str(record["label"]): type_colors.get(
                str(
                    self._preferred_region_cell_type(
                        record,
                        row_by_id[str(record["id"])],
                        record_rows,
                    )
                    or ""
                ),
                str(record["colorHex"]),
            )
            for record in records
            if str(record["id"]) in row_by_id
        }

    def _multiplex_overlay_rgb(self) -> np.ndarray:
        config = self._require_config()
        data = self._ensure_pixels()
        cached = data.get("overlay_rgb")
        if isinstance(cached, np.ndarray) and cached.ndim == 3 and cached.shape[2] == 3:
            return np.asarray(cached, dtype=float)
        figure, rgb = overlay_multi_channels(
            data["df_pixels"],
            data["shapes"],
            config.image_id,
            [channel.to_dict() for channel in config.channels],
            config.overlay_channels,
            white_channel=config.white_channel,
            white_weight=config.white_weight,
            clip_hi=99.8,
            pixel_size_um=config.pixel_size_um,
            save_path=None,
        )
        plt.close(figure)
        data["overlay_rgb"] = np.asarray(rgb, dtype=float)
        data["overlay_clip_high_percentile"] = 99.8
        return np.asarray(rgb, dtype=float)

    def _write_region_preview_pair(
        self,
        records: Sequence[Dict[str, Any]],
        selected_cell_types: Sequence[str],
        params: RegionParams,
        comparison_path: Path,
    ) -> Dict[str, Any]:
        if self.assignment_result is None:
            raise RuntimeError("Run cell type assignment before creating a Region comparison preview.")
        masks = {str(record["label"]): np.asarray(record["mask"], dtype=bool) for record in records}
        boundary_colors = self._region_boundary_colors(records, params)
        render_arguments = {
            "celltype_cfg": self.celltype_config,
            "masks": masks,
            "selected_types": list(masks),
            "boundary_color": str(params.boundary_color),
            "use_type_colors": bool(params.use_type_colors),
            "thickness": max(1, int(round(float(params.line_width)))),
            "boundary_colors": boundary_colors,
            "line_style": str(params.line_style),
        }
        mask_rgb = make_region_canvas_rgb(
            celltype_mask=self.assignment_result["celltype_mask"],
            display_celltypes=list(selected_cell_types),
            **render_arguments,
        )
        overlay_source = self._multiplex_overlay_rgb()
        if tuple(overlay_source.shape[:2]) != tuple(mask_rgb.shape[:2]):
            raise RuntimeError(
                "The multiplex overlay and cell-type mask have different source dimensions: "
                f"{overlay_source.shape[:2]} != {mask_rgb.shape[:2]}."
            )
        overlay_rgb = draw_region_boundaries_rgb(overlay_source, **render_arguments)

        comparison_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path = comparison_path.with_name(f"{comparison_path.stem}__overlay.png")
        mask_path = comparison_path.with_name(f"{comparison_path.stem}__mask.png")
        overlay_image = Image.fromarray(np.rint(overlay_rgb * 255.0).astype(np.uint8))
        mask_image = Image.fromarray(np.rint(mask_rgb * 255.0).astype(np.uint8))
        overlay_image.save(overlay_path, format="PNG", optimize=False)
        mask_image.save(mask_path, format="PNG", optimize=False)
        gap = 24
        comparison_image = Image.new(
            "RGB",
            (int(overlay_image.width + mask_image.width + gap), int(max(overlay_image.height, mask_image.height))),
            (0, 0, 0),
        )
        comparison_image.paste(overlay_image, (0, 0))
        comparison_image.paste(mask_image, (int(overlay_image.width + gap), 0))
        comparison_image.save(comparison_path, format="PNG", optimize=False)
        return {
            "overlayPreviewPath": overlay_path,
            "maskPreviewPath": mask_path,
            "comparisonPreviewPath": comparison_path,
            "width": int(mask_image.width),
            "height": int(mask_image.height),
            "comparisonWidth": int(comparison_image.width),
            "comparisonHeight": int(comparison_image.height),
        }

    @staticmethod
    def _write_png_backed_svg(png_path: Path, svg_path: Path, title: str) -> Path:
        """Write a standards-based SVG wrapper that preserves the exact PNG rendering."""
        png_payload = png_path.read_bytes()
        if not png_payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"Region SVG source is not a PNG file: {png_path}")
        with Image.open(io.BytesIO(png_payload)) as image:
            width, height = int(image.width), int(image.height)
        encoded = base64.b64encode(png_payload).decode("ascii")
        escaped_title = html.escape(title, quote=False)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img">\n'
            f"  <title>{escaped_title}</title>\n"
            f'  <image x="0" y="0" width="{width}" height="{height}" preserveAspectRatio="none" '
            f'href="data:image/png;base64,{encoded}"/>\n'
            "</svg>\n"
        )
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.write_text(svg, encoding="utf-8", newline="\n")
        return svg_path

    def region(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.assignment_result is None:
            raise RuntimeError("Run cell type assignment before region analysis.")
        present_types = self._present_region_cell_types()
        raw_selected_types = payload.get("selectedTypes")
        selected_types = _normalized_string_list(
            present_types[:1] if raw_selected_types is None else raw_selected_types,
            "Region selectedTypes",
            allowed=set(present_types),
        )
        params, normalized_parameters = self._normalize_region_parameters(payload, selected_types)
        self._invalidate_workflow_from("region")
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
        _close_figures(self.region_result)
        self.distribution_result = None
        computed_masks = self.region_result.get("masks") if isinstance(self.region_result, dict) else None
        nonempty_computational_count = sum(
            1
            for mask in (computed_masks or {}).values()
            if isinstance(mask, np.ndarray) and np.any(mask)
        )
        if nonempty_computational_count <= 0:
            self.region_result = None
            raise RuntimeError(
                "Region analysis produced no nonempty boundary with the current parameters. "
                "Lower the minimum area or minimum cell count, or increase closing/dilation, then run Region again."
            )
        self.analysis_parameters["region"] = normalized_parameters
        warnings: list[str] = []
        records = self._load_region_records(warnings)
        self._set_region_records(records)
        regions, dominant_counts, boundaries = self._region_catalog(records)
        preview_pair = self._write_region_preview_pair(
            records,
            present_types,
            params,
            output_dir / "region_analysis_preview.png",
        )
        self._mark_workflow_stage("region", invalidate_after=True)
        _progress(request_id, 1.0, "Region analysis complete")
        return {
            "summary": {"boundaryCount": len(boundaries), "dominantCounts": dominant_counts},
            "parameters": normalized_parameters,
            "regions": regions,
            "dominantCounts": dominant_counts,
            "boundaries": boundaries,
            "previewPath": str(preview_pair["comparisonPreviewPath"]),
            "overlayPreviewPath": str(preview_pair["overlayPreviewPath"]),
            "maskPreviewPath": str(preview_pair["maskPreviewPath"]),
            "comparisonPreviewPath": str(preview_pair["comparisonPreviewPath"]),
            "width": int(preview_pair["width"]),
            "height": int(preview_pair["height"]),
            "comparisonWidth": int(preview_pair["comparisonWidth"]),
            "comparisonHeight": int(preview_pair["comparisonHeight"]),
            "warnings": warnings,
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def region_preview(self, _request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.assignment_result is None:
            raise RuntimeError("Run cell type assignment before creating a Region preview.")
        records, warnings = self._select_region_records(payload)
        requested_boundary_count = len(records)
        selected_cell_types = self._select_region_cell_types(payload)
        boundary_cell_type_mode = self._boundary_cell_type_mode(payload)
        records, regions, dominant_counts, boundaries = self._filter_region_records_by_cell_types(
            records,
            selected_cell_types,
            boundary_cell_type_mode,
        )
        selected_cell_count = self._region_displayed_cell_count(records, selected_cell_types)
        params, normalized_parameters = self._normalize_region_parameters(
            payload,
            selected_cell_types,
            inherit_saved=True,
        )
        output_dir = _section_dir(config, "region_analysis")
        identity = {
            "boundaries": [str(record["id"]) for record in records],
            "cellTypes": selected_cell_types,
            "boundaryCellTypeMode": boundary_cell_type_mode,
            "parameters": normalized_parameters,
        }
        comparison_path = self._preview_cache_path(
            output_dir,
            "region_filtered_preview",
            payload.get("previewKey"),
            identity,
        )
        preview_pair = self._write_region_preview_pair(
            records,
            selected_cell_types,
            params,
            comparison_path,
        )
        preview_path = (
            preview_pair["maskPreviewPath"]
            if boundary_cell_type_mode == "source"
            else preview_pair["comparisonPreviewPath"]
        )
        return {
            "previewPath": str(preview_path),
            "overlayPreviewPath": str(preview_pair["overlayPreviewPath"]),
            "maskPreviewPath": str(preview_pair["maskPreviewPath"]),
            "comparisonPreviewPath": str(preview_pair["comparisonPreviewPath"]),
            "width": int(preview_pair["width"]),
            "height": int(preview_pair["height"]),
            "comparisonWidth": int(preview_pair["comparisonWidth"]),
            "comparisonHeight": int(preview_pair["comparisonHeight"]),
            "boundaryCellTypeMode": boundary_cell_type_mode,
            "boundaryCount": len(records),
            "cellCount": selected_cell_count,
            "regions": regions,
            "dominantCounts": dominant_counts,
            "boundaries": boundaries,
            "summary": {
                "requestedBoundaryCount": requested_boundary_count,
                "boundaryCount": len(records),
                "cellTypeCount": len(selected_cell_types),
                "cellCount": selected_cell_count,
                "regions": regions,
                "dominantCounts": dominant_counts,
                "boundaries": boundaries,
            },
            "warnings": warnings,
        }

    @staticmethod
    def _manual_polygon_mask(polygons_value: Any, shape: tuple[int, int]) -> np.ndarray:
        if not isinstance(polygons_value, list) or not polygons_value:
            raise ValueError("Region manual polygons must contain at least one polygon.")
        if len(polygons_value) > 256:
            raise ValueError("Region manual polygons must contain at most 256 polygons.")
        height, width = int(shape[0]), int(shape[1])
        canvas = Image.new("1", (width, height), 0)
        drawing = ImageDraw.Draw(canvas)
        total_points = 0
        for polygon_index, polygon_value in enumerate(polygons_value, start=1):
            points_value = polygon_value.get("points") if isinstance(polygon_value, dict) else polygon_value
            if not isinstance(points_value, list) or len(points_value) < 3:
                raise ValueError(f"Region manual polygon {polygon_index} must contain at least three points.")
            total_points += len(points_value)
            if total_points > 20000:
                raise ValueError("Region manual polygons contain too many points.")
            points: list[tuple[float, float]] = []
            for point_index, point_value in enumerate(points_value, start=1):
                if isinstance(point_value, dict):
                    x_value, y_value = point_value.get("x"), point_value.get("y")
                elif isinstance(point_value, (list, tuple)) and len(point_value) >= 2:
                    x_value, y_value = point_value[0], point_value[1]
                else:
                    raise ValueError(
                        f"Region manual polygon {polygon_index} point {point_index} must provide x and y coordinates."
                    )
                if isinstance(x_value, bool) or isinstance(y_value, bool) or not isinstance(x_value, (int, float)) or not isinstance(y_value, (int, float)):
                    raise ValueError(
                        f"Region manual polygon {polygon_index} point {point_index} coordinates must be numeric."
                    )
                x, y = float(x_value), float(y_value)
                if not math.isfinite(x) or not math.isfinite(y):
                    raise ValueError(
                        f"Region manual polygon {polygon_index} point {point_index} coordinates must be finite."
                    )
                points.append((x, y))
            if len({(round(x, 6), round(y, 6)) for x, y in points}) < 3:
                raise ValueError(f"Region manual polygon {polygon_index} must contain three distinct points.")
            drawing.polygon(points, fill=1)
        mask = np.asarray(canvas, dtype=bool)
        if not np.any(mask):
            raise ValueError("Region manual polygons do not intersect the source image.")
        return mask

    def _manual_region_candidate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.assignment_result is None or self.nuclei_result is None:
            raise RuntimeError("Run final nuclei segmentation and cell type assignment before editing Region boundaries.")
        mode_value = payload.get("mode", "create")
        if not isinstance(mode_value, str) or mode_value.strip().lower() not in {"create", "include", "exclude"}:
            raise ValueError("Region manual mode must be 'create', 'include', or 'exclude'.")
        mode = mode_value.strip().lower()
        seed_cell_types = self._select_region_cell_types(payload, key="seedCellTypes")
        params, normalized_parameters = self._normalize_region_parameters(
            payload,
            seed_cell_types,
            inherit_saved=True,
        )

        records = self._load_region_records([])
        target_record: Dict[str, Any] | None = None
        raw_target = payload.get("targetBoundaryLabel")
        if mode in {"include", "exclude"}:
            if not isinstance(raw_target, str) or not raw_target.strip():
                raise ValueError(f"Region manual {mode} mode requires targetBoundaryLabel.")
            target_record = next((item for item in records if item["label"] == raw_target.strip()), None)
            if target_record is None:
                raise ValueError(f"Region manual target boundary is unavailable: {raw_target.strip()}")
        elif raw_target not in {None, ""}:
            raise ValueError("Region manual create mode does not accept targetBoundaryLabel.")

        target_preferred_type: str | None = None
        if target_record is not None:
            existing_rows, _, _ = self._region_catalog(records)
            row_by_id = {str(row["id"]): row for row in existing_rows}
            record_rows = {
                str(record["id"]): (record, row_by_id[str(record["id"])])
                for record in records
                if str(record["id"]) in row_by_id
            }
            target_pair = record_rows.get(str(target_record.get("id") or ""))
            if target_pair is not None:
                target_preferred_type = self._preferred_region_cell_type(
                    target_pair[0],
                    target_pair[1],
                    record_rows,
                )

        shape = tuple(int(value) for value in self.assignment_result["celltype_mask"].shape)
        polygon_mask = self._manual_polygon_mask(payload.get("polygons"), shape)
        df_cells = self.assignment_result["df_cells"]
        required_columns = {"label", "celltype", "centroid_x_px", "centroid_y_px"}
        if not required_columns.issubset(df_cells.columns):
            raise RuntimeError(f"Cell assignments must contain columns: {required_columns}")
        height, width = shape
        cy = np.clip(np.rint(df_cells["centroid_y_px"].to_numpy(float)).astype(int), 0, height - 1)
        cx = np.clip(np.rint(df_cells["centroid_x_px"].to_numpy(float)).astype(int), 0, width - 1)
        labels = df_cells["label"].astype(int).to_numpy()
        cell_types = df_cells["celltype"].astype(str).to_numpy()
        polygon_selection = polygon_mask[cy, cx] & np.isin(cell_types, seed_cell_types)
        selected_seed_labels = {int(value) for value in labels[polygon_selection] if int(value) > 0}
        if not selected_seed_labels:
            raise ValueError("The drawn polygon did not select any centroid from the selected seed cell types.")

        base_labels: set[int] = set()
        if target_record is not None:
            base_mask = np.asarray(target_record["mask"], dtype=bool)
            base_labels = {int(value) for value in labels[base_mask[cy, cx]] if int(value) > 0}
        if mode == "create":
            result_labels = set(selected_seed_labels)
        elif mode == "include":
            result_labels = base_labels | selected_seed_labels
        else:
            result_labels = base_labels - selected_seed_labels
        if not result_labels:
            raise ValueError("The manual Region edit removed every seed cell from the adjusted ROI.")

        px_area_um2 = float(config.pixel_size_um[0]) * float(config.pixel_size_um[1])
        candidate_mask = build_region_mask_from_cell_labels(
            nuclei_labels=np.asarray(self.nuclei_result["labels"]),
            df_cells=df_cells,
            selected_labels=sorted(result_labels),
            close_px=um_to_px_iso(params.close_um, config.pixel_size_um),
            dilate_px=um_to_px_iso(params.dilate_um, config.pixel_size_um),
            min_area_px=int(round(float(params.min_area_um2) / max(1e-12, px_area_um2))),
            min_cells=int(params.min_cells),
        )
        if not np.any(candidate_mask):
            raise ValueError(
                "The selected cells did not produce a nonempty ROI with the current closing, dilation, area, and cell-count settings."
            )

        raw_display_name = payload.get("displayName")
        if raw_display_name is not None and (not isinstance(raw_display_name, str) or not raw_display_name.strip()):
            raise ValueError("Region manual displayName must be a nonempty string when provided.")
        display_name = (
            raw_display_name.strip()
            if isinstance(raw_display_name, str)
            else (f"Adjusted {target_record['label']}" if target_record is not None else "Manual ROI")
        )
        color_hex = mcolors.to_hex(str(params.boundary_color), keep_alpha=False)
        identity = {
            "mode": mode,
            "targetId": target_record["id"] if target_record is not None else None,
            "displayName": display_name,
            "polygons": payload.get("polygons"),
            "seedCellTypes": seed_cell_types,
            "parameters": normalized_parameters,
        }
        preview_id = f"preview_{hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:16]}"
        candidate_record = {
            "id": preview_id,
            "label": display_name,
            "path": Path(),
            "relativePath": "",
            "sourceType": "manual" if mode == "create" else "adjusted",
            "maskKey": preview_id,
            "colorHex": color_hex,
            "mask": candidate_mask,
            "modifiedUtc": 0.0,
            "originalPath": (
                target_record.get("originalPath") or target_record.get("path")
                if target_record is not None
                else None
            ),
            "originalLabel": (
                target_record.get("originalLabel") or target_record.get("label")
                if target_record is not None
                else None
            ),
            "registry": {
                "seed_cell_types": list(seed_cell_types),
                "target_boundary_id": str(target_record["id"]) if target_record is not None else None,
                "target_boundary_label": str(target_record["label"]) if target_record is not None else None,
                "target_cell_type": target_preferred_type,
            },
        }
        display_cell_types = (
            self._select_region_cell_types(payload)
            if "selectedCellTypes" in payload
            else list(seed_cell_types)
        )
        return {
            "mode": mode,
            "seedCellTypes": seed_cell_types,
            "selectedCellTypes": display_cell_types,
            "selectedSeedCellCount": len(selected_seed_labels),
            "baseCellCount": len(base_labels),
            "resultSeedCellCount": len(result_labels),
            "parameters": normalized_parameters,
            "params": params,
            "record": candidate_record,
            "targetRecord": target_record,
            "identity": identity,
        }

    def region_manual_preview(self, _request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        candidate = self._manual_region_candidate(payload)
        record = candidate["record"]
        output_dir = _section_dir(config, "region_analysis")
        preview_path = self._preview_cache_path(
            output_dir,
            "region_manual_preview",
            payload.get("previewKey") or "manual_editor",
            candidate["identity"],
        )
        preview_path, width, height = self._write_exact_region_preview(
            [record],
            candidate["selectedCellTypes"],
            candidate["params"],
            preview_path,
        )
        regions, _, _ = self._region_catalog([record])
        return {
            "previewPath": str(preview_path),
            "width": width,
            "height": height,
            "summary": {
                "mode": candidate["mode"],
                "selectedSeedCellCount": candidate["selectedSeedCellCount"],
                "baseCellCount": candidate["baseCellCount"],
                "resultSeedCellCount": candidate["resultSeedCellCount"],
                "region": regions[0],
            },
            "parameters": candidate["parameters"],
        }

    @staticmethod
    def _unique_region_label(requested: str, records: Sequence[Dict[str, Any]]) -> str:
        used = {str(record["label"]).casefold() for record in records}
        if requested.casefold() not in used:
            return requested
        suffix = 2
        while f"{requested} ({suffix})".casefold() in used:
            suffix += 1
        return f"{requested} ({suffix})"

    def region_manual_save(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if not isinstance(payload.get("displayName"), str) or not str(payload.get("displayName")).strip():
            raise ValueError("Region manual save requires a nonempty displayName.")
        candidate = self._manual_region_candidate(payload)
        existing_records = self._load_region_records([])
        display_name = self._unique_region_label(str(payload["displayName"]).strip(), existing_records)
        semantic_identity = dict(candidate["identity"])
        semantic_identity["displayName"] = display_name
        digest = hashlib.sha256(
            json.dumps(semantic_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        roi_id = f"roi_{digest}"
        mask_key = f"manual_{digest}"
        candidate_record = candidate["record"]
        candidate_record["id"] = roi_id
        candidate_record["label"] = display_name
        candidate_record["maskKey"] = mask_key

        output_dir = _section_dir(config, "region_analysis")
        target_record = candidate["targetRecord"]
        original_relative: str | None = None
        original_label: str | None = None
        target_preferred_type: str | None = None
        if target_record is not None:
            original_path = target_record.get("originalPath") or target_record.get("path")
            if isinstance(original_path, Path):
                try:
                    original_relative = str(original_path.resolve().relative_to(output_dir.resolve())).replace("\\", "/")
                except ValueError:
                    original_relative = None
            original_label = str(target_record.get("originalLabel") or target_record.get("label"))
            existing_rows, _, _ = self._region_catalog(existing_records)
            row_by_id = {str(row["id"]): row for row in existing_rows}
            record_rows = {
                str(record["id"]): (record, row_by_id[str(record["id"])])
                for record in existing_records
                if str(record["id"]) in row_by_id
            }
            target_pair = record_rows.get(str(target_record.get("id") or ""))
            if target_pair is not None:
                target_preferred_type = self._preferred_region_cell_type(
                    target_pair[0],
                    target_pair[1],
                    record_rows,
                )
        metadata = {
            "id": roi_id,
            "source_type": str(candidate_record["sourceType"]),
            "color_hex": str(candidate_record["colorHex"]),
            "original_mask_path": original_relative,
            "original_label": original_label,
            "target_boundary_id": str(target_record["id"]) if target_record is not None else None,
            "target_boundary_label": str(target_record["label"]) if target_record is not None else None,
            "target_cell_type": target_preferred_type,
            "created_mode": str(candidate["mode"]),
            "seed_cell_types": list(candidate["seedCellTypes"]),
            "display_parameters": dict(candidate["parameters"]),
        }
        _progress(request_id, 0.20, "Saving the adjusted Region boundary")
        saved = save_adjusted_region_analysis(
            df_cells=self.assignment_result["df_cells"],
            celltype_mask=self.assignment_result["celltype_mask"],
            celltype_cfg=self.celltype_config,
            save_dir=output_dir,
            pixel_size_um=config.pixel_size_um,
            adjusted_masks={mask_key: np.asarray(candidate_record["mask"], dtype=bool)},
            selected_types=candidate["seedCellTypes"],
            edit_meta={
                "target_type": str(target_record["label"]) if target_record is not None else None,
                "edit_mode": str(candidate["mode"]),
                "display_name": display_name,
            },
            edited_boundary_types=[mask_key],
            boundary_display_names={mask_key: display_name},
            line_width=float(candidate["params"].line_width),
            line_style=str(candidate["params"].line_style),
            boundary_color=str(candidate["params"].boundary_color),
            use_type_colors=bool(candidate["params"].use_type_colors),
            contour_downsample=int(candidate["params"].contour_downsample),
            replace_registry_source=None,
            registry_entry_metadata={mask_key: metadata},
            save_outputs=True,
        )
        _close_figures(saved)
        warnings: list[str] = []
        records = self._load_region_records(warnings)
        self._set_region_records(records)
        regions, dominant_counts, boundaries = self._region_catalog(records)
        saved_record = next((record for record in records if record["id"] == roi_id), None)
        if saved_record is None:
            raise RuntimeError("The adjusted Region boundary was saved but could not be reloaded from the boundary registry.")
        preview_path = self._preview_cache_path(
            output_dir,
            "region_manual_saved",
            roi_id,
            semantic_identity,
        )
        preview_path, width, height = self._write_exact_region_preview(
            [saved_record],
            candidate["selectedCellTypes"],
            candidate["params"],
            preview_path,
        )
        if "region" not in self.analysis_parameters:
            self.analysis_parameters["region"] = dict(candidate["parameters"])
        self.distribution_result = None
        self._mark_workflow_stage("region", invalidate_after=True)
        _progress(request_id, 1.0, "Adjusted Region boundary saved")
        return {
            "summary": {
                "savedRegionId": roi_id,
                "savedRegionLabel": display_name,
                "boundaryCount": len(boundaries),
            },
            "regions": regions,
            "dominantCounts": dominant_counts,
            "boundaries": boundaries,
            "previewPath": str(preview_path),
            "width": width,
            "height": height,
            "parameters": candidate["parameters"],
            "warnings": warnings,
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def region_custom_export(self, _request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.assignment_result is None:
            raise RuntimeError("Run cell type assignment before exporting Region views.")
        records, warnings = self._select_region_records(payload)
        all_records = self._load_region_records([])
        requested_boundary_count = len(records)
        selected_cell_types = self._select_region_cell_types(payload)
        boundary_cell_type_mode = self._boundary_cell_type_mode(payload)
        records, regions, dominant_counts, boundaries = self._filter_region_records_by_cell_types(
            records,
            selected_cell_types,
            boundary_cell_type_mode,
        )
        if not records:
            raise ValueError("No selected Region boundary matches the selected cell types.")
        params, normalized_parameters = self._normalize_region_parameters(
            payload,
            selected_cell_types,
            inherit_saved=True,
        )
        identity = {
            "boundaries": [str(record["id"]) for record in records],
            "cellTypes": selected_cell_types,
            "boundaryCellTypeMode": boundary_cell_type_mode,
            "parameters": normalized_parameters,
        }
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:14]
        root = _section_dir(config, "integrated_region_analysis")
        original_dir = root / "01_original_unmodified"
        customized_dir = root / "02_customized_display"
        original_dir.mkdir(parents=True, exist_ok=True)
        customized_dir.mkdir(parents=True, exist_ok=True)

        customized_png = customized_dir / f"customized_region__{digest}.png"
        customized_pair = self._write_region_preview_pair(
            records,
            selected_cell_types,
            params,
            customized_png,
        )
        customized_tiff = customized_png.with_suffix(".tiff")
        with Image.open(customized_pair["comparisonPreviewPath"]) as image:
            image.save(customized_tiff, format="TIFF", compression="tiff_deflate")
        self._write_png_backed_svg(
            Path(customized_pair["comparisonPreviewPath"]),
            customized_png.with_suffix(".svg"),
            "Customized Region display",
        )

        original_records: list[Dict[str, Any]] = []
        used_original_keys: set[str] = set()
        all_by_id = {str(record["id"]): record for record in all_records}

        def append_original(record: Dict[str, Any], key: str) -> None:
            if key in used_original_keys:
                return
            original_records.append(record)
            used_original_keys.add(key)

        for record in records:
            if str(record.get("sourceType")) == "computational":
                append_original(dict(record), f"id:{record['id']}")
                continue
            registry = record.get("registry") if isinstance(record.get("registry"), dict) else {}
            target_record = all_by_id.get(str(registry.get("target_boundary_id") or ""))
            if target_record is not None and str(target_record.get("id")) != str(record.get("id")):
                append_original(dict(target_record), f"id:{target_record['id']}")
                continue
            original_path = record.get("originalPath")
            if not isinstance(original_path, Path) or not original_path.is_file():
                continue
            original_mask = np.asarray(record["mask"], dtype=bool)
            original_label = str(record.get("originalLabel") or record["label"])
            try:
                candidate_mask = np.asarray(load_any_tiff(original_path))
                if candidate_mask.ndim == 2 and candidate_mask.shape == original_mask.shape and np.any(candidate_mask):
                    original_mask = candidate_mask.astype(bool)
                else:
                    continue
            except Exception as error:
                warnings.append(f"Could not load the original boundary for {record['label']}: {error}")
                continue
            original_record = dict(record)
            original_record["label"] = original_label
            original_record["mask"] = original_mask
            append_original(original_record, f"path:{original_path.resolve()}")
        if not original_records:
            for record in all_records:
                if str(record.get("sourceType")) == "computational":
                    append_original(dict(record), f"id:{record['id']}")
        used_original_labels: set[str] = set()
        for record in original_records:
            base_label = str(record["label"])
            label = base_label
            suffix = 2
            while label.casefold() in used_original_labels:
                label = f"{base_label} (Original {suffix})"
                suffix += 1
            record["label"] = label
            used_original_labels.add(label.casefold())
        original_png = original_dir / f"original_region__{digest}.png"
        original_pair = self._write_region_preview_pair(
            original_records,
            selected_cell_types,
            params,
            original_png,
        )
        original_tiff = original_png.with_suffix(".tiff")
        with Image.open(original_pair["comparisonPreviewPath"]) as image:
            image.save(original_tiff, format="TIFF", compression="tiff_deflate")
        self._write_png_backed_svg(
            Path(original_pair["comparisonPreviewPath"]),
            original_png.with_suffix(".svg"),
            "Original unmodified Region display",
        )

        write_json(
            customized_dir / f"customized_region__{digest}.json",
            {
                "workflow": "customized_region_export",
                "selectedBoundaryIds": [str(record["id"]) for record in records],
                "selectedBoundaryLabels": [str(record["label"]) for record in records],
                "selectedCellTypes": selected_cell_types,
                "boundaryCellTypeMode": boundary_cell_type_mode,
                "parameters": normalized_parameters,
                "width": int(customized_pair["width"]),
                "height": int(customized_pair["height"]),
                "comparisonWidth": int(customized_pair["comparisonWidth"]),
                "comparisonHeight": int(customized_pair["comparisonHeight"]),
            },
        )
        write_json(
            original_dir / f"original_region__{digest}.json",
            {
                "workflow": "original_unmodified_region_export",
                "sourceBoundaryIds": [str(record["id"]) for record in records],
                "exportedBoundaryLabels": [str(record["label"]) for record in original_records],
                "selectedCellTypes": selected_cell_types,
                "boundaryCellTypeMode": boundary_cell_type_mode,
                "parameters": normalized_parameters,
                "width": int(original_pair["width"]),
                "height": int(original_pair["height"]),
                "comparisonWidth": int(original_pair["comparisonWidth"]),
                "comparisonHeight": int(original_pair["comparisonHeight"]),
            },
        )
        return {
            "previewPath": str(customized_pair["comparisonPreviewPath"]),
            "overlayPreviewPath": str(customized_pair["overlayPreviewPath"]),
            "maskPreviewPath": str(customized_pair["maskPreviewPath"]),
            "comparisonPreviewPath": str(customized_pair["comparisonPreviewPath"]),
            "originalOverlayPreviewPath": str(original_pair["overlayPreviewPath"]),
            "originalMaskPreviewPath": str(original_pair["maskPreviewPath"]),
            "originalComparisonPreviewPath": str(original_pair["comparisonPreviewPath"]),
            "width": int(customized_pair["width"]),
            "height": int(customized_pair["height"]),
            "comparisonWidth": int(customized_pair["comparisonWidth"]),
            "comparisonHeight": int(customized_pair["comparisonHeight"]),
            "boundaryCellTypeMode": boundary_cell_type_mode,
            "requestedBoundaryCount": requested_boundary_count,
            "boundaryCount": len(records),
            "regions": regions,
            "dominantCounts": dominant_counts,
            "boundaries": boundaries,
            "artifacts": _artifact_manifest(Path(config.save_dir), root),
            "warnings": warnings,
        }

    def cell_distribution(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.assignment_result is None or self.region_result is None:
            raise RuntimeError("Run cell type assignment and region analysis before cell distribution analysis.")
        boundaries = list((self.region_result.get("saved_paths", {}).get("mask_paths", {}) or {}).items())
        if not boundaries:
            raise RuntimeError("Region analysis did not produce a boundary mask.")
        raw_boundary_label = payload.get("boundaryLabel")
        if raw_boundary_label is not None and (
            not isinstance(raw_boundary_label, str) or not raw_boundary_label.strip()
        ):
            raise ValueError("Distribution boundaryLabel must be a nonempty string.")
        requested_label = raw_boundary_label.strip() if isinstance(raw_boundary_label, str) else str(boundaries[0][0])
        selected_boundary = next(
            ((str(key), Path(value)) for key, value in boundaries if str(key) == requested_label),
            None,
        )
        if selected_boundary is None:
            raise ValueError(f"Distribution boundaryLabel is unavailable: {requested_label}")
        requested_label, boundary_path = selected_boundary
        band_width_um = float(
            _normalized_number(
                payload.get("bandWidthUm", 10.0),
                "Distribution bandWidthUm",
                strictly_positive=True,
            )
        )
        overlay_channels = _normalized_string_list(
            payload.get("overlayChannels") or [],
            "Distribution overlayChannels",
            allowed={channel.channel for channel in config.channels},
            allow_empty=True,
        )
        present_types = [
            str(value)
            for value in self.assignment_result["df_cells"]["celltype"].astype(str).unique().tolist()
            if str(value) not in {"Unassigned", "Ambiguous"}
        ]
        selected_types = _normalized_string_list(
            payload.get("selectedCellTypes") or present_types,
            "Distribution selectedCellTypes",
            allowed=set(present_types),
        )
        self._invalidate_workflow_from("distribution")
        _progress(request_id, 0.08, "Building distance bands")
        distribution_config = _distribution_config_for_output(Path(config.save_dir))
        region_masks = run_region_mask_band_analysis(
            config=distribution_config,
            data_result=self._ensure_pixels(),
            boundary_label=requested_label,
            boundary_mask_path=boundary_path,
            arrays_npz_path=None,
            band_width_um=band_width_um,
            overlay_channels=overlay_channels,
        )
        _progress(request_id, 0.58, "Calculating cell density")
        density = run_cell_density_analysis(
            config=distribution_config,
            assignment_df=self.assignment_result["df_cells"],
            celltype_cfg=self.celltype_config,
            region_masks_result=region_masks,
            selected_celltypes=selected_types,
        )
        self.distribution_result = {"region_masks": region_masks, "density": density}
        output_dir = _section_dir(config, "cell_distribution_analysis")
        self.analysis_parameters["distribution"] = {
            "boundaryLabel": requested_label,
            "bandWidthUm": band_width_um,
            "overlayChannels": list(overlay_channels),
            "selectedCellTypes": list(selected_types),
        }
        self._mark_workflow_stage("distribution", invalidate_after=True)
        _progress(request_id, 1.0, "Cell distribution analysis complete")
        return {
            "summary": {"selectedCellTypes": selected_types, "boundaryLabel": requested_label},
            **_distribution_result_preview_contract(region_masks, density),
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def distance(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.assignment_result is None:
            raise RuntimeError("Run cell type assignment before distance analysis.")
        raw_mode = payload.get("mode", "nearest")
        if not isinstance(raw_mode, str) or not raw_mode.strip():
            raise ValueError("Distance mode must be 'nearest' or 'boundary'.")
        mode = raw_mode.strip().lower()
        if mode not in {"nearest", "boundary"}:
            raise ValueError("Distance mode must be 'nearest' or 'boundary'.")
        present_types = [
            str(value)
            for value in self.assignment_result["df_cells"]["celltype"].astype(str).unique().tolist()
            if str(value) not in {"Unassigned", "Ambiguous"}
        ]
        raw_target = payload.get("targetType")
        if raw_target is None:
            raw_target = present_types[0] if present_types else ""
        if not isinstance(raw_target, str) or not raw_target.strip():
            raise ValueError("Select a target cell type.")
        target = raw_target.strip()
        if target not in set(present_types):
            raise ValueError(f"Distance targetType is unavailable: {target}")
        raw_queries = payload.get("queryTypes")
        queries = _normalized_string_list(
            present_types[1:] if raw_queries is None else raw_queries,
            "Distance queryTypes",
            allowed=set(present_types),
        )

        boundary_label: str | None = None
        boundary_path: Path | None = None
        region_filter: str | None = None
        if mode == "boundary":
            if self.region_result is None:
                raise RuntimeError("Run region analysis before cell-to-boundary distance analysis.")
            boundaries = list((self.region_result.get("saved_paths", {}).get("mask_paths", {}) or {}).items())
            if not boundaries:
                raise RuntimeError("No boundary mask is available.")
            raw_boundary_label = payload.get("boundaryLabel")
            if raw_boundary_label is not None and (
                not isinstance(raw_boundary_label, str) or not raw_boundary_label.strip()
            ):
                raise ValueError("Boundary distance boundaryLabel must be a nonempty string.")
            requested_boundary_label = (
                raw_boundary_label.strip()
                if isinstance(raw_boundary_label, str)
                else str(boundaries[0][0])
            )
            selected_boundary = next(
                (
                    (str(key), Path(value))
                    for key, value in boundaries
                    if str(key) == requested_boundary_label
                ),
                None,
            )
            if selected_boundary is None:
                raise ValueError(f"Boundary distance boundaryLabel is unavailable: {requested_boundary_label}")
            boundary_label, boundary_path = selected_boundary
            raw_region_filter = payload.get("regionFilter", "all")
            if not isinstance(raw_region_filter, str) or not raw_region_filter.strip():
                raise ValueError("Boundary distance regionFilter must be 'all', 'inside', or 'outside'.")
            region_filter = raw_region_filter.strip().lower()
            if region_filter not in {"all", "inside", "outside"}:
                raise ValueError("Boundary distance regionFilter must be 'all', 'inside', or 'outside'.")

        self._invalidate_workflow_from("distance", preserve_distance_parameters=True)
        output_dir = _section_dir(config, "distance_analysis")
        _progress(request_id, 0.12, "Computing distances")
        normalized_parameters: Dict[str, Any] = {
            "mode": mode,
            "targetType": target,
            "queryTypes": list(queries),
        }
        if mode == "boundary":
            assert boundary_label is not None and boundary_path is not None and region_filter is not None
            result = run_boundary_distance_analysis(
                df_cells=self.assignment_result["df_cells"],
                celltype_cfg=self.celltype_config,
                celltype_mask=self.assignment_result["celltype_mask"],
                save_dir=output_dir,
                pixel_size_um=config.pixel_size_um,
                boundary_mask_path=boundary_path,
                boundary_name=boundary_label,
                query_types=queries,
                region_filter=region_filter,
                save_outputs=True,
            )
            normalized_parameters["boundaryLabel"] = boundary_label
            normalized_parameters["regionFilter"] = region_filter
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
        distance_parameters = self.analysis_parameters.get("distance")
        if not isinstance(distance_parameters, dict):
            distance_parameters = {}
        distance_parameters[mode] = normalized_parameters
        distance_parameters["lastMode"] = mode
        self.analysis_parameters["distance"] = distance_parameters
        self._mark_workflow_stage("distance", invalidate_after=True)
        _progress(request_id, 1.0, "Distance analysis complete")
        return {
            "summary": {"mode": mode, "targetType": target, "queryTypes": queries},
            "previewPath": str(preview_path) if preview_path else None,
            "artifacts": _artifact_manifest(Path(config.save_dir), output_dir),
        }

    def apply_recommendation(self, _request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_config()
        kind = str(payload.get("kind") or "").strip()
        if kind not in {"nuclei", "assignment"}:
            raise ValueError("Recommendation kind must be 'nuclei' or 'assignment'.")
        raw_parameters = payload.get("parameters")
        if not isinstance(raw_parameters, dict):
            raise ValueError("Applied recommendation parameters are required.")
        parameters = {
            str(key): _json_scalar(value)
            for key, value in raw_parameters.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        if not parameters:
            raise ValueError("Applied recommendation parameters are empty.")
        if not self.recommendation_state.get(kind, False):
            raise RuntimeError(f"No pending {kind} recommendation is available to apply.")

        stage = "nuclei" if kind == "nuclei" else "cellTypes"
        snapshot = {
            "workflow": dict(self.workflow_state),
            "recommendations": dict(self.recommendation_state),
            "pending": {
                current_kind: dict(self.pending_parameters[current_kind])
                for current_kind in ("nuclei", "assignment")
            },
            "nuclei_result": self.nuclei_result,
            "assignment_result": self.assignment_result,
            "neighborhood_result": self.neighborhood_result,
            "region_result": self.region_result,
            "distribution_result": self.distribution_result,
            "analysis_parameters": copy.deepcopy(self.analysis_parameters),
        }
        try:
            self._invalidate_workflow_from(stage, persist=False)
            self.pending_parameters[kind] = parameters
            self.recommendation_state[kind] = False
            self._persist_workflow_state()
        except Exception:
            self.workflow_state = snapshot["workflow"]
            self.recommendation_state = snapshot["recommendations"]
            self.pending_parameters = snapshot["pending"]
            self.nuclei_result = snapshot["nuclei_result"]
            self.assignment_result = snapshot["assignment_result"]
            self.neighborhood_result = snapshot["neighborhood_result"]
            self.region_result = snapshot["region_result"]
            self.distribution_result = snapshot["distribution_result"]
            self.analysis_parameters = snapshot["analysis_parameters"]
            raise
        return {
            "applied": True,
            "kind": kind,
            "parameters": parameters,
            "workflow": dict(self.workflow_state),
        }

    def outputs(self, _request_id: str, _payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        missing = [stage for stage in WORKFLOW_STAGES[:-1] if not self.workflow_state.get(stage, False)]
        if missing:
            raise RuntimeError(f"Complete all analysis steps before refreshing outputs (missing: {', '.join(missing)}).")
        self._mark_workflow_stage("outputs")
        output_folder = Path(config.save_dir)
        return {
            "outputFolder": str(config.save_dir),
            "files": [
                record
                for record in list_output_files(output_folder)
                if self._output_record_is_current(str(record.get("relative_path") or ""))
            ],
            "artifacts": [
                record
                for record in _artifact_manifest(output_folder)
                if self._output_record_is_current(str(record.get("relativePath") or ""))
            ],
        }

    def _load_optimizer_recommendation(self, json_path: Path, csv_path: Path, kind: str) -> Dict[str, Any]:
        if json_path.is_file():
            try:
                value = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    return {str(key): _json_scalar(item) for key, item in value.items()}
            except Exception:
                pass
        if not csv_path.is_file():
            return {}
        try:
            results = pd.read_csv(csv_path)
            if kind == "nuclei":
                row = recommend_nuclei_parameter_sweep_result(results)
                keys = [
                    "min_diam_um", "max_diam_um", "tophat_radius_um", "gauss_sigma_um", "local_win_um",
                    "local_offset", "h_maxima_um", "seed_min_dist_um", "watershed_compactness", "post_resplit_mult",
                ]
                labels = SWEEP_PARAM_LABELS
            else:
                row = recommend_celltype_assignment_optimizer_result(
                    results,
                    defined_celltype_names=[str(item["name"]) for item in self.celltype_config],
                )
                keys = [
                    "r_voronoi_um", "r_buffer_um", "r_vote_um", "tophat_r_um", "gauss_sigma_um",
                    "thresh_mode", "min_pos_object_size_px", "min_pos_pix", "resolve_ambiguous",
                    "ambiguous_min_probability", "ambiguous_min_gap",
                ]
                labels = CELLTYPE_OPTIMIZER_PARAM_LABELS
            if row is None:
                return {}
            recommendation: Dict[str, Any] = {}
            for key in keys:
                column = labels.get(key, key)
                if column in row:
                    recommendation[key] = _json_scalar(row[column])
            return recommendation
        except Exception:
            return {}

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
            "all_pos": [
                str(value)
                for value in (
                    item.get("all_pos")
                    or item.get("allPositive")
                    or item.get("allPositiveMarkers")
                    or []
                )
            ],
            "all_neg": [
                str(value)
                for value in (
                    item.get("all_neg")
                    or item.get("allNegative")
                    or item.get("allNegativeMarkers")
                    or []
                )
            ],
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
                engine.compute_runtime.close()
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
    engine.compute_runtime.close()
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
