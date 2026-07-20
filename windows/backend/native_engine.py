from __future__ import annotations

import argparse
import copy
import contextlib
import hashlib
import io
import json
import math
import mimetypes
import os
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
ENGINE_VERSION = "1.2.2"
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
            strictly_positive: bool = False,
            integer: bool = False,
        ) -> float | int:
            return _normalized_number(
                value,
                label,
                minimum=minimum,
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
                validated["region"] = {
                    "selectedTypes": string_list(
                        source.get("selectedTypes"),
                        "region.selectedTypes",
                        allowed=available_types,
                    ),
                    "closeUm": number(source.get("closeUm"), "region.closeUm"),
                    "dilateUm": number(source.get("dilateUm"), "region.dilateUm"),
                    "minAreaUm2": number(source.get("minAreaUm2"), "region.minAreaUm2"),
                    "minCells": number(
                        source.get("minCells"),
                        "region.minCells",
                        minimum=1.0,
                        integer=True,
                    ),
                    "contourDownsample": number(
                        source.get("contourDownsample"),
                        "region.contourDownsample",
                        strictly_positive=True,
                        integer=True,
                    ),
                    "lineWidth": number(
                        source.get("lineWidth"),
                        "region.lineWidth",
                        strictly_positive=True,
                    ),
                    "lineStyle": text(source.get("lineStyle"), "region.lineStyle"),
                    "boundaryColor": text(source.get("boundaryColor"), "region.boundaryColor"),
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
            "distance_nearest": distance_dir / "nearest_distance_preview.png",
            "distance_boundary": distance_dir / "boundary_distance_preview.png",
        }
        distribution_previews = sorted(distribution_dir.rglob("*.png")) if distribution_dir.is_dir() else []
        if distribution_previews:
            preview_paths["distribution"] = distribution_previews[0]

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

        boundaries: list[Dict[str, str]] = []
        registry_path = region_dir / "boundary_mask_registry.json"
        if stage_allowed("region") and self.assignment_result is not None and registry_path.is_file():
            try:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                records = registry.get("entries", []) if isinstance(registry, dict) else registry
                region_root = region_dir.resolve()
                mask_paths: Dict[str, Path] = {}
                for item in records if isinstance(records, list) else []:
                    if not isinstance(item, dict):
                        continue
                    relative = str(item.get("mask_path") or "").strip()
                    if not relative:
                        continue
                    candidate = (region_dir / relative).resolve()
                    try:
                        candidate.relative_to(region_root)
                    except ValueError:
                        continue
                    if not candidate.is_file():
                        continue
                    label = str(item.get("display_name") or item.get("mask_key") or candidate.stem)
                    mask_paths[label] = candidate
                    boundaries.append({"label": label, "path": str(candidate)})
                if mask_paths:
                    self.region_result = {"saved_paths": {"mask_paths": mask_paths}}
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
            "distribution": "distribution",
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
            "previewPaths": restored_preview_paths,
            "nucleiParameters": self.pending_parameters["nuclei"] or nuclei_params,
            "assignmentParameters": self.pending_parameters["assignment"] or assignment_params,
            "nucleiRecommendation": nuclei_recommendation,
            "assignmentRecommendation": assignment_recommendation,
            "cellTypes": self.celltype_config,
            "resolvedCellTypes": resolved_cell_types,
            "boundaries": boundaries,
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

    def region(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._require_config()
        if self.assignment_result is None:
            raise RuntimeError("Run cell type assignment before region analysis.")
        present_types = [
            str(value)
            for value in self.assignment_result["df_cells"]["celltype"].astype(str).unique().tolist()
            if str(value) not in {"Unassigned", "Ambiguous"}
        ]
        raw_selected_types = payload.get("selectedTypes")
        selected_types = _normalized_string_list(
            present_types[:1] if raw_selected_types is None else raw_selected_types,
            "Region selectedTypes",
            allowed=set(present_types),
        )
        close_um = float(_normalized_number(payload.get("closeUm", 15.0), "Region closeUm"))
        dilate_um = float(_normalized_number(payload.get("dilateUm", 10.0), "Region dilateUm"))
        min_area_um2 = float(_normalized_number(payload.get("minAreaUm2", 20000.0), "Region minAreaUm2"))
        min_cells = int(
            _normalized_number(
                payload.get("minCells", 5),
                "Region minCells",
                minimum=1.0,
                integer=True,
            )
        )
        contour_downsample = int(
            _normalized_number(
                payload.get("contourDownsample", 2),
                "Region contourDownsample",
                strictly_positive=True,
                integer=True,
            )
        )
        line_width = float(
            _normalized_number(
                payload.get("lineWidth", 2.0),
                "Region lineWidth",
                strictly_positive=True,
            )
        )
        line_style = payload.get("lineStyle", "--")
        boundary_color = payload.get("boundaryColor", "#A1D99B")
        use_type_colors = payload.get("useTypeColors", False)
        if not isinstance(line_style, str) or not line_style.strip():
            raise ValueError("Region lineStyle must be a nonempty string.")
        if not isinstance(boundary_color, str) or not boundary_color.strip():
            raise ValueError("Region boundaryColor must be a nonempty string.")
        if not isinstance(use_type_colors, bool):
            raise ValueError("Region useTypeColors must be a boolean.")
        params = RegionParams(
            selected_types=selected_types,
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
        preview_path = self._save_first_result_figure_preview(self.region_result, output_dir / "region_analysis_preview.png")
        _close_figures(self.region_result)
        boundaries = []
        for label, path in (self.region_result.get("saved_paths", {}).get("mask_paths", {}) or {}).items():
            boundaries.append({"label": str(label), "path": str(path)})
        self.distribution_result = None
        self.analysis_parameters["region"] = {
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
        self._mark_workflow_stage("region", invalidate_after=True)
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
        distribution_config = read_distribution_config(Path(config.save_dir))
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
        preview_candidates = sorted(output_dir.rglob("*.png"))
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
            "previewPath": str(preview_candidates[0]) if preview_candidates else None,
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
