import os
import tempfile
import traceback
from pathlib import Path

# Mirror notebook Cell 1 before importing numpy/scipy/skimage/numba.
CPU_COUNT = os.cpu_count() or 4
DEFAULT_NATIVE_THREADS = max(1, int(os.environ.get("SPATIALSCOPE_NATIVE_THREADS", str(max(1, CPU_COUNT - 1)))))
DEFAULT_SWEEP_WORKERS = max(1, min(CPU_COUNT, int(os.environ.get("SPATIALSCOPE_SWEEP_WORKERS", str(max(1, CPU_COUNT - 1))))))
N_THREADS = DEFAULT_NATIVE_THREADS
os.environ.setdefault("NUMBA_THREADING_LAYER", "workqueue")
os.environ.setdefault("OMP_NUM_THREADS", str(N_THREADS))
os.environ.setdefault("OMP_MAX_ACTIVE_LEVELS", "1")
os.environ.setdefault("MKL_NUM_THREADS", str(N_THREADS))
os.environ.setdefault("OPENBLAS_NUM_THREADS", str(N_THREADS))
os.environ.setdefault("NUMEXPR_NUM_THREADS", str(N_THREADS))
os.environ.setdefault("KMP_WARNINGS", "0")

import base64
import colorsys
import hashlib
import importlib
import importlib.metadata as importlib_metadata
import importlib.machinery
import importlib.util
import io
import json
import math
import re
import shutil
import sys
import uuid
import zipfile
from pathlib import Path as _PathCompat
from typing import Any, Dict, List, Sequence, Tuple

import streamlit as st

APP_ROOT = Path(__file__).resolve().parent
APP_ICON_PATH = APP_ROOT / "assets" / "SpatialScope.png"
ZH_HANS_STRINGS_PATH = APP_ROOT / "assets" / "zh-Hans.strings"
st.set_page_config(
    page_title="SpatialScope",
    page_icon=str(APP_ICON_PATH) if APP_ICON_PATH.exists() else None,
    layout="wide",
    initial_sidebar_state="expanded",
)
TMP_ROOT = Path(os.environ.get("SPATIALSCOPE_SESSION_ROOT", tempfile.gettempdir())) / "SpatialScope" / "sessions"
SETTINGS_PATH = Path(
    os.environ.get(
        "SPATIALSCOPE_SETTINGS_PATH",
        str(Path.home() / ".spatialscope" / "settings.json"),
    )
)
DESKTOP_PATHS_PATH = Path(
    os.environ.get(
        "SPATIALSCOPE_DESKTOP_PATHS_PATH",
        str(SETTINGS_PATH.with_name("desktop-paths.json")),
    )
)

_streamlit_error = st.error


def _tracked_streamlit_error(body, *args, **kwargs):
    try:
        section = st.session_state.get("sidebar_active_section")
        if section:
            errors = dict(st.session_state.get("section_errors", {}))
            errors[str(section)] = str(body)
            st.session_state["section_errors"] = errors
    except Exception:
        pass
    return _streamlit_error(body, *args, **kwargs)


st.error = _tracked_streamlit_error

RUNTIME_IMPORT_ERROR = None
RUNTIME_IMPORT_TRACEBACK = ""


def _load_ui_language_setting() -> str:
    requested = os.environ.get("SPATIALSCOPE_UI_LANGUAGE", "system").strip().lower()
    try:
        if SETTINGS_PATH.exists():
            payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            requested = str(payload.get("ui_language", requested)).strip().lower()
    except Exception:
        pass
    return requested if requested in {"system", "en", "zh-hans"} else "system"


def _persist_ui_language_setting() -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps({"ui_language": st.session_state.get("ui_language", "system")}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


_ZH_HANS_TRANSLATIONS: Dict[str, str] | None = None


def _effective_ui_language() -> str:
    requested = str(st.session_state.get("ui_language", "system")).strip().lower()
    if requested == "system":
        requested = os.environ.get("SPATIALSCOPE_SYSTEM_LANGUAGE", "en").strip().lower()
    return "zh-hans" if requested == "zh-hans" else "en"


def _load_zh_hans_translations() -> Dict[str, str]:
    global _ZH_HANS_TRANSLATIONS
    if _ZH_HANS_TRANSLATIONS is not None:
        return _ZH_HANS_TRANSLATIONS

    translations: Dict[str, str] = {}
    if ZH_HANS_STRINGS_PATH.exists():
        pattern = re.compile(r'^"((?:\\.|[^"\\])*)"\s*=\s*"((?:\\.|[^"\\])*)";$')
        for raw_line in ZH_HANS_STRINGS_PATH.read_text(encoding="utf-8").splitlines():
            match = pattern.match(raw_line.strip())
            if not match:
                continue
            try:
                source = json.loads(f'"{match.group(1)}"')
                translation = json.loads(f'"{match.group(2)}"')
            except Exception:
                continue
            translations[str(source)] = str(translation)

    translations.update(
        {
            "Language/语言": "Language/语言",
            "Finished": "已完成",
            "Error": "错误",
            "1. Inputs & config": "1. 输入与配置",
            "2. Overlay preview": "2. 叠加图预览",
            "3. Nuclei segmentation": "3. 细胞核分割",
            "4. Cell type assignments": "4. 细胞类型分配",
            "5. Neighborhood analysis": "5. 邻域分析",
            "6. Region analysis": "6. 区域分析",
            "7. Cell distribution analysis": "7. 细胞分布分析",
            "8. Distance analysis": "8. 距离分析",
            "9. Outputs": "9. 输出",
            "Input source and configuration": "输入来源与配置",
            "Input source": "输入来源",
            "Local folder": "本地文件夹",
            "Upload files": "上传文件",
            "Session info": "会话信息",
            "Analysis sections": "分析部分",
            "Detected channels": "检测到的通道",
            "Channel selectors": "通道选择器",
            "Pixel size": "像素尺寸",
            "Overlay preview": "叠加图预览",
            "Nuclei segmentation": "细胞核分割",
            "Cell type assignments": "细胞类型分配",
            "Neighborhood analysis": "邻域分析",
            "Region analysis": "区域分析",
            "Cell distribution analysis": "细胞分布分析",
            "Distance analysis": "距离分析",
            "Outputs": "输出",
        }
    )
    _ZH_HANS_TRANSLATIONS = translations
    return translations


def inject_ui_translation() -> None:
    translations = _load_zh_hans_translations()
    mode = _effective_ui_language()
    st.iframe(
        rf"""
        <script>
        (() => {{
          try {{
            const hostWindow = window.parent;
            const doc = hostWindow.document;
            const zh = {json.dumps(translations, ensure_ascii=False)};
            const reverse = Object.fromEntries(Object.entries(zh).map(([source, translated]) => [translated, source]));
            const map = {json.dumps(mode)} === "zh-hans" ? zh : reverse;

            const translate = (raw) => {{
              if (!raw) return raw;
              const match = raw.match(/^(\s*)(.*?)(\s*)$/s);
              const leading = match ? match[1] : "";
              const core = match ? match[2] : raw;
              const trailing = match ? match[3] : "";
              let translated = map[core];
              if (!translated && core.includes("·")) {{
                const pieces = core.split(/\s+·\s+/);
                translated = pieces.map((piece) => map[piece] || piece).join("  ·  ");
              }}
              return translated ? `${{leading}}${{translated}}${{trailing}}` : raw;
            }};

            const translateElement = (element) => {{
              for (const attribute of ["aria-label", "placeholder", "title"]) {{
                if (element.hasAttribute && element.hasAttribute(attribute)) {{
                  const current = element.getAttribute(attribute);
                  const translated = translate(current);
                  if (translated !== current) element.setAttribute(attribute, translated);
                }}
              }}
            }};

            const apply = (root) => {{
              if (!root) return;
              if (root.nodeType === Node.TEXT_NODE) {{
                const translated = translate(root.nodeValue);
                if (translated !== root.nodeValue) root.nodeValue = translated;
                return;
              }}
              if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
              if (root.nodeType === Node.ELEMENT_NODE) translateElement(root);
              const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
              let node;
              while ((node = walker.nextNode())) {{
                if (node.nodeType === Node.TEXT_NODE) {{
                  const translated = translate(node.nodeValue);
                  if (translated !== node.nodeValue) node.nodeValue = translated;
                }} else {{
                  translateElement(node);
                }}
              }}
            }};

            if (hostWindow.__spatialScopeTranslationObserver) {{
              hostWindow.__spatialScopeTranslationObserver.disconnect();
            }}
            apply(doc.body);
            const observer = new MutationObserver((mutations) => {{
              for (const mutation of mutations) {{
                if (mutation.type === "characterData") apply(mutation.target);
                for (const node of mutation.addedNodes || []) apply(node);
              }}
            }});
            observer.observe(doc.body, {{ childList: true, subtree: true, characterData: true }});
            hostWindow.__spatialScopeTranslationObserver = observer;
          }} catch (error) {{
            console.warn("SpatialScope UI translation could not be applied", error);
          }}
        }})();
        </script>
        """,
        height=1,
        width=1,
        tab_index=-1,
    )


def _sync_desktop_paths() -> None:
    try:
        if not DESKTOP_PATHS_PATH.exists():
            return
        current_mtime = int(DESKTOP_PATHS_PATH.stat().st_mtime_ns)
        if st.session_state.get("desktop_paths_mtime") == current_mtime:
            return
        payload = json.loads(DESKTOP_PATHS_PATH.read_text(encoding="utf-8"))
        input_folder = str(payload.get("input_folder", "")).strip()
        output_folder = str(payload.get("output_folder", "")).strip()
        if input_folder:
            st.session_state["local_folder_input"] = input_folder
            st.session_state["input_mode_radio"] = "Local folder"
        if output_folder:
            st.session_state["local_output_folder"] = output_folder
        st.session_state["desktop_paths_mtime"] = current_mtime
    except Exception:
        pass

def _patch_streamlit_image_to_url_compat() -> None:
    try:
        from streamlit.elements import image as st_image  # type: ignore
    except Exception:
        return
    if hasattr(st_image, "image_to_url"):
        return

    image_to_url_fn = None
    try:
        from streamlit.elements.lib.image_utils import image_to_url as image_to_url_fn  # type: ignore
    except Exception:
        try:
            from streamlit.elements.image_utils import image_to_url as image_to_url_fn  # type: ignore
        except Exception:
            image_to_url_fn = None

    if image_to_url_fn is None:
        return

    def _make_layout_config(width):
        if width is None:
            return None

        try:
            import importlib
            for module_name in (
                "streamlit.elements.lib.layout_utils",
                "streamlit.elements.lib.layout_types",
                "streamlit.elements.layout_utils",
                "streamlit.elements.layout_types",
            ):
                try:
                    module = importlib.import_module(module_name)
                except Exception:
                    continue
                LayoutConfig = getattr(module, "LayoutConfig", None)
                if LayoutConfig is None:
                    continue
                try:
                    return LayoutConfig(width=width)
                except Exception:
                    try:
                        return LayoutConfig(width=int(width))
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            from types import SimpleNamespace
            return SimpleNamespace(width=width)
        except Exception:
            return {"width": width}

    def _compat_image_to_url(image, width=None, clamp=False, channels="RGB", output_format="auto", image_id=None):
        errors = []
        layout_config = _make_layout_config(width)

        if layout_config is not None:
            try:
                return image_to_url_fn(
                    image=image,
                    layout_config=layout_config,
                    clamp=clamp,
                    channels=channels,
                    output_format=output_format,
                    image_id=image_id,
                )
            except Exception as exc:
                errors.append(exc)

            try:
                return image_to_url_fn(
                    image,
                    layout_config,
                    clamp,
                    channels,
                    output_format,
                    image_id,
                )
            except Exception as exc:
                errors.append(exc)

        try:
            return image_to_url_fn(
                image=image,
                width=width,
                clamp=clamp,
                channels=channels,
                output_format=output_format,
                image_id=image_id,
            )
        except Exception as exc:
            errors.append(exc)

        try:
            return image_to_url_fn(image, width, clamp, channels, output_format, image_id)
        except Exception as exc:
            errors.append(exc)

        try:
            return image_to_url_fn(
                image=image,
                clamp=clamp,
                channels=channels,
                output_format=output_format,
            )
        except Exception as exc:
            errors.append(exc)
            joined = " | ".join(str(err) for err in errors if str(err).strip())
            raise RuntimeError(
                "Failed to adapt Streamlit image_to_url compatibility call. "
                + joined
            ) from exc

    st_image.image_to_url = _compat_image_to_url


def _candidate_canvas_module_files() -> list[Path]:
    this_file = Path(__file__).resolve()
    cwd_resolved = _PathCompat.cwd().resolve()
    candidates: list[Path] = []

    for dist_name in ("streamlit-drawable-canvas-fix", "streamlit-drawable-canvas"):
        try:
            dist = importlib_metadata.distribution(dist_name)
        except Exception:
            continue
        for rel in (
            _PathCompat("streamlit_drawable_canvas/__init__.py"),
            _PathCompat("streamlit_drawable_canvas.py"),
        ):
            try:
                candidate = _PathCompat(dist.locate_file(rel)).resolve()
            except Exception:
                continue
            if candidate.exists() and candidate != this_file:
                candidates.append(candidate)

    for path_entry in sys.path:
        try:
            root = _PathCompat(path_entry or ".").resolve()
        except Exception:
            continue
        if root == cwd_resolved:
            continue
        for rel in (
            _PathCompat("streamlit_drawable_canvas/__init__.py"),
            _PathCompat("streamlit_drawable_canvas.py"),
        ):
            candidate = (root / rel).resolve()
            if candidate.exists() and candidate != this_file:
                candidates.append(candidate)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _load_canvas_module_from_file(module_path: Path):
    module_path = _PathCompat(module_path).resolve()
    module_name = f"_streamlit_drawable_canvas_real_{abs(hash(str(module_path)))}"
    if module_path.name == "__init__.py":
        spec = importlib.util.spec_from_file_location(
            module_name,
            str(module_path),
            submodule_search_locations=[str(module_path.parent)],
        )
    else:
        spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    if module is None:
        raise ImportError("module is None. This should never happen.")
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[attr-defined]
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _load_st_canvas_component():
    errors = []
    _patch_streamlit_image_to_url_compat()

    try:
        module = importlib.import_module("streamlit_drawable_canvas")
        canvas_fn = getattr(module, "st_canvas", None)
        if callable(canvas_fn):
            return canvas_fn, None
    except Exception as exc:
        errors.append(exc)

    for module_path in _candidate_canvas_module_files():
        try:
            module = _load_canvas_module_from_file(module_path)
            canvas_fn = getattr(module, "st_canvas", None)
            if callable(canvas_fn):
                return canvas_fn, None
        except Exception as exc:
            errors.append(exc)

    joined = " | ".join(str(exc) for exc in errors if str(exc).strip())
    return None, RuntimeError(
        "Could not load the drawing canvas component. "
        "The app searched both the local shim and the installed site-packages canvas module. "
        "Please restart Streamlit after replacing the canvas shim. "
        f"Details: {joined}"
    )


try:
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go
    from matplotlib.patches import Patch
    from PIL import Image, ImageColor, ImageDraw
    from scipy import ndimage as ndi
    from skimage.measure import find_contours

    st_canvas, DRAWABLE_CANVAS_ERROR = _load_st_canvas_component()

    from src.spatialscope_analysis.celltype_assignment import (
        CELLTYPE_PARAM_LABELS,
        CELLTYPE_PARAM_ORDER,
        CELLTYPE_OPTIMIZER_PARAM_LABELS,
        CELLTYPE_OPTIMIZER_PARAM_ORDER,
        CelltypeAssignmentParams,
        COLOR_HEX_LIST,
        default_celltype,
        guess_nuclear_channel,
        marker_choices_for_ui,
        recommend_celltype_assignment_optimizer_result,
        recommend_celltype_assignment_parameter_sweep_result,
        run_celltype_assignment,
        run_celltype_assignment_parameter_optimizer,
        run_celltype_assignment_parameter_sweep,
        save_celltype_config,
        token_mapping_for_ui,
    )
    from src.spatialscope_analysis.distance_analysis import (
        discover_boundary_masks,
        run_boundary_distance_analysis,
        run_nearest_neighbor_analysis,
    )
    from src.spatialscope_analysis.io import (
        discover_text_image_files,
        files_to_long_df,
        list_output_files,
        safe_name,
        pipeline_config_to_json_dict,
        resolve_folder,
        save_uploaded_file_bytes,
        valid_pixel_size,
        write_json,
        zip_directory_bytes,
        load_any_tiff,
    )
    from src.spatialscope_analysis.models import ChannelConfig, NucleiParams, PipelineConfig, RegionParams
    from src.spatialscope_analysis.nuclei_segmentation import (
        SWEEP_PARAM_LABELS,
        SWEEP_PARAM_ORDER,
        pick_nucleus_channel,
        run_nuclei_parameter_optimizer,
        recommend_nuclei_parameter_sweep_result,
        run_nuclei_parameter_sweep,
        run_nuclei_segmentation,
    )
    from src.spatialscope_analysis.region_analysis import (
        apply_boundary_edit_to_mask,
        discover_boundary_mask_files,
        make_celltype_mask_rgb,
        make_region_canvas_rgb,
        make_region_overlay_figure,
        make_roi_comparison_figure,
        run_region_boundary_analysis,
        save_adjusted_region_analysis,
        save_manual_roi_analysis,
    )
    from src.spatialscope_analysis.neighborhood_analysis import (
        make_neighborhood_figure,
        run_neighborhood_analysis,
        save_neighborhood_analysis_outputs,
    )
    from src.spatialscope_analysis.visualization import COMMON_FIRST, overlay_multi_channels, plot_split_channels
except Exception as exc:  # pragma: no cover - UI fallback for broken local envs
    RUNTIME_IMPORT_ERROR = exc
    RUNTIME_IMPORT_TRACEBACK = traceback.format_exc()

if RUNTIME_IMPORT_ERROR is not None:
    st.title("SpatialScope analysis pipeline")
    st.error(
        "SpatialScope could not start its bundled scientific runtime. "
        "Reinstall the current Windows release, then restart the application."
    )
    with st.expander("Show original import traceback"):
        st.code(RUNTIME_IMPORT_TRACEBACK)
    st.stop()


def session_workspace_root() -> Path:
    return TMP_ROOT / st.session_state["session_id"]


def session_input_dir() -> Path:
    return session_workspace_root() / "inputs"


def session_output_dir() -> Path:
    return session_workspace_root() / "outs"


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


def get_section_output_dir(config, section: str) -> Path:
    if section not in SECTION_OUTPUT_SUBDIRS:
        raise KeyError(f"Unknown section output directory: {section}")
    out_dir = Path(config.save_dir) / SECTION_OUTPUT_SUBDIRS[section]
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def ensure_all_section_output_dirs(config) -> None:
    for section_name in SECTION_OUTPUT_SUBDIRS:
        get_section_output_dir(config, section_name)


def init_state() -> None:
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = uuid.uuid4().hex
    if "config" not in st.session_state:
        st.session_state["config"] = None
    if "available_files" not in st.session_state:
        st.session_state["available_files"] = []
    if "data_result" not in st.session_state:
        st.session_state["data_result"] = None
    if "nuclei_result" not in st.session_state:
        st.session_state["nuclei_result"] = None
    if "nuclei_scan_result" not in st.session_state:
        st.session_state["nuclei_scan_result"] = None
    if "nuclei_auto_scan_result" not in st.session_state:
        st.session_state["nuclei_auto_scan_result"] = None
    if "nuclei_scan_signature" not in st.session_state:
        st.session_state["nuclei_scan_signature"] = None
    if "nuclei_scan_pending" not in st.session_state:
        st.session_state["nuclei_scan_pending"] = False
    if "nuclei_scan_initialized_channel" not in st.session_state:
        st.session_state["nuclei_scan_initialized_channel"] = None
    if "last_applied_nuclei_scan_combo" not in st.session_state:
        st.session_state["last_applied_nuclei_scan_combo"] = None
    if "nuclei_scan_notice" not in st.session_state:
        st.session_state["nuclei_scan_notice"] = ""
    if "pending_nuclei_scan_selection" not in st.session_state:
        st.session_state["pending_nuclei_scan_selection"] = None
    if "pending_nuclei_scan_auto_run" not in st.session_state:
        st.session_state["pending_nuclei_scan_auto_run"] = False
    if "celltype_items" not in st.session_state:
        st.session_state["celltype_items"] = []
    if "celltype_cfg" not in st.session_state:
        st.session_state["celltype_cfg"] = None
    if "assignment_result" not in st.session_state:
        st.session_state["assignment_result"] = None
    if "neighborhood_result" not in st.session_state:
        st.session_state["neighborhood_result"] = None
    if "region_result" not in st.session_state:
        st.session_state["region_result"] = None
    if "nn_result" not in st.session_state:
        st.session_state["nn_result"] = None
    if "boundary_result" not in st.session_state:
        st.session_state["boundary_result"] = None
    if "region_manual_roi_result" not in st.session_state:
        st.session_state["region_manual_roi_result"] = None
    if "region_integrated_result" not in st.session_state:
        st.session_state["region_integrated_result"] = None
    if "cell_distribution_region_masks_result" not in st.session_state:
        st.session_state["cell_distribution_region_masks_result"] = None
    if "cell_distribution_density_result" not in st.session_state:
        st.session_state["cell_distribution_density_result"] = None
    if "cell_distribution_cluster_result" not in st.session_state:
        st.session_state["cell_distribution_cluster_result"] = None
    if "region_adjust_canvas_version" not in st.session_state:
        st.session_state["region_adjust_canvas_version"] = 0
    if "region_roi_canvas_version" not in st.session_state:
        st.session_state["region_roi_canvas_version"] = 0
    if "region_adjust_preview_mask" not in st.session_state:
        st.session_state["region_adjust_preview_mask"] = None
    if "region_adjust_preview_context" not in st.session_state:
        st.session_state["region_adjust_preview_context"] = None
    if "pending_region_adjust_target_type" not in st.session_state:
        st.session_state["pending_region_adjust_target_type"] = None
    if "pending_region_adjust_display_types" not in st.session_state:
        st.session_state["pending_region_adjust_display_types"] = None
    if "region_roi_preview_mask" not in st.session_state:
        st.session_state["region_roi_preview_mask"] = None
    if "local_folder_input" not in st.session_state:
        st.session_state["local_folder_input"] = ""
    if "input_mode_radio" not in st.session_state:
        st.session_state["input_mode_radio"] = "Local folder"
    if "local_output_folder" not in st.session_state:
        st.session_state["local_output_folder"] = ""
    if "n_channels" not in st.session_state:
        st.session_state["n_channels"] = 0
    if "uploaded_file_signature" not in st.session_state:
        st.session_state["uploaded_file_signature"] = tuple()
    if "uploaded_files_widget_nonce" not in st.session_state:
        st.session_state["uploaded_files_widget_nonce"] = 0
    if "channel_color_shuffle_index" not in st.session_state:
        st.session_state["channel_color_shuffle_index"] = 0
    if "neighborhood_cluster_color_shuffle_index" not in st.session_state:
        st.session_state["neighborhood_cluster_color_shuffle_index"] = 0
    if "neighborhood_cluster_signature" not in st.session_state:
        st.session_state["neighborhood_cluster_signature"] = tuple()
    if "neighborhood_saved_signature" not in st.session_state:
        st.session_state["neighborhood_saved_signature"] = None
    if "single_seg_cpu_percent_ui" not in st.session_state:
        default_final_pct = int(round(100.0 * min(CPU_COUNT, max(1, DEFAULT_NATIVE_THREADS)) / max(1, CPU_COUNT)))
        st.session_state["single_seg_cpu_percent_ui"] = min(100, max(10, default_final_pct))
    if "scan_cpu_percent_ui" not in st.session_state:
        default_scan_pct = int(round(100.0 * min(CPU_COUNT, max(1, DEFAULT_SWEEP_WORKERS)) / max(1, CPU_COUNT)))
        st.session_state["scan_cpu_percent_ui"] = min(100, max(10, default_scan_pct))
    if "scan_parallel_workers_ui" not in st.session_state:
        st.session_state["scan_parallel_workers_ui"] = min(CPU_COUNT, max(1, DEFAULT_SWEEP_WORKERS))
    if "scan_threads_per_worker_ui" not in st.session_state:
        st.session_state["scan_threads_per_worker_ui"] = 1
    if "scan_backend_ui" not in st.session_state:
        st.session_state["scan_backend_ui"] = "threading" if os.name == "nt" else "loky"
    if "ui_language" not in st.session_state:
        st.session_state["ui_language"] = _load_ui_language_setting()
    if "assignment_param_scan_result" not in st.session_state:
        st.session_state["assignment_param_scan_result"] = None
    if "assignment_param_scan_signature" not in st.session_state:
        st.session_state["assignment_param_scan_signature"] = None
    if "assignment_param_scan_initialized_cfg_signature" not in st.session_state:
        st.session_state["assignment_param_scan_initialized_cfg_signature"] = None
    if "last_applied_assignment_param_combo" not in st.session_state:
        st.session_state["last_applied_assignment_param_combo"] = None
    if "assignment_param_scan_notice" not in st.session_state:
        st.session_state["assignment_param_scan_notice"] = ""
    if "pending_assignment_param_scan_selection" not in st.session_state:
        st.session_state["pending_assignment_param_scan_selection"] = None
    if "outputs_zip_bytes" not in st.session_state:
        st.session_state["outputs_zip_bytes"] = None
    if "outputs_zip_signature" not in st.session_state:
        st.session_state["outputs_zip_signature"] = None
    if "outputs_zip_path" not in st.session_state:
        st.session_state["outputs_zip_path"] = None
    if "outputs_viewed" not in st.session_state:
        st.session_state["outputs_viewed"] = False


def _close_figure_obj(obj: Any) -> None:
    try:
        if obj is not None and hasattr(obj, "savefig") and hasattr(obj, "canvas"):
            plt.close(obj)
    except Exception:
        pass


def _close_result_figures(result: Any) -> None:
    if not isinstance(result, dict):
        return
    for key in ["figure", "overlay_figure", "split_figure", "panel_figure"]:
        _close_figure_obj(result.get(key))


def invalidate_output_zip_cache() -> None:
    st.session_state["outputs_zip_bytes"] = None
    st.session_state["outputs_zip_signature"] = None
    zip_path = st.session_state.get("outputs_zip_path")
    st.session_state["outputs_zip_path"] = None
    if zip_path:
        try:
            Path(zip_path).unlink(missing_ok=True)
        except Exception:
            pass



def current_output_zip_path(output_signature: str | None = None) -> Path:
    signature = (output_signature or "nosignature").encode("utf-8")
    short_hash = hashlib.sha256(signature).hexdigest()[:12]
    return session_workspace_root() / f"SpatialScope_outputs_{st.session_state['session_id']}_{short_hash}.zip"


def prepare_output_zip_file(folder_path: Path, zip_path: Path) -> Path:
    folder_path = Path(folder_path)
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip_path = zip_path.with_suffix(".tmp.zip")
    with zipfile.ZipFile(tmp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder_path.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(folder_path)))
    tmp_zip_path.replace(zip_path)
    return zip_path


@st.cache_data(show_spinner=False)
def read_file_bytes_cached(path_str: str, mtime_ns: int, size: int) -> bytes:
    return Path(path_str).read_bytes()


def refresh_output_zip_state(save_dir: Path) -> None:
    save_dir = Path(save_dir)
    if not save_dir.exists():
        return
    output_signature = build_output_signature(save_dir)
    zip_path = current_output_zip_path(output_signature)
    zip_path = prepare_output_zip_file(save_dir, zip_path)
    stat = zip_path.stat()
    st.session_state["outputs_zip_path"] = str(zip_path)
    st.session_state["outputs_zip_signature"] = output_signature
    st.session_state["outputs_zip_bytes"] = read_file_bytes_cached(str(zip_path), int(stat.st_mtime_ns), int(stat.st_size))


def figure_to_png_bytes(fig, dpi: int = 220) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0)
    buffer.seek(0)
    return buffer.getvalue()


def saved_path_to_media_payload(path_value: Any) -> tuple[bytes | None, str | None]:
    if not path_value:
        return None, None
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return None, None
    suffix = path.suffix.lower()
    if suffix == ".svg":
        return path.read_bytes(), "image/svg+xml"
    if suffix == ".png":
        return path.read_bytes(), "image/png"
    return None, None


def render_zoomable_media(media_bytes: bytes, mime_type: str, component_key: str, component_height: int = 860) -> None:
    if mime_type == "image/png":
        st.image(media_bytes, use_container_width=True)
        return

    if mime_type == "image/svg+xml":
        b64 = base64.b64encode(media_bytes).decode("ascii")
        safe_key = re.sub(r"[^0-9A-Za-z_]+", "_", component_key)
        html = f"""
        <div id="static_{safe_key}" style="width:100%;background:white;border:1px solid #e5e7eb;border-radius:8px;overflow:auto;padding:6px;box-sizing:border-box;">
          <img src="data:{mime_type};base64,{b64}" style="display:block;width:100%;height:auto;max-width:100%;margin:0 auto;" />
        </div>
        """
        st.iframe(html, height=component_height, width="stretch", tab_index=-1)
        return

    st.download_button(
        "Download figure",
        data=media_bytes,
        file_name=f"{component_key}.bin",
        mime=mime_type,
        key=f"download_{component_key}",
    )



def render_zoomable_figure(
    fig: Any = None,
    component_key: str = "figure",
    saved_paths: Sequence[Any] | None = None,
    component_height: int = 860,
    prefer_svg: bool = False,
) -> None:
    media_bytes = None
    mime_type = None

    if saved_paths is not None:
        def _path_priority(path_value: Any) -> tuple[int, str]:
            try:
                suffix = Path(path_value).suffix.lower()
            except Exception:
                suffix = ""
            if prefer_svg:
                priority = {".svg": 0, ".png": 1}.get(suffix, 2)
            else:
                priority = {".png": 0, ".svg": 1}.get(suffix, 2)
            return (priority, str(path_value))
        for path_value in sorted([p for p in saved_paths if p], key=_path_priority):
            media_bytes, mime_type = saved_path_to_media_payload(path_value)
            if media_bytes is not None:
                break

    if media_bytes is None and fig is not None:
        try:
            media_bytes = figure_to_png_bytes(fig)
            mime_type = "image/png"
        except Exception:
            media_bytes = None
            mime_type = None

    if media_bytes is not None and mime_type is not None:
        render_zoomable_media(media_bytes, mime_type, component_key=component_key, component_height=component_height)
    elif fig is not None:
        st.pyplot(fig, clear_figure=False, use_container_width=True)


def _fit_canvas_size(height: int, width: int, max_width: int = 900, max_height: int = 760) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        return max(1, width), max(1, height)
    scale = min(float(max_width) / float(width), float(max_height) / float(height), 1.0)
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def _rgb_float_to_pil(rgb: np.ndarray, width: int | None = None, height: int | None = None) -> Image.Image:
    arr = np.clip(np.asarray(rgb, dtype=float), 0.0, 1.0)
    arr_u8 = (arr * 255).astype(np.uint8)
    image = Image.fromarray(arr_u8)
    if width is not None and height is not None and (image.width != int(width) or image.height != int(height)):
        image = image.resize((int(width), int(height)), Image.Resampling.BILINEAR)
    return image


def _resize_bool_mask(mask: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    image = Image.fromarray((np.asarray(mask).astype(np.uint8) * 255))
    resized = image.resize((int(target_shape[1]), int(target_shape[0])), Image.Resampling.NEAREST)
    return (np.array(resized) > 0)


def _extract_draw_mask_from_canvas(
    canvas_result: Any,
    background_rgb_resized_u8: np.ndarray,
    original_shape: tuple[int, int],
    diff_threshold: int = 18,
) -> np.ndarray:
    if canvas_result is None or getattr(canvas_result, "image_data", None) is None:
        return np.zeros(original_shape, dtype=bool)
    image_data = np.asarray(canvas_result.image_data)
    if image_data.ndim != 3 or image_data.shape[2] < 3:
        return np.zeros(original_shape, dtype=bool)
    drawn_rgb = image_data[:, :, :3].astype(np.int16)
    bg_rgb = np.asarray(background_rgb_resized_u8).astype(np.int16)
    if drawn_rgb.shape[:2] != bg_rgb.shape[:2]:
        return np.zeros(original_shape, dtype=bool)
    diff = np.max(np.abs(drawn_rgb - bg_rgb), axis=2) > int(diff_threshold)
    diff = ndi.binary_opening(diff, structure=np.ones((2, 2), dtype=bool))
    diff = ndi.binary_closing(diff, structure=np.ones((3, 3), dtype=bool))
    return _resize_bool_mask(diff, original_shape)


def _overlay_mask_on_rgb(rgb: np.ndarray, mask: np.ndarray, color_hex: str = "#ffcc00", alpha: float = 0.55) -> np.ndarray:
    out = np.clip(np.asarray(rgb, dtype=float), 0.0, 1.0).copy()
    if mask is None or not np.any(mask):
        return out
    color = np.array(ImageColor.getrgb(color_hex), dtype=float) / 255.0
    mask_bool = np.asarray(mask).astype(bool)
    out[mask_bool] = (1.0 - alpha) * out[mask_bool] + alpha * color
    return np.clip(out, 0.0, 1.0)


def _label_rois_from_binary_mask(mask: np.ndarray) -> np.ndarray:
    binary = np.asarray(mask).astype(bool)
    if not np.any(binary):
        return np.zeros(binary.shape, dtype=np.uint16)
    filled = ndi.binary_fill_holes(binary)
    labeled, _ = ndi.label(filled)
    return labeled.astype(np.uint16)


def _close_region_nested_results(region_result: Any) -> None:
    if not isinstance(region_result, dict):
        return
    _close_result_figures(region_result)
    if isinstance(region_result.get("adjusted_result"), dict):
        _close_result_figures(region_result.get("adjusted_result"))
    if isinstance(region_result.get("roi_result"), dict):
        _close_result_figures(region_result.get("roi_result"))


def _build_overlay_rgb_for_region_ui(config: PipelineConfig, data_result: Dict[str, Any]) -> np.ndarray:
    overlay_fig = None
    try:
        overlay_fig, overlay_rgb = overlay_multi_channels(
            df=data_result["df_pixels"],
            shapes=data_result["shapes"],
            image_id=config.image_id,
            channels_cfg=[channel.to_dict() for channel in config.channels],
            overlay_channels=config.overlay_channels or [channel.channel for channel in config.channels],
            white_channel=config.white_channel,
            white_weight=config.white_weight,
            clip_hi=99.8,
            pixel_size_um=config.pixel_size_um,
            save_path=None,
        )
        return overlay_rgb
    finally:
        _close_figure_obj(overlay_fig)


def _editable_boundary_color(type_name: str, celltype_cfg: Sequence[Dict[str, Any]], fallback: str = "#ffd400") -> str:
    for ct in celltype_cfg:
        if str(ct.get("name")) == str(type_name):
            return str(ct.get("color_hex", fallback))
    return fallback


def _mask_to_canvas_initial_drawing(
    mask: np.ndarray,
    canvas_width: int,
    canvas_height: int,
    stroke_color: str = "#ffd400",
    stroke_width: float = 3.0,
    contour_step: int = 2,
) -> Dict[str, Any]:
    mask = np.asarray(mask).astype(bool)
    if mask.ndim != 2 or not np.any(mask):
        return {"version": "4.4.0", "objects": [], "background": ""}
    height, width = mask.shape
    scale_x = float(canvas_width) / max(1.0, float(width))
    scale_y = float(canvas_height) / max(1.0, float(height))
    objects: List[Dict[str, Any]] = []
    for contour in find_contours(mask.astype(float), 0.5):
        if contour_step > 1 and contour.shape[0] > contour_step:
            contour = contour[::contour_step]
        if contour.shape[0] < 3:
            continue
        points = [{"x": float(pt[1] * scale_x), "y": float(pt[0] * scale_y)} for pt in contour]
        objects.append(
            {
                "type": "polygon",
                "version": "4.4.0",
                "left": 0.0,
                "top": 0.0,
                "originX": "left",
                "originY": "top",
                "fill": "rgba(0,0,0,0)",
                "stroke": str(stroke_color),
                "strokeWidth": float(stroke_width),
                "strokeLineCap": "round",
                "strokeLineJoin": "round",
                "transparentCorners": False,
                "objectCaching": False,
                "cornerColor": str(stroke_color),
                "borderColor": str(stroke_color),
                "points": points,
            }
        )
    return {"version": "4.4.0", "objects": objects, "background": ""}


def _boundary_line_mask_to_region_mask(line_mask: np.ndarray) -> np.ndarray:
    line_mask = np.asarray(line_mask).astype(bool)
    if not np.any(line_mask):
        return np.zeros_like(line_mask, dtype=bool)
    work = ndi.binary_dilation(line_mask, iterations=1)
    work = ndi.binary_closing(work, structure=np.ones((5, 5), dtype=bool))
    work = ndi.binary_fill_holes(work)
    labels, n_labels = ndi.label(work)
    if n_labels <= 0:
        return work.astype(bool)
    sizes = np.bincount(labels.ravel())
    keep = np.zeros_like(work, dtype=bool)
    for label_id in range(1, len(sizes)):
        if sizes[label_id] >= 25:
            keep |= labels == label_id
    return keep.astype(bool)


def _polygon_draw_mask_to_region_mask(draw_mask: np.ndarray) -> np.ndarray:
    draw_mask = np.asarray(draw_mask).astype(bool)
    if not np.any(draw_mask):
        return np.zeros_like(draw_mask, dtype=bool)
    work = ndi.binary_closing(draw_mask, structure=np.ones((5, 5), dtype=bool))
    work = ndi.binary_fill_holes(work)
    work = ndi.binary_opening(work, structure=np.ones((3, 3), dtype=bool))
    labels, n_labels = ndi.label(work)
    if n_labels <= 0:
        return work.astype(bool)
    sizes = np.bincount(labels.ravel())
    keep = np.zeros_like(work, dtype=bool)
    for label_id in range(1, len(sizes)):
        if sizes[label_id] >= 25:
            keep |= labels == label_id
    return keep.astype(bool)


def _coerce_float_list(values: Sequence[Any]) -> List[float]:
    coerced: List[float] = []
    for value in values:
        try:
            coerced.append(float(value))
        except Exception:
            continue
    return coerced


def _fabric_path_to_closed_polygons(path_data: Any) -> List[List[tuple[float, float]]]:
    if not isinstance(path_data, list):
        return []

    polygons: List[List[tuple[float, float]]] = []
    current: List[tuple[float, float]] = []
    cursor_x = 0.0
    cursor_y = 0.0
    start_point: tuple[float, float] | None = None

    def _append_line_points(values: Sequence[float], relative: bool) -> None:
        nonlocal cursor_x, cursor_y, current
        for idx in range(0, len(values) - 1, 2):
            x_val = float(values[idx])
            y_val = float(values[idx + 1])
            if relative:
                x_val += cursor_x
                y_val += cursor_y
            cursor_x = x_val
            cursor_y = y_val
            current.append((cursor_x, cursor_y))

    for segment in path_data:
        if not isinstance(segment, (list, tuple)) or not segment:
            continue
        command = str(segment[0])
        if not command:
            continue
        cmd = command[0]
        cmd_upper = cmd.upper()
        relative = cmd.islower()
        values = _coerce_float_list(segment[1:])

        if cmd_upper == "M":
            if len(current) >= 3:
                polygons.append(current.copy())
            current = []
            for idx in range(0, len(values) - 1, 2):
                x_val = float(values[idx])
                y_val = float(values[idx + 1])
                if relative:
                    x_val += cursor_x
                    y_val += cursor_y
                cursor_x = x_val
                cursor_y = y_val
                point = (cursor_x, cursor_y)
                if idx == 0:
                    current = [point]
                    start_point = point
                else:
                    current.append(point)
            continue

        if cmd_upper == "L":
            _append_line_points(values, relative)
            continue

        if cmd_upper == "H":
            for value in values:
                x_val = float(value)
                if relative:
                    x_val += cursor_x
                cursor_x = x_val
                current.append((cursor_x, cursor_y))
            continue

        if cmd_upper == "V":
            for value in values:
                y_val = float(value)
                if relative:
                    y_val += cursor_y
                cursor_y = y_val
                current.append((cursor_x, cursor_y))
            continue

        if cmd_upper == "Z":
            if len(current) >= 3:
                polygons.append(current.copy())
            current = []
            if start_point is not None:
                cursor_x, cursor_y = start_point
            start_point = None

    return polygons


def _fabric_polygon_object_to_points(obj: Dict[str, Any]) -> List[tuple[float, float]]:
    raw_points = obj.get("points")
    if not isinstance(raw_points, list):
        return []

    left = float(obj.get("left", 0.0) or 0.0)
    top = float(obj.get("top", 0.0) or 0.0)
    scale_x = float(obj.get("scaleX", 1.0) or 1.0)
    scale_y = float(obj.get("scaleY", 1.0) or 1.0)
    path_offset = obj.get("pathOffset") if isinstance(obj.get("pathOffset"), dict) else {}
    offset_x = float(path_offset.get("x", 0.0) or 0.0)
    offset_y = float(path_offset.get("y", 0.0) or 0.0)
    width = float(obj.get("width", 0.0) or 0.0)
    height = float(obj.get("height", 0.0) or 0.0)
    origin_adjust_x = offset_x if width > 0 else 0.0
    origin_adjust_y = offset_y if height > 0 else 0.0

    points: List[tuple[float, float]] = []
    for raw_point in raw_points:
        if not isinstance(raw_point, dict):
            continue
        try:
            point_x = float(raw_point.get("x", 0.0) or 0.0)
            point_y = float(raw_point.get("y", 0.0) or 0.0)
        except Exception:
            continue
        canvas_x = left + ((point_x - offset_x) * scale_x) + origin_adjust_x
        canvas_y = top + ((point_y - offset_y) * scale_y) + origin_adjust_y
        points.append((canvas_x, canvas_y))
    return points


def _extract_closed_region_mask_from_canvas_objects(
    canvas_result: Any,
    original_shape: tuple[int, int],
    canvas_shape: tuple[int, int],
) -> np.ndarray | None:
    json_data = getattr(canvas_result, "json_data", None)
    if not isinstance(json_data, dict):
        return None
    objects = json_data.get("objects")
    if not isinstance(objects, list) or not objects:
        return None

    canvas_h = max(1, int(canvas_shape[0]))
    canvas_w = max(1, int(canvas_shape[1]))
    raster = Image.new("L", (canvas_w, canvas_h), 0)
    painter = ImageDraw.Draw(raster)
    drew_any = False

    for obj in objects:
        if not isinstance(obj, dict):
            continue
        obj_type = str(obj.get("type", "")).strip().lower()
        if obj_type == "path":
            for polygon_points in _fabric_path_to_closed_polygons(obj.get("path")):
                if len(polygon_points) < 3:
                    continue
                painter.polygon(polygon_points, fill=255, outline=255)
                drew_any = True
            continue
        if obj_type == "polygon":
            polygon_points = _fabric_polygon_object_to_points(obj)
            if len(polygon_points) < 3:
                continue
            painter.polygon(polygon_points, fill=255, outline=255)
            drew_any = True

    if not drew_any:
        return None

    canvas_mask = np.asarray(raster, dtype=np.uint8) > 0
    return _resize_bool_mask(canvas_mask, original_shape).astype(bool)


def _prepare_canvas_background_image(image: Image.Image | None) -> Image.Image | None:
    if image is None:
        return None
    if not isinstance(image, Image.Image):
        return image
    prepared = image
    try:
        prepared = prepared.convert("RGB")
    except Exception:
        pass
    try:
        prepared = prepared.copy()
    except Exception:
        pass
    return prepared


def _run_st_canvas_safe(**kwargs):
    background_image = kwargs.get("background_image")
    candidates = []
    if background_image is None:
        candidates = [None]
    else:
        candidates.append(background_image)
        if isinstance(background_image, Image.Image):
            for mode in ("RGB", "RGBA"):
                try:
                    candidates.append(background_image.convert(mode).copy())
                except Exception:
                    pass

    last_exc = None
    for candidate in candidates:
        try:
            local_kwargs = dict(kwargs)
            local_kwargs["background_image"] = candidate
            return st_canvas(**local_kwargs)
        except Exception as exc:
            last_exc = exc

    if last_exc is not None:
        raise last_exc
    return st_canvas(**kwargs)


def _um_to_px_maybe_zero(value_um: float, pixel_size_um: tuple[float, float]) -> int:
    try:
        value_um = float(value_um)
    except Exception:
        return 0
    if value_um <= 0:
        return 0
    px_um_x, px_um_y = float(pixel_size_um[0]), float(pixel_size_um[1])
    scale = np.sqrt(max(1e-12, px_um_x * px_um_y))
    return max(1, int(round(value_um / scale)))


def _postprocess_drawn_region_mask(
    mask: np.ndarray,
    df_cells_for_count: pd.DataFrame | None,
    pixel_size_um: tuple[float, float],
) -> np.ndarray:
    from skimage import morphology

    work = np.asarray(mask).astype(bool)
    if not np.any(work):
        return np.zeros_like(work, dtype=bool)

    close_px = _um_to_px_maybe_zero(float(st.session_state.get("region_close_um", 0.0)), pixel_size_um)
    dilate_px = _um_to_px_maybe_zero(float(st.session_state.get("region_dilate_um", 0.0)), pixel_size_um)
    min_area_um2 = float(st.session_state.get("region_min_area_um2", 0.0))
    px_area_um2 = float(pixel_size_um[0]) * float(pixel_size_um[1])
    min_area_px = int(round(min_area_um2 / max(1e-12, px_area_um2))) if min_area_um2 > 0 else 0
    min_cells = max(1, int(st.session_state.get("region_min_cells", 1) or 1))

    if close_px > 0:
        if hasattr(morphology, "isotropic_closing"):
            work = morphology.isotropic_closing(work, radius=close_px)
        else:
            work = morphology.binary_closing(work, footprint=morphology.disk(close_px))

    work = ndi.binary_fill_holes(work)

    if min_area_px > 0:
        work = morphology.remove_small_objects(work, min_size=min_area_px)

    if dilate_px > 0:
        if hasattr(morphology, "isotropic_dilation"):
            work = morphology.isotropic_dilation(work, radius=dilate_px)
        else:
            work = morphology.binary_dilation(work, footprint=morphology.disk(dilate_px))

    work = ndi.binary_fill_holes(work)

    if min_cells > 1:
        labels_cc, n_labels = ndi.label(work)
        if n_labels > 0:
            keep = np.zeros_like(work, dtype=bool)
            if df_cells_for_count is not None and len(df_cells_for_count) > 0:
                h, w = work.shape
                cy = np.clip(np.rint(df_cells_for_count["centroid_y_px"].to_numpy(float)).astype(int), 0, h - 1)
                cx = np.clip(np.rint(df_cells_for_count["centroid_x_px"].to_numpy(float)).astype(int), 0, w - 1)
                region_ids = labels_cc[cy, cx]
                region_ids = region_ids[region_ids > 0]
                if region_ids.size > 0:
                    counts = np.bincount(region_ids, minlength=n_labels + 1)
                    keep_ids = np.where(counts >= min_cells)[0]
                    keep_ids = keep_ids[keep_ids > 0]
                    if keep_ids.size > 0:
                        keep = np.isin(labels_cc, keep_ids)
                work = keep.astype(bool)
            else:
                work = np.zeros_like(work, dtype=bool)

    return work.astype(bool)


def _postprocess_manual_roi_binary_mask(
    binary_mask: np.ndarray,
    df_cells_for_count: pd.DataFrame | None,
    pixel_size_um: tuple[float, float],
) -> np.ndarray:
    binary = np.asarray(binary_mask).astype(bool)
    if not np.any(binary):
        return np.zeros_like(binary, dtype=bool)

    labeled, n_labels = ndi.label(ndi.binary_fill_holes(binary))
    if n_labels <= 0:
        return np.zeros_like(binary, dtype=bool)

    processed = np.zeros_like(binary, dtype=bool)
    for label_id in range(1, n_labels + 1):
        component = labeled == label_id
        processed |= _postprocess_drawn_region_mask(component, df_cells_for_count, pixel_size_um)
    return processed.astype(bool)


def _suggest_new_manual_boundary_display_name(existing_display_names: Sequence[str]) -> str:
    used_names = {str(name).strip().lower() for name in existing_display_names if str(name).strip()}
    idx = 1
    while True:
        candidate = f"Manual ROI {idx:03d}"
        if candidate.lower() not in used_names:
            return candidate
        idx += 1


def _make_unique_manual_boundary_key(display_name: str, existing_mask_names: Sequence[str]) -> str:
    base = safe_name(str(display_name).strip(), "manual_roi")
    candidate = f"manual_drawn__{base}"
    existing = {str(name) for name in existing_mask_names}
    suffix = 2
    while candidate in existing:
        candidate = f"manual_drawn__{base}__{suffix:02d}"
        suffix += 1
    return candidate


def _render_manual_boundary_adjustment_ui(
    *,
    config: PipelineConfig,
    assignment_result: Dict[str, Any],
    region_result: Dict[str, Any],
    celltype_names: Sequence[str],
    available_boundary_types: Sequence[str],
    display_types: Sequence[str],
    display_celltypes: Sequence[str],
) -> None:
    with st.expander("Optional adjustment for computational ROI", expanded=False):
        if st_canvas is None:
            st.warning("Manual boundary adjustment is unavailable because the drawing component could not be loaded in this Streamlit environment. Please reinstall the canvas package from requirements.txt and restart Streamlit.")
            if DRAWABLE_CANVAS_ERROR is not None:
                st.caption(str(DRAWABLE_CANVAS_ERROR))
            return

        st.caption(
            "Optional: redraw a computational ROI with any custom polygon region, or use include/exclude edits on top of the current computationally recognized ROI. "
            "In redraw mode, the saved region follows the polygon you close on the canvas rather than computational ROI parameters or cell-type filters. "
            "The adjusted ROI is saved as a downstream-usable ROI for both Customized display and save and Distance analysis."
        )

        adjusted_existing = region_result.get("adjusted_result") if isinstance(region_result.get("adjusted_result"), dict) else None
        base_masks_source = adjusted_existing.get("masks") if isinstance(adjusted_existing, dict) and adjusted_existing.get("masks") else region_result["masks"]
        base_display_names = adjusted_existing.get("boundary_display_names") if isinstance(adjusted_existing, dict) else {}
        if not isinstance(base_display_names, dict):
            base_display_names = {}
        editable_boundary_types = [str(name) for name in base_masks_source.keys()]
        editable_boundary_display_names = [
            str(base_display_names.get(name, name)).strip() or str(name)
            for name in editable_boundary_types
        ]
        mask_shape = np.asarray(assignment_result["celltype_mask"]).shape
        pending_display_types = st.session_state.get("pending_region_adjust_display_types")
        if pending_display_types is not None:
            st.session_state["region_adjust_display_types"] = [
                str(name) for name in pending_display_types if str(name) in editable_boundary_types
            ]
            st.session_state["pending_region_adjust_display_types"] = None

        edit_ctrl_cols = st.columns([1.7, 1.4, 1.7, 1.7, 3.0])
        with edit_ctrl_cols[0]:
            adjust_display_types = st.multiselect(
                "Boundary types to show while editing",
                options=list(editable_boundary_types),
                default=[name for name in display_types if name in editable_boundary_types],
                key="region_adjust_display_types",
            )
        active_adjust_types = [name for name in adjust_display_types if name in editable_boundary_types]

        with edit_ctrl_cols[2]:
            adjust_display_celltypes = st.multiselect(
                "Cell types to show while editing",
                options=list(celltype_names),
                default=list(display_celltypes),
                key="region_adjust_display_celltypes",
            )
        active_adjust_celltypes = [name for name in adjust_display_celltypes if name in celltype_names] or list(celltype_names)

        with edit_ctrl_cols[3]:
            edit_mode_label = st.selectbox(
                "Adjustment mode",
                options=["Redraw ROI / boundary", "Include region / cells", "Exclude region / cells"],
                key="region_adjust_edit_mode",
            )
        allow_new_manual_boundary = edit_mode_label == "Redraw ROI / boundary"
        target_options = ([""] + editable_boundary_types) if allow_new_manual_boundary else list(editable_boundary_types)
        if not target_options:
            st.info("No computational ROI is currently available for editing. Use Redraw ROI / boundary to create a new manual ROI.")
            return
        pending_target_type = st.session_state.get("pending_region_adjust_target_type")
        if pending_target_type in target_options:
            st.session_state["region_adjust_target_type"] = pending_target_type
            st.session_state["pending_region_adjust_target_type"] = None
        current_target_state = st.session_state.get("region_adjust_target_type")
        if current_target_state not in target_options:
            st.session_state["region_adjust_target_type"] = target_options[0]
        with edit_ctrl_cols[1]:
            target_type = st.selectbox(
                "Boundary type to edit",
                options=target_options,
                key="region_adjust_target_type",
                format_func=lambda value: "Create new manual ROI / boundary" if str(value).strip() == "" else str(base_display_names.get(value, value)).strip() or str(value),
            )
        with edit_ctrl_cols[4]:
            st.caption(
                "Redraw replaces the selected ROI with the exact polygon region you close on the canvas, or creates a brand-new manual ROI when Boundary type to edit is left empty. Include and Exclude use your drawn polygon as an add/remove edit on top of the current ROI, then recompute the adjusted ROI with the current ROI parameters."
            )
        if edit_mode_label != "Redraw ROI / boundary" and not str(target_type).strip():
            st.info("Select an existing computational ROI to use Include or Exclude editing.")
            return

        creating_new_manual_boundary = not str(target_type).strip()
        if creating_new_manual_boundary:
            adjust_name_key = "region_adjust_display_name_new_manual"
            current_display_name = _suggest_new_manual_boundary_display_name(editable_boundary_display_names)
        else:
            adjust_name_key = f"region_adjust_display_name_{re.sub(r'[^0-9A-Za-z_]+', '_', str(target_type))}"
            current_display_name = str(base_display_names.get(target_type, target_type)).strip() or str(target_type)
        st.text_input(
            "Name for this adjusted ROI / boundary",
            value=current_display_name,
            key=adjust_name_key,
            help="This name is used in downstream Customized display and save and Distance analysis.",
        )

        raw_target_mask = None
        if not creating_new_manual_boundary:
            raw_target_mask = base_masks_source.get(target_type, region_result["masks"].get(target_type))
        current_target_mask = np.asarray(raw_target_mask, dtype=bool) if raw_target_mask is not None else np.zeros(mask_shape, dtype=bool)
        display_boundary_types = [name for name in active_adjust_types if name in editable_boundary_types]
        if target_type and target_type not in display_boundary_types:
            display_boundary_types.append(str(target_type))
        preview_context = {
            "edit_mode": str(edit_mode_label),
            "target_type": str(target_type),
            "creating_new_manual_boundary": bool(creating_new_manual_boundary),
            "display_boundary_types": tuple(str(name) for name in display_boundary_types),
            "display_celltypes": tuple(str(name) for name in active_adjust_celltypes),
            "canvas_version": int(st.session_state.get("region_adjust_canvas_version", 0)),
        }
        if st.session_state.get("region_adjust_preview_context") != preview_context:
            st.session_state["region_adjust_preview_mask"] = None
            st.session_state["region_adjust_preview_context"] = preview_context
        adjust_rgb = make_region_canvas_rgb(
            celltype_mask=assignment_result["celltype_mask"],
            celltype_cfg=st.session_state["celltype_cfg"],
            masks={name: np.asarray(mask, dtype=bool).copy() for name, mask in base_masks_source.items()},
            selected_types=display_boundary_types,
            display_celltypes=active_adjust_celltypes,
            boundary_color=str(st.session_state["region_boundary_color"]),
            use_type_colors=bool(st.session_state["region_use_type_colors"]),
            thickness=max(1, int(round(float(st.session_state["region_line_width"])))),
        )
        ah, aw = adjust_rgb.shape[:2]
        canvas_w, canvas_h = _fit_canvas_size(ah, aw, max_width=560, max_height=560)
        adjust_bg_pil = _prepare_canvas_background_image(_rgb_float_to_pil(adjust_rgb, width=canvas_w, height=canvas_h))
        adjust_bg_u8 = np.array(adjust_bg_pil)

        editable_color = _editable_boundary_color(
            target_type if not creating_new_manual_boundary else "manual_boundary",
            st.session_state["celltype_cfg"],
            fallback=str(st.session_state["region_boundary_color"]),
        )

        adjusted_preview_mask = st.session_state.get("region_adjust_preview_mask")
        if adjusted_preview_mask is None:
            adjusted_preview_mask = current_target_mask.copy()

        canvas_signature = hashlib.sha256(
            json.dumps(
                {
                    "target_type": str(target_type) if str(target_type).strip() else "__new_manual_boundary__",
                    "edit_mode": str(edit_mode_label),
                    "display_boundary_types": list(display_boundary_types),
                    "display_celltypes": list(active_adjust_celltypes),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:12]

        st.caption(
            "Draw a polygon over the current ROI view and right-click to close it. In redraw mode, the enclosed polygon becomes the ROI directly. In include/exclude mode, the current target-cell regions and ROI remain visible in the background, and the adjusted ROI is recomputed immediately for preview."
        )

        edit_panels = st.columns(2, gap="large")
        with edit_panels[0]:
            st.markdown("**Draw edit polygons**")
            safe_mode = re.sub(r'[^0-9A-Za-z_]+', '_', str(edit_mode_label).lower())
            canvas_result = _run_st_canvas_safe(
                fill_color=editable_color + "33",
                stroke_width=max(2, int(round(float(st.session_state["region_line_width"])))),
                stroke_color=editable_color,
                background_image=adjust_bg_pil,
                update_streamlit=True,
                height=canvas_h,
                width=canvas_w,
                drawing_mode="polygon",
                key=f"region_adjust_canvas_{canvas_signature}_{safe_mode}_v{int(st.session_state.get('region_adjust_canvas_version', 0))}",
                display_toolbar=True,
            )
            canvas_json_data = getattr(canvas_result, "json_data", None)
            has_canvas_object_payload = isinstance(canvas_json_data, dict) and isinstance(canvas_json_data.get("objects"), list)
            adjusted_draw_region = _extract_closed_region_mask_from_canvas_objects(
                canvas_result,
                original_shape=(ah, aw),
                canvas_shape=(canvas_h, canvas_w),
            )
            if adjusted_draw_region is None:
                if edit_mode_label == "Redraw ROI / boundary" and has_canvas_object_payload:
                    adjusted_draw_region = np.zeros((ah, aw), dtype=bool)
                else:
                    adjusted_draw_mask = _extract_draw_mask_from_canvas(canvas_result, adjust_bg_u8, (ah, aw))
                    adjusted_draw_region = _polygon_draw_mask_to_region_mask(adjusted_draw_mask)
            if target_type and str(target_type) in {str(name) for name in celltype_names}:
                df_target_cells = assignment_result["df_cells"][assignment_result["df_cells"]["celltype"].astype(str) == str(target_type)].copy()
            else:
                df_target_cells = assignment_result["df_cells"].copy()

            if edit_mode_label == "Redraw ROI / boundary":
                adjusted_preview_mask = np.asarray(adjusted_draw_region, dtype=bool)
            else:
                if np.any(adjusted_draw_region):
                    operation = "include" if edit_mode_label == "Include region / cells" else "exclude"
                    adjusted_candidate = apply_boundary_edit_to_mask(current_target_mask, adjusted_draw_region, operation)
                    adjusted_preview_mask = _postprocess_drawn_region_mask(adjusted_candidate, df_target_cells, config.pixel_size_um)
                else:
                    adjusted_preview_mask = current_target_mask.copy()
            st.session_state["region_adjust_preview_mask"] = adjusted_preview_mask

        preview_masks = {name: np.asarray(mask, dtype=bool).copy() for name, mask in base_masks_source.items()}
        preview_target_type = str(target_type) if str(target_type).strip() else "__manual_boundary_preview__"
        preview_masks[preview_target_type] = adjusted_preview_mask
        preview_selected_types = [name for name in display_boundary_types if name in preview_masks]
        if preview_target_type not in preview_selected_types:
            preview_selected_types.append(preview_target_type)

        preview_rgb = make_region_canvas_rgb(
            celltype_mask=assignment_result["celltype_mask"],
            celltype_cfg=st.session_state["celltype_cfg"],
            masks=preview_masks,
            selected_types=preview_selected_types,
            display_celltypes=active_adjust_celltypes,
            boundary_color=str(st.session_state["region_boundary_color"]),
            use_type_colors=bool(st.session_state["region_use_type_colors"]),
            thickness=max(1, int(round(float(st.session_state["region_line_width"])))),
        )
        preview_pil = _rgb_float_to_pil(preview_rgb, width=canvas_w, height=canvas_h)

        with edit_panels[1]:
            st.markdown("**Adjusted-boundary preview**")
            preview_caption = "Live preview of the adjusted ROI"
            if edit_mode_label != "Redraw ROI / boundary":
                preview_caption += " after include/exclude editing"
            st.image(preview_pil, caption=preview_caption, use_container_width=False)

        adjust_btn_cols = st.columns([1.4, 1.2, 3.4])
        with adjust_btn_cols[0]:
            save_adjust_clicked = st.button("Save adjusted region result", key="save_adjusted_region_btn")
        with adjust_btn_cols[1]:
            clear_adjust_clicked = st.button("Reset drawn polygons", key="clear_adjusted_region_btn")
        with adjust_btn_cols[2]:
            st.caption(
                "Saving writes a new adjusted-region figure, masks, tables, and area summary CSV without overwriting the original computed ROI outputs. "
                "The adjusted ROI remains available in downstream Customized display and save and Distance analysis."
            )

        if clear_adjust_clicked:
            st.session_state["region_adjust_canvas_version"] = int(st.session_state.get("region_adjust_canvas_version", 0)) + 1
            st.session_state["region_adjust_preview_mask"] = None
            st.rerun()

        if save_adjust_clicked:
            if edit_mode_label == "Redraw ROI / boundary" and not np.any(adjusted_preview_mask):
                st.error("No valid adjusted boundary was detected from the currently drawn polygon(s).")
            else:
                try:
                    adjusted_masks = {name: np.asarray(mask, dtype=bool).copy() for name, mask in base_masks_source.items()}
                    if creating_new_manual_boundary:
                        default_name = _suggest_new_manual_boundary_display_name(editable_boundary_display_names)
                        display_name_clean = str(st.session_state.get(adjust_name_key) or default_name).strip() or default_name
                        saved_target_type = _make_unique_manual_boundary_key(display_name_clean, adjusted_masks.keys())
                    else:
                        display_name_clean = str(st.session_state.get(adjust_name_key) or target_type).strip() or str(target_type)
                        saved_target_type = str(target_type)
                    adjusted_masks[saved_target_type] = adjusted_preview_mask
                    adjusted_display_name_map = {name: str(base_display_names.get(name, name)).strip() or str(name) for name in adjusted_masks.keys()}
                    adjusted_display_name_map[saved_target_type] = display_name_clean
                    edited_boundary_types = {
                        str(name)
                        for name in (adjusted_existing.get("edited_boundary_types", []) if isinstance(adjusted_existing, dict) else [])
                        if str(name) in adjusted_masks
                    }
                    edited_boundary_types.add(str(saved_target_type))
                    adjusted_selected_types = [name for name in display_boundary_types if name in adjusted_masks]
                    if saved_target_type not in adjusted_selected_types:
                        adjusted_selected_types.append(str(saved_target_type))
                    edit_mode_key = {
                        "Redraw ROI / boundary": "redraw_boundary_polygon",
                        "Include region / cells": "include_region",
                        "Exclude region / cells": "exclude_region",
                    }.get(edit_mode_label, "redraw_boundary_polygon")
                    adjusted_result = save_adjusted_region_analysis(
                        df_cells=assignment_result["df_cells"],
                        celltype_mask=assignment_result["celltype_mask"],
                        celltype_cfg=st.session_state["celltype_cfg"],
                        save_dir=get_section_output_dir(config, 'region_analysis'),
                        pixel_size_um=config.pixel_size_um,
                        adjusted_masks=adjusted_masks,
                        selected_types=adjusted_selected_types,
                        edit_meta={
                            "target_type": saved_target_type,
                            "edit_mode": edit_mode_key,
                            "display_name": display_name_clean,
                        },
                        edited_boundary_types=sorted(edited_boundary_types),
                        boundary_display_names=adjusted_display_name_map,
                        line_width=float(st.session_state["region_line_width"]),
                        line_style=str(st.session_state["region_line_style"]),
                        boundary_color=str(st.session_state["region_boundary_color"]),
                        use_type_colors=bool(st.session_state["region_use_type_colors"]),
                        contour_downsample=int(st.session_state["region_contour_ds"]),
                        save_outputs=True,
                    )
                    adjusted_result["boundary_display_names"] = dict(adjusted_display_name_map)
                    adjusted_result["edited_boundary_types"] = sorted(edited_boundary_types)
                    region_result["adjusted_result"] = adjusted_result
                    st.session_state["region_result"] = region_result
                    st.session_state["pending_region_adjust_target_type"] = saved_target_type
                    st.session_state["pending_region_adjust_display_types"] = list(dict.fromkeys(adjusted_selected_types))
                    st.session_state["region_adjust_canvas_version"] = int(st.session_state.get("region_adjust_canvas_version", 0)) + 1
                    st.session_state["region_adjust_preview_mask"] = None
                    st.session_state["region_adjust_preview_context"] = None
                    st.session_state["region_integrated_result"] = None
                    st.session_state["cell_distribution_region_masks_result"] = None
                    st.session_state["cell_distribution_density_result"] = None
                    st.session_state["cell_distribution_cluster_result"] = None
                    invalidate_output_zip_cache()
                    refresh_output_zip_state(config.save_dir)
                    st.success(f"Adjusted region result saved to {get_section_output_dir(config, 'region_analysis')}")
                except Exception as exc:
                    st.error(str(exc))

        adjusted_result = region_result.get("adjusted_result")
        if isinstance(adjusted_result, dict):
            st.markdown("#### Original vs adjusted region results")
            cmp_cols = st.columns(2)
            with cmp_cols[0]:
                st.markdown("**Original computed regions**")
                original_cmp_fig = make_region_overlay_figure(
                    celltype_mask=assignment_result["celltype_mask"],
                    celltype_cfg=st.session_state["celltype_cfg"],
                    masks=region_result["masks"],
                    selected_types=active_adjust_types,
                    display_celltypes=active_adjust_celltypes,
                    pixel_size_um=config.pixel_size_um,
                    title="Original regions",
                    line_width=float(st.session_state["region_line_width"]),
                    line_style=str(st.session_state["region_line_style"]),
                    boundary_color=str(st.session_state["region_boundary_color"]),
                    use_type_colors=bool(st.session_state["region_use_type_colors"]),
                    contour_downsample=int(st.session_state["region_contour_ds"]),
                )
                try:
                    render_zoomable_figure(fig=original_cmp_fig, component_key="region_original_compare", saved_paths=None, component_height=760)
                finally:
                    _close_figure_obj(original_cmp_fig)
            with cmp_cols[1]:
                st.markdown("**Adjusted regions**")
                adjusted_cmp_fig = make_region_overlay_figure(
                    celltype_mask=assignment_result["celltype_mask"],
                    celltype_cfg=st.session_state["celltype_cfg"],
                    masks=adjusted_result["masks"],
                    selected_types=active_adjust_types,
                    display_celltypes=active_adjust_celltypes,
                    pixel_size_um=config.pixel_size_um,
                    title="Adjusted regions",
                    line_width=float(st.session_state["region_line_width"]),
                    line_style=str(st.session_state["region_line_style"]),
                    boundary_color=str(st.session_state["region_boundary_color"]),
                    use_type_colors=bool(st.session_state["region_use_type_colors"]),
                    contour_downsample=int(st.session_state["region_contour_ds"]),
                )
                try:
                    render_zoomable_figure(fig=adjusted_cmp_fig, component_key="region_adjusted_compare", saved_paths=None, component_height=760)
                finally:
                    _close_figure_obj(adjusted_cmp_fig)
            st.markdown("#### Adjusted-region area summary")
            st.dataframe(adjusted_result.get("area_summary", pd.DataFrame()), use_container_width=True)


def _render_manual_roi_selection_ui(
    *,
    config: PipelineConfig,
    data_result: Dict[str, Any],
    assignment_result: Dict[str, Any],
    celltype_names: Sequence[str],
) -> None:
    st.caption(
        "Draw one or more enclosed ROIs. The region must be closed or use the field edge as part of the enclosure. "
        "You can draw on either panel, and the ROI preview will be mirrored onto the other panel for comparison. "
        "When you save, the currently selected ROI parameters (Close, Dilate, Min area, Min cells) are also applied to the drawn ROI mask(s)."
    )

    if st_canvas is None:
        st.warning("Manual ROI selection is unavailable because the drawing component could not be loaded in this Streamlit environment. Please reinstall the canvas package from requirements.txt and restart Streamlit.")
        if DRAWABLE_CANVAS_ERROR is not None:
            st.caption(str(DRAWABLE_CANVAS_ERROR))
        return

    overlay_rgb = _build_overlay_rgb_for_region_ui(config, data_result)
    roi_display_default = [name for name in st.session_state.get("region_roi_display_types", celltype_names) if name in celltype_names] or list(celltype_names)

    roi_ctrl_cols = st.columns([2.0, 1.4, 1.2, 1.2, 3.0])
    with roi_ctrl_cols[0]:
        roi_display_types = st.multiselect(
            "Cell types to display with ROIs",
            options=list(celltype_names),
            default=roi_display_default,
            key="region_roi_display_types",
        )
    roi_display_types = [name for name in roi_display_types if name in celltype_names] or list(celltype_names)
    with roi_ctrl_cols[1]:
        st.radio(
            "Draw ROIs on",
            options=["Overlay preview", "Cell type mask"],
            key="region_roi_active_panel",
            horizontal=False,
        )
    with roi_ctrl_cols[2]:
        st.selectbox("ROI drawing tool", options=["polygon", "rect", "freedraw"], key="region_roi_tool")
    with roi_ctrl_cols[3]:
        st.slider("ROI stroke width", min_value=2, max_value=18, value=4, step=1, key="region_roi_stroke_width")
    with roi_ctrl_cols[4]:
        st.caption(
            "Multiple ROIs are allowed. Saving writes a separate ROI figure, ROI mask TIFF, counts table, assignments table, and ROI area summary CSV."
        )

    st.text_input(
        "Names for manually selected ROIs (comma-separated; optional)",
        key="region_roi_custom_names",
        help="Example: Tumor core, Invasive edge, Distal stroma. If fewer names are provided than detected ROIs, the remaining ROIs keep default names.",
    )

    celltype_rgb = make_celltype_mask_rgb(
        assignment_result["celltype_mask"],
        st.session_state["celltype_cfg"],
        selected_types=roi_display_types,
    )
    rh, rw = celltype_rgb.shape[:2]
    roi_canvas_w, roi_canvas_h = _fit_canvas_size(rh, rw, max_width=560, max_height=560)
    overlay_bg_pil = _prepare_canvas_background_image(_rgb_float_to_pil(overlay_rgb, width=roi_canvas_w, height=roi_canvas_h))
    overlay_bg_u8 = np.array(overlay_bg_pil)
    celltype_bg_pil = _prepare_canvas_background_image(_rgb_float_to_pil(celltype_rgb, width=roi_canvas_w, height=roi_canvas_h))
    celltype_bg_u8 = np.array(celltype_bg_pil)

    active_panel = st.session_state.get("region_roi_active_panel", "Overlay preview")
    roi_color = "#ffcc00"
    roi_preview_mask = st.session_state.get("region_roi_preview_mask")
    if roi_preview_mask is None:
        roi_preview_mask = np.zeros((rh, rw), dtype=bool)

    df_cells_for_roi_count = assignment_result["df_cells"]
    if roi_display_types:
        df_cells_for_roi_count = df_cells_for_roi_count[
            df_cells_for_roi_count["celltype"].astype(str).isin([str(name) for name in roi_display_types])
        ].copy()

    roi_cols = st.columns(2, gap="large")
    if active_panel == "Overlay preview":
        with roi_cols[0]:
            st.markdown("**Draw ROIs on overlay preview**")
            try:
                canvas_result = _run_st_canvas_safe(
                    fill_color=roi_color + "33",
                    stroke_width=int(st.session_state.get("region_roi_stroke_width", 4)),
                    stroke_color=roi_color,
                    background_image=overlay_bg_pil,
                    update_streamlit=True,
                    height=roi_canvas_h,
                    width=roi_canvas_w,
                    drawing_mode=str(st.session_state.get("region_roi_tool", "polygon")),
                    key=f"region_roi_canvas_v{int(st.session_state.get('region_roi_canvas_version', 0))}_overlay",
                    display_toolbar=True,
                )
                current_roi_mask_raw = _extract_draw_mask_from_canvas(canvas_result, overlay_bg_u8, (rh, rw))
                current_roi_mask = _postprocess_manual_roi_binary_mask(current_roi_mask_raw, df_cells_for_roi_count, config.pixel_size_um)
                st.session_state["region_roi_preview_mask"] = current_roi_mask
            except Exception as exc:
                st.warning(f"Could not load the overlay ROI drawing canvas: {exc}")
                current_roi_mask = st.session_state.get("region_roi_preview_mask")
                if current_roi_mask is None:
                    current_roi_mask = np.zeros((rh, rw), dtype=bool)
        with roi_cols[1]:
            st.markdown("**Cell-type mask ROI mirror**")
            mirrored = _overlay_mask_on_rgb(celltype_rgb, current_roi_mask, color_hex=roi_color, alpha=0.45)
            mirrored_pil = _rgb_float_to_pil(mirrored, width=roi_canvas_w, height=roi_canvas_h)
            st.image(mirrored_pil, caption="Mirrored ROI preview after ROI-parameter post-processing", use_container_width=False)
    else:
        with roi_cols[0]:
            st.markdown("**Overlay preview ROI mirror**")
            mirrored = _overlay_mask_on_rgb(overlay_rgb, roi_preview_mask, color_hex=roi_color, alpha=0.45)
            mirrored_pil = _rgb_float_to_pil(mirrored, width=roi_canvas_w, height=roi_canvas_h)
            st.image(mirrored_pil, caption="Mirrored ROI preview", use_container_width=False)
        with roi_cols[1]:
            st.markdown("**Draw ROIs on cell type mask**")
            try:
                canvas_result = _run_st_canvas_safe(
                    fill_color=roi_color + "33",
                    stroke_width=int(st.session_state.get("region_roi_stroke_width", 4)),
                    stroke_color=roi_color,
                    background_image=celltype_bg_pil,
                    update_streamlit=True,
                    height=roi_canvas_h,
                    width=roi_canvas_w,
                    drawing_mode=str(st.session_state.get("region_roi_tool", "polygon")),
                    key=f"region_roi_canvas_v{int(st.session_state.get('region_roi_canvas_version', 0))}_celltype",
                    display_toolbar=True,
                )
                current_roi_mask_raw = _extract_draw_mask_from_canvas(canvas_result, celltype_bg_u8, (rh, rw))
                current_roi_mask = _postprocess_manual_roi_binary_mask(current_roi_mask_raw, df_cells_for_roi_count, config.pixel_size_um)
                st.session_state["region_roi_preview_mask"] = current_roi_mask
            except Exception as exc:
                st.warning(f"Could not load the cell type mask ROI drawing canvas: {exc}")

    preview_mask = st.session_state.get("region_roi_preview_mask")
    n_preview_rois = 0
    if preview_mask is not None and np.any(preview_mask):
        n_preview_rois = int(_label_rois_from_binary_mask(preview_mask).max())
    st.caption(f"Current ROI preview contains {n_preview_rois} ROI(s). If you provided custom names, they will be applied in this ROI order when you save.")

    roi_btn_cols = st.columns([1.0, 1.0, 4.0])
    with roi_btn_cols[0]:
        save_roi_clicked = st.button("Save ROI result", key="save_roi_result_btn")
    with roi_btn_cols[1]:
        clear_roi_clicked = st.button("Clear ROI drawing", key="clear_roi_result_btn")
    with roi_btn_cols[2]:
        st.caption(
            "The saved ROI result is independent from both the original computational ROIs and any adjusted-ROI result. "
            "The current ROI parameters are applied before saving. The names above are used later in Distance analysis."
        )

    if clear_roi_clicked:
        st.session_state["region_roi_canvas_version"] = int(st.session_state.get("region_roi_canvas_version", 0)) + 1
        st.session_state["region_roi_preview_mask"] = None
        st.rerun()

    if save_roi_clicked:
        try:
            binary_roi_mask = st.session_state.get("region_roi_preview_mask")
            if binary_roi_mask is None or not np.any(binary_roi_mask):
                raise RuntimeError("Draw at least one enclosed ROI before saving the ROI result.")
            roi_label_mask = _label_rois_from_binary_mask(binary_roi_mask)
            if int(roi_label_mask.max()) <= 0:
                raise RuntimeError("No valid ROI regions were detected from the current drawing after ROI-parameter post-processing.")
            roi_custom_names = [token.strip() for token in re.split(r"[;,\n]+", str(st.session_state.get("region_roi_custom_names") or "")) if token.strip()]
            roi_result = save_manual_roi_analysis(
                df_cells=assignment_result["df_cells"],
                celltype_mask=assignment_result["celltype_mask"],
                celltype_cfg=st.session_state["celltype_cfg"],
                overlay_rgb=overlay_rgb,
                save_dir=get_section_output_dir(config, 'region_analysis'),
                pixel_size_um=config.pixel_size_um,
                roi_label_mask=roi_label_mask,
                selected_types=roi_display_types,
                roi_source_panel=str(active_panel),
                roi_custom_names=roi_custom_names,
                save_outputs=True,
            )
            _close_result_figures(st.session_state.get("region_manual_roi_result"))
            st.session_state["region_manual_roi_result"] = roi_result
            st.session_state["region_roi_canvas_version"] = int(st.session_state.get("region_roi_canvas_version", 0)) + 1
            st.session_state["region_roi_preview_mask"] = None
            st.session_state["region_integrated_result"] = None
            st.session_state["cell_distribution_region_masks_result"] = None
            st.session_state["cell_distribution_density_result"] = None
            st.session_state["cell_distribution_cluster_result"] = None
            invalidate_output_zip_cache()
            refresh_output_zip_state(config.save_dir)
            st.success(f"ROI result saved to {get_section_output_dir(config, 'region_analysis')}")
        except Exception as exc:
            st.error(str(exc))

    roi_result = st.session_state.get("region_manual_roi_result")
    if isinstance(roi_result, dict):
        st.markdown("#### Manual ROI results")
        render_zoomable_figure(
            fig=roi_result.get("figure"),
            component_key="region_roi_result",
            saved_paths=[
                roi_result.get("saved_paths", {}).get("comparison_png"),
                roi_result.get("saved_paths", {}).get("comparison_svg"),
            ],
            component_height=900,
        )
        roi_info_cols = st.columns(2)
        with roi_info_cols[0]:
            st.markdown("#### celltype_counts_by_roi preview")
            st.dataframe(roi_result.get("counts_by_roi", pd.DataFrame()), use_container_width=True)
        with roi_info_cols[1]:
            st.markdown("#### roi_area_summary preview")
            st.dataframe(roi_result.get("area_summary", pd.DataFrame()), use_container_width=True)

def _collect_integrable_region_mask_records(region_result: Any) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []

    if isinstance(region_result, dict):
        original_masks = region_result.get("masks") or {}
        for mask_name, mask in original_masks.items():
            mask_bool = np.asarray(mask, dtype=bool)
            if np.any(mask_bool):
                records.append(
                    {
                        "label": f"Computed | {mask_name}",
                        "mask": mask_bool,
                        "source": "computed",
                        "mask_name": str(mask_name),
                        "original_label": f"Computed | {mask_name}",
                        "original_mask": mask_bool,
                    }
                )

        adjusted_result = region_result.get("adjusted_result")
        if isinstance(adjusted_result, dict):
            adjusted_display_names = adjusted_result.get("boundary_display_names") or {}
            if not isinstance(adjusted_display_names, dict):
                adjusted_display_names = {}
            adjusted_masks = adjusted_result.get("masks") or {}
            edited_mask_names = [
                str(name)
                for name in adjusted_result.get("edited_boundary_types", [])
                if str(name) in adjusted_masks
            ]
            if not edited_mask_names:
                for mask_name, mask in adjusted_masks.items():
                    original_mask = original_masks.get(mask_name)
                    display_name = str(adjusted_display_names.get(mask_name, mask_name)).strip() or str(mask_name)
                    if original_mask is None:
                        edited_mask_names.append(str(mask_name))
                        continue
                    if display_name != str(mask_name):
                        edited_mask_names.append(str(mask_name))
                        continue
                    if not np.array_equal(np.asarray(mask, dtype=bool), np.asarray(original_mask, dtype=bool)):
                        edited_mask_names.append(str(mask_name))
            for mask_name in edited_mask_names:
                mask = adjusted_masks.get(mask_name)
                if mask is None:
                    continue
                mask_bool = np.asarray(mask, dtype=bool)
                if not np.any(mask_bool):
                    continue
                display_name = str(adjusted_display_names.get(mask_name, mask_name)).strip() or str(mask_name)
                original_mask = original_masks.get(mask_name)
                original_mask_bool = np.asarray(original_mask, dtype=bool) if original_mask is not None else None
                if original_mask_bool is not None and not np.any(original_mask_bool):
                    original_mask_bool = None
                records.append(
                    {
                        "label": f"Adjusted | {display_name}",
                        "mask": mask_bool,
                        "source": "adjusted",
                        "mask_name": str(mask_name),
                        "original_label": f"Computed | {mask_name}" if original_mask_bool is not None else None,
                        "original_mask": original_mask_bool,
                    }
                )

    return records


def _collect_integrable_region_masks(region_result: Any) -> Dict[str, np.ndarray]:
    return {
        str(record["label"]): np.asarray(record["mask"], dtype=bool)
        for record in _collect_integrable_region_mask_records(region_result)
    }


def _region_display_basename(selected_mask_labels: Sequence[str], selected_celltypes: Sequence[str], workflow_key: str) -> str:
    payload = {
        "workflow": str(workflow_key),
        "masks": list(selected_mask_labels),
        "celltypes": list(selected_celltypes),
    }
    short_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"{workflow_key}__{short_hash}"


def _customized_region_output_dirs(config: PipelineConfig) -> Dict[str, Path]:
    root_dir = get_section_output_dir(config, 'integrated_region_analysis')
    original_dir = root_dir / "01_original_unmodified"
    customized_dir = root_dir / "02_customized_display"
    original_dir.mkdir(parents=True, exist_ok=True)
    customized_dir.mkdir(parents=True, exist_ok=True)
    return {
        "root": root_dir,
        "original": original_dir,
        "customized": customized_dir,
    }


def _save_region_display_artifacts(
    *,
    save_dir: Path,
    figure,
    workflow_key: str,
    requested_mask_labels: Sequence[str],
    exported_mask_labels: Sequence[str],
    selected_celltypes: Sequence[str],
    title: str,
    metadata_extra: Dict[str, Any] | None = None,
) -> Dict[str, Path]:
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    base_name = _region_display_basename(exported_mask_labels, selected_celltypes, workflow_key)
    svg_path = save_dir / f"{base_name}.svg"
    png_path = save_dir / f"{base_name}.png"
    tiff_path = save_dir / f"{base_name}.tiff"
    meta_path = save_dir / f"{base_name}.json"

    figure.savefig(svg_path, dpi=600, bbox_inches="tight", pad_inches=0)
    figure.savefig(png_path, dpi=300, bbox_inches="tight", pad_inches=0)
    figure.savefig(tiff_path, dpi=600, bbox_inches="tight", pad_inches=0)

    payload: Dict[str, Any] = {
        "workflow": str(workflow_key),
        "title": str(title),
        "requested_masks": list(requested_mask_labels),
        "exported_masks": list(exported_mask_labels),
        "selected_celltypes": list(selected_celltypes),
    }
    if metadata_extra:
        payload.update(metadata_extra)
    write_json(meta_path, payload)
    return {
        "svg": svg_path,
        "png": png_path,
        "tiff": tiff_path,
        "json": meta_path,
    }


def _render_integrated_region_selection_ui(
    *,
    config: PipelineConfig,
    data_result: Dict[str, Any],
    assignment_result: Dict[str, Any],
    region_result: Dict[str, Any] | None,
    celltype_names: Sequence[str],
) -> None:
    mask_records = _collect_integrable_region_mask_records(region_result)
    if not mask_records:
        st.info("No computational ROIs or adjusted ROIs are available yet. Save at least one of those results first.")
        return
    label_to_record = {str(record["label"]): record for record in mask_records}

    available_mask_names = list(label_to_record.keys())
    default_mask_names = [
        name
        for name in st.session_state.get("region_integration_selected_masks", available_mask_names[: min(3, len(available_mask_names))])
        if name in available_mask_names
    ] or available_mask_names[: min(3, len(available_mask_names))]
    default_celltypes = [
        name
        for name in st.session_state.get("region_integration_selected_celltypes", list(celltype_names))
        if name in celltype_names
    ] or list(celltype_names)

    ctrl_cols = st.columns([2.4, 2.0, 3.6])
    with ctrl_cols[0]:
        selected_mask_labels = st.multiselect(
            "Boundaries to include",
            options=available_mask_names,
            default=default_mask_names,
            key="region_integration_selected_masks",
        )
    with ctrl_cols[1]:
        selected_celltypes = st.multiselect(
            "Cell types to show",
            options=list(celltype_names),
            default=default_celltypes,
            key="region_integration_selected_celltypes",
        )
    with ctrl_cols[2]:
        st.caption(
            "Preview and save a two-panel export using the selected computed and/or adjusted boundaries. "
            "When you save a customized display, the app also writes an original unmodified export automatically into a separate output folder."
        )

    preview_figure = None
    selected_records = [label_to_record[name] for name in selected_mask_labels if name in label_to_record]
    selected_masks = {
        str(record["label"]): np.asarray(record["mask"], dtype=bool)
        for record in selected_records
    }
    overlay_rgb = None
    if selected_mask_labels and selected_celltypes and selected_masks:
        from src.spatialscope_analysis.region_analysis import make_roi_comparison_figure

        overlay_rgb = _build_overlay_rgb_for_region_ui(config, data_result)
        preview_figure = make_roi_comparison_figure(
            overlay_rgb=overlay_rgb,
            celltype_mask=assignment_result["celltype_mask"],
            celltype_cfg=st.session_state["celltype_cfg"],
            roi_masks=selected_masks,
            selected_types=selected_celltypes,
            pixel_size_um=config.pixel_size_um,
            title="Customized display preview",
        )
        st.markdown("#### Preview")
        render_zoomable_figure(
            fig=preview_figure,
            component_key="region_integrated_live_preview",
            saved_paths=None,
            component_height=900,
        )
    else:
        st.info("Select at least one boundary and one cell type to preview the customized display.")

    save_clicked = st.button("Save customized display", key="create_integrated_region_view_btn")
    try:
        if save_clicked:
            if not selected_mask_labels:
                raise RuntimeError("Select at least one boundary to save.")
            if not selected_celltypes:
                raise RuntimeError("Select at least one cell type to display on the masked plot.")
            if preview_figure is None:
                raise RuntimeError("Preview could not be created for the current selections.")
            if overlay_rgb is None:
                overlay_rgb = _build_overlay_rgb_for_region_ui(config, data_result)

            output_dirs = _customized_region_output_dirs(config)
            customized_saved_paths = _save_region_display_artifacts(
                save_dir=output_dirs["customized"],
                figure=preview_figure,
                workflow_key="customized_display",
                requested_mask_labels=selected_mask_labels,
                exported_mask_labels=list(selected_masks.keys()),
                selected_celltypes=selected_celltypes,
                title="Customized display",
                metadata_extra={
                    "export_folder_type": "customized_display",
                },
            )

            original_masks: Dict[str, np.ndarray] = {}
            original_skipped_labels: List[str] = []
            for record in selected_records:
                original_label = record.get("original_label")
                original_mask = record.get("original_mask")
                if original_label is None or original_mask is None or not np.any(np.asarray(original_mask, dtype=bool)):
                    original_skipped_labels.append(str(record["label"]))
                    continue
                if str(original_label) not in original_masks:
                    original_masks[str(original_label)] = np.asarray(original_mask, dtype=bool)

            if not original_masks and isinstance(region_result, dict):
                for mask_name, mask in (region_result.get("masks") or {}).items():
                    mask_bool = np.asarray(mask, dtype=bool)
                    if np.any(mask_bool):
                        original_masks[f"Computed | {mask_name}"] = mask_bool

            original_saved_paths = None
            original_exported_labels = list(original_masks.keys())
            if original_masks:
                from src.spatialscope_analysis.region_analysis import make_roi_comparison_figure

                original_figure = make_roi_comparison_figure(
                    overlay_rgb=overlay_rgb,
                    celltype_mask=assignment_result["celltype_mask"],
                    celltype_cfg=st.session_state["celltype_cfg"],
                    roi_masks=original_masks,
                    selected_types=selected_celltypes,
                    pixel_size_um=config.pixel_size_um,
                    title="Original unmodified display",
                )
                try:
                    original_saved_paths = _save_region_display_artifacts(
                        save_dir=output_dirs["original"],
                        figure=original_figure,
                        workflow_key="original_unmodified",
                        requested_mask_labels=selected_mask_labels,
                        exported_mask_labels=original_exported_labels,
                        selected_celltypes=selected_celltypes,
                        title="Original unmodified display",
                        metadata_extra={
                            "export_folder_type": "original_unmodified",
                            "customized_request_masks": list(selected_mask_labels),
                            "skipped_requested_masks_without_original_counterpart": original_skipped_labels,
                        },
                    )
                finally:
                    _close_figure_obj(original_figure)

            st.session_state["region_integrated_result"] = {
                "selected_masks": list(selected_mask_labels),
                "selected_celltypes": list(selected_celltypes),
                "saved_paths": customized_saved_paths,
                "original_saved_paths": original_saved_paths,
                "original_exported_masks": original_exported_labels,
                "original_skipped_masks": original_skipped_labels,
            }
            invalidate_output_zip_cache()
            refresh_output_zip_state(config.save_dir)
            if original_saved_paths is not None:
                st.success(
                    "Customized display saved to "
                    f"{output_dirs['customized']}. Original unmodified export was also saved to {output_dirs['original']}."
                )
            else:
                st.success(f"Customized display saved to {output_dirs['customized']}.")
    except Exception as exc:
        st.error(str(exc))
    finally:
        if preview_figure is not None:
            _close_figure_obj(preview_figure)

    integrated_result = st.session_state.get("region_integrated_result")
    if isinstance(integrated_result, dict):
        info_cols = st.columns(2)
        with info_cols[0]:
            st.markdown("**Saved customized boundaries**")
            st.write("\n".join([f"- {name}" for name in integrated_result.get("selected_masks", [])]))
        with info_cols[1]:
            st.markdown("**Displayed cell types**")
            st.write("\n".join([f"- {name}" for name in integrated_result.get("selected_celltypes", [])]))
        original_exported_masks = integrated_result.get("original_exported_masks", [])
        if original_exported_masks:
            st.markdown("**Original unmodified export includes**")
            st.write("\n".join([f"- {name}" for name in original_exported_masks]))


CELL_DISTRIBUTION_BG_BLEND = 0.45
CELL_DISTRIBUTION_BAND_ALPHA = 0.48
CELL_DISTRIBUTION_CHANNEL_ALPHA = 0.72
CELL_DISTRIBUTION_CONTOUR_LINEWIDTH = 1.0
CELL_DISTRIBUTION_CONTOUR_ALPHA = 0.95
CELL_DISTRIBUTION_INSIDE_CMAP = "viridis"
CELL_DISTRIBUTION_OUTSIDE_CMAP = "magma"
CELL_DISTRIBUTION_DISPLAY_ORIGIN = "upper"
CELL_DISTRIBUTION_STRUCTURE = ndi.generate_binary_structure(2, 2)


def _cell_distribution_output_dirs(config: PipelineConfig) -> Dict[str, Path]:
    root_dir = get_section_output_dir(config, "cell_distribution_analysis")
    region_masks_dir = root_dir / "01_region_masks"
    density_dir = root_dir / "02_cell_density"
    cluster_dir = root_dir / "03_cell_cluster_distribution"
    region_masks_dir.mkdir(parents=True, exist_ok=True)
    density_dir.mkdir(parents=True, exist_ok=True)
    cluster_dir.mkdir(parents=True, exist_ok=True)
    return {
        "root": root_dir,
        "region_masks": region_masks_dir,
        "cell_density": density_dir,
        "cell_cluster_distribution": cluster_dir,
    }


def _normalize_output_base(path: Path) -> Path:
    path = Path(path)
    if path.suffix.lower() in {".png", ".svg", ".tiff"}:
        return path.with_suffix("")
    return path


def _save_figure_both_formats(fig, path: Path, dpi: int = 300, bbox_inches: str = "tight", pad_inches: float = 0.0, facecolor: str = "white") -> tuple[Path, Path]:
    base = _normalize_output_base(path)
    base.parent.mkdir(parents=True, exist_ok=True)
    png_path = base.with_suffix(".png")
    svg_path = base.with_suffix(".svg")
    fig.savefig(str(png_path), dpi=dpi, bbox_inches=bbox_inches, pad_inches=pad_inches, facecolor=facecolor)
    fig.savefig(str(svg_path), dpi=dpi, bbox_inches=bbox_inches, pad_inches=pad_inches, facecolor=facecolor)
    return png_path, svg_path


def _cell_distribution_band_token(band_width_um: float) -> str:
    return _format_float(float(band_width_um)).replace(".", "p")


def _boundary_seed_mask(boundary_mask: np.ndarray) -> np.ndarray:
    from skimage import segmentation

    boundary_mask = np.asarray(boundary_mask, dtype=bool)
    if not np.any(boundary_mask):
        return np.zeros_like(boundary_mask, dtype=bool)
    seed_mask = segmentation.find_boundaries(boundary_mask, mode="thick")
    if np.any(seed_mask):
        return seed_mask.astype(bool)
    fallback = boundary_mask ^ ndi.binary_erosion(boundary_mask, structure=CELL_DISTRIBUTION_STRUCTURE)
    return fallback.astype(bool)


def _distance_bands_from_boundary(boundary_mask: np.ndarray, seed_mask: np.ndarray, band_width_um: float, pixel_size_um: tuple[float, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    boundary_mask = np.asarray(boundary_mask, dtype=bool)
    seed_mask = np.asarray(seed_mask, dtype=bool)
    if not np.any(boundary_mask):
        raise RuntimeError("The selected boundary mask is empty.")
    if not np.any(seed_mask):
        raise RuntimeError("The selected boundary mask does not contain a valid drawable edge.")
    if float(band_width_um) <= 0:
        raise RuntimeError("Band width must be > 0 µm.")

    distance_um = ndi.distance_transform_edt(~seed_mask, sampling=(float(pixel_size_um[1]), float(pixel_size_um[0]))).astype(np.float32, copy=False)
    inside_distance_um = distance_um.copy()
    inside_distance_um[~boundary_mask] = np.nan
    outside_distance_um = distance_um.copy()
    outside_distance_um[boundary_mask] = np.nan

    inside_band_index = np.full(boundary_mask.shape, -1, dtype=np.int32)
    inside_band_index[boundary_mask] = np.floor(np.clip(inside_distance_um[boundary_mask], 0, None) / float(band_width_um)).astype(np.int32)

    outside_mask = ~boundary_mask
    outside_band_index = np.full(boundary_mask.shape, -1, dtype=np.int32)
    outside_band_index[outside_mask] = np.floor(np.clip(outside_distance_um[outside_mask], 0, None) / float(band_width_um)).astype(np.int32)
    return inside_distance_um, outside_distance_um, inside_band_index, outside_band_index


def _band_summary(side_name: str, band_index: np.ndarray, band_width_um: float, pixel_area_um2: float) -> pd.DataFrame:
    valid = np.asarray(band_index) >= 0
    if not np.any(valid):
        return pd.DataFrame(columns=["side_region", "band_index", "dist_lo_um", "dist_hi_um", "area_px", "area_um2"])

    rows: List[Dict[str, Any]] = []
    for idx in range(int(np.nanmax(np.asarray(band_index)[valid])) + 1):
        mask = np.asarray(band_index) == idx
        if not np.any(mask):
            continue
        area_px = int(np.count_nonzero(mask))
        rows.append(
            {
                "side_region": str(side_name),
                "band_index": int(idx),
                "dist_lo_um": float(idx * float(band_width_um)),
                "dist_hi_um": float((idx + 1) * float(band_width_um)),
                "area_px": area_px,
                "area_um2": float(area_px * pixel_area_um2),
            }
        )
    return pd.DataFrame(rows)


def _band_rgba(band_index: np.ndarray, cmap_name: str, alpha: float = CELL_DISTRIBUTION_BAND_ALPHA) -> np.ndarray:
    rgba = np.zeros(np.asarray(band_index).shape + (4,), dtype=float)
    valid = np.asarray(band_index) >= 0
    if not np.any(valid):
        return rgba
    max_band = int(np.nanmax(np.asarray(band_index)[valid]))
    cmap = plt.get_cmap(cmap_name, max(2, max_band + 1))
    denom = max(1, max_band)
    colors = cmap(np.asarray(band_index)[valid] / denom)
    rgba[valid, :3] = colors[:, :3]
    rgba[valid, 3] = float(alpha)
    return rgba


def _light_background_rgb(rgb_image: np.ndarray, blend: float = CELL_DISTRIBUTION_BG_BLEND) -> np.ndarray:
    rgb = np.clip(np.asarray(rgb_image, dtype=float), 0.0, 1.0)
    return np.clip(float(blend) * rgb + (1.0 - float(blend)), 0.0, 1.0)


def _masked_distance(distance_um: np.ndarray, mask: np.ndarray) -> np.ma.MaskedArray:
    return np.ma.array(distance_um, mask=(~np.asarray(mask, dtype=bool)) | ~np.isfinite(distance_um))


def _draw_band_contours(ax, distance_um: np.ndarray, mask: np.ndarray, cmap_name: str, band_width_um: float) -> None:
    valid = np.asarray(mask, dtype=bool) & np.isfinite(distance_um)
    if not np.any(valid):
        return
    max_dist = float(np.nanmax(distance_um[valid]))
    levels = np.arange(float(band_width_um), max_dist + 1e-6, float(band_width_um))
    if levels.size == 0:
        return
    cmap = plt.get_cmap(cmap_name, max(2, len(levels)))
    line_colors = [cmap(i / max(1, len(levels) - 1)) for i in range(len(levels))]
    ax.contour(
        _masked_distance(distance_um, np.asarray(mask, dtype=bool)),
        levels=levels,
        colors=line_colors,
        linewidths=CELL_DISTRIBUTION_CONTOUR_LINEWIDTH,
        alpha=CELL_DISTRIBUTION_CONTOUR_ALPHA,
        origin=CELL_DISTRIBUTION_DISPLAY_ORIGIN,
    )


def _draw_boundary_interface(ax, seed_mask: np.ndarray, color: str = "white", linewidth: float = 1.1) -> None:
    if not np.any(seed_mask):
        return
    ax.contour(
        np.asarray(seed_mask, dtype=float),
        levels=[0.5],
        colors=[color],
        linewidths=linewidth,
        alpha=1.0,
        origin=CELL_DISTRIBUTION_DISPLAY_ORIGIN,
    )


def _distribution_figure_size(image_shape: Sequence[int]) -> tuple[float, float]:
    h, w = map(int, image_shape[:2])
    width = min(11.5, max(7.0, w / 170.0))
    height = width * h / max(w, 1)
    return width, height


def _channel_color_hex_from_config(config: PipelineConfig, channel_name: str) -> str:
    for channel in config.channels:
        if str(channel.channel) == str(channel_name):
            return str(channel.color_hex)
    return "#ffffff"


def _channel_image_from_data(data_result: Dict[str, Any], config: PipelineConfig, channel_name: str) -> np.ndarray:
    key = (str(config.image_id), str(channel_name))
    shapes = data_result.get("shapes", {})
    if key not in shapes:
        available = [str(item.channel) for item in config.channels]
        raise RuntimeError(f"Channel {channel_name!r} was not found. Available channels: {available}")
    h, w = shapes[key]
    df_pixels = data_result["df_pixels"]
    sub = df_pixels[(df_pixels["image_id"].astype(str) == str(config.image_id)) & (df_pixels["channel"].astype(str) == str(channel_name))]
    if len(sub) != int(h) * int(w):
        raise RuntimeError(f"Channel {channel_name!r} has an unexpected number of pixels.")
    return sub["value"].to_numpy().reshape(h, w)


def _norm_clip_local(arr: np.ndarray, lo_percentile: float = 0.0, hi_percentile: float = 99.8) -> np.ndarray:
    arr = np.asarray(arr, dtype=float)
    lo_v = float(np.nanpercentile(arr, lo_percentile))
    hi_v = float(np.nanpercentile(arr, hi_percentile))
    if not np.isfinite(lo_v) or not np.isfinite(hi_v) or hi_v <= lo_v:
        return np.zeros_like(arr, dtype=float)
    return np.clip((arr - lo_v) / (hi_v - lo_v), 0.0, 1.0)


def _channel_rgba_for_distribution(data_result: Dict[str, Any], config: PipelineConfig, channel_name: str) -> np.ndarray:
    img = _channel_image_from_data(data_result, config, channel_name)
    intensity = _norm_clip_local(img, hi_percentile=99.8)
    rgb = np.array(ImageColor.getrgb(_channel_color_hex_from_config(config, channel_name)), dtype=float) / 255.0
    rgba = np.zeros(intensity.shape + (4,), dtype=float)
    rgba[..., :3] = rgb
    rgba[..., 3] = CELL_DISTRIBUTION_CHANNEL_ALPHA * intensity
    return rgba


def _render_region_band_map(
    *,
    config: PipelineConfig,
    base_rgb: np.ndarray,
    boundary_label: str,
    band_width_um: float,
    inside_name: str,
    outside_name: str,
    inside_mask: np.ndarray,
    outside_mask: np.ndarray,
    inside_distance_um: np.ndarray,
    outside_distance_um: np.ndarray,
    inside_rgba: np.ndarray,
    outside_rgba: np.ndarray,
    seed_mask: np.ndarray,
    extra_channel_name: str | None = None,
    extra_channel_rgba: np.ndarray | None = None,
) -> Any:
    display_base_rgb = np.asarray(base_rgb)
    display_inside_rgba = np.asarray(inside_rgba)
    display_outside_rgba = np.asarray(outside_rgba)
    display_inside_mask = np.flipud(np.asarray(inside_mask, dtype=bool))
    display_outside_mask = np.flipud(np.asarray(outside_mask, dtype=bool))
    display_inside_distance_um = np.flipud(np.asarray(inside_distance_um))
    display_outside_distance_um = np.flipud(np.asarray(outside_distance_um))
    display_seed_mask = np.flipud(np.asarray(seed_mask, dtype=bool))

    fig, ax = plt.subplots(figsize=_distribution_figure_size(base_rgb.shape), facecolor="white")
    ax.set_facecolor("white")
    ax.imshow(np.clip(display_base_rgb, 0.0, 1.0), origin=CELL_DISTRIBUTION_DISPLAY_ORIGIN)
    ax.imshow(np.clip(display_outside_rgba, 0.0, 1.0), origin=CELL_DISTRIBUTION_DISPLAY_ORIGIN)
    ax.imshow(np.clip(display_inside_rgba, 0.0, 1.0), origin=CELL_DISTRIBUTION_DISPLAY_ORIGIN)
    _draw_band_contours(ax, display_inside_distance_um, display_inside_mask, CELL_DISTRIBUTION_INSIDE_CMAP, band_width_um)
    _draw_band_contours(ax, display_outside_distance_um, display_outside_mask, CELL_DISTRIBUTION_OUTSIDE_CMAP, band_width_um)
    _draw_boundary_interface(ax, display_seed_mask, color="#ffffff")

    handles = [
        Patch(facecolor=plt.get_cmap(CELL_DISTRIBUTION_INSIDE_CMAP)(0.75), edgecolor="none", alpha=CELL_DISTRIBUTION_BAND_ALPHA, label=f"{inside_name} {float(band_width_um):g} um bands"),
        Patch(facecolor=plt.get_cmap(CELL_DISTRIBUTION_OUTSIDE_CMAP)(0.75), edgecolor="none", alpha=CELL_DISTRIBUTION_BAND_ALPHA, label=f"{outside_name} {float(band_width_um):g} um bands"),
    ]
    title = f"{boundary_label} - {float(band_width_um):g} um boundary bands"
    if extra_channel_name is not None and extra_channel_rgba is not None:
        ax.imshow(np.clip(np.asarray(extra_channel_rgba), 0.0, 1.0), origin=CELL_DISTRIBUTION_DISPLAY_ORIGIN)
        handles.append(Patch(facecolor=_channel_color_hex_from_config(config, extra_channel_name), edgecolor="none", alpha=0.85, label=f"{extra_channel_name} overlay"))
        title = f"{title} + {extra_channel_name}"

    ax.legend(handles=handles, loc="upper right", frameon=True)
    ax.set_title(title)
    ax.set_axis_off()
    fig.subplots_adjust(left=0, right=1, bottom=0, top=0.96)
    return fig


def run_region_mask_band_analysis(
    *,
    config: PipelineConfig,
    data_result: Dict[str, Any],
    boundary_label: str,
    boundary_mask_path: Path,
    band_width_um: float,
    overlay_channels: Sequence[str],
) -> Dict[str, Any]:
    if not valid_pixel_size(config.pixel_size_um):
        raise RuntimeError("Valid pixel size is required before running Cell distribution analysis.")

    boundary_mask = np.asarray(load_any_tiff(Path(boundary_mask_path)), dtype=bool)
    if boundary_mask.ndim != 2:
        raise RuntimeError("The selected boundary mask is not a 2D image.")
    if not np.any(boundary_mask):
        raise RuntimeError("The selected boundary mask is empty.")

    seed_mask = _boundary_seed_mask(boundary_mask)
    inside_distance_um, outside_distance_um, inside_band_index, outside_band_index = _distance_bands_from_boundary(
        boundary_mask=boundary_mask,
        seed_mask=seed_mask,
        band_width_um=float(band_width_um),
        pixel_size_um=config.pixel_size_um,
    )

    pixel_area_um2 = float(config.pixel_size_um[0]) * float(config.pixel_size_um[1])
    inside_name = f"Inside {boundary_label}"
    outside_name = f"Outside {boundary_label}"
    band_summary_df = pd.concat(
        [
            _band_summary(inside_name, inside_band_index, float(band_width_um), pixel_area_um2),
            _band_summary(outside_name, outside_band_index, float(band_width_um), pixel_area_um2),
        ],
        ignore_index=True,
    )

    overlay_rgb = _build_overlay_rgb_for_region_ui(config, data_result)
    light_base_rgb = _light_background_rgb(overlay_rgb)
    inside_rgba = _band_rgba(inside_band_index, CELL_DISTRIBUTION_INSIDE_CMAP)
    outside_rgba = _band_rgba(outside_band_index, CELL_DISTRIBUTION_OUTSIDE_CMAP)

    base_fig = _render_region_band_map(
        config=config,
        base_rgb=light_base_rgb,
        boundary_label=boundary_label,
        band_width_um=float(band_width_um),
        inside_name=inside_name,
        outside_name=outside_name,
        inside_mask=boundary_mask,
        outside_mask=~boundary_mask,
        inside_distance_um=inside_distance_um,
        outside_distance_um=outside_distance_um,
        inside_rgba=inside_rgba,
        outside_rgba=outside_rgba,
        seed_mask=seed_mask,
    )

    output_dirs = _cell_distribution_output_dirs(config)
    base_name = f"region_bands__{safe_name(boundary_label, 'boundary')}__{_cell_distribution_band_token(float(band_width_um))}um"
    region_masks_dir = output_dirs["region_masks"]
    summary_csv = region_masks_dir / f"{base_name}__summary.csv"
    summary_json = region_masks_dir / f"{base_name}__summary.json"
    arrays_npz = region_masks_dir / f"{base_name}__arrays.npz"
    inputs_json = region_masks_dir / f"{base_name}__inputs.json"
    base_png_path, base_svg_path = _save_figure_both_formats(base_fig, region_masks_dir / f"{base_name}__band_map", dpi=300, bbox_inches="tight", pad_inches=0, facecolor="white")

    band_summary_df.to_csv(summary_csv, index=False)
    write_json(
        inputs_json,
        {
            "image_id": str(config.image_id),
            "boundary_label": str(boundary_label),
            "boundary_mask_path": str(Path(boundary_mask_path).resolve()),
            "pixel_size_um": [float(config.pixel_size_um[0]), float(config.pixel_size_um[1])],
            "band_width_um": float(band_width_um),
            "inside_label": inside_name,
            "outside_label": outside_name,
            "overlay_channels": [str(channel_name) for channel_name in overlay_channels],
            "output_dir": str(region_masks_dir.resolve()),
        },
    )
    write_json(summary_json, {"rows": band_summary_df.to_dict(orient="records")})
    np.savez_compressed(
        arrays_npz,
        boundary_mask=boundary_mask.astype(np.uint8),
        boundary_seed_mask=seed_mask.astype(np.uint8),
        inside_band_index=inside_band_index.astype(np.int16),
        outside_band_index=outside_band_index.astype(np.int16),
        inside_distance_um=np.nan_to_num(inside_distance_um, nan=-1.0).astype(np.float32),
        outside_distance_um=np.nan_to_num(outside_distance_um, nan=-1.0).astype(np.float32),
    )

    overlay_paths: Dict[str, Dict[str, str]] = {}
    for channel_name in overlay_channels:
        overlay_fig = _render_region_band_map(
            config=config,
            base_rgb=light_base_rgb,
            boundary_label=boundary_label,
            band_width_um=float(band_width_um),
            inside_name=inside_name,
            outside_name=outside_name,
            inside_mask=boundary_mask,
            outside_mask=~boundary_mask,
            inside_distance_um=inside_distance_um,
            outside_distance_um=outside_distance_um,
            inside_rgba=inside_rgba,
            outside_rgba=outside_rgba,
            seed_mask=seed_mask,
            extra_channel_name=channel_name,
            extra_channel_rgba=_channel_rgba_for_distribution(data_result, config, channel_name),
        )
        try:
            overlay_png_path, overlay_svg_path = _save_figure_both_formats(
                overlay_fig,
                region_masks_dir / f"{base_name}__overlay__{safe_name(channel_name, 'channel')}",
                dpi=300,
                bbox_inches="tight",
                pad_inches=0,
                facecolor="white",
            )
            overlay_paths[str(channel_name)] = {
                "png": str(overlay_png_path),
                "svg": str(overlay_svg_path),
            }
        finally:
            _close_figure_obj(overlay_fig)

    return {
        "figure": base_fig,
        "band_summary": band_summary_df,
        "boundary_label": str(boundary_label),
        "band_width_um": float(band_width_um),
        "saved_paths": {
            "png": base_png_path,
            "svg": base_svg_path,
            "summary_csv": summary_csv,
            "summary_json": summary_json,
            "arrays_npz": arrays_npz,
            "inputs_json": inputs_json,
            "overlay_paths": overlay_paths,
        },
    }


CELL_DENSITY_PLOT_BACKGROUND_ALPHA = 0.22
CELL_DENSITY_PLOT_LINEWIDTH = 2.3
CELL_DENSITY_PLOT_MARKERSIZE = 4.0


def _cell_density_plot_geometry(side: str, band_idx: int, band_width_um: float) -> tuple[float, float, float]:
    band_idx = int(band_idx)
    if str(side) == "inside":
        x_lo = -float((band_idx + 1) * band_width_um)
        x_hi = -float(band_idx * band_width_um)
    elif str(side) == "outside":
        x_lo = float(band_idx * band_width_um)
        x_hi = float((band_idx + 1) * band_width_um)
    else:
        raise ValueError(f"Unknown side: {side}")
    return x_lo, x_hi, 0.5 * (x_lo + x_hi)


def _celltype_color_map(celltype_cfg: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    return {str(ct.get("name")): str(ct.get("color_hex", "#ffffff")) for ct in celltype_cfg}


def _cell_density_excel_engine() -> str | None:
    if importlib.util.find_spec("openpyxl") is not None:
        return "openpyxl"
    if importlib.util.find_spec("xlsxwriter") is not None:
        return "xlsxwriter"
    return None


def run_cell_density_analysis(
    *,
    config: PipelineConfig,
    assignment_result: Dict[str, Any],
    region_masks_result: Dict[str, Any],
    selected_celltypes: Sequence[str],
) -> Dict[str, Any]:
    saved_paths = region_masks_result.get("saved_paths", {}) if isinstance(region_masks_result, dict) else {}
    arrays_npz_path = saved_paths.get("arrays_npz")
    inputs_json_path = saved_paths.get("inputs_json")
    if not arrays_npz_path or not Path(arrays_npz_path).exists():
        raise RuntimeError("Region mask arrays were not found. Generate Region masks first.")

    band_arrays = np.load(arrays_npz_path)
    inside_band_index = np.asarray(band_arrays["inside_band_index"]).astype(np.int32)
    outside_band_index = np.asarray(band_arrays["outside_band_index"]).astype(np.int32)
    inside_mask = inside_band_index >= 0
    outside_mask = outside_band_index >= 0
    boundary_label = str(region_masks_result.get("boundary_label") or "Selected boundary")
    band_width_um = float(region_masks_result.get("band_width_um", 10.0) or 10.0)
    if inputs_json_path and Path(inputs_json_path).exists():
        try:
            payload = json.loads(Path(inputs_json_path).read_text())
            boundary_label = str(payload.get("boundary_label") or boundary_label)
            band_width_um = float(payload.get("band_width_um", band_width_um) or band_width_um)
        except Exception:
            pass

    pixel_area_um2 = float(config.pixel_size_um[0]) * float(config.pixel_size_um[1])
    selected_celltypes = [str(name) for name in selected_celltypes if str(name).strip()]
    if not selected_celltypes:
        raise RuntimeError("Select at least one cell type.")

    df_cells = assignment_result["df_cells"].copy()
    required_cols = {"celltype", "centroid_x_px", "centroid_y_px"}
    if not required_cols.issubset(set(df_cells.columns)):
        raise RuntimeError(f"Cell-type assignment table is missing required columns: {required_cols}")

    h, w = inside_band_index.shape
    cy = np.clip(np.rint(df_cells["centroid_y_px"].to_numpy(float)).astype(int), 0, h - 1)
    cx = np.clip(np.rint(df_cells["centroid_x_px"].to_numpy(float)).astype(int), 0, w - 1)
    celltypes_arr = df_cells["celltype"].astype(str).to_numpy()
    selected_mask = np.isin(celltypes_arr, selected_celltypes)

    inside_lookup = inside_band_index[cy, cx]
    outside_lookup = outside_band_index[cy, cx]
    count_frames: List[pd.DataFrame] = []
    for side_key, side_name, lookup in (
        ("inside", f"Inside {boundary_label}", inside_lookup),
        ("outside", f"Outside {boundary_label}", outside_lookup),
    ):
        valid = selected_mask & (lookup >= 0)
        if not np.any(valid):
            continue
        count_frames.append(
            pd.DataFrame(
                {
                    "region_key": side_key,
                    "region": side_name,
                    "band_index": lookup[valid].astype(int),
                    "celltype": celltypes_arr[valid],
                }
            )
        )
    counts_df = pd.concat(count_frames, ignore_index=True) if count_frames else pd.DataFrame(columns=["region_key", "region", "band_index", "celltype"])
    counts_lookup: Dict[tuple[str, int, str], int] = {}
    if not counts_df.empty:
        grouped_counts = counts_df.groupby(["region_key", "band_index", "celltype"]).size().rename("cell_count").reset_index()
        counts_lookup = {
            (str(row["region_key"]), int(row["band_index"]), str(row["celltype"])): int(row["cell_count"])
            for _, row in grouped_counts.iterrows()
        }

    region_definitions = [
        {
            "key": "inside",
            "name": f"Inside {boundary_label}",
            "band_index": inside_band_index,
            "mask": inside_mask,
            "cmap": CELL_DISTRIBUTION_INSIDE_CMAP,
            "side": "inside",
        },
        {
            "key": "outside",
            "name": f"Outside {boundary_label}",
            "band_index": outside_band_index,
            "mask": outside_mask,
            "cmap": CELL_DISTRIBUTION_OUTSIDE_CMAP,
            "side": "outside",
        },
    ]

    band_rows: List[Dict[str, Any]] = []
    band_long_rows: List[Dict[str, Any]] = []
    for region_def in region_definitions:
        band_idx_map = np.asarray(region_def["band_index"], dtype=np.int32)
        valid_band_ids = sorted(int(v) for v in np.unique(band_idx_map[band_idx_map >= 0]))
        for band_idx in valid_band_ids:
            band_mask = band_idx_map == band_idx
            band_area_px = int(np.count_nonzero(band_mask))
            if band_area_px == 0:
                continue
            band_area_um2 = float(band_area_px * pixel_area_um2)
            band_area_mm2 = float(band_area_um2 / 1e6)
            x_lo_um, x_hi_um, x_center_um = _cell_density_plot_geometry(region_def["side"], band_idx, band_width_um)
            row: Dict[str, Any] = {
                "region_key": str(region_def["key"]),
                "region": str(region_def["name"]),
                "band_index": int(band_idx),
                "region_band_lo_um": float(band_idx * band_width_um),
                "region_band_hi_um": float((band_idx + 1) * band_width_um),
                "plot_x_lo_um": float(x_lo_um),
                "plot_x_hi_um": float(x_hi_um),
                "plot_x_center_um": float(x_center_um),
                "band_area_px": band_area_px,
                "band_area_um2": band_area_um2,
                "band_area_mm2": band_area_mm2,
            }

            for celltype_name in selected_celltypes:
                safe = safe_name(celltype_name, "celltype")
                cell_count = int(counts_lookup.get((str(region_def["key"]), int(band_idx), str(celltype_name)), 0))
                density_um2 = float(cell_count / band_area_um2) if band_area_um2 > 0 else np.nan
                density_mm2 = float(cell_count / band_area_mm2) if band_area_mm2 > 0 else np.nan
                row[f"{safe}_cell_count"] = cell_count
                row[f"{safe}_density_cells_per_um2"] = density_um2
                row[f"{safe}_density_cells_per_mm2"] = density_mm2
                band_long_rows.append(
                    {
                        "region_key": str(region_def["key"]),
                        "region": str(region_def["name"]),
                        "band_index": int(band_idx),
                        "plot_x_lo_um": float(x_lo_um),
                        "plot_x_hi_um": float(x_hi_um),
                        "plot_x_center_um": float(x_center_um),
                        "celltype": str(celltype_name),
                        "cell_count": cell_count,
                        "band_area_px": band_area_px,
                        "band_area_um2": band_area_um2,
                        "band_area_mm2": band_area_mm2,
                        "density_cells_per_um2": density_um2,
                        "density_cells_per_mm2": density_mm2,
                    }
                )
            band_rows.append(row)

    band_metrics_df = pd.DataFrame(band_rows)
    band_metrics_long_df = pd.DataFrame(band_long_rows)
    if band_metrics_df.empty:
        raise RuntimeError("No band metrics were produced. Generate Region masks first.")

    region_rows: List[Dict[str, Any]] = []
    for region_def in region_definitions:
        region_mask = np.asarray(region_def["mask"], dtype=bool)
        region_area_px = int(np.count_nonzero(region_mask))
        region_area_um2 = float(region_area_px * pixel_area_um2)
        region_area_mm2 = float(region_area_um2 / 1e6)
        row = {
            "region_key": str(region_def["key"]),
            "region": str(region_def["name"]),
            "region_area_px": region_area_px,
            "region_area_um2": region_area_um2,
            "region_area_mm2": region_area_mm2,
        }
        if not counts_df.empty:
            region_counts = counts_df[counts_df["region_key"].astype(str) == str(region_def["key"])]["celltype"].astype(str).value_counts().to_dict()
        else:
            region_counts = {}
        for celltype_name in selected_celltypes:
            safe = safe_name(celltype_name, "celltype")
            cell_count = int(region_counts.get(str(celltype_name), 0))
            row[f"{safe}_cell_count"] = cell_count
            row[f"{safe}_density_cells_per_um2"] = float(cell_count / region_area_um2) if region_area_um2 > 0 else np.nan
            row[f"{safe}_density_cells_per_mm2"] = float(cell_count / region_area_mm2) if region_area_mm2 > 0 else np.nan
        region_rows.append(row)
    region_metrics_df = pd.DataFrame(region_rows)

    max_band_by_region = {
        str(region_key): int(sub["band_index"].max()) if not sub.empty else 0
        for region_key, sub in band_metrics_df.groupby("region_key")
    }

    plot_df = band_metrics_df.sort_values("plot_x_center_um").copy()
    celltype_colors = _celltype_color_map(st.session_state["celltype_cfg"])
    fig, ax = plt.subplots(figsize=(8.8, 4.8), facecolor="white")
    ax.set_facecolor("white")

    for _, row in plot_df.iterrows():
        region_key = str(row["region_key"])
        cmap_name = CELL_DISTRIBUTION_INSIDE_CMAP if region_key == "inside" else CELL_DISTRIBUTION_OUTSIDE_CMAP
        max_idx = max_band_by_region.get(region_key, 0)
        cmap = plt.get_cmap(cmap_name, max(2, max_idx + 1))
        bg_color = cmap(float(int(row["band_index"])) / max(1, max_idx))
        ax.axvspan(
            float(row["plot_x_lo_um"]),
            float(row["plot_x_hi_um"]),
            color=bg_color,
            alpha=CELL_DENSITY_PLOT_BACKGROUND_ALPHA,
            linewidth=0,
            zorder=0,
        )

    for celltype_name in selected_celltypes:
        sub = band_metrics_long_df[band_metrics_long_df["celltype"].astype(str) == str(celltype_name)].sort_values("plot_x_center_um")
        if sub.empty:
            continue
        ax.plot(
            sub["plot_x_center_um"].to_numpy(float),
            sub["density_cells_per_mm2"].to_numpy(float),
            color=celltype_colors.get(str(celltype_name), "#ffffff"),
            linewidth=CELL_DENSITY_PLOT_LINEWIDTH,
            marker="o",
            markersize=CELL_DENSITY_PLOT_MARKERSIZE,
            label=str(celltype_name),
            zorder=3,
        )

    ax.axvline(0.0, color="white", linewidth=3.0, alpha=0.95, zorder=4)

    ymax = float(np.nanmax(band_metrics_long_df["density_cells_per_mm2"])) if len(band_metrics_long_df) else 1.0
    if not np.isfinite(ymax):
        ymax = 1.0
    ymax = max(1.0, ymax * 1.10)
    inside_df = plot_df[plot_df["region_key"].astype(str) == "inside"]
    outside_df = plot_df[plot_df["region_key"].astype(str) == "outside"]
    label_positions = {
        f"Inside {boundary_label}": 0.5 * (float(inside_df["plot_x_lo_um"].min()) + 0.0) if not inside_df.empty else None,
        f"Outside {boundary_label}": 0.5 * (0.0 + float(outside_df["plot_x_hi_um"].max())) if not outside_df.empty else None,
    }
    for label, xpos in label_positions.items():
        if xpos is not None and np.isfinite(xpos):
            ax.text(xpos, ymax * 0.985, label, ha="center", va="top", fontsize=10, color="black")

    background_handles = [
        Patch(facecolor=plt.get_cmap(CELL_DISTRIBUTION_INSIDE_CMAP)(0.75), edgecolor="none", alpha=CELL_DENSITY_PLOT_BACKGROUND_ALPHA, label=f"Inside {boundary_label} bands"),
        Patch(facecolor=plt.get_cmap(CELL_DISTRIBUTION_OUTSIDE_CMAP)(0.75), edgecolor="none", alpha=CELL_DENSITY_PLOT_BACKGROUND_ALPHA, label=f"Outside {boundary_label} bands"),
    ]
    line_handles, line_labels = ax.get_legend_handles_labels()
    ax.legend(background_handles + line_handles, [h.get_label() for h in background_handles] + line_labels, loc="upper left", frameon=True)
    ax.set_xlabel("Distance across inside -> outside (um)")
    ax.set_ylabel("Cell density (cells / mm²)")
    ax.set_title(f"{config.image_id} - Cell density by band")
    ax.set_ylim(0.0, ymax)
    ax.grid(axis="y", alpha=0.20, zorder=1)
    ax.tick_params(axis="both", labelsize=11)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", rotation_mode="anchor")

    output_dirs = _cell_distribution_output_dirs(config)
    density_dir = output_dirs["cell_density"]
    payload = {
        "boundary_label": str(boundary_label),
        "band_width_um": float(band_width_um),
        "celltypes": list(selected_celltypes),
    }
    short_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    base_name = f"cell_density__{safe_name(boundary_label, 'boundary')}__{_cell_distribution_band_token(float(band_width_um))}um__{short_hash}"

    csv_band_wide = density_dir / f"{base_name}__wide.csv"
    csv_band_long = density_dir / f"{base_name}__long.csv"
    csv_region = density_dir / f"{base_name}__region.csv"
    csv_inputs = density_dir / f"{base_name}__inputs.csv"
    excel_path = density_dir / f"{base_name}.xlsx"
    plot_png_path, plot_svg_path = _save_figure_both_formats(
        fig,
        density_dir / f"{base_name}__plot",
        dpi=300,
        bbox_inches="tight",
        pad_inches=0.05,
        facecolor="white",
    )

    band_metrics_df.sort_values(["plot_x_center_um"]).to_csv(csv_band_wide, index=False)
    band_metrics_long_df.sort_values(["plot_x_center_um", "celltype"]).to_csv(csv_band_long, index=False)
    region_metrics_df.to_csv(csv_region, index=False)
    inputs_df = pd.DataFrame(
        [
            {
                "image_id": str(config.image_id),
                "boundary_label": str(boundary_label),
                "band_width_um": float(band_width_um),
                "pixel_size_x_um": float(config.pixel_size_um[0]),
                "pixel_size_y_um": float(config.pixel_size_um[1]),
                "inside_label": f"Inside {boundary_label}",
                "outside_label": f"Outside {boundary_label}",
                "region_mask_arrays_npz": str(Path(arrays_npz_path).resolve()),
                "selected_celltypes": ", ".join(selected_celltypes),
            }
        ]
    )
    inputs_df.to_csv(csv_inputs, index=False)

    excel_engine = _cell_density_excel_engine()
    if excel_engine is not None:
        with pd.ExcelWriter(excel_path, engine=excel_engine) as writer:
            band_metrics_df.sort_values(["plot_x_center_um"]).to_excel(writer, sheet_name="band_metrics_wide", index=False)
            band_metrics_long_df.sort_values(["plot_x_center_um", "celltype"]).to_excel(writer, sheet_name="band_metrics_long", index=False)
            region_metrics_df.to_excel(writer, sheet_name="region_metrics", index=False)
            inputs_df.to_excel(writer, sheet_name="inputs", index=False)

    return {
        "figure": fig,
        "band_metrics_wide": band_metrics_df,
        "band_metrics_long": band_metrics_long_df,
        "region_metrics": region_metrics_df,
        "selected_celltypes": list(selected_celltypes),
        "boundary_label": str(boundary_label),
        "band_width_um": float(band_width_um),
        "saved_paths": {
            "png": plot_png_path,
            "svg": plot_svg_path,
            "csv_band_wide": csv_band_wide,
            "csv_band_long": csv_band_long,
            "csv_region": csv_region,
            "csv_inputs": csv_inputs,
            "excel": excel_path if excel_engine is not None else None,
        },
    }


def run_cell_cluster_distribution_analysis(
    *,
    config: PipelineConfig,
    neighborhood_result: Dict[str, Any],
    boundary_candidates: Sequence[Tuple[str, Path]],
    selected_boundary_labels: Sequence[str],
    selected_cluster_labels: Sequence[str],
) -> Dict[str, Any]:
    cluster_summary_raw = neighborhood_result.get("cluster_summary")
    tile_assignments_raw = neighborhood_result.get("tile_assignments")
    cluster_mask_raw = neighborhood_result.get("cluster_mask")

    if not isinstance(cluster_summary_raw, pd.DataFrame) or cluster_summary_raw.empty:
        raise RuntimeError("Neighborhood analysis does not currently contain any cluster summary rows.")
    if not isinstance(tile_assignments_raw, pd.DataFrame) or tile_assignments_raw.empty:
        raise RuntimeError("Neighborhood analysis does not currently contain any occupied neighborhood tiles.")
    if cluster_mask_raw is None:
        raise RuntimeError("Neighborhood analysis does not currently contain a cluster mask.")

    selected_boundary_labels = [str(label) for label in selected_boundary_labels if str(label).strip()]
    if not selected_boundary_labels:
        raise RuntimeError("Select at least one Region analysis boundary or ROI.")
    selected_cluster_labels = [str(label) for label in selected_cluster_labels if str(label).strip()]
    if not selected_cluster_labels:
        raise RuntimeError("Select at least one neighborhood cluster.")

    cluster_summary = cluster_summary_raw.copy()
    tile_assignments = tile_assignments_raw.copy()
    cluster_mask = np.asarray(cluster_mask_raw).astype(np.uint16)
    height, width = cluster_mask.shape

    if "cluster_label" not in cluster_summary.columns:
        raise RuntimeError("Neighborhood cluster summary is missing the cluster_label column.")
    if "cluster_id" not in cluster_summary.columns:
        cluster_summary["cluster_id"] = np.arange(1, len(cluster_summary) + 1, dtype=int)
    if "cluster_key" not in cluster_summary.columns:
        cluster_summary["cluster_key"] = cluster_summary["cluster_label"].astype(str)

    available_cluster_labels = cluster_summary["cluster_label"].astype(str).tolist()
    selected_cluster_labels = [label for label in selected_cluster_labels if label in set(available_cluster_labels)]
    if not selected_cluster_labels:
        raise RuntimeError("None of the selected neighborhood clusters are available in the current neighborhood-analysis result.")

    cluster_summary = (
        cluster_summary[cluster_summary["cluster_label"].astype(str).isin(selected_cluster_labels)]
        .copy()
        .sort_values(["cluster_id", "cluster_label"])
        .reset_index(drop=True)
    )
    selected_cluster_labels = cluster_summary["cluster_label"].astype(str).tolist()

    required_tile_cols = {
        "tile_row",
        "tile_col",
        "tile_index",
        "x0_px",
        "x1_px",
        "y0_px",
        "y1_px",
        "n_cells",
        "cluster_label",
    }
    if not required_tile_cols.issubset(tile_assignments.columns):
        raise RuntimeError(f"Neighborhood tile assignments are missing required columns: {sorted(required_tile_cols)}")
    if "cluster_id" not in tile_assignments.columns:
        cluster_id_map = dict(
            zip(
                cluster_summary["cluster_label"].astype(str),
                cluster_summary["cluster_id"].astype(int),
            )
        )
        tile_assignments["cluster_id"] = tile_assignments["cluster_label"].astype(str).map(cluster_id_map)

    tile_assignments = (
        tile_assignments[tile_assignments["cluster_label"].astype(str).isin(selected_cluster_labels)]
        .copy()
        .reset_index(drop=True)
    )
    if tile_assignments.empty:
        raise RuntimeError("No occupied neighborhood tiles remain after filtering to the selected neighborhood clusters.")

    x0_arr = tile_assignments["x0_px"].to_numpy(int)
    x1_arr = tile_assignments["x1_px"].to_numpy(int)
    y0_arr = tile_assignments["y0_px"].to_numpy(int)
    y1_arr = tile_assignments["y1_px"].to_numpy(int)
    tile_area_px = np.maximum(1, (x1_arr - x0_arr) * (y1_arr - y0_arr)).astype(int)
    center_x_arr = np.clip(((x0_arr + x1_arr - 1) // 2).astype(int), 0, width - 1)
    center_y_arr = np.clip(((y0_arr + y1_arr - 1) // 2).astype(int), 0, height - 1)

    boundary_label_to_path = {str(label): Path(path) for label, path in boundary_candidates}
    missing_boundaries = [label for label in selected_boundary_labels if label not in boundary_label_to_path]
    if missing_boundaries:
        raise RuntimeError(f"Some selected Region analysis masks are no longer available: {', '.join(missing_boundaries)}")

    classified_frames: List[pd.DataFrame] = []
    region_area_rows: List[Dict[str, Any]] = []
    total_area_px = int(height * width)

    for boundary_label in selected_boundary_labels:
        boundary_path = boundary_label_to_path[boundary_label]
        region_mask = load_any_tiff(boundary_path) > 0
        if region_mask.shape != (height, width):
            raise RuntimeError(
                f"Region mask {boundary_label!r} has shape {region_mask.shape}, but the neighborhood result uses {(height, width)}."
            )

        integral = np.pad(region_mask.astype(np.int32), ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)
        inside_px_arr = (
            integral[y1_arr, x1_arr]
            - integral[y0_arr, x1_arr]
            - integral[y1_arr, x0_arr]
            + integral[y0_arr, x0_arr]
        ).astype(int)
        center_inside_arr = region_mask[center_y_arr, center_x_arr].astype(bool)
        inside_region_arr = (inside_px_arr * 2 > tile_area_px) | ((inside_px_arr * 2 == tile_area_px) & center_inside_arr)
        inside_fraction_arr = inside_px_arr.astype(float) / tile_area_px.astype(float)

        frame = tile_assignments.copy()
        frame["boundary_label"] = str(boundary_label)
        frame["boundary_mask_path"] = str(boundary_path.resolve())
        frame["tile_area_px"] = tile_area_px
        frame["inside_px"] = inside_px_arr
        frame["inside_fraction"] = inside_fraction_arr
        frame["region_key"] = np.where(inside_region_arr, "inside", "outside")
        frame["region"] = np.where(
            inside_region_arr,
            f"Inside {boundary_label}",
            f"Outside {boundary_label}",
        )
        classified_frames.append(frame)

        inside_area_px = int(np.count_nonzero(region_mask))
        outside_area_px = int(total_area_px - inside_area_px)
        region_area_rows.extend(
            [
                {
                    "boundary_label": str(boundary_label),
                    "region_key": "inside",
                    "region": f"Inside {boundary_label}",
                    "region_area_px": inside_area_px,
                    "region_area_fraction": float(inside_area_px / max(1, total_area_px)),
                },
                {
                    "boundary_label": str(boundary_label),
                    "region_key": "outside",
                    "region": f"Outside {boundary_label}",
                    "region_area_px": outside_area_px,
                    "region_area_fraction": float(outside_area_px / max(1, total_area_px)),
                },
            ]
        )

    classified_tiles_df = pd.concat(classified_frames, ignore_index=True)
    region_area_df = pd.DataFrame(region_area_rows)

    cluster_region_metrics_df = (
        classified_tiles_df.groupby(
            ["boundary_label", "region_key", "region", "cluster_id", "cluster_label"],
            as_index=False,
        )
        .agg(
            occupied_tile_count=("tile_index", "count"),
            total_cells_in_tiles=("n_cells", "sum"),
            mean_inside_fraction=("inside_fraction", "mean"),
        )
        .sort_values(["boundary_label", "region_key", "cluster_id", "cluster_label"])
        .reset_index(drop=True)
    )

    region_metrics_df = (
        classified_tiles_df.groupby(["boundary_label", "region_key", "region"], as_index=False)
        .agg(
            occupied_tile_count=("tile_index", "count"),
            total_cells_in_tiles=("n_cells", "sum"),
            distinct_cluster_count=("cluster_label", "nunique"),
            mean_inside_fraction=("inside_fraction", "mean"),
        )
        .merge(region_area_df, on=["boundary_label", "region_key", "region"], how="right")
        .fillna(
            {
                "occupied_tile_count": 0,
                "total_cells_in_tiles": 0,
                "distinct_cluster_count": 0,
                "mean_inside_fraction": 0.0,
            }
        )
        .sort_values(["boundary_label", "region_key"])
        .reset_index(drop=True)
    )
    for int_col in ["occupied_tile_count", "total_cells_in_tiles", "distinct_cluster_count", "region_area_px"]:
        region_metrics_df[int_col] = region_metrics_df[int_col].astype(int)

    region_order: List[str] = []
    for boundary_label in selected_boundary_labels:
        region_order.extend([f"Inside {boundary_label}", f"Outside {boundary_label}"])
    cluster_order = cluster_summary["cluster_label"].astype(str).tolist()

    tile_count_matrix_df = (
        cluster_region_metrics_df.pivot_table(
            index="cluster_label",
            columns="region",
            values="occupied_tile_count",
            aggfunc="sum",
            fill_value=0,
        )
        .reindex(index=cluster_order, columns=region_order, fill_value=0)
        .astype(int)
    )
    cell_count_matrix_df = (
        cluster_region_metrics_df.pivot_table(
            index="cluster_label",
            columns="region",
            values="total_cells_in_tiles",
            aggfunc="sum",
            fill_value=0,
        )
        .reindex(index=cluster_order, columns=region_order, fill_value=0)
        .astype(int)
    )

    n_regions = max(1, len(region_order))
    n_clusters = max(1, len(cluster_order))
    fig_width = max(9.0, 3.4 + 1.05 * n_regions)
    fig_height = max(4.8, 1.5 + 0.40 * n_clusters)
    fig, axes = plt.subplots(1, 2, figsize=(fig_width, fig_height), constrained_layout=True, facecolor="white")
    matrix_specs = [
        (tile_count_matrix_df, "Occupied neighborhood tiles", "viridis"),
        (cell_count_matrix_df, "Cells in those neighborhood tiles", "magma"),
    ]
    annotate_values = n_regions <= 10 and n_clusters <= 14
    for ax, (matrix_df, panel_title, cmap_name) in zip(axes, matrix_specs):
        matrix_values = matrix_df.to_numpy(float)
        im = ax.imshow(matrix_values, aspect="auto", cmap=cmap_name)
        ax.set_title(panel_title)
        ax.set_xticks(np.arange(len(matrix_df.columns)))
        ax.set_xticklabels(matrix_df.columns.tolist(), rotation=45, ha="right", rotation_mode="anchor", fontsize=11)
        ax.set_yticks(np.arange(len(matrix_df.index)))
        ax.set_yticklabels(matrix_df.index.tolist(), fontsize=10)
        ax.set_xlabel("Region from Region analysis")
        if ax is axes[0]:
            ax.set_ylabel("Neighborhood cluster")
        if annotate_values:
            for row_idx in range(matrix_values.shape[0]):
                for col_idx in range(matrix_values.shape[1]):
                    ax.text(
                        col_idx,
                        row_idx,
                        f"{int(matrix_values[row_idx, col_idx])}",
                        ha="center",
                        va="center",
                        fontsize=8,
                        color="white" if matrix_values[row_idx, col_idx] > 0.55 * np.nanmax(matrix_values) else "black",
                    )
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    fig.suptitle(f"{config.image_id} - Neighborhood clusters by Region-analysis mask", fontsize=14)

    output_dirs = _cell_distribution_output_dirs(config)
    cluster_dir = output_dirs["cell_cluster_distribution"]
    payload = {
        "selected_boundaries": list(selected_boundary_labels),
        "selected_clusters": list(selected_cluster_labels),
        "grid_size_um": float(neighborhood_result.get("grid_size_um", 20.0) or 20.0),
        "classification_rule": "majority_overlap_with_center_tie_breaker",
    }
    short_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    base_name = f"cell_cluster_distribution__{short_hash}"

    csv_cluster_region = cluster_dir / f"{base_name}__cluster_region.csv"
    csv_region = cluster_dir / f"{base_name}__region.csv"
    csv_tiles = cluster_dir / f"{base_name}__tiles.csv"
    csv_tile_matrix = cluster_dir / f"{base_name}__tile_matrix.csv"
    csv_cell_matrix = cluster_dir / f"{base_name}__cell_matrix.csv"
    csv_inputs = cluster_dir / f"{base_name}__inputs.csv"
    excel_path = cluster_dir / f"{base_name}.xlsx"
    plot_png_path, plot_svg_path = _save_figure_both_formats(
        fig,
        cluster_dir / f"{base_name}__heatmap",
        dpi=300,
        bbox_inches="tight",
        pad_inches=0.05,
        facecolor="white",
    )

    cluster_region_metrics_df.to_csv(csv_cluster_region, index=False)
    region_metrics_df.to_csv(csv_region, index=False)
    classified_tiles_df.to_csv(csv_tiles, index=False)
    tile_count_matrix_df.to_csv(csv_tile_matrix)
    cell_count_matrix_df.to_csv(csv_cell_matrix)
    inputs_df = pd.DataFrame(
        [
            {
                "image_id": str(config.image_id),
                "grid_size_um": float(neighborhood_result.get("grid_size_um", 20.0) or 20.0),
                "classification_rule": "majority_overlap_with_center_tie_breaker",
                "selected_boundaries": ", ".join(selected_boundary_labels),
                "selected_clusters": " | ".join(selected_cluster_labels),
                "n_selected_boundaries": int(len(selected_boundary_labels)),
                "n_selected_clusters": int(len(selected_cluster_labels)),
            }
        ]
    )
    inputs_df.to_csv(csv_inputs, index=False)

    excel_engine = _cell_density_excel_engine()
    if excel_engine is not None:
        with pd.ExcelWriter(excel_path, engine=excel_engine) as writer:
            cluster_region_metrics_df.to_excel(writer, sheet_name="cluster_region_metrics", index=False)
            region_metrics_df.to_excel(writer, sheet_name="region_metrics", index=False)
            classified_tiles_df.to_excel(writer, sheet_name="tile_classifications", index=False)
            tile_count_matrix_df.to_excel(writer, sheet_name="tile_count_matrix")
            cell_count_matrix_df.to_excel(writer, sheet_name="cell_count_matrix")
            inputs_df.to_excel(writer, sheet_name="inputs", index=False)

    return {
        "figure": fig,
        "cluster_region_metrics": cluster_region_metrics_df,
        "region_metrics": region_metrics_df,
        "tile_classifications": classified_tiles_df,
        "tile_count_matrix": tile_count_matrix_df,
        "cell_count_matrix": cell_count_matrix_df,
        "selected_boundaries": list(selected_boundary_labels),
        "selected_clusters": list(selected_cluster_labels),
        "saved_paths": {
            "png": plot_png_path,
            "svg": plot_svg_path,
            "csv_cluster_region": csv_cluster_region,
            "csv_region": csv_region,
            "csv_tiles": csv_tiles,
            "csv_tile_matrix": csv_tile_matrix,
            "csv_cell_matrix": csv_cell_matrix,
            "csv_inputs": csv_inputs,
            "excel": excel_path if excel_engine is not None else None,
        },
    }


def render_cell_distribution_tab(tab):
    with tab:
        st.subheader("Cell distribution analysis")
        config: PipelineConfig | None = st.session_state.get("config")
        if config is None:
            st.warning("Save the configuration first.")
            return
        if st.session_state.get("celltype_cfg") is None:
            st.warning("Save the cell-type configuration first.")
            return

        try:
            assignment_result = ensure_assignment_outputs_available()
        except Exception as exc:
            st.warning(str(exc))
            return

        ensure_pixels_loaded()
        data_result = st.session_state.get("data_result") or {}

        region_masks_tab, density_tab, cluster_tab = st.tabs(["Region masks", "Cell density", "Cell cluster distribution"])

        with region_masks_tab:
            boundary_candidates = discover_boundary_masks(
                save_dir=get_section_output_dir(config, "region_analysis"),
                celltype_cfg=st.session_state["celltype_cfg"],
                df_cells=assignment_result["df_cells"],
            )
            if not boundary_candidates:
                st.info("No Region analysis boundary masks were found yet. Save at least one computational ROI or adjusted ROI in Region analysis first.")
            else:
                boundary_labels = [name for name, _ in boundary_candidates]
                label_to_path = {label: path for label, (_, path) in zip(boundary_labels, boundary_candidates)}
                if st.session_state.get("cell_distribution_boundary_label") not in boundary_labels:
                    st.session_state["cell_distribution_boundary_label"] = boundary_labels[0]

                ctrl_cols = st.columns([2.6, 1.2, 2.2])
                with ctrl_cols[0]:
                    st.selectbox("Boundary from Region analysis", options=boundary_labels, key="cell_distribution_boundary_label")
                with ctrl_cols[1]:
                    st.number_input("Band width (µm)", min_value=0.5, value=10.0, step=0.5, key="cell_distribution_band_width_um")
                with ctrl_cols[2]:
                    st.caption("Outputs are saved automatically after generation.")

                st.caption(
                    "This tool builds distance bands on both sides of the selected Region analysis boundary, previews the band map, and automatically saves the base outputs."
                )

                if st.button("Generate region masks", type="primary", key="run_cell_distribution_region_masks_btn"):
                    try:
                        boundary_label = str(st.session_state.get("cell_distribution_boundary_label") or "")
                        if not boundary_label:
                            raise RuntimeError("Select one boundary from Region analysis first.")
                        result = run_region_mask_band_analysis(
                            config=config,
                            data_result=data_result,
                            boundary_label=boundary_label,
                            boundary_mask_path=label_to_path[boundary_label],
                            band_width_um=float(st.session_state.get("cell_distribution_band_width_um", 10.0) or 10.0),
                            overlay_channels=[],
                        )
                        _close_result_figures(st.session_state.get("cell_distribution_region_masks_result"))
                        st.session_state["cell_distribution_region_masks_result"] = result
                        st.session_state["cell_distribution_density_result"] = None
                        invalidate_output_zip_cache()
                        refresh_output_zip_state(config.save_dir)
                        st.success(f"Region masks finished. Outputs are in {_cell_distribution_output_dirs(config)['region_masks']}")
                    except Exception as exc:
                        st.error(str(exc))

                region_masks_result = st.session_state.get("cell_distribution_region_masks_result")
                if isinstance(region_masks_result, dict):
                    render_zoomable_figure(
                        fig=region_masks_result.get("figure"),
                        component_key="cell_distribution_region_masks_result",
                        saved_paths=[
                            region_masks_result.get("saved_paths", {}).get("png"),
                            region_masks_result.get("saved_paths", {}).get("svg"),
                        ],
                        component_height=920,
                    )
                    st.markdown("#### Band summary")
                    st.dataframe(region_masks_result.get("band_summary", pd.DataFrame()), use_container_width=True)

        with density_tab:
            region_masks_result = st.session_state.get("cell_distribution_region_masks_result")
            if not isinstance(region_masks_result, dict):
                st.info("Generate Region masks first, then use that banded result for Cell density.")
            else:
                selectable_celltypes = list(
                    dict.fromkeys(
                        str(ct.get("name"))
                        for ct in st.session_state["celltype_cfg"]
                        if str(ct.get("name", "")).strip()
                    )
                ) or sorted(set(assignment_result["df_cells"]["celltype"].astype(str)))
                default_selected = [
                    name
                    for name in st.session_state.get("cell_distribution_density_celltypes", selectable_celltypes[:1])
                    if name in selectable_celltypes
                ] or selectable_celltypes[:1]

                st.multiselect(
                    "Cell types to calculate",
                    options=selectable_celltypes,
                    default=default_selected,
                    key="cell_distribution_density_celltypes",
                    help="Choose one or more assigned cell types. The app computes cell count divided by band area for each selected type.",
                )
                st.caption(
                    "Each selected cell type is plotted with its own cell-type color. "
                    "Inside bands are shown on the left of the white divider, and outside bands are shown on the right."
                )

                if st.button("Generate cell density", type="primary", key="run_cell_distribution_density_btn"):
                    try:
                        selected_celltypes = list(st.session_state.get("cell_distribution_density_celltypes", []))
                        result = run_cell_density_analysis(
                            config=config,
                            assignment_result=assignment_result,
                            region_masks_result=region_masks_result,
                            selected_celltypes=selected_celltypes,
                        )
                        _close_result_figures(st.session_state.get("cell_distribution_density_result"))
                        st.session_state["cell_distribution_density_result"] = result
                        invalidate_output_zip_cache()
                        refresh_output_zip_state(config.save_dir)
                        st.success(f"Cell density finished. Outputs are in {_cell_distribution_output_dirs(config)['cell_density']}")
                    except Exception as exc:
                        st.error(str(exc))

                density_result = st.session_state.get("cell_distribution_density_result")
                if isinstance(density_result, dict):
                    render_zoomable_figure(
                        fig=density_result.get("figure"),
                        component_key="cell_distribution_density_result",
                        saved_paths=[
                            density_result.get("saved_paths", {}).get("png"),
                            density_result.get("saved_paths", {}).get("svg"),
                        ],
                        component_height=760,
                    )
                    density_cols = st.columns(2)
                    with density_cols[0]:
                        st.markdown("#### Band metrics")
                        st.dataframe(density_result.get("band_metrics_long", pd.DataFrame()), use_container_width=True)
                    with density_cols[1]:
                        st.markdown("#### Region metrics")
                        st.dataframe(density_result.get("region_metrics", pd.DataFrame()), use_container_width=True)

        with cluster_tab:
            try:
                neighborhood_result = ensure_neighborhood_outputs_available()
            except Exception as exc:
                neighborhood_result = None
                st.info(str(exc))

            boundary_candidates = discover_boundary_masks(
                save_dir=get_section_output_dir(config, "region_analysis"),
                celltype_cfg=st.session_state["celltype_cfg"],
                df_cells=assignment_result["df_cells"],
            )
            if neighborhood_result is None:
                pass
            elif not boundary_candidates:
                st.info("No Region analysis boundary masks were found yet. Save at least one computational ROI, adjusted ROI, or manual ROI in Region analysis first.")
            else:
                boundary_labels = [name for name, _ in boundary_candidates]
                default_boundaries = [
                    name
                    for name in st.session_state.get(
                        "cell_distribution_cluster_boundaries",
                        boundary_labels[: min(3, len(boundary_labels))],
                    )
                    if name in boundary_labels
                ] or boundary_labels[: min(3, len(boundary_labels))]

                cluster_labels = _neighborhood_cluster_labels_from_result(neighborhood_result)
                if not cluster_labels:
                    cluster_summary = neighborhood_result.get("cluster_summary")
                    if isinstance(cluster_summary, pd.DataFrame) and "cluster_label" in cluster_summary.columns:
                        cluster_labels = cluster_summary.sort_values("cluster_id")["cluster_label"].astype(str).tolist() if "cluster_id" in cluster_summary.columns else cluster_summary["cluster_label"].astype(str).tolist()
                if not cluster_labels:
                    st.warning("Neighborhood analysis does not currently contain any cluster labels to summarize.")
                else:
                    default_clusters = [
                        label
                        for label in st.session_state.get(
                            "cell_distribution_cluster_labels",
                            neighborhood_result.get("display_cluster_labels", cluster_labels),
                        )
                        if label in cluster_labels
                    ] or cluster_labels

                    cluster_cols = st.columns([2.2, 2.8])
                    with cluster_cols[0]:
                        st.multiselect(
                            "Regions / boundaries from Region analysis",
                            options=boundary_labels,
                            default=default_boundaries,
                            key="cell_distribution_cluster_boundaries",
                            help="Each selected saved Region-analysis mask contributes two regions to the summary: inside and outside.",
                        )
                    with cluster_cols[1]:
                        st.multiselect(
                            "Neighborhood clusters to calculate",
                            options=cluster_labels,
                            default=default_clusters,
                            key="cell_distribution_cluster_labels",
                            help="Choose which neighborhood-cluster labels to summarize against the selected Region-analysis masks.",
                        )

                    st.caption(
                        "Each occupied neighborhood tile is classified as inside or outside a saved Region-analysis mask by majority overlap. "
                        "If a tile is exactly split 50/50 by the mask, the tile center pixel breaks the tie. "
                        "The same tile is summarized independently for each selected Region-analysis mask."
                    )

                    if st.button("Generate cell cluster distribution", type="primary", key="run_cell_distribution_cluster_btn"):
                        try:
                            selected_boundaries = list(st.session_state.get("cell_distribution_cluster_boundaries", []))
                            selected_clusters = list(st.session_state.get("cell_distribution_cluster_labels", []))
                            result = run_cell_cluster_distribution_analysis(
                                config=config,
                                neighborhood_result=neighborhood_result,
                                boundary_candidates=boundary_candidates,
                                selected_boundary_labels=selected_boundaries,
                                selected_cluster_labels=selected_clusters,
                            )
                            _close_result_figures(st.session_state.get("cell_distribution_cluster_result"))
                            st.session_state["cell_distribution_cluster_result"] = result
                            invalidate_output_zip_cache()
                            refresh_output_zip_state(config.save_dir)
                            st.success(
                                f"Cell cluster distribution finished. Outputs are in {_cell_distribution_output_dirs(config)['cell_cluster_distribution']}"
                            )
                        except Exception as exc:
                            st.error(str(exc))

                    cluster_result = st.session_state.get("cell_distribution_cluster_result")
                    if isinstance(cluster_result, dict):
                        render_zoomable_figure(
                            fig=cluster_result.get("figure"),
                            component_key="cell_distribution_cluster_result",
                            saved_paths=[
                                cluster_result.get("saved_paths", {}).get("png"),
                                cluster_result.get("saved_paths", {}).get("svg"),
                            ],
                            component_height=920,
                        )
                        cluster_metric_cols = st.columns(2)
                        with cluster_metric_cols[0]:
                            st.markdown("#### Cluster-by-region metrics")
                            st.dataframe(cluster_result.get("cluster_region_metrics", pd.DataFrame()), use_container_width=True)
                        with cluster_metric_cols[1]:
                            st.markdown("#### Region summary")
                            st.dataframe(cluster_result.get("region_metrics", pd.DataFrame()), use_container_width=True)
                        with st.expander("Tile classifications preview", expanded=False):
                            st.dataframe(cluster_result.get("tile_classifications", pd.DataFrame()).head(200), use_container_width=True)


def build_output_signature(folder_path: Path) -> str:
    rows = []
    if folder_path.exists():
        for path in sorted([p for p in folder_path.rglob("*") if p.is_file()]):
            stat = path.stat()
            rows.append(
                {
                    "path": str(path.relative_to(folder_path)),
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                }
            )
    return json.dumps(rows, sort_keys=True)


@st.cache_data(show_spinner=False)
def build_output_zip_bytes_cached(folder_path: str, output_signature: str) -> bytes:
    return zip_directory_bytes(Path(folder_path))


def clear_state_keys(keys: Sequence[str]) -> None:
    for key in keys:
        _close_result_figures(st.session_state.get(key))
        st.session_state[key] = None



def auto_color_from_filename(file_name: str) -> str:
    digest = hashlib.sha256(file_name.encode("utf-8")).hexdigest()
    hue = (int(digest[:8], 16) % 360) / 360.0
    sat = 0.62 + (int(digest[8:12], 16) % 18) / 100.0
    val = 0.82 + (int(digest[12:16], 16) % 12) / 100.0
    r, g, b = colorsys.hsv_to_rgb(hue, min(max(sat, 0.55), 0.90), min(max(val, 0.75), 0.98))
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def automatic_channel_color_palette(file_names: Sequence[str], shuffle_index: int = 0) -> List[str]:
    count = len(file_names)
    if count <= 0:
        return []
    offset = ((shuffle_index * 0.17320508075688773) + 0.11) % 1.0
    colors: List[str] = []
    for idx in range(count):
        hue = (offset + 0.6180339887498949 * idx) % 1.0
        sat = [0.72, 0.80, 0.64, 0.76][(idx + shuffle_index) % 4]
        val = [0.92, 0.84, 0.78, 0.88, 0.96][(idx + 2 * shuffle_index) % 5]
        r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
        colors.append("#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255)))
    return colors


def automatic_neighborhood_cluster_color_palette(cluster_labels: Sequence[str], shuffle_index: int = 0) -> List[str]:
    count = len(cluster_labels)
    if count <= 0:
        return []

    offset = ((shuffle_index * 0.13750352374993502) + 0.07) % 1.0
    step = max(1, count // 2)
    while count > 1 and math.gcd(step, count) != 1:
        step += 1

    sat_cycle = [0.92, 0.80, 0.68]
    val_cycle = [0.98, 0.90, 0.82]
    used_colors: set[str] = set()
    colors: List[str] = []

    for idx in range(count):
        slot = (idx * step) % count if count > 1 else 0
        hue = (offset + (slot / float(max(1, count)))) % 1.0
        sat = sat_cycle[(slot + shuffle_index) % len(sat_cycle)]
        val = val_cycle[(idx + 2 * shuffle_index) % len(val_cycle)]

        color_hex = None
        for attempt in range(18):
            local_hue = (hue + attempt / float(max(37, count * 11))) % 1.0
            local_sat = max(0.58, min(0.96, sat - 0.03 * (attempt % 3)))
            local_val = max(0.72, min(0.99, val - 0.03 * ((attempt // 3) % 3)))
            r, g, b = colorsys.hsv_to_rgb(local_hue, local_sat, local_val)
            candidate = "#{:02x}{:02x}{:02x}".format(int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))
            if candidate.lower() not in used_colors:
                color_hex = candidate
                break

        if color_hex is None:
            fallback_hue = (hue + ((idx + 1) * 0.7548776662466927)) % 1.0
            r, g, b = colorsys.hsv_to_rgb(fallback_hue, 0.74, 0.88)
            color_hex = "#{:02x}{:02x}{:02x}".format(int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))

        used_colors.add(color_hex.lower())
        colors.append(color_hex)

    return colors


def _neighborhood_cluster_labels_from_result(neighborhood_result: Any) -> List[str]:
    if not isinstance(neighborhood_result, dict):
        return []
    summary = neighborhood_result.get("cluster_summary")
    if summary is None or len(summary) == 0:
        return []
    return summary.sort_values("cluster_id")["cluster_label"].astype(str).tolist()


def sync_neighborhood_cluster_color_state(cluster_labels: Sequence[str], *, reset_colors: bool = False) -> None:
    labels = [str(label) for label in cluster_labels]
    signature = tuple(labels)
    previous_signature = tuple(st.session_state.get("neighborhood_cluster_signature", tuple()))
    shuffle_index = int(st.session_state.get("neighborhood_cluster_color_shuffle_index", 0))

    if reset_colors or signature != previous_signature:
        palette = automatic_neighborhood_cluster_color_palette(labels, shuffle_index=shuffle_index)
        previous_count = int(st.session_state.get("neighborhood_cluster_count", 0))
        for idx in range(max(previous_count, len(labels))):
            color_key = f"neighborhood_cluster_color_{idx}"
            label_key = f"neighborhood_cluster_label_{idx}"
            if idx < len(labels):
                st.session_state[label_key] = labels[idx]
                st.session_state[color_key] = palette[idx]
            else:
                st.session_state.pop(label_key, None)
                st.session_state.pop(color_key, None)
        st.session_state["neighborhood_cluster_count"] = len(labels)
        st.session_state["neighborhood_cluster_signature"] = signature


def collect_neighborhood_cluster_colors(cluster_labels: Sequence[str]) -> Dict[str, str]:
    colors: Dict[str, str] = {}
    for idx, label in enumerate(cluster_labels):
        colors[str(label)] = str(st.session_state.get(f"neighborhood_cluster_color_{idx}", "#cccccc"))
    return colors


def _sanitize_neighborhood_display_clusters(cluster_labels: Sequence[str]) -> List[str]:
    valid_labels = [str(label) for label in cluster_labels]
    valid_set = set(valid_labels)
    stored = [
        str(label)
        for label in st.session_state.get("neighborhood_display_clusters", valid_labels)
        if str(label) in valid_set
    ]
    if not stored and valid_labels:
        stored = list(valid_labels)
        st.session_state["neighborhood_display_clusters"] = list(valid_labels)
    return stored


def maybe_persist_neighborhood_outputs(
    config: PipelineConfig,
    display_cluster_labels: Sequence[str] | None = None,
) -> None:
    neighborhood_result = st.session_state.get("neighborhood_result")
    if not isinstance(neighborhood_result, dict):
        return

    cluster_labels = _neighborhood_cluster_labels_from_result(neighborhood_result)
    if cluster_labels:
        sync_neighborhood_cluster_color_state(cluster_labels)
    cluster_colors = collect_neighborhood_cluster_colors(cluster_labels) if cluster_labels else {}

    selected_display_clusters = [str(label) for label in (display_cluster_labels or cluster_labels)]
    if cluster_labels:
        selected_display_clusters = [label for label in selected_display_clusters if label in set(cluster_labels)]
        if not selected_display_clusters:
            selected_display_clusters = list(cluster_labels)

    save_signature = json.dumps(
        {
            "grid_size_um": float(neighborhood_result.get("grid_size_um", 20.0)),
            "cluster_labels": list(cluster_labels),
            "display_cluster_labels": list(selected_display_clusters),
            "cluster_colors": cluster_colors,
        },
        sort_keys=True,
    )
    if st.session_state.get("neighborhood_saved_signature") == save_signature and neighborhood_result.get("saved_paths"):
        return

    old_result = st.session_state.get("neighborhood_result")
    if isinstance(old_result, dict):
        _close_result_figures(old_result)

    payload = save_neighborhood_analysis_outputs(
        result=neighborhood_result,
        save_dir=get_section_output_dir(config, 'neighborhood_analysis'),
        pixel_size_um=config.pixel_size_um,
        cluster_colors=cluster_colors,
        display_cluster_labels=selected_display_clusters,
        save_outputs=True,
    )
    neighborhood_result["figure"] = payload["figure"]
    neighborhood_result["saved_paths"] = payload["saved_paths"]
    neighborhood_result["cluster_colors"] = cluster_colors
    neighborhood_result["display_cluster_labels"] = list(payload.get("display_cluster_labels", selected_display_clusters))
    st.session_state["neighborhood_result"] = neighborhood_result
    st.session_state["neighborhood_saved_signature"] = save_signature
    invalidate_output_zip_cache()
    refresh_output_zip_state(config.save_dir)


def sync_uploaded_channel_state(available_files: Sequence[str], *, reset_markers: bool = False, reset_colors: bool = False) -> None:
    normalized_files = sorted(list(available_files))
    signature = tuple(normalized_files)
    previous_signature = tuple(st.session_state.get("uploaded_file_signature", tuple()))
    signature_changed = signature != previous_signature
    shuffle_index = int(st.session_state.get("channel_color_shuffle_index", 0))

    if signature_changed:
        previous_n_channels = int(st.session_state.get("n_channels", 0))
        palette = automatic_channel_color_palette(normalized_files, shuffle_index=shuffle_index)
        for idx in range(max(previous_n_channels, len(normalized_files))):
            file_key = f"channel_file_{idx}"
            marker_key = f"channel_marker_{idx}"
            color_key = f"channel_color_{idx}"
            display_key = f"channel_file_display_{idx}"

            if idx < len(normalized_files):
                file_name = normalized_files[idx]
                st.session_state[file_key] = file_name
                st.session_state[marker_key] = Path(file_name).stem
                st.session_state[color_key] = palette[idx]
                st.session_state[display_key] = file_name
            else:
                st.session_state.pop(file_key, None)
                st.session_state.pop(marker_key, None)
                st.session_state.pop(color_key, None)
                st.session_state.pop(display_key, None)

        st.session_state["n_channels"] = len(normalized_files)
        st.session_state["uploaded_file_signature"] = signature

    if reset_markers:
        for idx, file_name in enumerate(normalized_files):
            st.session_state[f"channel_marker_{idx}"] = Path(file_name).stem

    if reset_colors:
        palette = automatic_channel_color_palette(normalized_files, shuffle_index=shuffle_index)
        for idx, _file_name in enumerate(normalized_files):
            st.session_state[f"channel_color_{idx}"] = palette[idx]


def invalidate_after_config_change() -> None:
    invalidate_output_zip_cache()
    st.session_state["neighborhood_saved_signature"] = None
    clear_state_keys(
        [
            "data_result",
            "nuclei_result",
            "nuclei_scan_result",
            "nuclei_auto_scan_result",
            "nuclei_scan_signature",
            "assignment_param_scan_result",
            "assignment_param_scan_signature",
            "assignment_result",
            "neighborhood_result",
            "region_result",
            "region_manual_roi_result",
            "region_integrated_result",
            "cell_distribution_region_masks_result",
            "cell_distribution_density_result",
            "cell_distribution_cluster_result",
            "nn_result",
            "boundary_result",
        ]
    )
    st.session_state["nuclei_scan_pending"] = True
    st.session_state["nuclei_scan_initialized_channel"] = None
    st.session_state["last_applied_nuclei_scan_combo"] = None
    st.session_state["assignment_param_scan_initialized_cfg_signature"] = None
    st.session_state["last_applied_assignment_param_combo"] = None


def invalidate_after_nuclei_change() -> None:
    invalidate_output_zip_cache()
    st.session_state["neighborhood_saved_signature"] = None
    clear_state_keys(["assignment_param_scan_result", "assignment_param_scan_signature", "assignment_result", "neighborhood_result", "region_result", "region_manual_roi_result", "region_integrated_result", "cell_distribution_region_masks_result", "cell_distribution_density_result", "cell_distribution_cluster_result", "nn_result", "boundary_result"])
    st.session_state["last_applied_assignment_param_combo"] = None


def invalidate_after_celltypes_change() -> None:
    invalidate_output_zip_cache()
    st.session_state["neighborhood_saved_signature"] = None
    clear_state_keys(["assignment_param_scan_result", "assignment_param_scan_signature", "assignment_result", "neighborhood_result", "region_result", "region_manual_roi_result", "region_integrated_result", "cell_distribution_region_masks_result", "cell_distribution_density_result", "cell_distribution_cluster_result", "nn_result", "boundary_result"])
    st.session_state["assignment_param_scan_initialized_cfg_signature"] = None
    st.session_state["last_applied_assignment_param_combo"] = None


def invalidate_after_assignment_change() -> None:
    invalidate_output_zip_cache()
    st.session_state["neighborhood_saved_signature"] = None
    clear_state_keys(["neighborhood_result", "region_result", "region_manual_roi_result", "region_integrated_result", "cell_distribution_region_masks_result", "cell_distribution_density_result", "cell_distribution_cluster_result", "nn_result", "boundary_result"])


def generate_distinct_celltype_color(existing_colors: Sequence[str]) -> str:
    used = {str(color).lower() for color in existing_colors if str(color).strip()}
    candidate_index = 0
    while True:
        hue = (0.61803398875 * (len(used) + candidate_index + 1)) % 1.0
        sat_cycle = [0.68, 0.74, 0.80]
        val_cycle = [0.92, 0.86, 0.78, 0.88]
        sat = sat_cycle[(len(used) + candidate_index) % len(sat_cycle)]
        val = val_cycle[(len(used) + candidate_index) % len(val_cycle)]
        r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
        color = "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))
        if color.lower() not in used:
            return color
        candidate_index += 1


def make_celltype_item(index: int, existing_colors: Sequence[str] | None = None) -> Dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:8],
        "default_name": f"celltype_{index + 1}",
        "default_color": generate_distinct_celltype_color(existing_colors or []),
    }


def ensure_celltype_items() -> None:
    if not st.session_state["celltype_items"]:
        st.session_state["celltype_items"] = [make_celltype_item(0, [])]


def add_celltype_item() -> None:
    items = list(st.session_state["celltype_items"])
    existing_colors = [
        st.session_state.get(f"ct_color_{item['id']}") or item.get("default_color")
        for item in items
    ]
    items.append(make_celltype_item(len(items), existing_colors))
    st.session_state["celltype_items"] = items


def remove_celltype_item(item_id: str) -> None:
    items = [item for item in st.session_state["celltype_items"] if item["id"] != item_id]
    if not items:
        items = [make_celltype_item(0, [])]
    st.session_state["celltype_items"] = items


def move_celltype_item(item_id: str, delta: int) -> None:
    items = list(st.session_state["celltype_items"])
    idx = next((i for i, item in enumerate(items) if item["id"] == item_id), None)
    if idx is None:
        return
    new_idx = idx + delta
    if new_idx < 0 or new_idx >= len(items):
        return
    items[idx], items[new_idx] = items[new_idx], items[idx]
    st.session_state["celltype_items"] = items


def format_any_groups_text(groups: Sequence[Sequence[str]]) -> str:
    return "; ".join([", ".join([str(marker) for marker in group if str(marker).strip()]) for group in groups if group])


def parse_any_groups_text(raw_text: str, marker_choices: Sequence[str]) -> List[List[str]]:
    marker_lookup = {str(marker).strip().lower(): str(marker) for marker in marker_choices}
    groups: List[List[str]] = []
    raw = str(raw_text or "").strip()
    if not raw:
        return groups
    for group_text in re.split(r";|\n", raw):
        tokens = [token.strip() for token in re.split(r"\||,", group_text) if token.strip()]
        group: List[str] = []
        seen = set()
        for token in tokens:
            normalized = marker_lookup.get(token.lower(), token)
            if normalized not in marker_choices:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            group.append(normalized)
        if group:
            groups.append(group)
    return groups


def initialize_celltype_row_state_from_saved_cfg() -> None:
    cfg = st.session_state.get("celltype_cfg")
    items = st.session_state.get("celltype_items", [])
    if not cfg or not items or len(items) != len(cfg):
        return
    for item, ct in zip(items, cfg):
        uid = item["id"]
        st.session_state.setdefault(f"ct_name_{uid}", ct.get("name", item.get("default_name", f"celltype_{uid}")))
        st.session_state.setdefault(f"ct_color_{uid}", ct.get("color_hex", item.get("default_color", "#ffffff")))
        st.session_state.setdefault(f"ct_all_pos_{uid}", list(ct.get("all_pos", [])))
        st.session_state.setdefault(f"ct_all_neg_{uid}", list(ct.get("all_neg", [])))
        groups = ct.get("any_pos_groups", [])
        if f"ct_any_group_ids_{uid}" not in st.session_state:
            if not groups:
                st.session_state[f"ct_any_group_ids_{uid}"] = []
            else:
                group_ids = []
                for group in groups:
                    group_id = uuid.uuid4().hex[:8]
                    group_ids.append(group_id)
                    st.session_state[f"ct_any_group_{uid}_{group_id}"] = list(group)
                st.session_state[f"ct_any_group_ids_{uid}"] = group_ids


def get_any_group_ids(uid: str) -> List[str]:
    return list(st.session_state.get(f"ct_any_group_ids_{uid}", []))


def ensure_any_group_slots(uid: str, min_groups: int = 1) -> List[str]:
    group_ids = get_any_group_ids(uid)
    changed = False
    while len(group_ids) < max(0, int(min_groups)):
        new_group_id = uuid.uuid4().hex[:8]
        group_ids.append(new_group_id)
        st.session_state.setdefault(f"ct_any_group_{uid}_{new_group_id}", [])
        changed = True
    if changed:
        st.session_state[f"ct_any_group_ids_{uid}"] = group_ids
    return list(group_ids)


def add_any_group(uid: str) -> None:
    group_ids = get_any_group_ids(uid)
    new_group_id = uuid.uuid4().hex[:8]
    group_ids.append(new_group_id)
    st.session_state[f"ct_any_group_ids_{uid}"] = group_ids
    st.session_state.setdefault(f"ct_any_group_{uid}_{new_group_id}", [])


def remove_any_group(uid: str, group_id: str) -> None:
    group_ids = [gid for gid in get_any_group_ids(uid) if gid != group_id]
    st.session_state[f"ct_any_group_ids_{uid}"] = group_ids
    st.session_state.pop(f"ct_any_group_{uid}_{group_id}", None)


def get_any_groups_from_state(uid: str, marker_choices: Sequence[str]) -> List[List[str]]:
    valid = set(marker_choices)
    groups: List[List[str]] = []
    for group_id in get_any_group_ids(uid):
        raw_values = list(st.session_state.get(f"ct_any_group_{uid}_{group_id}", []))
        cleaned = [value for value in raw_values if value in valid]
        if cleaned:
            groups.append(list(dict.fromkeys(cleaned)))
    return groups


def reset_session() -> None:
    workspace = session_workspace_root()
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
    next_uploader_nonce = int(st.session_state.get("uploaded_files_widget_nonce", 0)) + 1
    keys_to_drop = list(st.session_state.keys())
    for key in keys_to_drop:
        del st.session_state[key]
    st.session_state["uploaded_files_widget_nonce"] = next_uploader_nonce
    st.rerun()


NUCLEI_PARAM_SPECS: Dict[str, Dict[str, Any]] = {
    "min_diam_um": {"label": "MIN_DIAM_UM", "widget_key": "min_diam_um_ui", "min": 0.0, "max": 50.0, "step": 0.5},
    "max_diam_um": {"label": "MAX_DIAM_UM", "widget_key": "max_diam_um_ui", "min": 0.0, "max": 300.0, "step": 1.0},
    "tophat_radius_um": {"label": "TOPHAT_RADIUS_UM", "widget_key": "tophat_radius_um_ui", "min": 0.0, "max": 25.0, "step": 0.5},
    "gauss_sigma_um": {"label": "GAUSS_SIGMA_UM", "widget_key": "gauss_sigma_um_ui", "min": 0.0, "max": 10.0, "step": 0.1},
    "local_win_um": {"label": "LOCAL_WIN_UM", "widget_key": "local_win_um_ui", "min": 3.0, "max": 250.0, "step": 1.0},
    "local_offset": {"label": "LOCAL_OFFSET", "widget_key": "local_offset_ui", "min": -1.0, "max": 1.0, "step": 0.01},
    "h_maxima_um": {"label": "H_MAXIMA_UM", "widget_key": "h_maxima_um_ui", "min": 0.0, "max": 20.0, "step": 0.05},
    "seed_min_dist_um": {"label": "SEED_MIN_DIST_UM", "widget_key": "seed_min_dist_um_ui", "min": 0.0, "max": 50.0, "step": 0.1},
    "watershed_compactness": {"label": "WATERSHED_COMPACTNESS", "widget_key": "watershed_compactness_ui", "min": 0.0, "max": 10.0, "step": 0.05},
    "post_resplit_mult": {"label": "POST_RESPLIT_MULT", "widget_key": "post_resplit_mult_ui", "min": 0.0, "max": 10.0, "step": 0.05},
}

NUCLEI_OPTIMIZER_CORE_PARAM_SPECS: Dict[str, Dict[str, float]] = {
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

NUCLEI_SCAN_TEXT_KEYS: Dict[str, str] = {field: f"nuclei_scan_values_{field}" for field in SWEEP_PARAM_ORDER}


def ensure_nuclei_widget_defaults(channel_names: Sequence[str], default_nucleus: str | None = None) -> None:
    nucleus_channel = st.session_state.get("nucleus_channel_ui")
    if nucleus_channel not in channel_names:
        nucleus_channel = default_nucleus or (channel_names[0] if channel_names else None)
        if nucleus_channel is not None:
            st.session_state["nucleus_channel_ui"] = nucleus_channel

    if st.session_state.get("nucleus_channel_ui") is None:
        return

    default_params = NucleiParams(nucleus_channel=str(st.session_state["nucleus_channel_ui"]))
    for field, spec in NUCLEI_PARAM_SPECS.items():
        widget_key = spec["widget_key"]
        if widget_key not in st.session_state:
            st.session_state[widget_key] = getattr(default_params, field)


def _format_float(value: float) -> str:
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def _clip_param_value(field: str, value: float) -> float:
    spec = NUCLEI_PARAM_SPECS[field]
    step = float(spec["step"])
    clipped = min(max(float(value), float(spec["min"])), float(spec["max"]))
    if step >= 1:
        return float(round(clipped))
    decimals = max(0, len(str(step).split(".")[-1].rstrip("0")))
    return float(round(round(clipped / step) * step, decimals + 1))


def _unique_preserve_order(values: Sequence[float]) -> List[float]:
    out: List[float] = []
    seen = set()
    for value in values:
        key = round(float(value), 6)
        if key in seen:
            continue
        seen.add(key)
        out.append(float(value))
    return out


def current_nuclei_params_from_widgets() -> NucleiParams:
    return NucleiParams(
        nucleus_channel=st.session_state["nucleus_channel_ui"],
        min_diam_um=float(st.session_state["min_diam_um_ui"]),
        max_diam_um=float(st.session_state["max_diam_um_ui"]),
        tophat_radius_um=float(st.session_state["tophat_radius_um_ui"]),
        gauss_sigma_um=float(st.session_state["gauss_sigma_um_ui"]),
        local_win_um=float(st.session_state["local_win_um_ui"]),
        local_offset=float(st.session_state["local_offset_ui"]),
        h_maxima_um=float(st.session_state["h_maxima_um_ui"]),
        seed_min_dist_um=float(st.session_state["seed_min_dist_um_ui"]),
        watershed_compactness=float(st.session_state["watershed_compactness_ui"]),
        post_resplit_mult=float(st.session_state["post_resplit_mult_ui"]),
    )


def default_scan_values_for_field(field: str, current_value: float) -> List[float]:
    current_value = float(current_value)
    if field == "min_diam_um":
        values = [current_value - 1.0, current_value, current_value + 1.0]
    elif field == "local_offset":
        values = [current_value - 0.03, current_value, current_value + 0.03]
    elif field == "h_maxima_um":
        values = [max(0.05, current_value * 0.5), current_value, current_value * 1.5]
    else:
        values = [current_value]
    return _unique_preserve_order([_clip_param_value(field, value) for value in values])


def count_scan_combinations(candidates: Dict[str, Sequence[float]]) -> int:
    total = 1
    for field in SWEEP_PARAM_ORDER:
        total *= max(1, len(candidates.get(field, [])))
    return int(total)


def make_nuclei_candidate_summary_df(candidates: Dict[str, Sequence[float]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for field in SWEEP_PARAM_ORDER:
        values = [float(value) for value in candidates.get(field, [])]
        rows.append(
            {
                "parameter": SWEEP_PARAM_LABELS[field],
                "n_values": int(len(values)),
                "candidate_values": ", ".join(_format_float(value) for value in values),
            }
        )
    return pd.DataFrame(rows)


def nuclei_optimizer_search_space_specs() -> Dict[str, Dict[str, float]]:
    return {
        field: {
            "min": float(NUCLEI_PARAM_SPECS[field]["min"]),
            "max": float(NUCLEI_PARAM_SPECS[field]["max"]),
            "step": float(NUCLEI_PARAM_SPECS[field]["step"]),
        }
        for field in SWEEP_PARAM_ORDER
    }


def nuclei_optimizer_core_search_space_specs() -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for field in SWEEP_PARAM_ORDER:
        current_spec = NUCLEI_PARAM_SPECS[field]
        core_spec = NUCLEI_OPTIMIZER_CORE_PARAM_SPECS[field]
        out[field] = {
            "min": max(float(current_spec["min"]), float(core_spec["min"])),
            "max": min(float(current_spec["max"]), float(core_spec["max"])),
            "step": float(current_spec["step"]),
        }
    return out


def count_nuclei_search_space_combinations(search_specs: Dict[str, Dict[str, float]] | None = None) -> int:
    specs = search_specs or nuclei_optimizer_search_space_specs()
    total = 1
    for field in SWEEP_PARAM_ORDER:
        spec = specs[field]
        n_values = int(round((float(spec["max"]) - float(spec["min"])) / float(spec["step"]))) + 1
        total *= max(1, n_values)
    return int(total)


def make_nuclei_search_space_summary_df(search_specs: Dict[str, Dict[str, float]] | None = None) -> pd.DataFrame:
    specs = search_specs or nuclei_optimizer_search_space_specs()
    rows: List[Dict[str, Any]] = []
    for field in SWEEP_PARAM_ORDER:
        spec = specs[field]
        n_values = int(round((float(spec["max"]) - float(spec["min"])) / float(spec["step"]))) + 1
        rows.append(
            {
                "parameter": SWEEP_PARAM_LABELS[field],
                "min": _format_float(float(spec["min"])),
                "max": _format_float(float(spec["max"])),
                "step": _format_float(float(spec["step"])),
                "n_values": int(n_values),
            }
        )
    return pd.DataFrame(rows)


def set_default_nuclei_scan_candidates_from_widgets(force: bool = False) -> None:
    params = current_nuclei_params_from_widgets()
    for field in SWEEP_PARAM_ORDER:
        text_key = NUCLEI_SCAN_TEXT_KEYS[field]
        if force or text_key not in st.session_state or not str(st.session_state.get(text_key, "")).strip():
            values = default_scan_values_for_field(field, getattr(params, field))
            st.session_state[text_key] = ", ".join(_format_float(value) for value in values)


def parse_scan_values_from_state() -> Dict[str, List[float]]:
    params = current_nuclei_params_from_widgets()
    out: Dict[str, List[float]] = {}
    for field in SWEEP_PARAM_ORDER:
        text_key = NUCLEI_SCAN_TEXT_KEYS[field]
        raw = str(st.session_state.get(text_key, "") or "")
        tokens = [token.strip() for token in raw.replace(";", ",").replace("\n", ",").split(",") if token.strip()]
        values: List[float] = []
        for token in tokens:
            try:
                values.append(_clip_param_value(field, float(token)))
            except Exception:
                continue
        if not values:
            values = [_clip_param_value(field, getattr(params, field))]
            st.session_state[text_key] = _format_float(values[0])
        out[field] = _unique_preserve_order(values)
    return out


def nuclei_scan_signature(config: PipelineConfig, candidates: Dict[str, List[float]], nucleus_channel: str) -> str:
    payload = {
        "session_id": st.session_state.get("session_id"),
        "folder": str(config.folder),
        "save_dir": str(config.save_dir),
        "nucleus_channel": nucleus_channel,
        "candidates": {field: [float(v) for v in candidates[field]] for field in SWEEP_PARAM_ORDER},
    }
    return json.dumps(payload, sort_keys=True)


def run_current_nuclei_segmentation(config: PipelineConfig) -> Dict[str, Any]:
    ensure_pixels_loaded()
    data_result = st.session_state["data_result"]
    params = current_nuclei_params_from_widgets()
    cpu_percent = int(st.session_state.get("single_seg_cpu_percent_ui", 75) or 75)
    cpu_percent = max(10, min(100, cpu_percent))
    native_threads = max(1, min(CPU_COUNT, int(round(CPU_COUNT * cpu_percent / 100.0))))
    nuclei_save_dir = get_section_output_dir(config, 'nuclei')
    return run_nuclei_segmentation(
        df_pixels=data_result["df_pixels"],
        shapes=data_result["shapes"],
        image_id=config.image_id,
        save_dir=nuclei_save_dir,
        pixel_size_um=config.pixel_size_um,
        params=params,
        save_outputs=bool(st.session_state["save_nuclei_outputs_ui"]),
        native_threads=native_threads,
    )


def get_scan_parallel_config(
    cpu_percent_key: str = "scan_cpu_percent_ui",
    state_prefix: str = "scan",
) -> Dict[str, int | str]:
    cpu_percent = int(st.session_state.get(cpu_percent_key, 75) or 75)
    cpu_percent = max(10, min(100, cpu_percent))
    parallel_workers = max(1, min(CPU_COUNT, int(round(CPU_COUNT * cpu_percent / 100.0))))
    parallel_backend = "threading" if os.name == "nt" else "loky"
    st.session_state[f"{state_prefix}_parallel_workers_ui"] = parallel_workers
    st.session_state[f"{state_prefix}_backend_ui"] = parallel_backend
    st.session_state[f"{state_prefix}_threads_per_worker_ui"] = 1
    return {
        "cpu_percent": int(cpu_percent),
        "parallel_workers": int(parallel_workers),
        "parallel_backend": parallel_backend,
        "native_threads_per_worker": 1,
    }


def run_current_nuclei_parameter_scan(
    config: PipelineConfig,
    candidate_values: Dict[str, List[float]],
    *,
    cpu_percent_key: str = "scan_cpu_percent_ui",
    state_prefix: str = "scan",
    output_prefix: str = "nuclei_parameter_sweep",
) -> Dict[str, Any]:
    ensure_pixels_loaded()
    data_result = st.session_state["data_result"]
    params = current_nuclei_params_from_widgets()
    scan_parallel_config = get_scan_parallel_config(cpu_percent_key=cpu_percent_key, state_prefix=state_prefix)
    nuclei_save_dir = get_section_output_dir(config, 'nuclei')

    return run_nuclei_parameter_sweep(
        df_pixels=data_result["df_pixels"],
        shapes=data_result["shapes"],
        image_id=config.image_id,
        save_dir=nuclei_save_dir,
        pixel_size_um=config.pixel_size_um,
        base_params=params,
        sweep_values=candidate_values,
        save_outputs=True,
        parallel_workers=int(scan_parallel_config["parallel_workers"]),
        parallel_backend=str(scan_parallel_config["parallel_backend"]),
        native_threads_per_worker=int(scan_parallel_config["native_threads_per_worker"]),
        output_prefix=output_prefix,
    )


def run_current_nuclei_parameter_optimizer(
    config: PipelineConfig,
    *,
    cpu_percent_key: str = "auto_scan_cpu_percent_ui",
    state_prefix: str = "auto_scan",
    output_prefix: str = "nuclei_auto_optimizer",
) -> Dict[str, Any]:
    ensure_pixels_loaded()
    data_result = st.session_state["data_result"]
    params = current_nuclei_params_from_widgets()
    scan_parallel_config = get_scan_parallel_config(cpu_percent_key=cpu_percent_key, state_prefix=state_prefix)
    nuclei_save_dir = get_section_output_dir(config, 'nuclei')

    parallel_workers = int(scan_parallel_config["parallel_workers"])
    core_target_evaluations = max(256, min(2048, parallel_workers * 32))
    expansion_target_evaluations = max(192, min(1536, parallel_workers * 24))
    exhaustive_limit = max(4096, core_target_evaluations + expansion_target_evaluations)

    return run_nuclei_parameter_optimizer(
        df_pixels=data_result["df_pixels"],
        shapes=data_result["shapes"],
        image_id=config.image_id,
        save_dir=nuclei_save_dir,
        pixel_size_um=config.pixel_size_um,
        base_params=params,
        search_specs=nuclei_optimizer_search_space_specs(),
        priority_search_specs=nuclei_optimizer_core_search_space_specs(),
        save_outputs=True,
        max_evaluations=int(core_target_evaluations),
        priority_target_evaluations=int(core_target_evaluations),
        expansion_target_evaluations=int(expansion_target_evaluations),
        exhaustive_limit=int(exhaustive_limit),
        parallel_workers=parallel_workers,
        parallel_backend=str(scan_parallel_config["parallel_backend"]),
        native_threads_per_worker=int(scan_parallel_config["native_threads_per_worker"]),
        output_prefix=output_prefix,
        use_vertical_band_subset=True,
        vertical_band_count=10,
        vertical_band_selection_count=5,
    )


def stage_scan_result_row_for_widget_update(row: pd.Series, auto_run: bool = True) -> None:
    payload = {
        "combo_index": int(row["combo_index"]),
        "nucleus_channel": str(row["nucleus_channel"]),
        "params": {field: float(row[SWEEP_PARAM_LABELS[field]]) for field in SWEEP_PARAM_ORDER},
    }
    st.session_state["pending_nuclei_scan_selection"] = payload
    st.session_state["pending_nuclei_scan_auto_run"] = bool(auto_run)


def consume_pending_scan_widget_update(config: PipelineConfig) -> None:
    payload = st.session_state.get("pending_nuclei_scan_selection")
    if not payload:
        return

    st.session_state["nucleus_channel_ui"] = str(payload["nucleus_channel"])
    for field in SWEEP_PARAM_ORDER:
        widget_key = NUCLEI_PARAM_SPECS[field]["widget_key"]
        st.session_state[widget_key] = float(payload["params"][field])

    combo_index = int(payload["combo_index"])
    st.session_state["last_applied_nuclei_scan_combo"] = combo_index
    st.session_state["pending_nuclei_scan_selection"] = None

    auto_run = bool(st.session_state.get("pending_nuclei_scan_auto_run", False))
    st.session_state["pending_nuclei_scan_auto_run"] = False

    if auto_run:
        try:
            with st.spinner(f"Applying sweep combo #{combo_index} and running nuclei segmentation..."):
                result = run_current_nuclei_segmentation(config)
                _close_result_figures(st.session_state.get("nuclei_result"))
                st.session_state["nuclei_result"] = result
                invalidate_after_nuclei_change()
                refresh_output_zip_state(config.save_dir)
            st.session_state["nuclei_scan_notice"] = (
                f"Applied sweep combo #{combo_index} to the nuclei parameters and ran segmentation. "
                f"Outputs were written to {get_section_output_dir(config, 'nuclei')}."
            )
        except Exception as exc:
            st.session_state["nuclei_scan_notice"] = (
                f"Applied sweep combo #{combo_index}, but segmentation failed: {exc}"
            )
    else:
        st.session_state["nuclei_scan_notice"] = (
            f"Applied sweep combo #{combo_index} to the nuclei parameter boxes. "
            "You can now manually edit any parameter. "
            "Run nuclei segmentation always uses the current parameter boxes, so manual edits override the applied combo values."
        )



def make_scan_hover_text(row: pd.Series) -> str:
    lines = [
        f"Combo #{int(row['combo_index'])}",
        f"n_nuclei: {int(row['n_nuclei'])}",
    ]
    for field in SWEEP_PARAM_ORDER:
        label = SWEEP_PARAM_LABELS[field]
        lines.append(f"{label}: {_format_float(row[label])}")
    return "<br>".join(lines)


def build_nuclei_scan_plotly(df_results: pd.DataFrame):
    plot_df = df_results[df_results["error"].fillna("") == ""].copy()
    plot_df = plot_df.sort_values("combo_index").reset_index(drop=True)
    fig = go.Figure()
    if len(plot_df) > 0:
        fig.add_trace(
            go.Scatter(
                x=plot_df["combo_index"],
                y=plot_df["n_nuclei"],
                mode="lines+markers",
                hovertext=[make_scan_hover_text(row) for _, row in plot_df.iterrows()],
                hovertemplate="%{hovertext}<extra></extra>",
                customdata=plot_df[["combo_index"]].to_numpy(),
            )
        )
    fig.update_layout(
        title="Nuclei parameter scan",
        xaxis_title="Parameter combination index",
        yaxis_title="Segmented nuclei count",
        dragmode="select",
        height=420,
        margin=dict(l=40, r=20, t=50, b=40),
    )
    return fig, plot_df


def get_successful_scan_results(df_results: pd.DataFrame) -> pd.DataFrame:
    ranked = df_results.copy()
    if "error" in ranked.columns:
        ranked = ranked[ranked["error"].fillna("") == ""].copy()
    if len(ranked) == 0:
        return ranked
    sort_cols = ["n_nuclei", "positive_pixel_fraction", "mean_pixels_per_nucleus", "combo_index"]
    ascending = [False, False, False, True]
    available_cols = [col for col in sort_cols if col in ranked.columns]
    ascending = ascending[: len(available_cols)]
    return ranked.sort_values(available_cols, ascending=ascending).reset_index(drop=True)


def get_ranked_scan_results_table(df_results: pd.DataFrame) -> pd.DataFrame:
    ranked = get_successful_scan_results(df_results).copy()
    if len(ranked) == 0:
        return ranked
    ranked.insert(0, "rank", np.arange(1, len(ranked) + 1))
    return ranked


def get_scan_row_by_combo_index(df_results: pd.DataFrame, combo_index: int) -> pd.Series | None:
    match = df_results[df_results["combo_index"].astype(int) == int(combo_index)]
    if len(match) == 0:
        return None
    return match.iloc[0]


def format_scan_combo_summary(row: pd.Series) -> str:
    summary_parts = [
        f"#{int(row['combo_index'])}",
        f"nuclei={int(row['n_nuclei'])}",
    ]
    summary_parts.extend(
        f"{SWEEP_PARAM_LABELS[field]}={_format_float(row[SWEEP_PARAM_LABELS[field]])}"
        for field in SWEEP_PARAM_ORDER
    )
    return " | ".join(summary_parts)


def render_nuclei_scan_results_ui(
    *,
    scan_result: Dict[str, Any] | None,
    selection_key: str,
    apply_button_key: str,
    results_heading: str,
    recommendation_label: str,
    saved_outputs_caption: str,
    apply_caption: str,
) -> None:
    if scan_result is None:
        return

    plot_fig, _ = build_nuclei_scan_plotly(scan_result["results"])
    st.plotly_chart(
        plot_fig,
        key=f"{selection_key}_plot",
        use_container_width=True,
        config={"displaylogo": False},
    )

    st.markdown(results_heading)
    ranked_results_df = get_ranked_scan_results_table(scan_result["results"])
    if len(ranked_results_df) > 0:
        st.dataframe(ranked_results_df, use_container_width=True, height=360)
    else:
        st.dataframe(scan_result["results"], use_container_width=True, height=360)

    if "error" in scan_result["results"].columns:
        failed_count = int((scan_result["results"]["error"].fillna("") != "").sum())
        if failed_count > 0:
            st.caption(
                f"{failed_count} combination(s) failed validation or segmentation and are omitted from the ranked table."
            )
    st.caption(saved_outputs_caption)

    best_row = recommend_nuclei_parameter_sweep_result(scan_result["results"])
    if best_row is not None:
        best_combo_index = int(best_row["combo_index"])
        current_combo_choice = int(st.session_state.get(selection_key, 0) or 0)
        valid_combo_indices = set(ranked_results_df["combo_index"].astype(int).tolist())
        if current_combo_choice not in valid_combo_indices:
            st.session_state[selection_key] = best_combo_index
        st.info(f"{recommendation_label}: {format_scan_combo_summary(best_row)}")
    else:
        st.warning("This scan did not produce any successful combination to recommend.")

    if len(ranked_results_df) == 0:
        return

    st.markdown("#### Apply scan result to final run")
    apply_cols = st.columns([1.5, 1.5, 4.0])
    with apply_cols[0]:
        combo_max = int(scan_result["results"]["combo_index"].max()) if len(scan_result["results"]) > 0 else 0
        st.number_input(
            "Combo #",
            min_value=0,
            max_value=max(0, combo_max),
            step=1,
            key=selection_key,
            help="Type the scan combo number you want to copy into the final-run parameter boxes below.",
        )
    with apply_cols[1]:
        apply_scan_clicked = st.button(
            "Apply to final run",
            key=apply_button_key,
            help="Copy the selected scan combination into the final-run parameter boxes below.",
        )
    with apply_cols[2]:
        st.caption(apply_caption)

    selected_combo_index = int(st.session_state.get(selection_key, 0) or 0)
    if apply_scan_clicked:
        if selected_combo_index <= 0:
            st.info("Choose a combo number greater than 0 to apply a scanned combination to the final-run nuclei parameters.")
        else:
            selected_row = get_scan_row_by_combo_index(scan_result["results"], selected_combo_index)
            if selected_row is None:
                st.error(f"Combination #{selected_combo_index} was not found in the current scan results.")
            elif str(selected_row.get("error", "") or "").strip():
                st.error(
                    f"Combination #{selected_combo_index} failed during the scan and cannot be applied: {selected_row['error']}"
                )
            else:
                stage_scan_result_row_for_widget_update(selected_row, auto_run=False)
                st.rerun()


def maybe_show_nuclei_scan_notice() -> None:
    notice = str(st.session_state.get("nuclei_scan_notice") or "").strip()
    if notice:
        st.success(notice)
        st.session_state["nuclei_scan_notice"] = ""


CELLTYPE_ASSIGN_PARAM_SPECS: Dict[str, Dict[str, Any]] = {
    "r_voronoi_um": {"label": "R_VORONOI_UM", "widget_key": "ct_assign_r_voronoi_um_ui", "min": 0.0, "max": 20.0, "step": 0.5, "kind": "float"},
    "r_buffer_um": {"label": "R_BUFFER_UM", "widget_key": "ct_assign_r_buffer_um_ui", "min": 0.0, "max": 20.0, "step": 0.5, "kind": "float"},
    "r_vote_um": {"label": "R_VOTE_UM", "widget_key": "ct_assign_r_vote_um_ui", "min": 0.0, "max": 20.0, "step": 0.5, "kind": "float"},
    "tophat_r_um": {"label": "TOPHAT_R_UM", "widget_key": "ct_assign_tophat_r_um_ui", "min": 0.0, "max": 8.0, "step": 0.5, "kind": "float"},
    "gauss_sigma_um": {"label": "GAUSS_SIGMA_UM", "widget_key": "ct_assign_gauss_sigma_um_ui", "min": 0.0, "max": 3.0, "step": 0.1, "kind": "float"},
    "thresh_mode": {"label": "THRESH_MODE", "widget_key": "ct_assign_thresh_mode_ui", "kind": "choice", "options": ["global_otsu", "yen", "triangle"]},
    "min_pos_object_size_px": {"label": "MIN_POS_OBJECT_SIZE_PX", "widget_key": "ct_assign_min_pos_object_size_px_ui", "min": 0, "max": 200, "step": 1, "kind": "int"},
    "min_pos_pix": {"label": "MIN_POS_PIX", "widget_key": "ct_assign_min_pos_pix_ui", "min": 0, "max": 200, "step": 1, "kind": "int"},
}

CELLTYPE_ASSIGN_OPTIMIZER_PARAM_SPECS: Dict[str, Dict[str, Any]] = {
    **{field: dict(spec) for field, spec in CELLTYPE_ASSIGN_PARAM_SPECS.items()},
    "resolve_ambiguous": {
        "label": "RESOLVE_AMBIGUOUS",
        "widget_key": "ct_assign_resolve_ambiguous_ui",
        "kind": "bool",
        "options": [True, False],
    },
    "ambiguous_min_probability": {
        "label": "AMBIGUOUS_MIN_PROBABILITY",
        "widget_key": "ct_assign_ambiguous_min_probability_ui",
        "min": 0.00,
        "max": 0.99,
        "step": 0.01,
        "kind": "float",
    },
    "ambiguous_min_gap": {
        "label": "AMBIGUOUS_MIN_GAP",
        "widget_key": "ct_assign_ambiguous_min_gap_ui",
        "min": 0.00,
        "max": 0.50,
        "step": 0.01,
        "kind": "float",
    },
}

CELLTYPE_ASSIGN_OPTIMIZER_CORE_PARAM_SPECS: Dict[str, Dict[str, Any]] = {
    "r_voronoi_um": {"kind": "float", "min": 0.0, "max": 10.0, "step": 0.5},
    "r_buffer_um": {"kind": "float", "min": 0.0, "max": 8.0, "step": 0.5},
    "r_vote_um": {"kind": "float", "min": 0.0, "max": 10.0, "step": 0.5},
    "tophat_r_um": {"kind": "float", "min": 0.0, "max": 4.0, "step": 0.5},
    "gauss_sigma_um": {"kind": "float", "min": 0.0, "max": 1.5, "step": 0.1},
    "thresh_mode": {"kind": "choice", "options": ["global_otsu", "yen", "triangle"]},
    "min_pos_object_size_px": {"kind": "int", "min": 0, "max": 80, "step": 1},
    "min_pos_pix": {"kind": "int", "min": 0, "max": 40, "step": 1},
    "resolve_ambiguous": {"kind": "bool", "options": [True]},
    "ambiguous_min_probability": {"kind": "float", "min": 0.00, "max": 0.80, "step": 0.01},
    "ambiguous_min_gap": {"kind": "float", "min": 0.00, "max": 0.20, "step": 0.01},
}

CELLTYPE_ASSIGN_SCAN_TEXT_KEYS: Dict[str, str] = {
    field: f"celltype_assignment_scan_values_{field}"
    for field in CELLTYPE_PARAM_ORDER
    if CELLTYPE_ASSIGN_PARAM_SPECS[field]["kind"] != "choice"
}
CELLTYPE_ASSIGN_SCAN_CHOICE_KEY = "celltype_assignment_scan_choices_thresh_mode"

THRESH_MODE_HELP_MD = """
**THRESH_MODE guide**

- `global_otsu`: one global threshold from the whole marker image. Usually the best default.
- `yen`: usually a stricter threshold for bright sparse signal. Often returns fewer positive pixels.
- `triangle`: often useful when the intensity histogram is strongly skewed, with sparse bright objects on dark background.
"""


def _clip_celltype_assignment_param_value(field: str, value: float | int) -> float | int:
    spec = CELLTYPE_ASSIGN_PARAM_SPECS[field]
    kind = spec["kind"]
    if kind == "choice":
        return str(value)
    clipped = min(max(float(value), float(spec["min"])), float(spec["max"]))
    step = float(spec["step"])
    if kind == "int" or step >= 1:
        return int(round(clipped))
    decimals = max(0, len(str(step).split(".")[-1].rstrip("0")))
    return float(round(round(clipped / step) * step, decimals + 1))



def current_celltype_assignment_params_from_widgets() -> CelltypeAssignmentParams:
    payload: Dict[str, Any] = {}
    for field in CELLTYPE_PARAM_ORDER:
        spec = CELLTYPE_ASSIGN_PARAM_SPECS[field]
        widget_key = spec["widget_key"]
        value = st.session_state.get(widget_key)
        if value is None:
            continue
        if spec["kind"] == "choice":
            payload[field] = str(value)
        elif spec["kind"] == "int":
            payload[field] = int(_clip_celltype_assignment_param_value(field, int(value)))
        else:
            payload[field] = float(_clip_celltype_assignment_param_value(field, float(value)))
    payload["resolve_ambiguous"] = bool(st.session_state.get("ct_assign_resolve_ambiguous_ui", True))
    payload["ambiguous_min_probability"] = float(st.session_state.get("ct_assign_ambiguous_min_probability_ui", 0.60))
    payload["ambiguous_min_gap"] = float(st.session_state.get("ct_assign_ambiguous_min_gap_ui", 0.10))
    return CelltypeAssignmentParams(**payload)

def default_scan_values_for_celltype_assignment_field(field: str, current_value: Any) -> List[Any]:
    spec = CELLTYPE_ASSIGN_PARAM_SPECS[field]
    if spec["kind"] == "choice":
        base = [str(current_value)]
        for option in spec["options"]:
            if option not in base:
                base.append(option)
        return base[:3]

    value = float(current_value)
    if spec["kind"] == "int":
        values = [
            max(spec["min"], value - 2),
            value,
            min(spec["max"], value + 2),
        ]
        return [int(_clip_celltype_assignment_param_value(field, v)) for v in values]

    if field in {"r_voronoi_um", "r_buffer_um", "r_vote_um", "tophat_r_um", "gauss_sigma_um"}:
        values = [value * 0.5, value, value * 1.5] if value > 0 else [0.0, spec["step"], spec["step"] * 2]
    else:
        values = [value]
    return _unique_preserve_order([_clip_celltype_assignment_param_value(field, v) for v in values])


def set_default_celltype_assignment_scan_candidates_from_widgets(force: bool = False) -> None:
    params = current_celltype_assignment_params_from_widgets()
    for field in CELLTYPE_PARAM_ORDER:
        spec = CELLTYPE_ASSIGN_PARAM_SPECS[field]
        if spec["kind"] == "choice":
            if force or CELLTYPE_ASSIGN_SCAN_CHOICE_KEY not in st.session_state or not st.session_state.get(CELLTYPE_ASSIGN_SCAN_CHOICE_KEY):
                st.session_state[CELLTYPE_ASSIGN_SCAN_CHOICE_KEY] = default_scan_values_for_celltype_assignment_field(field, getattr(params, field))
        else:
            text_key = CELLTYPE_ASSIGN_SCAN_TEXT_KEYS[field]
            if force or text_key not in st.session_state or not str(st.session_state.get(text_key, "")).strip():
                values = default_scan_values_for_celltype_assignment_field(field, getattr(params, field))
                st.session_state[text_key] = ", ".join(_format_float(value) for value in values)


def parse_celltype_assignment_scan_values_from_state() -> Dict[str, List[Any]]:
    params = current_celltype_assignment_params_from_widgets()
    out: Dict[str, List[Any]] = {}
    for field in CELLTYPE_PARAM_ORDER:
        spec = CELLTYPE_ASSIGN_PARAM_SPECS[field]
        if spec["kind"] == "choice":
            values = list(dict.fromkeys([str(v) for v in st.session_state.get(CELLTYPE_ASSIGN_SCAN_CHOICE_KEY, []) if str(v).strip()]))
            if not values:
                values = [str(getattr(params, field))]
            out[field] = values
            continue

        text_key = CELLTYPE_ASSIGN_SCAN_TEXT_KEYS[field]
        raw = str(st.session_state.get(text_key, "") or "")
        tokens = [token.strip() for token in raw.replace(";", ",").replace("\n", ",").split(",") if token.strip()]
        parsed: List[Any] = []
        for token in tokens:
            try:
                if spec["kind"] == "int":
                    parsed.append(int(_clip_celltype_assignment_param_value(field, int(float(token)))))
                else:
                    parsed.append(float(_clip_celltype_assignment_param_value(field, float(token))))
            except Exception:
                continue
        if not parsed:
            parsed = [getattr(params, field)]
            st.session_state[text_key] = _format_float(parsed[0]) if spec["kind"] != "int" else str(parsed[0])
        out[field] = _unique_preserve_order(parsed)
    return out


def celltype_assignment_scan_signature(config: PipelineConfig, candidate_values: Dict[str, List[Any]]) -> str:
    payload = {
        "session_id": st.session_state.get("session_id"),
        "folder": str(config.folder),
        "save_dir": str(config.save_dir),
        "celltype_cfg": st.session_state.get("celltype_cfg"),
        "candidate_values": {field: [str(v) for v in candidate_values[field]] for field in CELLTYPE_PARAM_ORDER},
    }
    return json.dumps(payload, sort_keys=True)


def celltype_assignment_optimizer_search_space_specs() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for field in CELLTYPE_OPTIMIZER_PARAM_ORDER:
        spec = CELLTYPE_ASSIGN_OPTIMIZER_PARAM_SPECS[field]
        kind = str(spec["kind"])
        if kind in {"choice", "bool"}:
            out[field] = {
                "kind": kind,
                "options": list(spec["options"]),
            }
        else:
            out[field] = {
                "kind": kind,
                "min": spec["min"],
                "max": spec["max"],
                "step": spec["step"],
            }
    return out


def celltype_assignment_optimizer_core_search_space_specs() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for field in CELLTYPE_OPTIMIZER_PARAM_ORDER:
        full_spec = CELLTYPE_ASSIGN_OPTIMIZER_PARAM_SPECS[field]
        core_spec = CELLTYPE_ASSIGN_OPTIMIZER_CORE_PARAM_SPECS[field]
        kind = str(full_spec["kind"])
        if kind in {"choice", "bool"}:
            full_options = list(full_spec["options"])
            core_options = list(core_spec["options"])
            out[field] = {
                "kind": kind,
                "options": [option for option in full_options if option in core_options],
            }
        else:
            out[field] = {
                "kind": kind,
                "min": max(float(full_spec["min"]), float(core_spec["min"])),
                "max": min(float(full_spec["max"]), float(core_spec["max"])),
                "step": float(full_spec["step"]),
            }
    return out


def count_celltype_assignment_search_space_combinations(
    search_specs: Dict[str, Dict[str, Any]] | None = None,
) -> int:
    specs = search_specs or celltype_assignment_optimizer_search_space_specs()
    total = 1
    for field in CELLTYPE_OPTIMIZER_PARAM_ORDER:
        spec = specs[field]
        if str(spec["kind"]) in {"choice", "bool"}:
            total *= max(1, len(spec.get("options", [])))
        else:
            n_values = int(round((float(spec["max"]) - float(spec["min"])) / float(spec["step"]))) + 1
            total *= max(1, n_values)
    return int(total)


def make_celltype_assignment_search_space_summary_df(
    search_specs: Dict[str, Dict[str, Any]] | None = None,
) -> pd.DataFrame:
    specs = search_specs or celltype_assignment_optimizer_search_space_specs()
    rows: List[Dict[str, Any]] = []
    for field in CELLTYPE_OPTIMIZER_PARAM_ORDER:
        spec = specs[field]
        kind = str(spec["kind"])
        if kind in {"choice", "bool"}:
            options = list(spec.get("options", []))
            rows.append(
                {
                    "parameter": CELLTYPE_OPTIMIZER_PARAM_LABELS[field],
                    "kind": kind,
                    "range_or_options": ", ".join(str(option) for option in options),
                    "step": "",
                    "n_values": int(len(options)),
                }
            )
        else:
            n_values = int(round((float(spec["max"]) - float(spec["min"])) / float(spec["step"]))) + 1
            rows.append(
                {
                    "parameter": CELLTYPE_OPTIMIZER_PARAM_LABELS[field],
                    "kind": kind,
                    "range_or_options": f"{_format_float(float(spec['min']))} to {_format_float(float(spec['max']))}",
                    "step": _format_float(float(spec["step"])),
                    "n_values": int(n_values),
                }
            )
    return pd.DataFrame(rows)


def run_current_celltype_assignment(config: PipelineConfig) -> Dict[str, Any]:
    ensure_pixels_loaded()
    labels = ensure_labels_available()
    data_result = st.session_state["data_result"]
    params = current_celltype_assignment_params_from_widgets()
    assignment_save_dir = get_section_output_dir(config, 'celltype_assignment')
    return run_celltype_assignment(
        folder=config.folder,
        save_dir=assignment_save_dir,
        pixel_size_um=config.pixel_size_um,
        image_id=config.image_id,
        channels_cfg=[channel.to_dict() for channel in config.channels],
        celltype_cfg=st.session_state["celltype_cfg"],
        labels=labels,
        df_pixels=data_result["df_pixels"],
        shapes=data_result["shapes"],
        params=params,
        save_outputs=True,
    )


def run_current_celltype_assignment_parameter_scan(
    config: PipelineConfig,
    candidate_values: Dict[str, List[Any]],
    progress_callback=None,
) -> Dict[str, Any]:
    ensure_pixels_loaded()
    labels = ensure_labels_available()
    data_result = st.session_state["data_result"]
    params = current_celltype_assignment_params_from_widgets()
    assignment_params_save_dir = get_section_output_dir(config, 'celltype_assignment_parameters')
    return run_celltype_assignment_parameter_sweep(
        folder=config.folder,
        save_dir=assignment_params_save_dir,
        pixel_size_um=config.pixel_size_um,
        image_id=config.image_id,
        channels_cfg=[channel.to_dict() for channel in config.channels],
        celltype_cfg=st.session_state["celltype_cfg"],
        labels=labels,
        df_pixels=data_result["df_pixels"],
        shapes=data_result["shapes"],
        base_params=params,
        sweep_values=candidate_values,
        save_outputs=True,
        parallel_workers=1,
        progress_callback=progress_callback,
    )


def run_current_celltype_assignment_parameter_optimizer(
    config: PipelineConfig,
    *,
    cpu_percent_key: str = "assignment_auto_scan_cpu_percent_ui",
    state_prefix: str = "assignment_auto_scan",
    output_prefix: str = "celltype_assignment_auto_optimizer",
) -> Dict[str, Any]:
    ensure_pixels_loaded()
    labels = ensure_labels_available()
    data_result = st.session_state["data_result"]
    params = current_celltype_assignment_params_from_widgets()
    scan_parallel_config = get_scan_parallel_config(cpu_percent_key=cpu_percent_key, state_prefix=state_prefix)
    assignment_params_save_dir = get_section_output_dir(config, 'celltype_assignment_parameters')

    parallel_workers = int(scan_parallel_config["parallel_workers"])
    core_target_evaluations = max(128, min(768, parallel_workers * 24))
    expansion_target_evaluations = max(96, min(512, parallel_workers * 16))
    exhaustive_limit = max(2048, core_target_evaluations + expansion_target_evaluations)

    return run_celltype_assignment_parameter_optimizer(
        folder=config.folder,
        save_dir=assignment_params_save_dir,
        pixel_size_um=config.pixel_size_um,
        image_id=config.image_id,
        channels_cfg=[channel.to_dict() for channel in config.channels],
        celltype_cfg=st.session_state["celltype_cfg"],
        labels=labels,
        df_pixels=data_result["df_pixels"],
        shapes=data_result["shapes"],
        base_params=params,
        search_specs=celltype_assignment_optimizer_search_space_specs(),
        priority_search_specs=celltype_assignment_optimizer_core_search_space_specs(),
        save_outputs=True,
        max_evaluations=int(core_target_evaluations),
        priority_target_evaluations=int(core_target_evaluations),
        expansion_target_evaluations=int(expansion_target_evaluations),
        exhaustive_limit=int(exhaustive_limit),
        parallel_workers=parallel_workers,
        parallel_backend=str(scan_parallel_config["parallel_backend"]),
        native_threads_per_worker=int(scan_parallel_config["native_threads_per_worker"]),
        support_workers_per_worker=1,
        output_prefix=output_prefix,
        use_vertical_band_subset=True,
        vertical_band_count=10,
        vertical_band_selection_count=5,
    )


def stage_assignment_param_scan_row_for_widget_update(row: pd.Series) -> None:
    payload = {
        "combo_index": int(row["combo_index"]),
        "params": {},
    }
    for field in CELLTYPE_OPTIMIZER_PARAM_ORDER:
        label = CELLTYPE_OPTIMIZER_PARAM_LABELS[field]
        if label in row.index:
            payload["params"][field] = row[label]
    st.session_state["pending_assignment_param_scan_selection"] = payload


def consume_pending_assignment_param_widget_update() -> None:
    payload = st.session_state.get("pending_assignment_param_scan_selection")
    if not payload:
        return

    for field, value in payload.get("params", {}).items():
        if field in CELLTYPE_ASSIGN_PARAM_SPECS:
            widget_key = CELLTYPE_ASSIGN_PARAM_SPECS[field]["widget_key"]
            if CELLTYPE_ASSIGN_PARAM_SPECS[field]["kind"] == "choice":
                st.session_state[widget_key] = str(value)
            elif CELLTYPE_ASSIGN_PARAM_SPECS[field]["kind"] == "int":
                st.session_state[widget_key] = int(value)
            else:
                st.session_state[widget_key] = float(value)
            continue
        if field == "resolve_ambiguous":
            st.session_state["ct_assign_resolve_ambiguous_ui"] = bool(value)
        elif field == "ambiguous_min_probability":
            st.session_state["ct_assign_ambiguous_min_probability_ui"] = float(value)
        elif field == "ambiguous_min_gap":
            st.session_state["ct_assign_ambiguous_min_gap_ui"] = float(value)

    combo_index = int(payload["combo_index"])
    st.session_state["last_applied_assignment_param_combo"] = combo_index
    st.session_state["pending_assignment_param_scan_selection"] = None
    st.session_state["assignment_param_scan_notice"] = (
        f"Applied parameter-scan combo #{combo_index} as the current cell-type assignment parameters. "
        "You can now manually adjust any of those values before running the final cell-type assignment."
    )


def maybe_show_assignment_param_scan_notice() -> None:
    notice = str(st.session_state.get("assignment_param_scan_notice") or "").strip()
    if notice:
        st.success(notice)
        st.session_state["assignment_param_scan_notice"] = ""


def make_celltype_assignment_scan_hover_text(row: pd.Series, count_columns: Sequence[str]) -> str:
    lines = [f"Combo #{int(row['combo_index'])}"]
    for field in CELLTYPE_PARAM_ORDER:
        label = CELLTYPE_PARAM_LABELS[field]
        lines.append(f"{label}: {row[label]}")
    for col in count_columns:
        if col in row.index and pd.notna(row[col]):
            lines.append(f"{col.replace('count::', '')}: {int(row[col])}")
    if "assigned_defined_total" in row.index and pd.notna(row["assigned_defined_total"]):
        lines.append(f"assigned total: {int(row['assigned_defined_total'])}")
    return "<br>".join(lines)


def build_celltype_assignment_scan_plotly(df_results: pd.DataFrame, celltype_cfg: Sequence[Dict[str, Any]]):
    plot_df = df_results[df_results["error"].fillna("") == ""].copy()
    plot_df = plot_df.sort_values("combo_index").reset_index(drop=True)
    fig = go.Figure()
    count_columns = [f"count::{ct['name']}" for ct in celltype_cfg if f"count::{ct['name']}" in plot_df.columns]
    if len(plot_df) == 0:
        fig.update_layout(
            title="Cell-type assignment parameter scan",
            xaxis_title="Parameter combination index",
            yaxis_title="Detected cells",
            height=440,
            margin=dict(l=40, r=20, t=50, b=40),
        )
        return fig, plot_df
    hover_text = [make_celltype_assignment_scan_hover_text(row, count_columns + [col for col in ["count::Unassigned", "count::Ambiguous"] if col in plot_df.columns]) for _, row in plot_df.iterrows()]
    for ct in celltype_cfg:
        col = f"count::{ct['name']}"
        if col not in plot_df.columns:
            continue
        fig.add_trace(
            go.Scatter(
                x=plot_df["combo_index"],
                y=plot_df[col],
                mode="lines+markers",
                name=ct["name"],
                line=dict(color=ct.get("color_hex")),
                hovertext=hover_text,
                hovertemplate="%{hovertext}<extra></extra>",
            )
        )
    if "count::Unassigned" in plot_df.columns:
        fig.add_trace(
            go.Scatter(
                x=plot_df["combo_index"],
                y=plot_df["count::Unassigned"],
                mode="lines+markers",
                name="Unassigned",
                line=dict(dash="dot"),
                hovertext=hover_text,
                hovertemplate="%{hovertext}<extra></extra>",
            )
        )
    if "count::Ambiguous" in plot_df.columns:
        fig.add_trace(
            go.Scatter(
                x=plot_df["combo_index"],
                y=plot_df["count::Ambiguous"],
                mode="lines+markers",
                name="Ambiguous",
                line=dict(dash="dash"),
                hovertext=hover_text,
                hovertemplate="%{hovertext}<extra></extra>",
            )
        )
    fig.update_layout(
        title="Cell-type assignment parameter scan",
        xaxis_title="Parameter combination index",
        yaxis_title="Detected cells",
        height=440,
        margin=dict(l=40, r=20, t=50, b=40),
    )
    return fig, plot_df


def format_celltype_assignment_combo_summary(row: pd.Series) -> str:
    assigned_total = int(row["assigned_defined_total"]) if "assigned_defined_total" in row.index and pd.notna(row["assigned_defined_total"]) else 0
    ambiguous = int(row["count::Ambiguous"]) if "count::Ambiguous" in row.index and pd.notna(row["count::Ambiguous"]) else 0
    unassigned = int(row["count::Unassigned"]) if "count::Unassigned" in row.index and pd.notna(row["count::Unassigned"]) else 0
    unresolved_total = (
        int(row["unresolved_total"])
        if "unresolved_total" in row.index and pd.notna(row["unresolved_total"])
        else ambiguous + unassigned
    )
    return (
        f"#{int(row['combo_index'])} | unresolved={unresolved_total} | assigned={assigned_total} | ambiguous={ambiguous} | "
        f"unassigned={unassigned} | R_BUFFER_UM={row['R_BUFFER_UM']} | MIN_POS_PIX={row['MIN_POS_PIX']}"
    )


def ensure_pixels_loaded() -> None:
    if st.session_state["data_result"] is not None:
        return
    config: PipelineConfig | None = st.session_state["config"]
    if config is None:
        raise RuntimeError("Please save the configuration first.")
    df_pixels, shapes = files_to_long_df(
        folder=config.folder,
        channels_cfg=[channel.to_dict() for channel in config.channels],
        image_id=config.image_id,
        pixel_size_um=config.pixel_size_um if valid_pixel_size(config.pixel_size_um) else None,
        unit="um",
    )
    st.session_state["data_result"] = {
        "df_pixels": df_pixels,
        "shapes": shapes,
    }


def ensure_labels_available():
    if st.session_state["nuclei_result"] is not None:
        return st.session_state["nuclei_result"]["labels"]
    config: PipelineConfig | None = st.session_state["config"]
    if config is None:
        raise RuntimeError("Please save the configuration first.")
    label_path = get_section_output_dir(config, 'nuclei') / "nuclei_labels_uint16.tiff"
    if not label_path.exists():
        raise RuntimeError("No nuclei labels are available yet. Run nuclei segmentation first.")
    return load_any_tiff(label_path).astype("int32")


def ensure_assignment_outputs_available():
    if st.session_state["assignment_result"] is not None:
        return st.session_state["assignment_result"]
    config: PipelineConfig | None = st.session_state["config"]
    if config is None:
        raise RuntimeError("Please save the configuration first.")
    assignment_dir = get_section_output_dir(config, 'celltype_assignment')
    mask_path = assignment_dir / "celltypes_mask_uint16.tiff"
    cells_csv = assignment_dir / "cells_summary.csv"
    if not mask_path.exists() or not cells_csv.exists():
        raise RuntimeError("No cell-type assignment outputs are available yet. Run the assignment step first.")
    celltype_mask = load_any_tiff(mask_path).astype("uint16")
    df_cells = pd.read_csv(cells_csv)
    counts_path = assignment_dir / "celltype_counts.csv"
    counts = pd.read_csv(counts_path) if counts_path.exists() else df_cells["celltype"].value_counts().rename_axis("celltype").reset_index(name="count")
    result = {
        "celltype_mask": celltype_mask,
        "df_cells": df_cells,
        "counts": counts,
    }
    st.session_state["assignment_result"] = result
    return result


def ensure_neighborhood_outputs_available():
    if st.session_state["neighborhood_result"] is not None:
        return st.session_state["neighborhood_result"]
    config: PipelineConfig | None = st.session_state["config"]
    if config is None:
        raise RuntimeError("Please save the configuration first.")

    neighborhood_dir = get_section_output_dir(config, 'neighborhood_analysis')
    mask_path = neighborhood_dir / "neighborhood_cluster_mask_uint16.tiff"
    summary_csv = neighborhood_dir / "neighborhood_cluster_summary.csv"
    tiles_csv = neighborhood_dir / "neighborhood_tile_assignments.csv"
    params_json = neighborhood_dir / "neighborhood_params.json"

    if not mask_path.exists() or not summary_csv.exists() or not tiles_csv.exists():
        raise RuntimeError("No neighborhood analysis outputs are available yet. Run Neighborhood analysis first.")

    cluster_mask = load_any_tiff(mask_path).astype("uint16")
    cluster_summary = pd.read_csv(summary_csv)
    tile_assignments = pd.read_csv(tiles_csv)
    params_payload: Dict[str, Any] = {}
    if params_json.exists():
        try:
            params_payload = json.loads(params_json.read_text())
        except Exception:
            params_payload = {}

    cluster_labels = (
        cluster_summary.sort_values("cluster_id")["cluster_label"].astype(str).tolist()
        if "cluster_id" in cluster_summary.columns and "cluster_label" in cluster_summary.columns
        else cluster_summary.get("cluster_label", pd.Series(dtype=str)).astype(str).tolist()
    )
    cluster_keys = (
        cluster_summary.sort_values("cluster_id")["cluster_key"].astype(str).tolist()
        if "cluster_id" in cluster_summary.columns and "cluster_key" in cluster_summary.columns
        else cluster_summary.get("cluster_key", pd.Series(dtype=str)).astype(str).tolist()
    )

    result = {
        "cluster_mask": cluster_mask,
        "cluster_summary": cluster_summary,
        "tile_assignments": tile_assignments,
        "grid_size_um": float(params_payload.get("grid_size_um", 20.0) or 20.0),
        "tile_width_px": int(params_payload.get("tile_width_px", 0) or 0),
        "tile_height_px": int(params_payload.get("tile_height_px", 0) or 0),
        "n_tiles_x": int(params_payload.get("n_tiles_x", 0) or 0),
        "n_tiles_y": int(params_payload.get("n_tiles_y", 0) or 0),
        "cluster_labels": cluster_labels,
        "cluster_keys": cluster_keys,
        "excluded_celltypes": list(params_payload.get("excluded_celltypes", [])),
        "display_cluster_labels": list(params_payload.get("display_cluster_labels", cluster_labels)),
        "saved_paths": {
            "mask_tiff": mask_path,
            "summary_csv": summary_csv,
            "tiles_csv": tiles_csv,
            "params_json": params_json if params_json.exists() else None,
        },
    }
    st.session_state["neighborhood_result"] = result
    return result


def collect_channel_cfg(available_files: Sequence[str]) -> List[ChannelConfig]:
    rows: List[ChannelConfig] = []
    n_channels = int(st.session_state.get("n_channels", 0))
    for idx in range(n_channels):
        file_name = st.session_state.get(f"channel_file_{idx}")
        marker_name = (st.session_state.get(f"channel_marker_{idx}") or "").strip()
        color_hex = st.session_state.get(f"channel_color_{idx}") or COMMON_FIRST[min(idx, len(COMMON_FIRST) - 1)]
        if not file_name:
            raise RuntimeError(f"Channel {idx + 1} is missing a file selection.")
        if file_name not in available_files:
            raise RuntimeError(f"Selected file {file_name!r} is not in the available input file list.")
        if not marker_name:
            marker_name = Path(file_name).stem
        rows.append(ChannelConfig(file=file_name, channel=marker_name, color_hex=color_hex))
    return rows


def current_channel_names_from_widgets() -> List[str]:
    n_channels = int(st.session_state.get("n_channels", 0))
    names: List[str] = []
    for idx in range(n_channels):
        marker_name = (st.session_state.get(f"channel_marker_{idx}") or "").strip()
        file_name = st.session_state.get(f"channel_file_{idx}") or ""
        names.append(marker_name or Path(file_name).stem or f"channel_{idx + 1}")
    return names


def build_and_save_config(available_files: Sequence[str], uploaded_files) -> None:
    if int(st.session_state.get("x_px", 0)) <= 0 or int(st.session_state.get("y_px", 0)) <= 0:
        raise RuntimeError("x (px) and y (px) must both be > 0.")
    pixel_size_um = (
        float(st.session_state.get("x_um", 0.0)) / int(st.session_state.get("x_px", 1)),
        float(st.session_state.get("y_um", 0.0)) / int(st.session_state.get("y_px", 1)),
    )
    channels = collect_channel_cfg(available_files)
    overlay_channels = list(st.session_state.get("overlay_channels", []))
    if not overlay_channels:
        overlay_channels = [channel.channel for channel in channels]
    white_channel = st.session_state.get("white_channel")
    white_weight = float(st.session_state.get("white_weight", 0.0))

    input_mode = str(st.session_state.get("input_mode_radio", "Local folder"))
    if input_mode == "Upload files":
        if not uploaded_files:
            raise RuntimeError("Please upload at least one CSV/TXT file.")
        workspace_inputs = session_input_dir()
        workspace_outputs = session_output_dir()
        workspace_inputs.mkdir(parents=True, exist_ok=True)
        workspace_outputs.mkdir(parents=True, exist_ok=True)
        for uploaded in uploaded_files:
            save_uploaded_file_bytes(uploaded.name, uploaded.getvalue(), workspace_inputs)
        folder = workspace_inputs
        save_dir = workspace_outputs
        config_input_mode = "upload"
    else:
        folder_value = str(st.session_state.get("local_folder_input", "")).strip()
        if not folder_value:
            raise RuntimeError("Choose an input folder containing CSV/TXT channel files.")
        folder = resolve_folder(folder_value)
        if not folder.is_dir():
            raise RuntimeError(f"Input folder does not exist: {folder}")
        output_value = str(st.session_state.get("local_output_folder", "")).strip()
        save_dir = resolve_folder(output_value) if output_value else folder / "SpatialScope_outputs"
        save_dir.mkdir(parents=True, exist_ok=True)
        config_input_mode = "local"

    previous_config: PipelineConfig | None = st.session_state.get("config")
    previous_channel_names = [channel.channel for channel in previous_config.channels] if previous_config else None
    new_channel_names = [channel.channel for channel in channels]

    config = PipelineConfig(
        folder=folder,
        save_dir=save_dir,
        pixel_size_um=pixel_size_um,
        image_id="FieldA",
        channels=channels,
        overlay_channels=overlay_channels,
        white_channel=white_channel if white_channel not in {"", "None"} else None,
        white_weight=white_weight,
        input_mode=config_input_mode,
    )
    config.save_dir.mkdir(parents=True, exist_ok=True)
    ensure_all_section_output_dirs(config)
    write_json(get_section_output_dir(config, 'config') / "config.json", pipeline_config_to_json_dict(config))
    st.session_state["config"] = config
    invalidate_after_config_change()
    refresh_output_zip_state(config.save_dir)

    if previous_channel_names != new_channel_names:
        st.session_state["celltype_items"] = []
        st.session_state["celltype_cfg"] = None

    ensure_celltype_items()


def save_celltype_cfg_from_widgets(channel_names: Sequence[str]) -> List[Dict[str, Any]]:
    ensure_celltype_items()
    cfg: List[Dict[str, Any]] = []
    marker_choices = marker_choices_for_ui(channel_names)
    for item in st.session_state["celltype_items"]:
        uid = item["id"]
        name = (st.session_state.get(f"ct_name_{uid}") or "").strip()
        color_hex = st.session_state.get(f"ct_color_{uid}") or item["default_color"]
        if not name:
            continue
        all_pos = list(dict.fromkeys(st.session_state.get(f"ct_all_pos_{uid}", [])))
        all_neg = list(dict.fromkeys(st.session_state.get(f"ct_all_neg_{uid}", [])))
        any_groups = get_any_groups_from_state(uid, marker_choices)
        cfg.append(
            {
                "name": name,
                "color_hex": color_hex,
                "mode": "simple",
                "all_pos": all_pos,
                "all_neg": all_neg,
                "any_pos_groups": any_groups,
            }
        )
    if not cfg:
        raise RuntimeError("No valid cell types were defined.")
    st.session_state["celltype_cfg"] = cfg
    config: PipelineConfig | None = st.session_state.get("config")
    if config is not None:
        save_celltype_config(cfg, get_section_output_dir(config, 'celltype_definition'))
    invalidate_after_celltypes_change()
    if config is not None:
        refresh_output_zip_state(config.save_dir)
    return cfg


def inject_desktop_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ss-teal: #087e8b;
            --ss-teal-dark: #086875;
            --ss-ink: #162126;
            --ss-muted: #68757a;
            --ss-line: #d7dfe2;
            --ss-canvas: #f4f7f8;
            --ss-panel: #ffffff;
        }
        html, body, .stApp {
            font-family: Inter, "Segoe UI Variable", "Segoe UI", system-ui, sans-serif;
            letter-spacing: 0;
        }
        .stApp { background: var(--ss-canvas); color: var(--ss-ink); }
        header[data-testid="stHeader"], #MainMenu, footer, [data-testid="stToolbar"] { display: none !important; }
        [data-testid="stAppViewContainer"] > .main { padding-top: 0 !important; }
        .main .block-container { max-width: none; padding: 1.5rem 2rem 3rem; }
        [data-testid="stSidebar"] {
            min-width: 310px;
            max-width: 310px;
            background: #eef3f4;
            border-right: 1px solid var(--ss-line);
        }
        [data-testid="stSidebarContent"] { padding: 1.25rem 0.85rem 1.75rem; }
        [data-testid="stSidebar"] h2 { font-size: 1.35rem; margin: 0 0 0.75rem; }
        [data-testid="stSidebar"] h3 { font-size: 0.86rem; color: var(--ss-muted); margin-top: 1rem; }
        [data-testid="stSidebar"] div[role="radiogroup"] { gap: 0.35rem; }
        [data-testid="stSidebar"] div[role="radiogroup"] > label {
            width: 100%;
            min-height: 2.75rem;
            margin: 0;
            padding: 0.62rem 0.7rem;
            border: 1px solid transparent;
            border-radius: 7px;
            align-items: center;
            transition: border-color 120ms ease, background-color 120ms ease;
        }
        [data-testid="stSidebar"] div[role="radiogroup"] > label:hover { border-color: #aab9be; }
        [data-testid="stSidebar"] div[role="radiogroup"] > label p {
            width: 100%;
            margin: 0;
            font-size: 0.86rem;
            line-height: 1.25;
            font-weight: 600;
            word-break: normal;
            overflow-wrap: normal;
        }
        [data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {
            border-color: var(--ss-teal);
            box-shadow: 0 0 0 1px rgba(8, 126, 139, 0.08);
        }
        .main h1 { font-size: 2rem; }
        .main h2 { font-size: 1.6rem; }
        .main h3 { font-size: 1.32rem; }
        .main h4 { font-size: 1.06rem; }
        .main p, .main label, .main input, .main button, .main textarea { font-size: 0.98rem; }
        p, span, label, div { word-break: normal; }
        [data-testid="stWidgetLabel"] p,
        [data-testid="stWidgetLabel"] label,
        .stTextInput label,
        .stNumberInput label,
        .stSelectbox label,
        .stMultiSelect label {
            font-weight: 400 !important;
            overflow-wrap: normal !important;
            word-break: keep-all !important;
        }
        [data-testid="stMetricLabel"] p, code, kbd, .ss-unit { white-space: nowrap; }
        [data-testid="stExpander"], [data-testid="stFileUploaderDropzone"] {
            background: var(--ss-panel);
            border: 1px solid var(--ss-line);
            border-radius: 7px;
        }
        button[kind="primary"], .stDownloadButton button {
            background: var(--ss-teal);
            border-color: var(--ss-teal);
            color: white;
        }
        button[kind="primary"]:hover, .stDownloadButton button:hover {
            background: var(--ss-teal-dark);
            border-color: var(--ss-teal-dark);
        }
        [data-baseweb="tab-list"] { gap: 0.25rem; }
        [data-baseweb="tab"] { border-radius: 6px 6px 0 0; padding: 0.55rem 0.85rem; }
        input, textarea, [data-baseweb="select"] > div { border-radius: 6px !important; }
        @media (max-width: 980px) {
            [data-testid="stSidebar"] { min-width: 270px; max-width: 270px; }
            .main .block-container { padding-left: 1.25rem; padding-right: 1.25rem; }
            [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
            [data-testid="column"] { min-width: min(100%, 18rem) !important; flex: 1 1 18rem !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _section_statuses(section_labels: Sequence[str]) -> Dict[str, str]:
    completed = {
        section_labels[0]: st.session_state.get("config") is not None,
        section_labels[1]: st.session_state.get("data_result") is not None,
        section_labels[2]: st.session_state.get("nuclei_result") is not None,
        section_labels[3]: st.session_state.get("assignment_result") is not None,
        section_labels[4]: st.session_state.get("neighborhood_result") is not None,
        section_labels[5]: any(
            st.session_state.get(key) is not None
            for key in ("region_result", "region_manual_roi_result", "region_integrated_result")
        ),
        section_labels[6]: any(
            st.session_state.get(key) is not None
            for key in (
                "cell_distribution_region_masks_result",
                "cell_distribution_density_result",
                "cell_distribution_cluster_result",
            )
        ),
        section_labels[7]: any(st.session_state.get(key) is not None for key in ("nn_result", "boundary_result")),
        section_labels[8]: bool(
            st.session_state.get("outputs_viewed")
            and st.session_state.get("outputs_zip_path")
        ),
    }
    partial = {
        section_labels[0]: bool(st.session_state.get("available_files")),
        section_labels[2]: any(
            st.session_state.get(key) is not None
            for key in ("nuclei_scan_result", "nuclei_auto_scan_result")
        ),
        section_labels[3]: any(
            st.session_state.get(key) is not None
            for key in ("celltype_cfg", "assignment_param_scan_result")
        ),
    }
    prerequisites = {
        section_labels[0]: True,
        section_labels[1]: completed[section_labels[0]],
        section_labels[2]: completed[section_labels[1]],
        section_labels[3]: completed[section_labels[2]],
        section_labels[4]: completed[section_labels[3]],
        section_labels[5]: completed[section_labels[3]],
        section_labels[6]: completed[section_labels[5]],
        section_labels[7]: completed[section_labels[3]],
        section_labels[8]: any(completed.values()),
    }
    errors = st.session_state.get("section_errors", {})
    statuses: Dict[str, str] = {}
    for label in section_labels:
        if completed.get(label, False):
            statuses[label] = "Finished"
        elif isinstance(errors, dict) and errors.get(label):
            statuses[label] = "Error"
        elif partial.get(label, False):
            statuses[label] = "Running"
        elif prerequisites.get(label, False):
            statuses[label] = "Ready"
        else:
            statuses[label] = "Not started"
    return statuses


def _inject_sidebar_status_colors(statuses: Sequence[str]) -> None:
    palette = {
        "Not started": ("#e7ecee", "#68757a"),
        "Ready": ("#dceff4", "#086875"),
        "Running": ("#fff0c7", "#8a5a00"),
        "Finished": ("#dff2e7", "#17653a"),
        "Error": ("#f9dddd", "#9a2828"),
    }
    rules = []
    for index, status in enumerate(statuses, start=1):
        background, foreground = palette[status]
        rules.append(
            f'[data-testid="stSidebar"] div[role="radiogroup"] > label:nth-child({index}) '
            f'{{ background: {background}; color: {foreground}; }}'
        )
    st.markdown(f"<style>{''.join(rules)}</style>", unsafe_allow_html=True)


def render_sidebar_navigation() -> str:
    section_labels = [
        "1. Inputs & config",
        "2. Overlay preview",
        "3. Nuclei segmentation",
        "4. Cell type assignments",
        "5. Neighborhood analysis",
        "6. Region analysis",
        "7. Cell distribution analysis",
        "8. Distance analysis",
        "9. Outputs",
    ]

    statuses = _section_statuses(section_labels)

    with st.sidebar:
        st.markdown("## SpatialScope")
        language_options = ["system", "en", "zh-hans"]
        st.selectbox(
            "Language/语言",
            options=language_options,
            index=language_options.index(st.session_state.get("ui_language", "system")),
            format_func=lambda value: {
                "system": "Follow System",
                "en": "English",
                "zh-hans": "简体中文",
            }[value],
            key="ui_language",
            on_change=_persist_ui_language_setting,
        )
        with st.expander("Session info", expanded=False):
            st.write(f"CPU count: {CPU_COUNT}")
            st.write(f"Default native thread pool: {os.environ.get('OMP_NUM_THREADS')}")
            st.info(
                "SpatialScope can read a local folder or uploaded ImageJ-exported CSV/TXT files. "
                "Local-folder results are written to the selected output folder."
            )
            if st.button("Reset session", type="secondary", key="reset_session_sidebar_btn"):
                reset_session()

        st.markdown("### Analysis sections")
        current_label = str(st.session_state.get("sidebar_active_section", section_labels[0]))
        if current_label in {"4. Cell types", "5. Cell-type assignment parameters", "6. Cell-type assignment"}:
            current_label = "4. Cell type assignments"
        if current_label == "7. Integrated region analysis":
            current_label = "6. Region analysis"
        if current_label == "7. Distance analysis":
            current_label = "8. Distance analysis"
        if current_label == "8. Distance analysis":
            current_label = "8. Distance analysis"
        if current_label == "8. Outputs":
            current_label = "9. Outputs"
        if current_label == "9. Outputs":
            current_label = "9. Outputs"
        if current_label not in section_labels:
            current_label = section_labels[0]
        selected_label = st.radio(
            "Analysis sections",
            options=section_labels,
            index=section_labels.index(current_label),
            key="sidebar_active_section",
            format_func=lambda label: f"{label}  ·  {statuses[label]}",
            label_visibility="collapsed",
        )
        _inject_sidebar_status_colors([statuses[label] for label in section_labels])

    return str(selected_label)


def render_config_tab(tab):
    with tab:
        st.subheader("Input source and configuration")
        st.radio(
            "Input source",
            options=["Local folder", "Upload files"],
            horizontal=True,
            key="input_mode_radio",
        )
        uploaded_files = None
        available_files: List[str] = []

        if st.session_state.get("input_mode_radio") == "Local folder":
            st.text_input(
                "Input folder",
                key="local_folder_input",
                placeholder=r"C:\Data\SpatialScope\channels",
            )
            st.text_input(
                "Output folder",
                key="local_output_folder",
                placeholder=r"C:\Data\SpatialScope\results",
            )
            st.caption("Use File > Choose Input Folder or Choose Output Folder for the native Windows folder picker.")
            folder_value = str(st.session_state.get("local_folder_input", "")).strip()
            if folder_value:
                try:
                    available_files = discover_text_image_files(resolve_folder(folder_value))
                except Exception as exc:
                    st.warning(f"Could not read the selected input folder: {exc}")
        else:
            uploaded_files = st.file_uploader(
                "Upload ImageJ-exported text images (.csv/.txt)",
                type=["csv", "txt"],
                accept_multiple_files=True,
                key=f"uploaded_files_widget_{int(st.session_state.get('uploaded_files_widget_nonce', 0))}",
            )
            available_files = sorted([uploaded.name for uploaded in uploaded_files]) if uploaded_files else []

        st.session_state["available_files"] = available_files

        if available_files:
            sync_uploaded_channel_state(available_files)
            st.success(f"Detected {len(available_files)} channel files.")
            st.code("\n".join(available_files))

        if not available_files:
            st.session_state["n_channels"] = 0
            if st.session_state.get("input_mode_radio") == "Local folder":
                st.warning("Choose a folder containing CSV/TXT channel files to continue.")
            else:
                st.warning("Upload CSV/TXT files to continue.")
            return

        st.markdown("#### Detected channels")
        st.info(
            f"Automatically detected **{len(available_files)} channel(s)** from the selected CSV/TXT files. "
            "Each file is treated as one channel."
        )

        button_col1, button_col2 = st.columns([1, 1])
        with button_col1:
            if st.button("Reset marker names from filenames", key="reset_marker_names"):
                sync_uploaded_channel_state(available_files, reset_markers=True)
        with button_col2:
            if st.button("Reassign automatic colors", key="reset_auto_colors"):
                st.session_state["channel_color_shuffle_index"] = int(st.session_state.get("channel_color_shuffle_index", 0)) + 1
                sync_uploaded_channel_state(available_files, reset_colors=True)

        st.caption("Automatic colors are assigned to each channel. Clicking 'Reassign automatic colors' generates a new shuffled color set.")

        st.markdown("#### Channel selectors")
        n_channels = int(st.session_state["n_channels"])
        for idx in range(n_channels):
            file_name = available_files[idx]
            st.session_state[f"channel_file_{idx}"] = file_name

            col1, col2, col3 = st.columns([2.4, 2.0, 1.0])
            with col1:
                st.text_input(
                    f"Channel {idx + 1} file",
                    disabled=True,
                    key=f"channel_file_display_{idx}",
                )
            with col2:
                st.text_input(f"Marker {idx + 1}", key=f"channel_marker_{idx}")
            with col3:
                st.color_picker(f"Color {idx + 1}", key=f"channel_color_{idx}")

        st.markdown("#### Pixel size")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.number_input("x (µm)", min_value=0.0, step=0.1, key="x_um")
        with col2:
            st.number_input("x (px)", min_value=0, step=1, key="x_px")
        with col3:
            st.number_input("y (µm)", min_value=0.0, step=0.1, key="y_um")
        with col4:
            st.number_input("y (px)", min_value=0, step=1, key="y_px")

        x_px = int(st.session_state.get("x_px", 0))
        y_px = int(st.session_state.get("y_px", 0))
        if x_px > 0 and y_px > 0:
            pixel_size_um = (
                float(st.session_state.get("x_um", 0.0)) / x_px,
                float(st.session_state.get("y_um", 0.0)) / y_px,
            )
            st.caption(f"Computed PIXEL_SIZE_UM = {pixel_size_um}  (x_um/x_px, y_um/y_px)")
        else:
            st.caption("Enter x (px) and y (px) > 0 to compute PIXEL_SIZE_UM.")

        current_channel_names = current_channel_names_from_widgets()
        if "overlay_channels" not in st.session_state or not st.session_state["overlay_channels"]:
            st.session_state["overlay_channels"] = current_channel_names
        if "white_channel" not in st.session_state:
            st.session_state["white_channel"] = "None"
        if "white_weight" not in st.session_state:
            st.session_state["white_weight"] = 0.0

        st.markdown("#### Overlay options")
        st.multiselect(
            "Overlay channels",
            options=current_channel_names,
            default=[name for name in st.session_state.get("overlay_channels", []) if name in current_channel_names] or current_channel_names,
            key="overlay_channels",
        )
        col1, col2 = st.columns([2, 2])
        with col1:
            st.selectbox("White overlay channel", options=["None"] + current_channel_names, key="white_channel")
        with col2:
            st.slider("White overlay weight", min_value=0.0, max_value=1.0, step=0.05, key="white_weight")

        if st.button("Save configuration", type="primary"):
            try:
                build_and_save_config(available_files, uploaded_files)
                st.success("Configuration saved.")
            except Exception as exc:
                st.error(str(exc))



def render_overlay_tab(tab):
    with tab:
        st.subheader("Load inputs and create overlay figures")
        config: PipelineConfig | None = st.session_state.get("config")
        if config is None:
            st.warning("Save the configuration first.")
            return

        if st.button("Load inputs and generate overlay", key="run_overlay_btn"):
            try:
                with st.spinner("Loading CSV/TXT grids and building figures..."):
                    ensure_pixels_loaded()
                    data_result = st.session_state["data_result"]
                    df_pixels = data_result["df_pixels"]
                    shapes = data_result["shapes"]

                    overlay_fig, _ = overlay_multi_channels(
                        df=df_pixels,
                        shapes=shapes,
                        image_id=config.image_id,
                        channels_cfg=[channel.to_dict() for channel in config.channels],
                        overlay_channels=config.overlay_channels or [channel.channel for channel in config.channels],
                        white_channel=config.white_channel,
                        white_weight=config.white_weight,
                        clip_hi=99.8,
                        pixel_size_um=config.pixel_size_um,
                        save_path=get_section_output_dir(config, 'overlay') / "overlay.svg",
                    )
                    split_fig = plot_split_channels(
                        df=df_pixels,
                        shapes=shapes,
                        image_id=config.image_id,
                        channels_cfg=[channel.to_dict() for channel in config.channels],
                        pixel_size_um=config.pixel_size_um,
                        clip_hi=99.8,
                        save_path=get_section_output_dir(config, 'overlay') / "split_channels.svg",
                    )
                    _close_figure_obj(st.session_state["data_result"].get("overlay_figure"))
                    _close_figure_obj(st.session_state["data_result"].get("split_figure"))
                    st.session_state["data_result"]["overlay_figure"] = overlay_fig
                    st.session_state["data_result"]["split_figure"] = split_fig
                    invalidate_output_zip_cache()
                    refresh_output_zip_state(config.save_dir)
                st.success(f"Saved overlay figures to {get_section_output_dir(config, 'overlay')}")
            except Exception as exc:
                st.error(str(exc))

        data_result = st.session_state.get("data_result") or {}
        overlay_dir = get_section_output_dir(config, 'overlay')
        overlay_saved_paths = [overlay_dir / "overlay.png", overlay_dir / "overlay.svg"]
        split_saved_paths = [overlay_dir / "split_channels.png", overlay_dir / "split_channels.svg"]
        if data_result.get("overlay_figure") is not None or any(Path(p).exists() for p in overlay_saved_paths):
            st.markdown("#### Overlay")
            render_zoomable_figure(
                fig=data_result.get("overlay_figure"),
                component_key="overlay_preview",
                saved_paths=overlay_saved_paths,
                component_height=1200,
            )
        if data_result.get("split_figure") is not None or any(Path(p).exists() for p in split_saved_paths):
            st.markdown("#### Split channels")
            render_zoomable_figure(
                fig=data_result.get("split_figure"),
                component_key="split_channels_preview",
                saved_paths=split_saved_paths,
                component_height=1200,
                prefer_svg=True,
            )



def render_nuclei_tab(tab):
    with tab:
        st.subheader("Nuclei segmentation")
        config: PipelineConfig | None = st.session_state.get("config")
        if config is None:
            st.warning("Save the configuration first.")
            return

        channel_names = [channel.channel for channel in config.channels]
        default_nucleus = guess_nuclear_channel(channel_names) or (channel_names[0] if channel_names else None)
        ensure_nuclei_widget_defaults(channel_names, default_nucleus)

        consume_pending_scan_widget_update(config)

        st.selectbox("Nucleus Channel", options=channel_names, key="nucleus_channel_ui")
        st.caption("Select the nucleus channel first. Both the optional parameter scan and the final nuclei segmentation use this channel.")

        if st.session_state.get("nuclei_scan_initialized_channel") != st.session_state.get("nucleus_channel_ui"):
            set_default_nuclei_scan_candidates_from_widgets(force=True)
            st.session_state["nuclei_scan_initialized_channel"] = st.session_state.get("nucleus_channel_ui")
            st.session_state["nuclei_scan_pending"] = False
            st.session_state["nuclei_scan_result"] = None
            st.session_state["nuclei_auto_scan_result"] = None
            st.session_state["nuclei_scan_signature"] = None
            st.session_state["last_applied_nuclei_scan_combo"] = None

        maybe_show_nuclei_scan_notice()

        with st.expander("Optional parameter scan", expanded=False):
            st.caption(
                "Optional: test many parameter combinations around your current nuclei settings. "
                "You can skip this section and adjust the nuclei parameter boxes manually."
            )

            st.slider(
                "CPU to use for parameter scan (%)",
                min_value=10,
                max_value=100,
                step=5,
                key="scan_cpu_percent_ui",
                help="Higher values usually finish faster, but they use more CPU and memory.",
            )
            scan_parallel_config = get_scan_parallel_config()
            st.caption(
                f"The scan will use about {int(scan_parallel_config['parallel_workers'])} worker(s) out of {CPU_COUNT} available CPU core(s). "
                "The app uses a process-based scan engine automatically."
            )

            action_cols = st.columns([1.2, 1.2, 2.6])
            with action_cols[0]:
                rerun_scan_clicked = st.button("Run parameter scan", key="run_nuclei_scan_btn")
            with action_cols[1]:
                if st.button("Reset scan candidates", key="reset_nuclei_scan_btn"):
                    set_default_nuclei_scan_candidates_from_widgets(force=True)
                    st.rerun()
            with action_cols[2]:
                st.caption(
                    "Tip: keep the candidate lists compact. The scan tests the full Cartesian product of all listed values."
                )

            with st.expander("Candidate values to test (comma-separated)", expanded=False):
                scan_cols = st.columns(2)
                for idx, field in enumerate(SWEEP_PARAM_ORDER):
                    spec = NUCLEI_PARAM_SPECS[field]
                    with scan_cols[idx % 2]:
                        st.text_input(
                            spec["label"],
                            key=NUCLEI_SCAN_TEXT_KEYS[field],
                            help="The scan tests every combination of the values listed across all fields.",
                        )

            candidate_values = parse_scan_values_from_state()
            n_combinations = count_scan_combinations(candidate_values)
            st.caption(
                f"Current scan size: {n_combinations} combinations. "
                f"At the current CPU setting, the scan will use about {int(scan_parallel_config['parallel_workers'])} worker(s)."
            )

            current_signature = nuclei_scan_signature(config, candidate_values, st.session_state["nucleus_channel_ui"])
            if rerun_scan_clicked:
                try:
                    with st.spinner(
                        f"Running nuclei parameter scan across {n_combinations} combinations "
                        f"with about {int(scan_parallel_config['parallel_workers'])} worker(s)..."
                    ):
                        scan_result = run_current_nuclei_parameter_scan(
                            config,
                            candidate_values,
                            cpu_percent_key="scan_cpu_percent_ui",
                            state_prefix="scan",
                            output_prefix="nuclei_parameter_sweep",
                        )
                        st.session_state["nuclei_scan_result"] = scan_result
                        st.session_state["nuclei_scan_signature"] = current_signature
                        st.session_state["nuclei_scan_pending"] = False
                        st.session_state["last_applied_nuclei_scan_combo"] = None
                        invalidate_output_zip_cache()
                        refresh_output_zip_state(config.save_dir)
                    st.success(f"Nuclei parameter scan finished. Saved sweep files to {get_section_output_dir(config, 'nuclei')}")
                except Exception as exc:
                    st.session_state["nuclei_scan_pending"] = False
                    st.error(str(exc))

            scan_result = st.session_state.get("nuclei_scan_result")
            render_nuclei_scan_results_ui(
                scan_result=scan_result,
                selection_key="selected_nuclei_scan_combo_ui",
                apply_button_key="apply_scan_combo_btn",
                results_heading="#### Parameter-scan results ranked by nuclei count",
                recommendation_label="Largest-count parameter-scan result",
                saved_outputs_caption=(
                    "Saved scan outputs: nuclei_parameter_sweep_results.csv, nuclei_parameter_sweep_grid.json, "
                    "nuclei_parameter_sweep.svg, and nuclei_parameter_sweep.png"
                ),
                apply_caption=(
                    "Use a combo number from the parameter-scan results table above. Applying a combo updates the "
                    "Parameters for final run values below, and you can still fine-tune those values before running segmentation."
                ),
            )

        with st.expander("Optional automatic full-range optimizer", expanded=False):
            full_search_space_specs = nuclei_optimizer_search_space_specs()
            core_search_space_specs = nuclei_optimizer_core_search_space_specs()
            full_search_space_n_combinations = count_nuclei_search_space_combinations(full_search_space_specs)
            core_search_space_n_combinations = count_nuclei_search_space_combinations(core_search_space_specs)

            st.caption(
                "Optional: one click to search the full slider-defined nuclei parameter space automatically. "
                "To speed up the search, the optimizer divides the image into 10 equal-width vertical bands and "
                "evaluates 5 alternating bands instead of the whole image."
            )
            st.info(
                f"The full slider-defined search space contains {full_search_space_n_combinations:,} possible "
                f"step-defined parameter combinations. The preserved legacy/core region contains "
                f"{core_search_space_n_combinations:,} combinations. When the full space is too large to enumerate "
                "directly, the app automatically uses a core-first optimizer: it preserves the legacy/core search region "
                "first, then uses an evolutionary/genetic search with successive halving to explore the newly expanded outer range. "
                "All nuclei counts in this optimizer are measured on the selected vertical-band sample."
            )
            st.caption(
                "The default sampled bands are 1, 3, 5, 7, and 9 out of 10, covering about 50% of the image area. "
                "Applying a selected optimizer combo still copies the parameters into the full-image final segmentation settings below."
            )

            st.slider(
                "CPU to use for automatic optimizer (%)",
                min_value=10,
                max_value=100,
                step=5,
                key="auto_scan_cpu_percent_ui",
                help="Higher values usually finish faster, but they use more CPU and memory.",
            )
            auto_scan_parallel_config = get_scan_parallel_config(
                cpu_percent_key="auto_scan_cpu_percent_ui",
                state_prefix="auto_scan",
            )
            auto_scan_parallel_workers = int(auto_scan_parallel_config["parallel_workers"])
            core_target_evaluations = max(256, min(2048, auto_scan_parallel_workers * 32))
            expansion_target_evaluations = max(192, min(1536, auto_scan_parallel_workers * 24))

            st.caption(
                f"At the current CPU setting, the optimizer will use about {auto_scan_parallel_workers} worker(s), "
                f"test about {core_target_evaluations:,} combinations in the legacy/core range first, "
                f"then fully evaluate about {expansion_target_evaluations:,} additional combinations in the expanded outer range "
                "after cheaply screening a larger offspring pool with a successive-halving evolutionary/genetic search seeded by the best core results. "
                "All of those evaluations are done on the selected vertical-band sample."
            )

            with st.expander("Full parameter search space", expanded=False):
                st.dataframe(
                    make_nuclei_search_space_summary_df(full_search_space_specs),
                    use_container_width=True,
                )

            with st.expander("Core parameter search space preserved first", expanded=False):
                st.dataframe(
                    make_nuclei_search_space_summary_df(core_search_space_specs),
                    use_container_width=True,
                )

            auto_scan_clicked = st.button("Run automatic optimizer", key="run_nuclei_auto_scan_btn")
            if auto_scan_clicked:
                try:
                    with st.spinner(
                        f"Running the automatic full-range nuclei optimizer with about "
                        f"{auto_scan_parallel_workers} worker(s)..."
                    ):
                        auto_scan_result = run_current_nuclei_parameter_optimizer(
                            config,
                            cpu_percent_key="auto_scan_cpu_percent_ui",
                            state_prefix="auto_scan",
                            output_prefix="nuclei_auto_optimizer",
                        )
                        st.session_state["nuclei_auto_scan_result"] = auto_scan_result
                        st.session_state["last_applied_nuclei_scan_combo"] = None
                        invalidate_output_zip_cache()
                        refresh_output_zip_state(config.save_dir)
                    st.success(
                        f"Automatic nuclei optimizer finished on the vertical-band sample. Saved optimizer files to {get_section_output_dir(config, 'nuclei')}"
                    )
                except Exception as exc:
                    st.error(str(exc))

            auto_scan_result = st.session_state.get("nuclei_auto_scan_result")
            if auto_scan_result is not None:
                search_mode = str(auto_scan_result.get("search_mode") or "adaptive_global_search")
                if search_mode == "exhaustive_full_grid":
                    st.info(
                        f"Optimizer mode: full exhaustive search. This run evaluated "
                        f"{int(auto_scan_result.get('evaluated_unique_combinations', 0)):,} unique combinations "
                        f"out of {int(auto_scan_result.get('full_space_n_combinations', 0)):,} possible step-defined combinations "
                        "on the selected vertical-band sample."
                    )
                else:
                    selected_band_indices = [
                        int(v) + 1 for v in (auto_scan_result.get("selected_vertical_band_indices") or [])
                    ]
                    selected_band_text = (
                        ", ".join(str(v) for v in selected_band_indices)
                        if selected_band_indices
                        else "1, 3, 5, 7, 9"
                    )
                    st.info(
                        f"Optimizer mode: core-first search with successive-halving evolutionary expanded-range exploration. This run evaluated "
                        f"{int(auto_scan_result.get('evaluated_unique_combinations', 0)):,} unique combinations "
                        f"out of {int(auto_scan_result.get('full_space_n_combinations', 0)):,} possible step-defined combinations. "
                        f"Core-range evaluations: {int(auto_scan_result.get('evaluated_priority_combinations', 0)):,}. "
                        f"Expanded-range evaluations: {int(auto_scan_result.get('evaluated_expansion_combinations', 0)):,}. "
                        f"Cheap-screened offspring: {int(auto_scan_result.get('expansion_screened_candidate_count', 0)):,}. "
                        f"Screening rounds: {int(auto_scan_result.get('expansion_screening_round_count', 0)):,}. "
                        f"Genetic generations: {int(auto_scan_result.get('expansion_generation_count', 0)):,}. "
                        f"Population size: {int(auto_scan_result.get('expansion_population_size', 0)):,}. "
                        f"Sampled bands: {selected_band_text} of {int(auto_scan_result.get('vertical_band_count', 10))}. "
                        f"Sampled area: {100.0 * float(auto_scan_result.get('roi_sampled_area_fraction', 0.50)):.1f}% of the image."
                    )
                    screening_stage_factors = auto_scan_result.get("expansion_screening_stage_factors") or []
                    if screening_stage_factors:
                        st.caption(
                            "Successive-halving screening used downsample factors: "
                            + ", ".join(f"{int(factor)}x" for factor in screening_stage_factors)
                        )

            render_nuclei_scan_results_ui(
                scan_result=auto_scan_result,
                selection_key="selected_nuclei_auto_combo_ui",
                apply_button_key="apply_auto_scan_combo_btn",
                results_heading="#### Automatic optimizer results ranked by nuclei count",
                recommendation_label="Largest-count automatic optimizer result",
                saved_outputs_caption=(
                    "Saved automatic optimizer outputs: nuclei_auto_optimizer_results.csv, nuclei_auto_optimizer_grid.json, "
                    "nuclei_auto_optimizer.svg, and nuclei_auto_optimizer.png. These optimizer counts are based on the selected vertical-band sample."
                ),
                apply_caption=(
                    "Use a combo number from the automatic optimizer results table above. Applying a combo updates the "
                    "Parameters for final run values below, and you can still fine-tune those values before running segmentation on the full image."
                ),
            )

        st.markdown("### Segmentation")
        st.markdown("#### Parameters for final run")
        col1, col2 = st.columns(2)
        with col1:
            st.slider("MIN_DIAM_UM", min_value=0.0, max_value=float(NUCLEI_PARAM_SPECS["min_diam_um"]["max"]), value=6.0, step=0.5, key="min_diam_um_ui")
            st.slider("MAX_DIAM_UM", min_value=0.0, max_value=float(NUCLEI_PARAM_SPECS["max_diam_um"]["max"]), value=60.0, step=1.0, key="max_diam_um_ui")
            st.slider("TOPHAT_RADIUS_UM", min_value=0.0, max_value=float(NUCLEI_PARAM_SPECS["tophat_radius_um"]["max"]), value=2.0, step=0.5, key="tophat_radius_um_ui")
            st.slider("GAUSS_SIGMA_UM", min_value=0.0, max_value=float(NUCLEI_PARAM_SPECS["gauss_sigma_um"]["max"]), value=0.5, step=0.1, key="gauss_sigma_um_ui")
            st.slider("LOCAL_WIN_UM", min_value=float(NUCLEI_PARAM_SPECS["local_win_um"]["min"]), max_value=float(NUCLEI_PARAM_SPECS["local_win_um"]["max"]), value=25.0, step=1.0, key="local_win_um_ui")
        with col2:
            st.slider("LOCAL_OFFSET", min_value=float(NUCLEI_PARAM_SPECS["local_offset"]["min"]), max_value=float(NUCLEI_PARAM_SPECS["local_offset"]["max"]), value=-0.03, step=0.01, key="local_offset_ui")
            st.slider("H_MAXIMA_UM", min_value=0.0, max_value=float(NUCLEI_PARAM_SPECS["h_maxima_um"]["max"]), value=0.25, step=0.05, key="h_maxima_um_ui")
            st.slider("SEED_MIN_DIST_UM", min_value=0.0, max_value=float(NUCLEI_PARAM_SPECS["seed_min_dist_um"]["max"]), value=0.1, step=0.1, key="seed_min_dist_um_ui")
            st.slider("WATERSHED_COMPACTNESS", min_value=0.0, max_value=float(NUCLEI_PARAM_SPECS["watershed_compactness"]["max"]), value=0.5, step=0.05, key="watershed_compactness_ui")
            st.slider("POST_RESPLIT_MULT", min_value=0.0, max_value=float(NUCLEI_PARAM_SPECS["post_resplit_mult"]["max"]), value=0.5, step=0.05, key="post_resplit_mult_ui")

        control_cols = st.columns([1.4, 1.6])
        with control_cols[0]:
            st.checkbox("Save outputs (unchecked = preview only)", value=True, key="save_nuclei_outputs_ui")
        with control_cols[1]:
            st.slider(
                "CPU to use for final nuclei segmentation (%)",
                min_value=10,
                max_value=100,
                step=5,
                key="single_seg_cpu_percent_ui",
                help="Higher values usually finish faster, but they use more CPU and memory during the final run.",
            )
        final_cpu_percent = int(st.session_state.get("single_seg_cpu_percent_ui", 75) or 75)
        final_cpu_percent = max(10, min(100, final_cpu_percent))
        final_native_threads = max(1, min(CPU_COUNT, int(round(CPU_COUNT * final_cpu_percent / 100.0))))
        st.caption(
            f"At the current setting, the final nuclei segmentation will use about {final_native_threads} native thread(s) out of {CPU_COUNT} available CPU core(s)."
        )

        run_cols = st.columns([1.5, 4.5])
        with run_cols[0]:
            run_nuclei_clicked = st.button("Run nuclei segmentation", type="primary", key="run_nuclei_btn")
        with run_cols[1]:
            st.caption(
                "The final run always uses the values currently shown in Parameters for final run. If you applied a scan combo and then edited a value manually, the edited value will be used."
            )

        if run_nuclei_clicked:
            try:
                with st.spinner(
                    f"Running nuclei segmentation with about {final_native_threads} native thread(s) at the current {final_cpu_percent}% CPU setting..."
                ):
                    result = run_current_nuclei_segmentation(config)
                    _close_result_figures(st.session_state.get("nuclei_result"))
                    st.session_state["nuclei_result"] = result
                    invalidate_after_nuclei_change()
                    refresh_output_zip_state(config.save_dir)
                if st.session_state.get("last_applied_nuclei_scan_combo") is not None:
                    st.success(
                        f"Nuclei segmentation finished using the current parameter boxes. "
                        f"The last copied scan combo was #{int(st.session_state.get('last_applied_nuclei_scan_combo'))}, "
                        "but any manual edits in the boxes were used for this run. "
                        f"Outputs are in {get_section_output_dir(config, 'nuclei')}"
                    )
                else:
                    st.success(f"Nuclei segmentation finished using the current parameter boxes. Outputs are in {get_section_output_dir(config, 'nuclei')}")
            except Exception as exc:
                st.error(str(exc))

        nuclei_result = st.session_state.get("nuclei_result") or {}
        nuclei_saved_paths = [
            nuclei_result.get("saved_paths", {}).get("panel_png"),
            nuclei_result.get("saved_paths", {}).get("panel_svg"),
            get_section_output_dir(config, 'nuclei') / "nuclei_segmentation_panel.png",
            get_section_output_dir(config, 'nuclei') / "nuclei_segmentation_panel.svg",
        ]
        summary_csv_path = get_section_output_dir(config, 'nuclei') / "nuclei_summary.csv"
        if nuclei_result or any(Path(p).exists() for p in nuclei_saved_paths if p):
            n_nuclei_value = nuclei_result.get("n_nuclei")
            if n_nuclei_value is None and summary_csv_path.exists():
                try:
                    n_nuclei_value = int(len(pd.read_csv(summary_csv_path)))
                except Exception:
                    n_nuclei_value = None
            if n_nuclei_value is not None:
                st.metric("Segmented nuclei", int(n_nuclei_value))
            render_zoomable_figure(
                fig=nuclei_result.get("figure"),
                component_key="nuclei_segmentation_result",
                saved_paths=nuclei_saved_paths,
                component_height=1200,
            )
            st.markdown("#### nuclei_summary.csv preview")
            if nuclei_result.get("df_props") is not None:
                st.dataframe(nuclei_result["df_props"].head(20), use_container_width=True)
            elif summary_csv_path.exists():
                st.dataframe(pd.read_csv(summary_csv_path).head(20), use_container_width=True)


def render_celltypes_tab(tab):
    with tab:
        st.subheader("Define cell types")
        config: PipelineConfig | None = st.session_state.get("config")
        if config is None:
            st.warning("Save the configuration first.")
            return

        channel_names = [channel.channel for channel in config.channels]
        marker_choices = marker_choices_for_ui(channel_names)

        ensure_celltype_items()
        initialize_celltype_row_state_from_saved_cfg()

        st.write(f"Available markers: {', '.join(marker_choices)}")
        st.caption(
            "No priority is used. Each nucleus is tested against every defined cell type. "
            "If exactly one cell type matches, that type is assigned. "
            "If none match, the nucleus is labeled Unassigned. "
            "If more than one cell type matches, the nucleus is labeled Ambiguous."
        )

        top_cols = st.columns([1.0, 1.0, 5.8])
        with top_cols[0]:
            if st.button("Add cell type", key="add_celltype_btn"):
                add_celltype_item()
                st.rerun()
        with top_cols[1]:
            if st.button("Save cell types", type="primary", key="save_celltypes_btn"):
                try:
                    save_celltype_cfg_from_widgets(channel_names)
                    st.success("Cell-type configuration saved.")
                except Exception as exc:
                    st.error(str(exc))
        with top_cols[2]:
            st.caption(
                "Table-style editor: each row is one cell type. "
                "Use 'ALL positive' and 'ALL negative' for required markers. "
                "Use 'Any-positive groups' for markers where at least one selected marker must be positive."
            )

        header_cols = st.columns([1.3, 0.9, 1.9, 1.9, 4.2, 0.7])
        headers = ["Name", "Color", "ALL positive", "ALL negative", "Any-positive groups", "Remove"]
        for col, title in zip(header_cols, headers):
            with col:
                st.markdown(f"**{title}**")

        for idx, item in enumerate(st.session_state["celltype_items"]):
            uid = item["id"]
            default_name = item["default_name"]
            default_color = item["default_color"]

            st.markdown(f"###### Cell type {idx + 1}")
            row_cols = st.columns([1.3, 0.9, 1.9, 1.9, 4.2, 0.7])

            with row_cols[0]:
                st.text_input(
                    "Name",
                    value=st.session_state.get(f"ct_name_{uid}", default_name),
                    key=f"ct_name_{uid}",
                    label_visibility="collapsed",
                    placeholder="Cell type name",
                )
            with row_cols[1]:
                st.color_picker(
                    "Color",
                    value=st.session_state.get(f"ct_color_{uid}", default_color),
                    key=f"ct_color_{uid}",
                    label_visibility="collapsed",
                )
            with row_cols[2]:
                st.multiselect(
                    "ALL positive markers",
                    options=marker_choices,
                    key=f"ct_all_pos_{uid}",
                    label_visibility="collapsed",
                )
            with row_cols[3]:
                st.multiselect(
                    "ALL negative markers",
                    options=marker_choices,
                    key=f"ct_all_neg_{uid}",
                    label_visibility="collapsed",
                )
            with row_cols[4]:
                group_ids = ensure_any_group_slots(uid, min_groups=1)
                for group_index, group_id in enumerate(group_ids, start=1):
                    group_cols = st.columns([5.2, 0.9])
                    with group_cols[0]:
                        st.multiselect(
                            f"Any-positive group {group_index}",
                            options=marker_choices,
                            key=f"ct_any_group_{uid}_{group_id}",
                            label_visibility="collapsed",
                            placeholder=f"Choose markers for group {group_index}",
                        )
                    with group_cols[1]:
                        allow_remove = len(group_ids) > 1
                        if allow_remove and st.button("−", key=f"remove_any_group_{uid}_{group_id}", help="Remove this any-positive group"):
                            remove_any_group(uid, group_id)
                            st.rerun()
            with row_cols[5]:
                if st.button("✕", key=f"remove_{uid}", help="Remove this cell type"):
                    remove_celltype_item(uid)
                    st.rerun()

def render_assignment_params_tab(tab):
    with tab:
        st.subheader("Assignment parameters")
        config: PipelineConfig | None = st.session_state.get("config")
        if config is None:
            st.warning("Save the configuration first.")
            return
        if st.session_state.get("celltype_cfg") is None:
            st.warning("Save the cell-type configuration first.")
            return

        consume_pending_assignment_param_widget_update()

        st.caption(
            "These settings control how positive marker pixels are linked to each nucleus before the final cell-type label is assigned. "
            "The parameter boxes below are always the source of truth for the final cell-type assignment run."
        )

        cfg_signature = json.dumps(st.session_state.get("celltype_cfg"), sort_keys=True)
        if st.session_state.get("assignment_param_scan_initialized_cfg_signature") != cfg_signature:
            set_default_celltype_assignment_scan_candidates_from_widgets(force=True)
            st.session_state["assignment_param_scan_initialized_cfg_signature"] = cfg_signature
            st.session_state["assignment_param_scan_result"] = None
            st.session_state["assignment_param_scan_signature"] = None
            st.session_state["assignment_auto_scan_result"] = None
            st.session_state["last_applied_assignment_param_combo"] = None

        maybe_show_assignment_param_scan_notice()

        with st.expander("Optional parameter scan (advanced)", expanded=False):
            st.caption(
                "Optional: test multiple combinations of the cell-type assignment parameters and see how many nuclei are assigned to each defined cell type. "
                "You can skip this section and adjust the parameter boxes manually."
            )
            action_cols = st.columns([1.2, 1.2, 2.6])
            with action_cols[0]:
                rerun_scan_clicked = st.button("Run parameter scan", key="run_assignment_param_scan_btn")
            with action_cols[1]:
                if st.button("Reset scan candidates", key="reset_assignment_param_scan_btn"):
                    set_default_celltype_assignment_scan_candidates_from_widgets(force=True)
                    st.rerun()
            with action_cols[2]:
                st.caption(
                    "Tip: keep each candidate list compact. The scan tests the full Cartesian product across all listed values."
                )

            with st.expander("Candidate values to test", expanded=False):
                scan_cols = st.columns(2)
                numeric_fields = [field for field in CELLTYPE_PARAM_ORDER if CELLTYPE_ASSIGN_PARAM_SPECS[field]["kind"] != "choice"]
                for idx, field in enumerate(numeric_fields):
                    with scan_cols[idx % 2]:
                        st.text_input(
                            CELLTYPE_PARAM_LABELS[field],
                            key=CELLTYPE_ASSIGN_SCAN_TEXT_KEYS[field],
                            help="The scan uses every value listed here for this parameter.",
                        )
                st.multiselect(
                    "THRESH_MODE choices",
                    options=CELLTYPE_ASSIGN_PARAM_SPECS["thresh_mode"]["options"],
                    default=st.session_state.get(CELLTYPE_ASSIGN_SCAN_CHOICE_KEY, ["global_otsu", "yen", "triangle"]),
                    key=CELLTYPE_ASSIGN_SCAN_CHOICE_KEY,
                    help="Choose one or more thresholding methods to include in the scan.",
                )

            candidate_values = parse_celltype_assignment_scan_values_from_state()
            n_combinations = 1
            for field in CELLTYPE_PARAM_ORDER:
                n_combinations *= len(candidate_values[field])
            st.caption(
                f"Current scan size: {n_combinations} parameter combinations. "
                "The scan runs one combination at a time and reports counts for each defined cell type, plus Unassigned and Ambiguous."
            )

            current_signature = celltype_assignment_scan_signature(config, candidate_values)
            if rerun_scan_clicked:
                progress_text = st.empty()
                progress_bar = st.progress(0)
                try:
                    def _scan_progress(done: int, total: int) -> None:
                        total_safe = max(1, int(total))
                        done_safe = max(0, min(int(done), total_safe))
                        percent = int(round(100 * done_safe / total_safe))
                        progress_text.caption(
                            f"Running cell-type assignment parameter scan... {done_safe}/{total_safe} combinations completed"
                        )
                        progress_bar.progress(percent)

                    with st.spinner(
                        f"Running cell-type assignment parameter scan across {n_combinations} combinations one by one..."
                    ):
                        scan_result = run_current_celltype_assignment_parameter_scan(
                            config,
                            candidate_values,
                            progress_callback=_scan_progress,
                        )
                        st.session_state["assignment_param_scan_result"] = scan_result
                        st.session_state["assignment_param_scan_signature"] = current_signature
                        st.session_state["last_applied_assignment_param_combo"] = None
                        invalidate_output_zip_cache()
                        refresh_output_zip_state(config.save_dir)
                    progress_text.caption(f"Finished: {n_combinations}/{n_combinations} combinations completed")
                    progress_bar.progress(100)
                    st.success(f"Cell-type assignment parameter scan finished. Saved scan outputs to {get_section_output_dir(config, 'celltype_assignment_parameters')}")
                except Exception as exc:
                    progress_text.empty()
                    progress_bar.empty()
                    st.error(str(exc))

            scan_result = st.session_state.get("assignment_param_scan_result")
            best_row = None
            if scan_result is not None:
                st.markdown("#### Line plot of detected cell counts")
                st.caption("Each colored line uses the cell-type color defined in the Define cell types subsection.")
                plot_fig, _ = build_celltype_assignment_scan_plotly(scan_result["results"], st.session_state["celltype_cfg"])
                st.plotly_chart(
                    plot_fig,
                    key="celltype_assignment_scan_plot",
                    use_container_width=True,
                    config={"displaylogo": False},
                )

                st.markdown("#### Parameter-scan results preview")
                preview_df = scan_result["results"].copy()
                if "error" in preview_df.columns:
                    sort_cols = ["error", "assigned_defined_total", "count::Ambiguous", "count::Unassigned", "combo_index"]
                    existing_cols = [col for col in sort_cols if col in preview_df.columns]
                    asc = [True, False, True, True, True][:len(existing_cols)]
                    preview_df = preview_df.sort_values(existing_cols, ascending=asc)
                st.dataframe(preview_df, use_container_width=True, height=360)
                st.caption(
                    "Saved scan outputs: celltype_assignment_parameter_sweep_results.csv, "
                    "celltype_assignment_parameter_sweep_grid.json, celltype_assignment_parameter_sweep.svg, "
                    "and celltype_assignment_parameter_sweep.png. The saved SVG/PNG line plot uses the same cell-type colors."
                )

                best_row = recommend_celltype_assignment_parameter_sweep_result(
                    scan_result["results"],
                    defined_celltype_names=[ct["name"] for ct in st.session_state["celltype_cfg"]],
                )
                combo_max = int(scan_result["results"]["combo_index"].max()) if len(scan_result["results"]) > 0 else 0
                if best_row is not None:
                    valid_combo_indices = set(
                        scan_result["results"].loc[
                            scan_result["results"]["error"].fillna("") == "", "combo_index"
                        ].astype(int).tolist()
                    )
                    current_combo_choice = int(st.session_state.get("selected_assignment_param_combo_ui", 0) or 0)
                    if current_combo_choice not in valid_combo_indices:
                        st.session_state["selected_assignment_param_combo_ui"] = int(best_row["combo_index"])
                    st.info(f"Recommended scan result: {format_celltype_assignment_combo_summary(best_row)}")
                else:
                    st.warning("The current parameter scan did not produce a successful combination to recommend.")

                combo_cols = st.columns([1.8, 2.7, 4.7])
                with combo_cols[0]:
                    st.number_input(
                        "Combo # to apply",
                        min_value=0,
                        max_value=max(0, combo_max),
                        step=1,
                        key="selected_assignment_param_combo_ui",
                        help="Choose which scan combination to apply into the editable cell-type assignment parameters below. Running the final assignment always uses the current parameter values shown below.",
                    )
                with combo_cols[1]:
                    copy_selected_clicked = st.button(
                        "Apply current combo selection as parameters",
                        key="copy_assignment_param_combo_btn",
                        help="Copy the currently selected scan combination into the editable cell-type assignment parameters below.",
                    )
                with combo_cols[2]:
                    st.caption(
                        "This button copies the selected scan combination into the editable cell-type assignment parameters below. "
                        "It does not run the final cell-type assignment. After applying it, you can still manually adjust any parameter, and the final run will use those edited values."
                    )

                selected_combo_index = int(st.session_state.get("selected_assignment_param_combo_ui", 0) or 0)
                if copy_selected_clicked:
                    if selected_combo_index <= 0:
                        st.info("Choose a combo number greater than 0 to apply a scanned combination to the current cell-type assignment parameters.")
                    else:
                        selected_row = get_scan_row_by_combo_index(scan_result["results"], selected_combo_index)
                        if selected_row is None:
                            st.error(f"Combination #{selected_combo_index} was not found in the current scan results.")
                        elif str(selected_row.get("error", "") or "").strip():
                            st.error(
                                f"Combination #{selected_combo_index} failed during the scan and cannot be applied: {selected_row['error']}"
                            )
                        else:
                            stage_assignment_param_scan_row_for_widget_update(selected_row)
                            st.rerun()

        with st.expander("Optional automatic full-range optimizer", expanded=False):
            full_search_space_specs = celltype_assignment_optimizer_search_space_specs()
            core_search_space_specs = celltype_assignment_optimizer_core_search_space_specs()
            full_search_space_n_combinations = count_celltype_assignment_search_space_combinations(full_search_space_specs)
            core_search_space_n_combinations = count_celltype_assignment_search_space_combinations(core_search_space_specs)

            st.caption(
                "Optional: one click to search the full slider-defined cell-type assignment parameter space automatically. "
                "To speed up the search, the optimizer divides the image into 10 equal-width vertical bands and "
                "evaluates 5 alternating bands instead of the whole image."
            )
            st.info(
                f"The full slider-defined search space contains {full_search_space_n_combinations:,} possible "
                f"step-defined parameter combinations. The preserved practical/core region contains "
                f"{core_search_space_n_combinations:,} combinations. When the full space is too large to enumerate "
                "directly, the app automatically uses a core-first optimizer: it searches the practical/core region "
                "first, then uses an evolutionary/genetic search with successive halving to explore the broader outer range. "
                "The search objective is to minimize unresolved cells on the selected vertical-band sample, where unresolved means Ambiguous + Unassigned."
            )
            st.caption(
                "The default sampled bands are 1, 3, 5, 7, and 9 out of 10, covering about 50% of the image area. "
                "Applying a selected optimizer combo still copies the parameters into the full-image final assignment settings below."
            )

            st.slider(
                "CPU to use for automatic optimizer (%)",
                min_value=10,
                max_value=100,
                step=5,
                key="assignment_auto_scan_cpu_percent_ui",
                help="Higher values usually finish faster, but they use more CPU and memory.",
            )
            assignment_auto_parallel_config = get_scan_parallel_config(
                cpu_percent_key="assignment_auto_scan_cpu_percent_ui",
                state_prefix="assignment_auto_scan",
            )
            assignment_auto_parallel_workers = int(assignment_auto_parallel_config["parallel_workers"])
            assignment_core_target_evaluations = max(128, min(768, assignment_auto_parallel_workers * 24))
            assignment_expansion_target_evaluations = max(96, min(512, assignment_auto_parallel_workers * 16))

            st.caption(
                f"At the current CPU setting, the optimizer will use about {assignment_auto_parallel_workers} worker(s), "
                f"test about {assignment_core_target_evaluations:,} combinations in the practical/core range first, "
                f"then fully evaluate about {assignment_expansion_target_evaluations:,} additional combinations in the expanded outer range "
                "after cheaply screening a larger offspring pool with a successive-halving evolutionary/genetic search. "
                "The ranking objective is unresolved total first (Ambiguous + Unassigned), then Ambiguous, then Unassigned. "
                "All of those evaluations are done on the selected vertical-band sample."
            )

            with st.expander("Full parameter search space", expanded=False):
                st.dataframe(
                    make_celltype_assignment_search_space_summary_df(full_search_space_specs),
                    use_container_width=True,
                )

            with st.expander("Core parameter search space searched first", expanded=False):
                st.dataframe(
                    make_celltype_assignment_search_space_summary_df(core_search_space_specs),
                    use_container_width=True,
                )

            assignment_auto_scan_clicked = st.button(
                "Run automatic optimizer",
                key="run_assignment_auto_scan_btn",
            )
            if assignment_auto_scan_clicked:
                try:
                    with st.spinner(
                        f"Running the automatic full-range cell-type assignment optimizer with about "
                        f"{assignment_auto_parallel_workers} worker(s)..."
                    ):
                        auto_scan_result = run_current_celltype_assignment_parameter_optimizer(
                            config,
                            cpu_percent_key="assignment_auto_scan_cpu_percent_ui",
                            state_prefix="assignment_auto_scan",
                            output_prefix="celltype_assignment_auto_optimizer",
                        )
                        st.session_state["assignment_auto_scan_result"] = auto_scan_result
                        st.session_state["last_applied_assignment_param_combo"] = None
                        invalidate_output_zip_cache()
                        refresh_output_zip_state(config.save_dir)
                    st.success(
                        f"Automatic cell-type assignment optimizer finished on the vertical-band sample. Saved optimizer files to {get_section_output_dir(config, 'celltype_assignment_parameters')}"
                    )
                except Exception as exc:
                    st.error(str(exc))

            auto_scan_result = st.session_state.get("assignment_auto_scan_result")
            if auto_scan_result is not None:
                search_mode = str(auto_scan_result.get("search_mode") or "adaptive_global_search")
                if search_mode == "exhaustive_full_grid":
                    st.info(
                        f"Optimizer mode: full exhaustive search. This run evaluated "
                        f"{int(auto_scan_result.get('evaluated_unique_combinations', 0)):,} unique combinations "
                        f"out of {int(auto_scan_result.get('full_space_n_combinations', 0)):,} possible step-defined combinations "
                        "on the selected vertical-band sample."
                    )
                else:
                    selected_band_indices = [
                        int(v) + 1 for v in (auto_scan_result.get("selected_vertical_band_indices") or [])
                    ]
                    selected_band_text = (
                        ", ".join(str(v) for v in selected_band_indices)
                        if selected_band_indices
                        else "1, 3, 5, 7, 9"
                    )
                    st.info(
                        f"Optimizer mode: core-first search with successive-halving evolutionary expanded-range exploration. This run evaluated "
                        f"{int(auto_scan_result.get('evaluated_unique_combinations', 0)):,} unique combinations "
                        f"out of {int(auto_scan_result.get('full_space_n_combinations', 0)):,} possible step-defined combinations. "
                        f"Core-range evaluations: {int(auto_scan_result.get('evaluated_priority_combinations', 0)):,}. "
                        f"Expanded-range evaluations: {int(auto_scan_result.get('evaluated_expansion_combinations', 0)):,}. "
                        f"Cheap-screened offspring: {int(auto_scan_result.get('expansion_screened_candidate_count', 0)):,}. "
                        f"Screening rounds: {int(auto_scan_result.get('expansion_screening_round_count', 0)):,}. "
                        f"Genetic generations: {int(auto_scan_result.get('expansion_generation_count', 0)):,}. "
                        f"Population size: {int(auto_scan_result.get('expansion_population_size', 0)):,}. "
                        f"Sampled bands: {selected_band_text} of {int(auto_scan_result.get('vertical_band_count', 10))}. "
                        f"Sampled area: {100.0 * float(auto_scan_result.get('roi_sampled_area_fraction', 0.50)):.1f}% of the image."
                    )
                    screening_stage_factors = auto_scan_result.get("expansion_screening_stage_factors") or []
                    if screening_stage_factors:
                        st.caption(
                            "Successive-halving screening used downsample factors: "
                            + ", ".join(f"{int(factor)}x" for factor in screening_stage_factors)
                        )

                st.markdown("#### Automatic optimizer line plot of detected cell counts")
                st.caption("Each colored line uses the cell-type color defined in the Define cell types subsection. Counts come from the selected vertical-band sample.")
                plot_fig, _ = build_celltype_assignment_scan_plotly(auto_scan_result["results"], st.session_state["celltype_cfg"])
                st.plotly_chart(
                    plot_fig,
                    key="celltype_assignment_auto_scan_plot",
                    use_container_width=True,
                    config={"displaylogo": False},
                )

                st.markdown("#### Automatic optimizer results ranked by lowest unresolved cells")
                preview_df = auto_scan_result["results"].copy()
                if "error" in preview_df.columns:
                    sort_cols = [
                        "error",
                        "unresolved_total",
                        "count::Ambiguous",
                        "count::Unassigned",
                        "assigned_defined_total",
                        "combo_index",
                    ]
                    existing_cols = [col for col in sort_cols if col in preview_df.columns]
                    ascending_map = {
                        "error": True,
                        "unresolved_total": True,
                        "count::Ambiguous": True,
                        "count::Unassigned": True,
                        "assigned_defined_total": False,
                        "combo_index": True,
                    }
                    preview_df = preview_df.sort_values(existing_cols, ascending=[ascending_map[col] for col in existing_cols])
                st.dataframe(preview_df, use_container_width=True, height=360)
                st.caption(
                    "Saved automatic optimizer outputs: celltype_assignment_auto_optimizer_results.csv, "
                    "celltype_assignment_auto_optimizer_grid.json, celltype_assignment_auto_optimizer.svg, "
                    "and celltype_assignment_auto_optimizer.png. These optimizer counts are based on the selected vertical-band sample."
                )

                best_row = recommend_celltype_assignment_optimizer_result(
                    auto_scan_result["results"],
                    defined_celltype_names=[ct["name"] for ct in st.session_state["celltype_cfg"]],
                )
                combo_max = int(auto_scan_result["results"]["combo_index"].max()) if len(auto_scan_result["results"]) > 0 else 0
                if best_row is not None:
                    valid_combo_indices = set(
                        auto_scan_result["results"].loc[
                            auto_scan_result["results"]["error"].fillna("") == "", "combo_index"
                        ].astype(int).tolist()
                    )
                    current_combo_choice = int(st.session_state.get("selected_assignment_auto_combo_ui", 0) or 0)
                    if current_combo_choice not in valid_combo_indices:
                        st.session_state["selected_assignment_auto_combo_ui"] = int(best_row["combo_index"])
                    st.info(f"Recommended automatic optimizer result: {format_celltype_assignment_combo_summary(best_row)}")
                else:
                    st.warning("The current automatic optimizer run did not produce a successful combination to recommend.")

                combo_cols = st.columns([1.8, 2.7, 4.7])
                with combo_cols[0]:
                    st.number_input(
                        "Optimizer combo # to apply",
                        min_value=0,
                        max_value=max(0, combo_max),
                        step=1,
                        key="selected_assignment_auto_combo_ui",
                        help="Choose which automatic optimizer combination to apply into the editable cell-type assignment parameters below.",
                    )
                with combo_cols[1]:
                    copy_selected_clicked = st.button(
                        "Apply optimizer combo as parameters",
                        key="copy_assignment_auto_combo_btn",
                        help="Copy the currently selected automatic optimizer combination into the editable cell-type assignment parameters below.",
                    )
                with combo_cols[2]:
                    st.caption(
                        "This button copies the selected optimizer combination into the editable cell-type assignment parameters below, "
                        "including the ambiguous-cell resolution settings when present. It does not run the final assignment, and the final assignment still runs on the full image."
                    )

                selected_combo_index = int(st.session_state.get("selected_assignment_auto_combo_ui", 0) or 0)
                if copy_selected_clicked:
                    if selected_combo_index <= 0:
                        st.info("Choose a combo number greater than 0 to apply an automatic optimizer combination to the current cell-type assignment parameters.")
                    else:
                        selected_row = get_scan_row_by_combo_index(auto_scan_result["results"], selected_combo_index)
                        if selected_row is None:
                            st.error(f"Combination #{selected_combo_index} was not found in the current automatic optimizer results.")
                        elif str(selected_row.get("error", "") or "").strip():
                            st.error(
                                f"Combination #{selected_combo_index} failed during the automatic optimizer run and cannot be applied: {selected_row['error']}"
                            )
                        else:
                            stage_assignment_param_scan_row_for_widget_update(selected_row)
                            st.rerun()

        st.markdown("#### Main assignment parameters")
        col1, col2 = st.columns(2)
        with col1:
            st.slider("R_VORONOI_UM", min_value=0.0, max_value=20.0, value=3.0, step=0.5, key="ct_assign_r_voronoi_um_ui")
            st.slider("R_BUFFER_UM", min_value=0.0, max_value=20.0, value=2.0, step=0.5, key="ct_assign_r_buffer_um_ui")
            st.slider("R_VOTE_UM", min_value=0.0, max_value=20.0, value=3.0, step=0.5, key="ct_assign_r_vote_um_ui")
            st.slider("TOPHAT_R_UM", min_value=0.0, max_value=8.0, value=1.0, step=0.5, key="ct_assign_tophat_r_um_ui")
        with col2:
            st.slider("GAUSS_SIGMA_UM", min_value=0.0, max_value=3.0, value=0.5, step=0.1, key="ct_assign_gauss_sigma_um_ui")
            thresh_cols = st.columns([1.0, 1.2])
            with thresh_cols[0]:
                st.selectbox(
                    "THRESH_MODE",
                    options=CELLTYPE_ASSIGN_PARAM_SPECS["thresh_mode"]["options"],
                    key="ct_assign_thresh_mode_ui",
                )
            with thresh_cols[1]:
                st.markdown(THRESH_MODE_HELP_MD)
            st.number_input("MIN_POS_OBJECT_SIZE_PX", min_value=0, max_value=200, value=9, step=1, key="ct_assign_min_pos_object_size_px_ui")
            st.number_input("MIN_POS_PIX", min_value=0, max_value=200, value=5, step=1, key="ct_assign_min_pos_pix_ui")

        st.markdown("#### Ambiguous-cell resolution")
        amb_cols = st.columns([1.35, 1.4, 1.4, 3.0])
        with amb_cols[0]:
            st.checkbox(
                "Resolve ambiguous cells",
                value=True,
                key="ct_assign_resolve_ambiguous_ui",
                help="Try to reassign cells that match more than one cell type using probability-style evidence scores.",
            )
        with amb_cols[1]:
            st.slider(
                "Minimum winning probability",
                min_value=0.00,
                max_value=0.99,
                value=0.60,
                step=0.01,
                key="ct_assign_ambiguous_min_probability_ui",
            )
        with amb_cols[2]:
            st.slider(
                "Minimum probability gap",
                min_value=0.00,
                max_value=0.50,
                value=0.10,
                step=0.01,
                key="ct_assign_ambiguous_min_gap_ui",
            )
        with amb_cols[3]:
            st.caption(
                "If a nucleus matches multiple cell types, the app estimates a probability-style score for each matched type. "
                "The nucleus is reassigned only when the best-matching type is clearly stronger than the runner-up; otherwise it stays Ambiguous."
            )


def render_assignment_tab(tab):
    with tab:
        st.subheader("Cell type assignments")
        config: PipelineConfig | None = st.session_state.get("config")
        if config is None:
            st.warning("Save the configuration first.")
            return
        if st.session_state.get("celltype_cfg") is None:
            st.warning("Save the cell-type configuration first.")
            return

        st.caption(
            "This step uses the current values from the Assignment parameters subsection. "
            "If you copied a scan combo into those boxes and then manually edited some values, the edited box values are what will be used here."
        )

        if st.button("Run cell-type assignment", type="primary", key="run_assignment_btn"):
            try:
                with st.spinner("Assigning marker positivity and cell types..."):
                    result = run_current_celltype_assignment(config)
                    _close_result_figures(st.session_state.get("assignment_result"))
                    st.session_state["assignment_result"] = result
                    invalidate_after_assignment_change()
                    refresh_output_zip_state(config.save_dir)
                st.success(f"Cell-type assignment finished. Outputs are in {get_section_output_dir(config, 'celltype_assignment')}")
            except Exception as exc:
                st.error(str(exc))

        assignment_result = st.session_state.get("assignment_result")
        if assignment_result is None:
            try:
                assignment_result = ensure_assignment_outputs_available()
            except Exception:
                assignment_result = None

        assignment_saved_panel_paths = [
            assignment_result.get("saved_paths", {}).get("panel_png") if assignment_result else None,
            assignment_result.get("saved_paths", {}).get("panel_svg") if assignment_result else None,
            get_section_output_dir(config, 'celltype_assignment') / "celltypes_panel.png",
            get_section_output_dir(config, 'celltype_assignment') / "celltypes_panel.svg",
        ]
        assignment_saved_split_paths = [
            assignment_result.get("saved_paths", {}).get("split_png") if assignment_result else None,
            assignment_result.get("saved_paths", {}).get("split_svg") if assignment_result else None,
            get_section_output_dir(config, 'celltype_assignment') / "celltypes_split_panels.png",
            get_section_output_dir(config, 'celltype_assignment') / "celltypes_split_panels.svg",
        ]
        if assignment_result is not None:
            st.markdown("#### celltype_counts.csv")
            st.dataframe(assignment_result["counts"], use_container_width=True)
            if assignment_result.get("thresholds") is not None:
                with st.expander("Marker assignment thresholds"):
                    st.dataframe(assignment_result["thresholds"], use_container_width=True)
        if assignment_result is not None or any(Path(p).exists() for p in assignment_saved_panel_paths if p):
            st.markdown("#### Cell-type panel")
            render_zoomable_figure(
                fig=assignment_result.get("panel_figure") if assignment_result else None,
                component_key="celltype_panel_result",
                saved_paths=assignment_saved_panel_paths,
                component_height=1200,
            )
        if assignment_result is not None or any(Path(p).exists() for p in assignment_saved_split_paths if p):
            st.markdown("#### Split panels")
            render_zoomable_figure(
                fig=assignment_result.get("split_figure") if assignment_result else None,
                component_key="celltype_split_result",
                saved_paths=assignment_saved_split_paths,
                component_height=1200,
            )
        if assignment_result is not None:
            st.markdown("#### cells_summary.csv preview")
            st.dataframe(assignment_result["df_cells"].head(20), use_container_width=True)



def render_celltype_assignments_tab(tab):
    with tab:
        st.subheader("Cell type assignments")
        st.caption(
            "This workflow is organized into three subsections so you can define cell types, tune assignment parameters, and then run the final cell-type assignment in order."
        )
        define_tab, params_tab, assignment_tab = st.tabs(
            ["Define cell types", "Assignment parameters", "Cell type assignments"]
        )
        render_celltypes_tab(define_tab)
        render_assignment_params_tab(params_tab)
        render_assignment_tab(assignment_tab)






def render_neighborhood_tab(tab):
    with tab:
        st.subheader("Neighborhood analysis")
        config: PipelineConfig | None = st.session_state.get("config")
        if config is None:
            st.warning("Save the configuration first.")
            return
        if st.session_state.get("celltype_cfg") is None:
            st.warning("Save the cell-type configuration first.")
            return

        try:
            assignment_result = ensure_assignment_outputs_available()
        except Exception as exc:
            st.warning(str(exc))
            return

        st.caption(
            "Optional independent analysis: divide the image into user-defined square neighborhoods and assign a cluster to each occupied square based on the set of identified cell types present in that square. "
            "Cells labeled Unassigned or Ambiguous are excluded from this analysis. You can skip this tab and go directly to Region analysis."
        )

        ctrl_cols = st.columns([1.5, 3.8])
        with ctrl_cols[0]:
            st.number_input(
                "Neighborhood square size (µm)",
                min_value=1.0,
                value=20.0,
                step=1.0,
                key="neighborhood_grid_um",
                help="The image is partitioned into square neighborhoods of this side length in microns.",
            )
        with ctrl_cols[1]:
            st.caption(
                "Each unique combination of valid assigned cell types within a square becomes one neighborhood cluster. "
                "The whole image is then masked by these clusters."
            )

        if st.button("Run neighborhood analysis", type="primary", key="run_neighborhood_btn"):
            try:
                result = run_neighborhood_analysis(
                    df_cells=assignment_result["df_cells"],
                    image_shape=assignment_result["celltype_mask"].shape,
                    pixel_size_um=config.pixel_size_um,
                    grid_size_um=float(st.session_state.get("neighborhood_grid_um", 20.0) or 20.0),
                )
                _close_result_figures(st.session_state.get("neighborhood_result"))
                st.session_state["neighborhood_result"] = result
                st.session_state["neighborhood_saved_signature"] = None
                st.session_state["neighborhood_cluster_color_shuffle_index"] = 0
                st.session_state["cell_distribution_cluster_result"] = None
                sync_neighborhood_cluster_color_state(result.get("cluster_labels", []), reset_colors=True)
                st.session_state["neighborhood_display_clusters"] = list(result.get("cluster_labels", []))
                maybe_persist_neighborhood_outputs(config, display_cluster_labels=result.get("cluster_labels", []))
                st.success(f"Neighborhood analysis finished. Outputs are in {get_section_output_dir(config, 'neighborhood_analysis')}")
            except Exception as exc:
                st.error(str(exc))

        neighborhood_result = st.session_state.get("neighborhood_result")
        if not isinstance(neighborhood_result, dict):
            st.info("Run neighborhood analysis to generate neighborhood clusters.")
            return

        cluster_labels = _neighborhood_cluster_labels_from_result(neighborhood_result)
        if cluster_labels:
            st.markdown("### Adjust the display")
            st.caption("Choose which neighborhood clusters to show in the figure and keep in the saved neighborhood-analysis outputs.")
            selected_display_clusters = _sanitize_neighborhood_display_clusters(cluster_labels)

            selection_cols = st.columns([2.4, 3.6])
            with selection_cols[0]:
                selected_display_clusters = st.multiselect(
                    "Cluster types to display and save",
                    options=cluster_labels,
                    default=selected_display_clusters,
                    key="neighborhood_display_clusters",
                    help="Only the selected cluster types are shown in the neighborhood figure and saved outputs.",
                )
            with selection_cols[1]:
                st.caption(
                    "Choose which neighborhood clusters to show in the figure and write to the saved neighborhood-analysis outputs. "
                    "The analysis itself still keeps the full underlying tile assignments in memory."
                )

            color_action_cols = st.columns([1.4, 4.6])
            with color_action_cols[0]:
                if st.button("Reassign automatic cluster colors", key="neighborhood_reset_colors_btn"):
                    st.session_state["neighborhood_cluster_color_shuffle_index"] = int(st.session_state.get("neighborhood_cluster_color_shuffle_index", 0)) + 1
                    sync_neighborhood_cluster_color_state(cluster_labels, reset_colors=True)
                    st.session_state["neighborhood_saved_signature"] = None
                    st.rerun()
            with color_action_cols[1]:
                st.caption(
                    "Use the compact cluster-color table below to adjust colors. The figure and saved neighborhood-analysis outputs are refreshed automatically when the displayed clusters or colors change."
                )

            st.markdown("#### Cluster color table")
            n_color_cols = 3 if len(cluster_labels) <= 9 else 4
            for row_start in range(0, len(cluster_labels), n_color_cols):
                color_cols = st.columns(n_color_cols, gap="medium")
                for local_idx, label in enumerate(cluster_labels[row_start:row_start + n_color_cols]):
                    idx = row_start + local_idx
                    with color_cols[local_idx]:
                        st.text_input(
                            f"Neighborhood cluster label {idx + 1}",
                            value=str(label),
                            disabled=True,
                            key=f"neighborhood_cluster_label_display_{idx}",
                            label_visibility="collapsed",
                        )
                        st.color_picker(
                            f"Cluster color {idx + 1}",
                            key=f"neighborhood_cluster_color_{idx}",
                            label_visibility="collapsed",
                        )

            if not selected_display_clusters:
                st.warning("Select at least one cluster type to display and save.")
            else:
                maybe_persist_neighborhood_outputs(config, display_cluster_labels=selected_display_clusters)

        neighborhood_result = st.session_state.get("neighborhood_result")
        saved_paths = neighborhood_result.get("saved_paths", {}) if isinstance(neighborhood_result, dict) else {}
        figure = neighborhood_result.get("figure") if isinstance(neighborhood_result, dict) else None
        if figure is not None or saved_paths:
            render_zoomable_figure(
                fig=figure,
                component_key="neighborhood_clusters_result",
                saved_paths=[saved_paths.get("png"), saved_paths.get("svg")],
                component_height=980,
            )

        summary_df = neighborhood_result.get("cluster_summary", pd.DataFrame()) if isinstance(neighborhood_result, dict) else pd.DataFrame()
        tile_df = neighborhood_result.get("tile_assignments", pd.DataFrame()) if isinstance(neighborhood_result, dict) else pd.DataFrame()
        displayed_labels = neighborhood_result.get("display_cluster_labels", cluster_labels) if isinstance(neighborhood_result, dict) else []
        if displayed_labels and len(summary_df) > 0:
            summary_df = summary_df[summary_df["cluster_label"].astype(str).isin([str(v) for v in displayed_labels])].copy()
        if displayed_labels and len(tile_df) > 0:
            tile_df = tile_df[tile_df["cluster_label"].astype(str).isin([str(v) for v in displayed_labels])].copy()

        info_cols = st.columns(2)
        with info_cols[0]:
            st.markdown("#### Neighborhood cluster summary")
            st.dataframe(summary_df, use_container_width=True)
        with info_cols[1]:
            st.markdown("#### Neighborhood tile assignments preview")
            st.dataframe(tile_df.head(50), use_container_width=True)


def render_regions_tab(tab):
    with tab:
        st.markdown('<div style="font-size:1.95rem;font-weight:700;margin-bottom:0.35rem;">Region analysis</div>', unsafe_allow_html=True)
        config: PipelineConfig | None = st.session_state.get("config")
        if config is None:
            st.warning("Save the configuration first.")
            return
        if st.session_state.get("celltype_cfg") is None:
            st.warning("Save the cell-type configuration first.")
            return

        try:
            assignment_result = ensure_assignment_outputs_available()
        except Exception as exc:
            st.warning(str(exc))
            return

        ensure_pixels_loaded()
        data_result = st.session_state.get("data_result") or {}
        st.session_state.setdefault("region_boundary_color", "#a1d99b")

        celltype_names = [ct["name"] for ct in st.session_state["celltype_cfg"]]
        present_types = sorted(set(assignment_result["df_cells"]["celltype"].astype(str)))
        celltype_names = [name for name in celltype_names if name in present_types] or present_types

        st.markdown("### ROI parameters")
        st.caption(
            "These ROI parameters are shared across computational ROI identification and adjusted computational ROIs. "
            "For adjusted ROIs, the saved mask is post-processed using the current Close, Dilate, Min area, and Min cells values."
        )

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.slider("Close (µm)", min_value=0.0, max_value=80.0, value=15.0, step=1.0, key="region_close_um")
        with col2:
            st.slider("Dilate (µm)", min_value=0.0, max_value=80.0, value=10.0, step=1.0, key="region_dilate_um")
        with col3:
            st.number_input("Min area (µm²)", min_value=0.0, value=20000.0, step=1000.0, key="region_min_area_um2")
        with col4:
            st.number_input("Min cells", min_value=1, value=5, step=1, key="region_min_cells")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.selectbox("Contour downsample", options=[1, 2, 4, 8], index=1, key="region_contour_ds")
        with col2:
            st.slider("Boundary line width", min_value=0.5, max_value=10.0, value=2.0, step=0.5, key="region_line_width")
        with col3:
            st.selectbox(
                "Boundary line style",
                options=["-", "--", "-.", ":"],
                format_func=lambda v: {"-": "Solid", "--": "Dashed", "-.": "Dash-dot", ":": "Dotted"}[v],
                key="region_line_style",
            )

        st.checkbox("Use each type's own color for the boundary", value=False, key="region_use_type_colors")

        st.markdown("### Computational ROI identification")
        st.multiselect(
            "Select one or more cell types to define computational ROIs",
            options=celltype_names,
            default=celltype_names[:1],
            key="region_selected_types",
        )

        if st.button("Run ROI identification + counts", type="primary", key="run_region_btn"):
            try:
                selected_types = list(st.session_state.get("region_selected_types", []))
                if not selected_types:
                    raise RuntimeError("Please select at least one cell type.")
                params = RegionParams(
                    selected_types=selected_types,
                    close_um=float(st.session_state["region_close_um"]),
                    dilate_um=float(st.session_state["region_dilate_um"]),
                    min_area_um2=float(st.session_state["region_min_area_um2"]),
                    min_cells=int(st.session_state["region_min_cells"]),
                    contour_downsample=int(st.session_state["region_contour_ds"]),
                    line_width=float(st.session_state["region_line_width"]),
                    line_style=str(st.session_state["region_line_style"]),
                    boundary_color=str(st.session_state["region_boundary_color"]),
                    use_type_colors=bool(st.session_state["region_use_type_colors"]),
                )
                with st.spinner("Building ROI masks and counting cells..."):
                    result = run_region_boundary_analysis(
                        df_cells=assignment_result["df_cells"],
                        celltype_mask=assignment_result["celltype_mask"],
                        celltype_cfg=st.session_state["celltype_cfg"],
                        save_dir=get_section_output_dir(config, 'region_analysis'),
                        pixel_size_um=config.pixel_size_um,
                        params=params,
                        save_outputs=True,
                    )
                    _close_region_nested_results(st.session_state.get("region_result"))
                    st.session_state["region_result"] = result
                    st.session_state["region_adjust_canvas_version"] = int(st.session_state.get("region_adjust_canvas_version", 0)) + 1
                    st.session_state["region_adjust_preview_mask"] = None
                    st.session_state["region_integrated_result"] = None
                    st.session_state["cell_distribution_region_masks_result"] = None
                    st.session_state["cell_distribution_density_result"] = None
                    st.session_state["cell_distribution_cluster_result"] = None
                    invalidate_output_zip_cache()
                    refresh_output_zip_state(config.save_dir)
                st.success(f"Computational ROI identification finished. Outputs are in {get_section_output_dir(config, 'region_analysis')}")
            except Exception as exc:
                st.error(str(exc))

        region_result = st.session_state.get("region_result")
        available_boundary_types: list[str] = []
        display_types: list[str] = []
        display_celltypes: list[str] = []
        if region_result is None:
            st.info("Run Computational ROI identification to generate computational ROIs.")
        else:
            available_boundary_types = list(region_result.get("masks", {}).keys())
            if not available_boundary_types:
                st.info("No ROI masks were generated for the selected cell types.")
            else:
                current_display_default = [name for name in st.session_state.get("region_display_types", available_boundary_types) if name in available_boundary_types] or available_boundary_types
                current_celltype_display_default = [name for name in st.session_state.get("region_display_celltypes", celltype_names) if name in celltype_names] or celltype_names

                display_ctrl_cols = st.columns(2)
                with display_ctrl_cols[0]:
                    st.multiselect(
                        "Computational ROIs to display",
                        options=available_boundary_types,
                        default=current_display_default,
                        key="region_display_types",
                        help="Choose which computational ROI types to show in the figure below.",
                    )
                with display_ctrl_cols[1]:
                    st.multiselect(
                        "Cell types to display",
                        options=celltype_names,
                        default=current_celltype_display_default,
                        key="region_display_celltypes",
                        help="Choose which cell types from the cell-type assignment mask to show underneath the displayed ROIs.",
                    )

                display_types = [name for name in st.session_state.get("region_display_types", []) if name in available_boundary_types] or available_boundary_types
                display_celltypes = [name for name in st.session_state.get("region_display_celltypes", []) if name in celltype_names] or celltype_names

                display_fig = make_region_overlay_figure(
                    celltype_mask=assignment_result["celltype_mask"],
                    celltype_cfg=st.session_state["celltype_cfg"],
                    masks=region_result["masks"],
                    selected_types=display_types,
                    display_celltypes=display_celltypes,
                    pixel_size_um=config.pixel_size_um,
                    title="Computed ROIs: " + ", ".join(display_types),
                    line_width=float(st.session_state["region_line_width"]),
                    line_style=str(st.session_state["region_line_style"]),
                    boundary_color=str(st.session_state["region_boundary_color"]),
                    use_type_colors=bool(st.session_state["region_use_type_colors"]),
                    contour_downsample=int(st.session_state["region_contour_ds"]),
                )
                try:
                    render_zoomable_figure(
                        fig=display_fig,
                        component_key="region_analysis_result_display",
                        saved_paths=None,
                        component_height=900,
                    )
                finally:
                    _close_figure_obj(display_fig)

                info_cols = st.columns(2)
                with info_cols[0]:
                    st.markdown("#### celltype_counts_by_region preview")
                    st.dataframe(region_result["counts_by_region"], use_container_width=True)
                with info_cols[1]:
                    st.markdown("#### region_area_summary preview")
                    st.dataframe(region_result.get("area_summary", pd.DataFrame()), use_container_width=True)

        st.markdown("### Optional tools")
        st.markdown("#### Adjustment for computational ROI")
        if region_result is None:
            st.info("Run Computational ROI identification first to enable adjustment of computational ROIs.")
        else:
            try:
                _render_manual_boundary_adjustment_ui(
                    config=config,
                    assignment_result=assignment_result,
                    region_result=region_result,
                    celltype_names=celltype_names,
                    available_boundary_types=available_boundary_types,
                    display_types=display_types,
                    display_celltypes=display_celltypes,
                )
            except Exception as exc:
                st.error(f"Adjusted computational ROI UI failed: {exc}")

        st.markdown("### Customized display and save")
        st.caption(
            "Select which computed and/or adjusted boundaries to include in an export. "
            "Saving also writes an original unmodified export into a separate output folder automatically."
        )
        _render_integrated_region_selection_ui(
            config=config,
            data_result=data_result,
            assignment_result=assignment_result,
            region_result=st.session_state.get("region_result"),
            celltype_names=celltype_names,
        )
def render_distance_tab(tab):
    with tab:
        st.subheader("Distance analyses")
        config: PipelineConfig | None = st.session_state.get("config")
        if config is None:
            st.warning("Save the configuration first.")
            return
        if st.session_state.get("celltype_cfg") is None:
            st.warning("Save the cell-type configuration first.")
            return

        try:
            assignment_result = ensure_assignment_outputs_available()
        except Exception as exc:
            st.warning(str(exc))
            return

        df_cells = assignment_result["df_cells"]
        celltype_mask = assignment_result["celltype_mask"]

        present_types = sorted(set(df_cells["celltype"].astype(str)))
        nn_tab, boundary_tab = st.tabs(["Nearest-neighbor distances", "Cell-to-boundary distances"])

        with nn_tab:
            col1, col2 = st.columns(2)
            with col1:
                st.selectbox("Target cell type", options=present_types, key="nn_target_type")
            with col2:
                st.multiselect(
                    "Query cell types",
                    options=present_types,
                    default=present_types[:1],
                    key="nn_query_types",
                )
            if st.button("Compute nearest-neighbor distances", type="primary", key="run_nn_btn"):
                try:
                    queries = list(st.session_state.get("nn_query_types", []))
                    if not queries:
                        raise RuntimeError("Select at least one query cell type.")
                    with st.spinner("Computing nearest-neighbor distances..."):
                        result = run_nearest_neighbor_analysis(
                            df_cells=df_cells,
                            celltype_cfg=st.session_state["celltype_cfg"],
                            save_dir=get_section_output_dir(config, 'distance_analysis'),
                            pixel_size_um=config.pixel_size_um,
                            target_type=st.session_state["nn_target_type"],
                            query_types=queries,
                            save_outputs=True,
                        )
                        _close_result_figures(st.session_state.get("nn_result"))
                        st.session_state["nn_result"] = result
                        invalidate_output_zip_cache()
                        refresh_output_zip_state(config.save_dir)
                    st.success("Nearest-neighbor distance analysis finished.")
                except Exception as exc:
                    st.error(str(exc))

            nn_result = st.session_state.get("nn_result")
            if nn_result is not None:
                render_zoomable_figure(
                    fig=nn_result.get("figure"),
                    component_key="nearest_neighbor_result",
                    saved_paths=[
                        nn_result.get("saved_paths", {}).get("svg"),
                        nn_result.get("saved_paths", {}).get("png"),
                    ],
                    component_height=860,
                )
                st.markdown("#### Distances preview")
                st.dataframe(nn_result["distances"].head(20), use_container_width=True)
                if not nn_result["ttests"].empty:
                    st.markdown("#### Paired t-tests")
                    st.dataframe(nn_result["ttests"], use_container_width=True)

        with boundary_tab:
            boundary_candidates = discover_boundary_masks(
                save_dir=get_section_output_dir(config, 'region_analysis'),
                celltype_cfg=st.session_state["celltype_cfg"],
                df_cells=df_cells,
            )
            if not boundary_candidates:
                st.info("No boundary masks were found yet. Save at least one computational ROI or adjusted ROI in Region analysis first.")
            else:
                boundary_labels = [name for name, _ in boundary_candidates]
                label_to_path = {label: path for label, (_, path) in zip(boundary_labels, boundary_candidates)}
                current_boundary_label = st.session_state.get("boundary_mask_label")
                if boundary_labels and current_boundary_label not in boundary_labels:
                    st.session_state["boundary_mask_label"] = boundary_labels[0]

                col1, col2, col3 = st.columns([2, 2, 1.5])
                with col1:
                    st.selectbox("Boundary / ROI", options=boundary_labels, key="boundary_mask_label")
                with col2:
                    st.multiselect(
                        "Query cell types",
                        options=present_types,
                        default=present_types[:1],
                        key="boundary_query_types",
                    )
                with col3:
                    st.selectbox(
                        "Filter",
                        options=["all", "inside", "outside"],
                        format_func=lambda v: {
                            "all": "All cells",
                            "inside": "Only cells inside region",
                            "outside": "Only cells outside region",
                        }[v],
                        key="boundary_region_filter",
                    )

                if st.button("Compute boundary distances", type="primary", key="run_boundary_dist_btn"):
                    try:
                        queries = list(st.session_state.get("boundary_query_types", []))
                        if not queries:
                            raise RuntimeError("Select at least one query cell type.")
                        selected_label = st.session_state["boundary_mask_label"]
                        boundary_path = label_to_path[selected_label]
                        boundary_name = selected_label
                        with st.spinner("Computing distances to boundary..."):
                            result = run_boundary_distance_analysis(
                                df_cells=df_cells,
                                celltype_cfg=st.session_state["celltype_cfg"],
                                celltype_mask=celltype_mask,
                                save_dir=get_section_output_dir(config, 'distance_analysis'),
                                pixel_size_um=config.pixel_size_um,
                                boundary_mask_path=boundary_path,
                                boundary_name=boundary_name,
                                query_types=queries,
                                region_filter=st.session_state["boundary_region_filter"],
                                save_outputs=True,
                            )
                            _close_result_figures(st.session_state.get("boundary_result"))
                            st.session_state["boundary_result"] = result
                            invalidate_output_zip_cache()
                            refresh_output_zip_state(config.save_dir)
                        st.success("Boundary distance analysis finished.")
                    except Exception as exc:
                        st.error(str(exc))

                boundary_result = st.session_state.get("boundary_result")
                if boundary_result is not None:
                    render_zoomable_figure(
                        fig=boundary_result.get("figure"),
                        component_key="boundary_distance_result",
                        saved_paths=[
                            boundary_result.get("saved_paths", {}).get("svg"),
                            boundary_result.get("saved_paths", {}).get("png"),
                        ],
                        component_height=860,
                    )
                    st.markdown("#### Distances preview")
                    st.dataframe(boundary_result["distances"].head(20), use_container_width=True)
                    if not boundary_result.get("ttests", pd.DataFrame()).empty:
                        st.markdown("#### P-value statistics")
                        st.dataframe(boundary_result["ttests"], use_container_width=True)

def render_outputs_tab(tab):
    with tab:
        st.session_state["outputs_viewed"] = True
        st.subheader("Outputs")
        config: PipelineConfig | None = st.session_state.get("config")
        if config is None:
            st.warning("Save the configuration first.")
            return

        st.write(f"Output root directory: `{config.save_dir}`")
        st.caption("Section outputs are organized into subfolders inside this root output directory.")
        output_rows = list_output_files(config.save_dir)
        if not output_rows:
            st.info("No outputs have been generated yet.")
            return

        df_files = pd.DataFrame(output_rows)
        st.dataframe(df_files, use_container_width=True)

        output_signature = build_output_signature(config.save_dir)
        try:
            zip_path_value = st.session_state.get("outputs_zip_path")
            expected_zip_path = current_output_zip_path(output_signature)
            zip_path = Path(zip_path_value) if zip_path_value else expected_zip_path
            if str(zip_path) != str(expected_zip_path):
                zip_path = expected_zip_path
            if st.session_state.get("outputs_zip_signature") != output_signature or (not zip_path.exists()):
                with st.spinner("Preparing newest ZIP download automatically..."):
                    zip_path = prepare_output_zip_file(config.save_dir, zip_path)
                    st.session_state["outputs_zip_path"] = str(zip_path)
                    st.session_state["outputs_zip_signature"] = output_signature
            zip_stat = zip_path.stat()
            zip_bytes = st.session_state.get("outputs_zip_bytes")
            if zip_bytes is None or st.session_state.get("outputs_zip_signature") != output_signature:
                zip_bytes = read_file_bytes_cached(str(zip_path), int(zip_stat.st_mtime_ns), int(zip_stat.st_size))
                st.session_state["outputs_zip_bytes"] = zip_bytes
        except Exception as exc:
            st.session_state["outputs_zip_path"] = None
            st.error(f"Automatic ZIP preparation failed: {exc}")
            zip_bytes = None

        if zip_bytes:
            st.download_button(
                "Download current outputs as ZIP",
                data=zip_bytes,
                file_name=f"SpatialScope_outputs_{st.session_state['session_id']}.zip",
                mime="application/zip",
                key="download_outputs_zip_btn",
            )

        st.caption(
            "Files are stored in a temporary upload-session folder while the app is running. "
            "The newest output ZIP is prepared automatically after each rerun whenever outputs change."
        )


def render_app_page_header() -> None:
    if not APP_ICON_PATH.exists():
        st.title("SpatialScope")
        return

    try:
        icon_b64 = base64.b64encode(APP_ICON_PATH.read_bytes()).decode("ascii")
        st.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:14px;margin-bottom:0.65rem;position:relative;z-index:1;">
              <img src="data:image/png;base64,{icon_b64}" style="width:52px;height:52px;display:block;object-fit:contain;" />
              <div style="font-size:2rem;font-weight:700;line-height:1.1;letter-spacing:0;">SpatialScope</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        st.title("SpatialScope")


def main():
    init_state()
    _sync_desktop_paths()
    inject_desktop_styles()
    inject_ui_translation()

    selected_section = render_sidebar_navigation()

    render_app_page_header()

    section_renderers = {
        "1. Inputs & config": render_config_tab,
        "2. Overlay preview": render_overlay_tab,
        "3. Nuclei segmentation": render_nuclei_tab,
        "4. Cell type assignments": render_celltype_assignments_tab,
        "5. Neighborhood analysis": render_neighborhood_tab,
        "6. Region analysis": render_regions_tab,
        "7. Cell distribution analysis": render_cell_distribution_tab,
        "8. Distance analysis": render_distance_tab,
        "9. Outputs": render_outputs_tab,
    }

    render_fn = section_renderers.get(selected_section, render_config_tab)
    render_fn(st.container())


if __name__ == "__main__":
    main()
