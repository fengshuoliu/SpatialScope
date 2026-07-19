from __future__ import annotations

import ast
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from matplotlib.colors import ListedColormap
from matplotlib.patches import Rectangle
from scipy import ndimage as ndi
from skimage import filters, measure, morphology, segmentation

from .io import load_any_tiff, load_text_grid, save_uint16_tiff, write_json
from .visualization import (
    add_colored_type_text,
    add_scalebar_20um,
    axis_off,
    build_celltype_cmap,
    COLOR_HEX_LIST,
    COMMON_FIRST,
)
from .io import valid_pixel_size, to_image


def guess_nuclear_channel(channels: Sequence[str]) -> str | None:
    if not channels:
        return None
    upper = {channel.upper(): channel for channel in channels}
    for key in ["DAPI", "HOECHST", "NUCLEUS", "NUCLEAR"]:
        if key in upper:
            return upper[key]
    for channel in channels:
        upper_channel = channel.upper()
        if ("DAPI" in upper_channel) or ("HOECHST" in upper_channel) or ("NUC" in upper_channel):
            return channel
    return None


def marker_choices_for_ui(channel_names: Sequence[str]) -> List[str]:
    nuc_guess = guess_nuclear_channel(channel_names)
    marker_choices = ["nucleus"] + ([c for c in channel_names if c != nuc_guess] if nuc_guess else list(channel_names))
    seen = set()
    return [m for m in marker_choices if not (m in seen or seen.add(m))]


def safe_token(name: str) -> str:
    if str(name).strip().lower() == "nucleus":
        return "NUCLEUS"
    token = re.sub(r"[^0-9A-Za-z]+", "_", str(name)).strip("_")
    return token.upper() if token else "MARKER"


def token_mapping_for_ui(channel_names: Sequence[str]) -> Dict[str, str]:
    return {name: safe_token(name) for name in marker_choices_for_ui(channel_names)}


def safe_key(name: str) -> str:
    key = re.sub(r"[^0-9A-Za-z]+", "_", str(name)).strip("_")
    return key.upper() if key else "MARKER"


NUC_KEY = "NUCLEUS"


def marker_name_to_key(marker_name: str) -> str:
    upper_name = (marker_name or "").strip().upper()
    if upper_name in {"NUCLEUS", "NUCLEAR", "NUC"}:
        return NUC_KEY
    if "DAPI" in upper_name or "HOECHST" in upper_name:
        return NUC_KEY
    return safe_key(marker_name)


def normalize_expr(expr: str) -> str:
    expr_norm = (expr or "").strip()
    expr_norm = re.sub(r"\bAND\b", "and", expr_norm, flags=re.I)
    expr_norm = re.sub(r"\bOR\b", "or", expr_norm, flags=re.I)
    expr_norm = re.sub(r"\bNOT\b", "not", expr_norm, flags=re.I)
    return expr_norm


def default_celltype(name: str, color_hex: str) -> Dict[str, Any]:
    return {
        "name": name,
        "color_hex": color_hex,
        "mode": "simple",
        "all_pos": [],
        "all_neg": [],
        "any_pos_groups": [],
    }


def save_celltype_config(celltype_cfg: Sequence[Dict[str, Any]], save_dir: Path) -> Path:
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    path = save_dir / "celltype_config.json"
    path.write_text(json.dumps(list(celltype_cfg), indent=2))
    return path


from dataclasses import asdict, dataclass, replace
from itertools import product
from threadpoolctl import threadpool_limits


@dataclass(frozen=True)
class CelltypeAssignmentParams:
    r_voronoi_um: float = 3.0
    r_buffer_um: float = 2.0
    r_vote_um: float = 3.0
    tophat_r_um: float = 1.0
    gauss_sigma_um: float = 0.5
    thresh_mode: str = "global_otsu"
    min_pos_object_size_px: int = 9
    min_pos_pix: int = 5
    resolve_ambiguous: bool = True
    ambiguous_min_probability: float = 0.60
    ambiguous_min_gap: float = 0.10

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


CELLTYPE_PARAM_ORDER = [
    "r_voronoi_um",
    "r_buffer_um",
    "r_vote_um",
    "tophat_r_um",
    "gauss_sigma_um",
    "thresh_mode",
    "min_pos_object_size_px",
    "min_pos_pix",
]

CELLTYPE_PARAM_LABELS = {
    "r_voronoi_um": "R_VORONOI_UM",
    "r_buffer_um": "R_BUFFER_UM",
    "r_vote_um": "R_VOTE_UM",
    "tophat_r_um": "TOPHAT_R_UM",
    "gauss_sigma_um": "GAUSS_SIGMA_UM",
    "thresh_mode": "THRESH_MODE",
    "min_pos_object_size_px": "MIN_POS_OBJECT_SIZE_PX",
    "min_pos_pix": "MIN_POS_PIX",
}

CELLTYPE_OPTIMIZER_PARAM_ORDER = [
    *CELLTYPE_PARAM_ORDER,
    "resolve_ambiguous",
    "ambiguous_min_probability",
    "ambiguous_min_gap",
]

CELLTYPE_OPTIMIZER_PARAM_LABELS = {
    **CELLTYPE_PARAM_LABELS,
    "resolve_ambiguous": "RESOLVE_AMBIGUOUS",
    "ambiguous_min_probability": "AMBIGUOUS_MIN_PROBABILITY",
    "ambiguous_min_gap": "AMBIGUOUS_MIN_GAP",
}


def make_celltype_assignment_parameter_sweep_figure(
    df_results: pd.DataFrame,
    count_columns: Sequence[str],
    celltype_cfg: Sequence[Dict[str, Any]] | None = None,
) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(8.6, 4.8))
    if df_results is None or len(df_results) == 0:
        ax.text(0.5, 0.5, "No parameter-scan results", ha="center", va="center")
        ax.set_axis_off()
        return fig

    ok = df_results.copy()
    if "error" in ok.columns:
        ok = ok[ok["error"].fillna("") == ""].copy()

    if ok.empty:
        ax.text(0.5, 0.5, "No successful parameter-scan combinations", ha="center", va="center")
        ax.set_axis_off()
        return fig

    color_map: Dict[str, str] = {}
    if celltype_cfg is not None:
        for ct in celltype_cfg:
            name = str(ct.get("name", "")).strip()
            color_hex = str(ct.get("color_hex", "")).strip()
            if name and color_hex:
                color_map[name] = color_hex
    color_map.setdefault("Unassigned", "#808080")
    color_map.setdefault("Ambiguous", "#202020")

    x = ok["combo_index"].to_numpy()
    for col in count_columns:
        if col not in ok.columns:
            continue
        label = col.replace("count::", "")
        line_kwargs = {
            "marker": "o",
            "linewidth": 1.6,
            "markersize": 3.5,
            "label": label,
        }
        if label in color_map:
            line_kwargs["color"] = color_map[label]
        if label == "Unassigned":
            line_kwargs["linestyle"] = ":"
        elif label == "Ambiguous":
            line_kwargs["linestyle"] = "--"
        ax.plot(x, ok[col].to_numpy(), **line_kwargs)

    ax.set_xlabel("Combination index")
    ax.set_ylabel("Detected cells")
    ax.set_title("Cell-type counts across parameter combinations")
    ax.grid(alpha=0.25)
    ax.tick_params(axis="both", labelsize=11)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", rotation_mode="anchor")
    if len(count_columns) <= 10:
        ax.legend(frameon=False, fontsize=9, ncol=2)
    fig.tight_layout()
    return fig



def _coerce_assignment_params(
    params: CelltypeAssignmentParams | Dict[str, Any] | None,
) -> CelltypeAssignmentParams:
    if params is None:
        return CelltypeAssignmentParams()
    if isinstance(params, CelltypeAssignmentParams):
        return params
    if isinstance(params, dict):
        payload = {}
        allowed_fields = set(CELLTYPE_PARAM_ORDER) | {"resolve_ambiguous", "ambiguous_min_probability", "ambiguous_min_gap"}
        for field in allowed_fields:
            if field in params:
                payload[field] = params[field]
        if "thresh_mode" in payload:
            payload["thresh_mode"] = str(payload["thresh_mode"])
        if "min_pos_object_size_px" in payload:
            payload["min_pos_object_size_px"] = max(0, int(payload["min_pos_object_size_px"]))
        if "min_pos_pix" in payload:
            payload["min_pos_pix"] = max(0, int(payload["min_pos_pix"]))
        for field in ["r_voronoi_um", "r_buffer_um", "r_vote_um", "tophat_r_um", "gauss_sigma_um", "ambiguous_min_probability", "ambiguous_min_gap"]:
            if field in payload:
                payload[field] = float(payload[field])
        if "resolve_ambiguous" in payload:
            payload["resolve_ambiguous"] = bool(payload["resolve_ambiguous"])
        return CelltypeAssignmentParams(**payload)
    raise TypeError(f"Unsupported assignment params type: {type(params)!r}")

def _run_celltype_assignment_impl(
    folder: Path,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    image_id: str,
    channels_cfg: Sequence[Dict[str, Any]],
    celltype_cfg: Sequence[Dict[str, Any]],
    labels: np.ndarray | None = None,
    df_pixels: pd.DataFrame | None = None,
    shapes: Dict[Tuple[str, str], Tuple[int, int]] | None = None,
    params: CelltypeAssignmentParams | Dict[str, Any] | None = None,
    save_outputs: bool = True,
    make_figures: bool = True,
    native_threads: int | None = None,
    support_workers: int | None = None,
) -> Dict[str, Any]:
    if not celltype_cfg:
        raise RuntimeError("CELLTYPE_CFG is empty.")

    params = _coerce_assignment_params(params)
    folder = Path(folder)
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    if not valid_pixel_size(pixel_size_um):
        raise RuntimeError("PIXEL_SIZE_UM missing/invalid. Please provide valid x/y pixel sizes.")

    px_um_x, px_um_y = float(pixel_size_um[0]), float(pixel_size_um[1])
    px_area_um2 = px_um_x * px_um_y

    cpu = os.cpu_count() or 1
    target_threads = int(native_threads) if native_threads is not None else int(os.environ.get("OMP_NUM_THREADS", max(1, cpu - 1)))
    target_threads = max(1, target_threads)
    numba_threads = min(target_threads, 24)
    support_n_jobs = max(1, int(support_workers if support_workers is not None else target_threads))

    if labels is None:
        label_path = save_dir / "nuclei_labels_uint16.tiff"
        if not label_path.exists():
            raise RuntimeError("Missing nuclei labels. Run nuclei segmentation first.")
        labels = load_any_tiff(label_path).astype(np.int32)

    if labels.ndim != 2:
        raise RuntimeError(f"Nuclei labels must be a 2D label image, got shape={labels.shape}")

    h, w = labels.shape
    n_labels = int(labels.max())
    if n_labels <= 0:
        raise RuntimeError("No nuclei labels were found in the current label mask.")

    ch2file = {c["channel"]: (folder / c["file"]) for c in channels_cfg}
    channel_names = list(ch2file.keys())
    nuc_channel = guess_nuclear_channel(channel_names)

    def get_channel_image(channel_name: str) -> np.ndarray:
        if df_pixels is not None and shapes is not None:
            try:
                return to_image(df_pixels, shapes, image_id, channel_name).astype(np.float32)
            except Exception:
                pass
        path = ch2file.get(channel_name)
        if path is None or not path.exists():
            raise FileNotFoundError(f"Channel {channel_name!r} file not found in CFG/Folder.")
        return load_text_grid(path).astype(np.float32, copy=False)

    def um_to_px_iso(value_um: float) -> int:
        scale = np.sqrt(max(1e-12, px_um_x * px_um_y))
        return max(1, int(round(float(value_um) / scale)))

    r_voronoi_px = um_to_px_iso(params.r_voronoi_um)
    r_buffer_px = um_to_px_iso(params.r_buffer_um)
    r_vote_px = um_to_px_iso(params.r_vote_um)
    tophat_px = um_to_px_iso(params.tophat_r_um) if float(params.tophat_r_um) > 0 else 0
    gauss_sigma = float(params.gauss_sigma_um) / max(1e-12, np.sqrt(px_um_x * px_um_y))
    gauss_sigma = max(0.0, gauss_sigma)
    thresh_mode = str(params.thresh_mode)
    min_pos_pix = max(0, int(params.min_pos_pix))
    min_pos_object_size_px = max(0, int(params.min_pos_object_size_px))

    outside = labels == 0
    dist_outside, idxs = ndi.distance_transform_edt(outside, return_indices=True)
    iy, ix = idxs
    nearest_label_map = labels[iy, ix]
    voronoi_band = outside & (dist_outside <= r_voronoi_px)

    lab2 = segmentation.expand_labels(labels, distance=r_buffer_px)
    boundaries_thick = segmentation.find_boundaries(lab2, mode="thick")
    buffer_zone = morphology.binary_dilation(boundaries_thick, morphology.disk(max(1, r_buffer_px // 2)))
    buffer_zone &= voronoi_band

    owner_map = labels.copy()
    owner_map[voronoi_band] = nearest_label_map[voronoi_band]

    def disk_offsets(radius: int) -> Tuple[np.ndarray, np.ndarray]:
        yy, xx = np.mgrid[-radius : radius + 1, -radius : radius + 1]
        mask = (yy * yy + xx * xx) <= radius * radius
        return yy[mask].astype(np.int32), xx[mask].astype(np.int32)

    vote_dys, vote_dxs = disk_offsets(r_vote_px)
    cand_dys, cand_dxs = disk_offsets(r_buffer_px)

    try:
        from numba import njit, prange, set_num_threads

        try:
            set_num_threads(numba_threads)
        except Exception:
            pass
        numba_ok = True
    except Exception:
        numba_ok = False

    if numba_ok:

        @njit(parallel=True, fastmath=True)
        def resolve_buffer_pixels(
            buf_ys,
            buf_xs,
            img_norm,
            owner_map_local,
            lab2_local,
            nearest_label_map_local,
            vote_dys_local,
            vote_dxs_local,
            cand_dys_local,
            cand_dxs_local,
            h_local,
            w_local,
        ):
            max_cands = 8
            out = np.zeros(buf_ys.shape[0], np.int32)
            for i in prange(buf_ys.shape[0]):
                y = buf_ys[i]
                x = buf_xs[i]
                cand_labels = np.zeros(max_cands, np.int32)
                n_cand = 0
                for k in range(cand_dys_local.shape[0]):
                    yy = y + cand_dys_local[k]
                    xx = x + cand_dxs_local[k]
                    if 0 <= yy < h_local and 0 <= xx < w_local:
                        lbl = lab2_local[yy, xx]
                        if lbl > 0:
                            seen = False
                            for j in range(n_cand):
                                if cand_labels[j] == lbl:
                                    seen = True
                                    break
                            if (not seen) and (n_cand < max_cands):
                                cand_labels[n_cand] = lbl
                                n_cand += 1
                if n_cand == 0:
                    out[i] = nearest_label_map_local[y, x]
                    continue
                best_lbl = 0
                best_vote = -1.0
                for j in range(n_cand):
                    lbl = cand_labels[j]
                    vote = 0.0
                    for k in range(vote_dys_local.shape[0]):
                        yy = y + vote_dys_local[k]
                        xx = x + vote_dxs_local[k]
                        if 0 <= yy < h_local and 0 <= xx < w_local:
                            if owner_map_local[yy, xx] == lbl:
                                vote += img_norm[yy, xx]
                    if vote > best_vote:
                        best_vote = vote
                        best_lbl = lbl
                if best_lbl == 0:
                    best_lbl = nearest_label_map_local[y, x]
                out[i] = best_lbl
            return out

    else:

        def resolve_buffer_pixels(
            buf_ys,
            buf_xs,
            img_norm,
            owner_map_local,
            lab2_local,
            nearest_label_map_local,
            vote_dys_local,
            vote_dxs_local,
            cand_dys_local,
            cand_dxs_local,
            h_local,
            w_local,
        ):
            out = np.zeros(buf_ys.shape[0], np.int32)
            for i, (y, x) in enumerate(zip(buf_ys, buf_xs)):
                candidates = set()
                for dy, dx in zip(cand_dys_local, cand_dxs_local):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h_local and 0 <= xx < w_local:
                        lbl = lab2_local[yy, xx]
                        if lbl > 0:
                            candidates.add(int(lbl))
                if not candidates:
                    out[i] = int(nearest_label_map_local[y, x])
                    continue
                best_lbl, best_vote = 0, -1.0
                for lbl in candidates:
                    vote = 0.0
                    for dy, dx in zip(vote_dys_local, vote_dxs_local):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < h_local and 0 <= xx < w_local and owner_map_local[yy, xx] == lbl:
                            vote += float(img_norm[yy, xx])
                    if vote > best_vote:
                        best_vote = vote
                        best_lbl = lbl
                out[i] = best_lbl or int(nearest_label_map_local[y, x])
            return out

    def preprocess_marker(img: np.ndarray) -> np.ndarray:
        img = img.astype(np.float32, copy=False)
        lo, hi = np.nanpercentile(img, [1, 99.8])
        norm = np.clip((img - lo) / max(hi - lo, 1e-6), 0, 1)
        if tophat_px > 0:
            norm = morphology.white_tophat(norm, footprint=morphology.disk(tophat_px))
        if gauss_sigma > 0:
            norm = filters.gaussian(norm, sigma=gauss_sigma, preserve_range=True)
        return norm

    def marker_positive_mask(img_norm: np.ndarray) -> Tuple[np.ndarray, float]:
        if thresh_mode == "global_otsu":
            thr = filters.threshold_otsu(img_norm)
        elif thresh_mode == "yen":
            thr = filters.threshold_yen(img_norm)
        elif thresh_mode == "triangle":
            thr = filters.threshold_triangle(img_norm)
        else:
            thr = filters.threshold_otsu(img_norm)
        pos = img_norm > thr
        if min_pos_object_size_px > 1:
            pos = morphology.remove_small_objects(pos, min_size=min_pos_object_size_px)
        return pos, float(thr)

    required_marker_keys = {
        marker_name_to_key(marker)
        for cell_type in celltype_cfg
        for marker in (
            list(cell_type.get("all_pos", []))
            + list(cell_type.get("all_neg", []))
            + [
                grouped_marker
                for group in cell_type.get("any_pos_groups", [])
                for grouped_marker in group
            ]
        )
        if marker_name_to_key(marker) != NUC_KEY
    }
    # Match the macOS assignmentRelevantChannels behavior. Unused channels do
    # not contribute evidence and must not spend minutes generating assignment
    # maps that are discarded by every cell-type rule.
    assign_channels = [
        channel
        for channel in channel_names
        if marker_name_to_key(channel) in required_marker_keys
    ]
    assign_maps: Dict[str, np.ndarray] = {}
    df_stats_list: List[pd.DataFrame] = []
    threshold_rows: List[Dict[str, Any]] = []

    def assign_marker_fast(img: np.ndarray, marker_display_name: str):
        marker_key = marker_name_to_key(marker_display_name)
        if marker_key == NUC_KEY:
            raise ValueError("Do not run marker assignment for nucleus (built-in).")

        img_norm = preprocess_marker(img)
        pos_mask, thr = marker_positive_mask(img_norm)
        threshold_rows.append(
            {
                "marker_display_name": marker_display_name,
                "marker_key": marker_key,
                "threshold": float(thr),
                "positive_pixels": int(pos_mask.sum()),
            }
        )

        assign_map = np.zeros_like(labels, dtype=np.uint16)

        inside_mask = (labels > 0) & pos_mask
        assign_map[inside_mask] = labels[inside_mask].astype(np.uint16)

        band_mask = pos_mask & voronoi_band
        nonbuf_mask = band_mask & (~buffer_zone)
        assign_map[nonbuf_mask] = nearest_label_map[nonbuf_mask].astype(np.uint16)

        by, bx = np.nonzero(pos_mask & buffer_zone)
        if by.size:
            picked = resolve_buffer_pixels(
                by.astype(np.int32),
                bx.astype(np.int32),
                img_norm,
                owner_map,
                lab2,
                nearest_label_map,
                vote_dys,
                vote_dxs,
                cand_dys,
                cand_dxs,
                h,
                w,
            )
            assign_map[by, bx] = picked.astype(np.uint16)

        flat_lab = assign_map.ravel().astype(np.int32)
        flat_val = img_norm.ravel()
        max_lab = int(flat_lab.max()) if flat_lab.size else 0
        pix_counts = np.bincount(flat_lab, minlength=max_lab + 1).astype(np.int64)
        val_sums = np.bincount(flat_lab, weights=flat_val, minlength=max_lab + 1)

        dfm = pd.DataFrame(
            {
                "label": np.arange(max_lab + 1, dtype=int),
                f"{marker_key}_pos_pix": pix_counts,
                f"{marker_key}_sum_intensity": val_sums,
            }
        )
        dfm = dfm[dfm["label"] > 0].reset_index(drop=True)

        if save_outputs:
            save_uint16_tiff(save_dir / f"marker_assign_{marker_key}_uint16.tiff", assign_map.astype(np.uint16))

        return marker_key, assign_map, dfm

    with threadpool_limits(limits=target_threads):
        for channel in assign_channels:
            marker_key = marker_name_to_key(channel)
            if marker_key == NUC_KEY:
                continue
            img = get_channel_image(channel)
            key, amap, dfm = assign_marker_fast(img, channel)
            assign_maps[key] = amap
            df_stats_list.append(dfm)

    def build_df_props_from_current_labels(labels_current: np.ndarray) -> pd.DataFrame:
        intensity_image = None
        if nuc_channel is not None:
            try:
                intensity_image = get_channel_image(nuc_channel)
            except Exception:
                intensity_image = None

        base_props = ("label", "area", "perimeter", "eccentricity", "solidity", "centroid", "bbox")
        if intensity_image is not None:
            props = measure.regionprops_table(
                labels_current,
                intensity_image=intensity_image,
                properties=base_props + ("mean_intensity", "max_intensity"),
            )
        else:
            props = measure.regionprops_table(labels_current, properties=base_props)

        out = pd.DataFrame(props)
        out.rename(
            columns={
                "centroid-0": "centroid_y_px",
                "centroid-1": "centroid_x_px",
                "bbox-0": "bbox_min_y_px",
                "bbox-1": "bbox_min_x_px",
                "bbox-2": "bbox_max_y_px",
                "bbox-3": "bbox_max_x_px",
            },
            inplace=True,
        )

        if not out.empty:
            out["label"] = out["label"].astype(int)
            out["centroid_x_um"] = out["centroid_x_px"].to_numpy(float) * px_um_x
            out["centroid_y_um"] = out["centroid_y_px"].to_numpy(float) * px_um_y
            out["area_um2"] = out["area"].to_numpy(float) * px_area_um2
            out["perimeter_um"] = out["perimeter"].to_numpy(float) * np.sqrt(max(1e-12, px_um_x * px_um_y))

        return out.sort_values("label").reset_index(drop=True)

    df_props = build_df_props_from_current_labels(labels)
    if df_props.empty:
        raise RuntimeError("No nuclei properties could be computed. Segmentation labels appear empty.")

    df_cells = df_props.copy()
    for dfm in df_stats_list:
        df_cells = df_cells.merge(dfm, on="label", how="left")
    df_cells.fillna(0, inplace=True)

    nuc_area = np.bincount(labels.ravel().astype(np.int64), minlength=n_labels + 1).astype(np.int64)
    lab_idx = df_cells["label"].to_numpy(np.int64)

    if len(lab_idx) == 0:
        raise RuntimeError("No nuclei labels were available for cell-type assignment.")
    if lab_idx.min() < 1 or lab_idx.max() > n_labels:
        raise RuntimeError(
            f"Label mismatch: df_cells has labels {lab_idx.min()}..{lab_idx.max()}, but current labels max is {n_labels}."
        )

    df_cells["NUCLEUS_pos_pix"] = nuc_area[lab_idx]
    df_cells["NUCLEUS_pos"] = df_cells["NUCLEUS_pos_pix"] > 0

    if "centroid_x_um" not in df_cells.columns:
        df_cells["centroid_x_um"] = df_cells["centroid_x_px"].to_numpy(float) * px_um_x
    if "centroid_y_um" not in df_cells.columns:
        df_cells["centroid_y_um"] = df_cells["centroid_y_px"].to_numpy(float) * px_um_y

    marker_keys = sorted(assign_maps.keys())
    for marker_key in marker_keys:
        marker_pix = df_cells.get(f"{marker_key}_pos_pix", 0)
        if min_pos_pix <= 0:
            df_cells[f"{marker_key}_pos"] = marker_pix > 0
        else:
            df_cells[f"{marker_key}_pos"] = marker_pix >= min_pos_pix

    def is_pos(row: pd.Series, marker_key: str) -> bool:
        if marker_key == NUC_KEY:
            return bool(row.get("NUCLEUS_pos", False))
        col = f"{marker_key}_pos"
        return bool(row.get(col, False))

    def match_simple(ct: Dict[str, Any], row: pd.Series) -> bool:
        all_pos = [marker_name_to_key(marker) for marker in ct.get("all_pos", [])]
        all_neg = [marker_name_to_key(marker) for marker in ct.get("all_neg", [])]
        any_groups = [[marker_name_to_key(marker) for marker in group] for group in ct.get("any_pos_groups", [])]

        if not all(is_pos(row, mk) for mk in all_pos):
            return False
        if not all((not is_pos(row, mk)) for mk in all_neg):
            return False
        for group in any_groups:
            if group and (not any(is_pos(row, mk) for mk in group)):
                return False
        return True


    compiled_expr: List[Any] = []
    compiled_expr_ast: List[Any] = []
    for ct in celltype_cfg:
        if ct.get("mode") == "expr":
            expr = normalize_expr(ct.get("expr", ""))
            try:
                compiled_expr.append(compile(expr, "<celltype_expr>", "eval") if expr else None)
                compiled_expr_ast.append(ast.parse(expr, mode="eval").body if expr else None)
            except Exception:
                compiled_expr.append(None)
                compiled_expr_ast.append(None)
        else:
            compiled_expr.append(None)
            compiled_expr_ast.append(None)

    env_keys = [NUC_KEY] + marker_keys

    def match_expr(ct_index: int, row: pd.Series) -> bool:
        code = compiled_expr[ct_index]
        if code is None:
            return False
        env = {key: is_pos(row, key) for key in env_keys}
        try:
            return bool(eval(code, {"__builtins__": {}}, env))
        except Exception:
            return False

    def marker_probability(row: pd.Series, marker_key: str) -> float:
        if marker_key == NUC_KEY:
            pix = float(row.get("NUCLEUS_pos_pix", 0))
            return 1.0 if pix > 0 else 0.0
        pix = float(row.get(f"{marker_key}_pos_pix", 0.0))
        intensity = float(row.get(f"{marker_key}_sum_intensity", 0.0))
        scale_pix = max(1.0, float(max(min_pos_pix, 1)))
        pix_prob = 1.0 - np.exp(-max(0.0, pix) / scale_pix)
        intensity_prob = 1.0 - np.exp(-max(0.0, intensity) / max(1.0, scale_pix / 2.0))
        return float(np.clip(0.65 * pix_prob + 0.35 * intensity_prob, 0.0, 1.0))

    def negative_probability(row: pd.Series, marker_key: str) -> float:
        return float(np.clip(1.0 - marker_probability(row, marker_key), 0.0, 1.0))

    def geometric_mean_prob(values: Sequence[float]) -> float:
        vals = [float(np.clip(v, 1e-6, 1.0)) for v in values if v is not None]
        if not vals:
            return 0.5
        return float(np.exp(np.mean(np.log(vals))))

    def score_simple_probability(ct: Dict[str, Any], row: pd.Series) -> float:
        all_pos = [marker_name_to_key(marker) for marker in ct.get("all_pos", [])]
        all_neg = [marker_name_to_key(marker) for marker in ct.get("all_neg", [])]
        any_groups = [[marker_name_to_key(marker) for marker in group] for group in ct.get("any_pos_groups", [])]

        terms: List[float] = []
        for mk in all_pos:
            terms.append(marker_probability(row, mk))
        for mk in all_neg:
            terms.append(negative_probability(row, mk))
        for group in any_groups:
            if group:
                terms.append(max([marker_probability(row, mk) for mk in group] or [0.0]))
        return geometric_mean_prob(terms)

    def eval_probability_ast(node: Any, row: pd.Series) -> float:
        if node is None:
            return 0.0
        if isinstance(node, ast.Name):
            return marker_probability(row, marker_name_to_key(node.id))
        if isinstance(node, ast.Constant):
            return 1.0 if bool(node.value) else 0.0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.Invert)):
            return float(np.clip(1.0 - eval_probability_ast(node.operand, row), 0.0, 1.0))
        if isinstance(node, ast.BoolOp):
            values = [eval_probability_ast(v, row) for v in node.values]
            if isinstance(node.op, ast.And):
                return geometric_mean_prob(values)
            if isinstance(node.op, ast.Or):
                return float(np.clip(1.0 - np.prod([1.0 - np.clip(v, 0.0, 1.0) for v in values]), 0.0, 1.0))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd):
            return geometric_mean_prob([eval_probability_ast(node.left, row), eval_probability_ast(node.right, row)])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            lv = eval_probability_ast(node.left, row)
            rv = eval_probability_ast(node.right, row)
            return float(np.clip(1.0 - (1.0 - lv) * (1.0 - rv), 0.0, 1.0))
        return 0.0

    def score_expr_probability(ct_index: int, row: pd.Series) -> float:
        return float(np.clip(eval_probability_ast(compiled_expr_ast[ct_index], row), 0.0, 1.0))

    k_types = len(celltype_cfg)
    celltype_id = np.zeros(len(df_cells), dtype=np.uint16)
    celltype_name = np.array(["Unassigned"] * len(df_cells), dtype=object)
    matched_celltypes: List[str] = []
    n_matched_celltypes = np.zeros(len(df_cells), dtype=np.int32)
    ambiguous_best_type = np.array([""] * len(df_cells), dtype=object)
    ambiguous_best_probability = np.zeros(len(df_cells), dtype=float)
    ambiguous_second_probability = np.zeros(len(df_cells), dtype=float)
    ambiguous_probability_gap = np.zeros(len(df_cells), dtype=float)
    ambiguous_candidate_probabilities: List[str] = []

    for i in range(len(df_cells)):
        row = df_cells.iloc[i]
        matches: List[tuple[int, str, int]] = []
        for k, ct in enumerate(celltype_cfg, start=1):
            ct_index = k - 1
            ok = match_simple(ct, row) if ct.get("mode") == "simple" else match_expr(ct_index, row)
            if ok:
                matches.append((k, ct["name"], ct_index))

        n_matches = len(matches)
        n_matched_celltypes[i] = n_matches
        matched_celltypes.append("|".join(name for _, name, _ in matches))

        if n_matches == 1:
            celltype_id[i] = matches[0][0]
            celltype_name[i] = matches[0][1]
            ambiguous_candidate_probabilities.append("")
        elif n_matches == 0:
            celltype_id[i] = 0
            celltype_name[i] = "Unassigned"
            ambiguous_candidate_probabilities.append("")
        else:
            candidate_scores: List[tuple[int, str, float]] = []
            for k, name, ct_index in matches:
                ct = celltype_cfg[ct_index]
                score = score_simple_probability(ct, row) if ct.get("mode") == "simple" else score_expr_probability(ct_index, row)
                candidate_scores.append((k, name, float(max(score, 1e-6))))
            total_score = float(sum(score for _, _, score in candidate_scores))
            if total_score <= 0:
                probabilities = [(k, name, 1.0 / len(candidate_scores)) for k, name, _ in candidate_scores]
            else:
                probabilities = [(k, name, score / total_score) for k, name, score in candidate_scores]
            probabilities = sorted(probabilities, key=lambda item: item[2], reverse=True)
            best_k, best_name, best_prob = probabilities[0]
            second_prob = probabilities[1][2] if len(probabilities) > 1 else 0.0

            ambiguous_best_type[i] = best_name
            ambiguous_best_probability[i] = float(best_prob)
            ambiguous_second_probability[i] = float(second_prob)
            ambiguous_probability_gap[i] = float(best_prob - second_prob)
            ambiguous_candidate_probabilities.append(
                "; ".join([f"{name}={prob:.3f}" for _, name, prob in probabilities])
            )

            if params.resolve_ambiguous and best_prob >= float(params.ambiguous_min_probability) and (best_prob - second_prob) >= float(params.ambiguous_min_gap):
                celltype_id[i] = int(best_k)
                celltype_name[i] = best_name
            else:
                celltype_id[i] = 0
                celltype_name[i] = "Ambiguous"

    df_cells["celltype_id"] = celltype_id.astype(int)
    df_cells["celltype"] = celltype_name
    df_cells["matched_celltypes"] = matched_celltypes
    df_cells["n_matched_celltypes"] = n_matched_celltypes.astype(int)
    df_cells["ambiguous_best_type"] = ambiguous_best_type
    df_cells["ambiguous_best_probability"] = ambiguous_best_probability.astype(float)
    df_cells["ambiguous_second_probability"] = ambiguous_second_probability.astype(float)
    df_cells["ambiguous_probability_gap"] = ambiguous_probability_gap.astype(float)
    df_cells["ambiguous_candidate_probabilities"] = ambiguous_candidate_probabilities

    celltype_id_by_label = np.zeros(n_labels + 1, dtype=np.uint16)
    lab_ct = df_cells[["label", "celltype_id"]].to_numpy()
    celltype_id_by_label[lab_ct[:, 0].astype(int)] = lab_ct[:, 1].astype(np.uint16)

    def type_has_non_nucleus(ct: Dict[str, Any]) -> bool:
        if ct.get("mode") == "simple":
            keys: List[str] = []
            keys += [marker_name_to_key(m) for m in ct.get("all_pos", [])]
            for group in ct.get("any_pos_groups", []):
                keys += [marker_name_to_key(m) for m in group]
            keys = [key for key in keys if key != NUC_KEY]
            return len(keys) > 0
        expr = normalize_expr(ct.get("expr", ""))
        toks = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expr)
        toks = [tok for tok in toks if tok.lower() not in {"and", "or", "not", "true", "false"}]
        toks = [tok for tok in toks if tok.upper() != NUC_KEY]
        return len(toks) > 0

    type_has_marker = np.array([False] + [type_has_non_nucleus(ct) for ct in celltype_cfg], dtype=bool)
    celltype_cfg_by_name = {str(ct["name"]): ct for ct in celltype_cfg}

    def support_marker_keys_for_assigned_row(row: pd.Series) -> List[str]:
        ct_name = str(row.get("celltype", "") or "")
        ct = celltype_cfg_by_name.get(ct_name)
        if not ct:
            return []
        keys: List[str] = []
        if ct.get("mode") == "simple":
            for marker in ct.get("all_pos", []):
                mk = marker_name_to_key(marker)
                if mk != NUC_KEY and mk in assign_maps and is_pos(row, mk):
                    keys.append(mk)
            for group in ct.get("any_pos_groups", []):
                group_keys = [marker_name_to_key(marker) for marker in group]
                for mk in group_keys:
                    if mk != NUC_KEY and mk in assign_maps and is_pos(row, mk):
                        keys.append(mk)
        else:
            for mk in marker_keys:
                if mk != NUC_KEY and mk in assign_maps and is_pos(row, mk):
                    keys.append(mk)
        return list(dict.fromkeys(keys))

    label_rows = {int(row["label"]): row for _, row in df_cells.iterrows()}
    label_to_support_marker_keys: Dict[int, List[str]] = {}
    for row in df_cells.itertuples(index=False):
        label_id = int(getattr(row, "label"))
        ct_id = int(getattr(row, "celltype_id", 0))
        if ct_id <= 0:
            continue
        row_series = label_rows.get(label_id)
        if row_series is None:
            continue
        label_to_support_marker_keys[label_id] = support_marker_keys_for_assigned_row(row_series)

    slices_list = ndi.find_objects(labels)
    margin = int(max(r_voronoi_px, r_buffer_px) + 2)

    def expand_slice(slice_pair, margin_local: int, h_local: int, w_local: int):
        sy, sx = slice_pair
        y0 = max(0, sy.start - margin_local)
        y1 = min(h_local, sy.stop + margin_local)
        x0 = max(0, sx.start - margin_local)
        x1 = min(w_local, sx.stop + margin_local)
        return slice(y0, y1), slice(x0, x1)


    def _smooth_support_within_territory(
        support_seed: np.ndarray,
        nucleus_mask: np.ndarray,
        territory_mask: np.ndarray,
        distance_loc: np.ndarray,
        marker_present: bool,
    ) -> np.ndarray:
        support = (support_seed | nucleus_mask).astype(bool)
        support &= territory_mask
        if not np.any(support):
            return nucleus_mask.astype(bool)

        close_radius = max(1, min(6, int(round(max(1, r_buffer_px) / 2.0))))
        if marker_present:
            blur_sigma = max(0.8, min(2.5, max(1, r_buffer_px) / 2.5))
            soft = ndi.gaussian_filter(support.astype(float), sigma=blur_sigma)
            threshold = 0.18
            support = soft > threshold
            support = morphology.binary_closing(support, footprint=morphology.disk(close_radius))
            support = morphology.binary_opening(support, footprint=morphology.disk(1))
        else:
            halo = territory_mask & (distance_loc <= max(1, r_buffer_px))
            support |= halo
            soft = ndi.gaussian_filter(support.astype(float), sigma=max(0.8, min(1.8, max(1, r_buffer_px) / 3.0)))
            support = soft > 0.28
            support = morphology.binary_closing(support, footprint=morphology.disk(max(1, min(close_radius, 3))))

        support &= territory_mask
        support |= nucleus_mask
        support = ndi.binary_fill_holes(support)
        support &= territory_mask
        support |= nucleus_mask
        return support.astype(bool)

    def support_for_label(label_id: int):
        slc0 = slices_list[label_id - 1]
        if slc0 is None:
            return label_id, None, None
        slc = expand_slice(slc0, margin, h, w)
        lbl_loc = labels[slc]
        own_loc = owner_map[slc]
        dist_loc = dist_outside[slc]
        ct = int(celltype_id_by_label[label_id])

        if ct <= 0:
            return label_id, ct, None

        nucleus_mask = lbl_loc == label_id
        territory_mask = own_loc == label_id

        marker_keys = label_to_support_marker_keys.get(label_id, [])
        marker_support = np.zeros_like(nucleus_mask, dtype=bool)
        for mk in marker_keys:
            amap = assign_maps.get(mk)
            if amap is not None:
                marker_support |= (amap[slc] == label_id)

        if ct >= 1 and ct <= k_types and (not type_has_marker[ct]):
            marker_support |= territory_mask & (dist_loc <= max(1, r_buffer_px))

        support = _smooth_support_within_territory(
            support_seed=marker_support,
            nucleus_mask=nucleus_mask,
            territory_mask=territory_mask,
            distance_loc=dist_loc,
            marker_present=bool(np.any(marker_support)),
        )
        return label_id, ct, (slc, support)

    # The per-label support pass already fans out across ``support_n_jobs``.
    # Keep each job's native kernels single-threaded when several jobs run so
    # the total worker budget stays at the machine's logical CPU count instead
    # of multiplying into severe nested oversubscription.
    support_native_threads = 1 if support_n_jobs > 1 else target_threads
    with threadpool_limits(limits=support_native_threads):
        results = Parallel(n_jobs=support_n_jobs, prefer="threads", batch_size=64)(
            delayed(support_for_label)(label_id) for label_id in range(1, n_labels + 1)
        )

    celltype_mask = np.zeros_like(labels, dtype=np.uint16)
    for label_id, ct, payload in results:
        if payload is None:
            continue
        slc, support = payload
        sub = celltype_mask[slc]
        sub[support] = np.uint16(ct)
        celltype_mask[slc] = sub

    counts = df_cells["celltype"].value_counts().rename_axis("celltype").reset_index(name="count")
    thresholds_df = pd.DataFrame(threshold_rows)

    panel_fig = None
    split_fig = None

    panel_svg = save_dir / "celltypes_panel.svg"
    panel_png = save_dir / "celltypes_panel.png"
    panel_tiff = save_dir / "celltypes_panel.tiff"
    split_svg = save_dir / "celltypes_split_panels.svg"
    split_png = save_dir / "celltypes_split_panels.png"
    split_tiff = save_dir / "celltypes_split_panels.tiff"

    if make_figures:
        ct_names = [ct["name"] for ct in celltype_cfg]
        ct_hex = [ct["color_hex"] for ct in celltype_cfg]
        cmap_ct = ListedColormap([(0, 0, 0)] + [mcolors.to_rgb(hx) for hx in ct_hex])

        nuc_norm = None
        if nuc_channel is not None:
            try:
                nuc_img = get_channel_image(nuc_channel)
                p1, p99 = np.nanpercentile(nuc_img, [1, 99.8])
                nuc_norm = np.clip((nuc_img - p1) / (p99 - p1 + 1e-6), 0, 1)
            except Exception:
                nuc_norm = None

        if nuc_norm is None:
            panel_fig, ax = plt.subplots(1, 1, figsize=(7, 7))
            ax.imshow(celltype_mask, cmap=cmap_ct, origin="upper", interpolation="nearest", vmin=0, vmax=k_types)
            axis_off(ax)
            add_scalebar_20um(ax, celltype_mask.shape, px_um_x, bar_um=20.0)
            add_colored_type_text(ax, ct_names, ct_hex)
            panel_fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
        else:
            panel_fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12, 6))
            ax0.imshow(nuc_norm, cmap="gray", origin="upper")
            axis_off(ax0)
            add_scalebar_20um(ax0, nuc_norm.shape, px_um_x, bar_um=20.0)
            ax0.text(
                0.985,
                0.985,
                nuc_channel,
                transform=ax0.transAxes,
                ha="right",
                va="top",
                fontsize=13,
                fontweight="bold",
                color="white",
                bbox=dict(boxstyle="round,pad=0.25", facecolor=(0, 0, 0, 0.25), edgecolor="none"),
            )

            ax1.imshow(celltype_mask, cmap=cmap_ct, origin="upper", interpolation="nearest", vmin=0, vmax=k_types)
            axis_off(ax1)
            add_scalebar_20um(ax1, celltype_mask.shape, px_um_x, bar_um=20.0)
            add_colored_type_text(ax1, ct_names, ct_hex)
            panel_fig.subplots_adjust(left=0, right=1, bottom=0, top=1, wspace=0.02)

        split_fig, axes2 = plt.subplots(1, k_types, figsize=(6 * max(1, k_types), 6))
        split_fig.patch.set_alpha(0.0)
        if k_types == 1:
            axes2 = [axes2]

        for i in range(1, k_types + 1):
            ax = axes2[i - 1]
            mask_i = (celltype_mask == i).astype(np.uint8)
            color_rgb = np.array(mcolors.to_rgb(ct_hex[i - 1]), dtype=float)
            rgba_i = np.zeros((*mask_i.shape, 4), dtype=float)
            rgba_i[..., :3] = color_rgb
            rgba_i[..., 3] = mask_i.astype(float)
            celltype_token = safe_key(ct_names[i - 1]).lower()

            ax.set_facecolor((0, 0, 0, 0))
            background_patch = Rectangle(
                (-0.5, -0.5),
                mask_i.shape[1],
                mask_i.shape[0],
                facecolor="black",
                edgecolor="none",
                linewidth=0,
                zorder=0,
            )
            background_patch.set_gid(f"celltype_split_background__{celltype_token}")
            ax.add_patch(background_patch)

            image_artist = ax.imshow(rgba_i, origin="upper", interpolation="nearest", zorder=1)
            image_artist.set_gid(f"celltype_split_image__{celltype_token}")
            axis_off(ax)
            add_scalebar_20um(ax, mask_i.shape, px_um_x, bar_um=20.0)
            ax.text(
                0.985,
                0.985,
                ct_names[i - 1],
                transform=ax.transAxes,
                ha="right",
                va="top",
                fontsize=14,
                fontweight="bold",
                color=ct_hex[i - 1],
                bbox=dict(boxstyle="round,pad=0.25", facecolor=(0, 0, 0, 0.25), edgecolor="none"),
            )

        split_fig.subplots_adjust(left=0, right=1, bottom=0, top=1, wspace=0.02)

    if save_outputs:
        save_uint16_tiff(save_dir / "celltypes_mask_uint16.tiff", celltype_mask.astype(np.uint16))
        df_cells.to_csv(save_dir / "cells_summary.csv", index=False)
        counts.to_csv(save_dir / "celltype_counts.csv", index=False)
        thresholds_df.to_csv(save_dir / "marker_assignment_thresholds.csv", index=False)
        save_celltype_config(celltype_cfg, save_dir)
        if make_figures and panel_fig is not None and split_fig is not None:
            panel_fig.savefig(panel_svg, bbox_inches="tight", pad_inches=0)
            panel_fig.savefig(panel_png, dpi=300, bbox_inches="tight", pad_inches=0)
            panel_fig.savefig(panel_tiff, dpi=600, bbox_inches="tight", pad_inches=0)
            split_fig.savefig(split_svg, bbox_inches="tight", pad_inches=0, transparent=True)
            split_fig.savefig(split_png, dpi=300, bbox_inches="tight", pad_inches=0, transparent=True)
            split_fig.savefig(split_tiff, dpi=600, bbox_inches="tight", pad_inches=0, transparent=True)

    return {
        "df_cells": df_cells,
        "counts": counts,
        "celltype_mask": celltype_mask,
        "thresholds": thresholds_df,
        "nuc_channel": nuc_channel,
        "panel_figure": panel_fig,
        "split_figure": split_fig,
        "params_used": params.to_dict(),
        "saved_paths": {
            "celltype_mask_tiff": save_dir / "celltypes_mask_uint16.tiff",
            "cells_summary_csv": save_dir / "cells_summary.csv",
            "celltype_counts_csv": save_dir / "celltype_counts.csv",
            "marker_assignment_thresholds_csv": save_dir / "marker_assignment_thresholds.csv",
            "panel_svg": panel_svg,
            "panel_png": panel_png,
            "panel_tiff": panel_tiff,
            "split_svg": split_svg,
            "split_png": split_png,
            "split_tiff": split_tiff,
            "celltype_config_json": save_dir / "celltype_config.json",
        },
    }


def run_celltype_assignment(
    folder: Path,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    image_id: str,
    channels_cfg: Sequence[Dict[str, Any]],
    celltype_cfg: Sequence[Dict[str, Any]],
    labels: np.ndarray | None = None,
    df_pixels: pd.DataFrame | None = None,
    shapes: Dict[Tuple[str, str], Tuple[int, int]] | None = None,
    params: CelltypeAssignmentParams | Dict[str, Any] | None = None,
    save_outputs: bool = True,
    make_figures: bool = True,
    native_threads: int | None = None,
    support_workers: int | None = None,
) -> Dict[str, Any]:
    return _run_celltype_assignment_impl(
        folder=folder,
        save_dir=save_dir,
        pixel_size_um=pixel_size_um,
        image_id=image_id,
        channels_cfg=channels_cfg,
        celltype_cfg=celltype_cfg,
        labels=labels,
        df_pixels=df_pixels,
        shapes=shapes,
        params=params,
        save_outputs=save_outputs,
        make_figures=make_figures,
        native_threads=native_threads,
        support_workers=support_workers,
    )


def rank_celltype_assignment_parameter_sweep_results(
    df_results: pd.DataFrame,
    defined_celltype_names: Sequence[str] | None = None,
) -> pd.DataFrame:
    ranked = df_results.copy()
    if "error" in ranked.columns:
        ranked = ranked[ranked["error"].fillna("") == ""].copy()
    if len(ranked) == 0:
        return ranked

    if defined_celltype_names is None:
        defined_celltype_names = [
            col.replace("count::", "")
            for col in ranked.columns
            if str(col).startswith("count::") and col not in {"count::Unassigned", "count::Ambiguous"}
        ]
    defined_celltype_names = list(defined_celltype_names)

    if "assigned_defined_total" not in ranked.columns:
        total = np.zeros(len(ranked), dtype=float)
        for name in defined_celltype_names:
            col = f"count::{name}"
            if col in ranked.columns:
                total = total + pd.to_numeric(ranked[col], errors="coerce").fillna(0).to_numpy()
        ranked["assigned_defined_total"] = total

    sort_cols = ["assigned_defined_total"]
    ascending = [False]
    if "count::Ambiguous" in ranked.columns:
        sort_cols.append("count::Ambiguous")
        ascending.append(True)
    if "count::Unassigned" in ranked.columns:
        sort_cols.append("count::Unassigned")
        ascending.append(True)
    if "combo_index" in ranked.columns:
        sort_cols.append("combo_index")
        ascending.append(True)
    return ranked.sort_values(sort_cols, ascending=ascending).reset_index(drop=True)


def recommend_celltype_assignment_parameter_sweep_result(
    df_results: pd.DataFrame,
    defined_celltype_names: Sequence[str] | None = None,
) -> pd.Series | None:
    ranked = rank_celltype_assignment_parameter_sweep_results(df_results, defined_celltype_names=defined_celltype_names)
    if len(ranked) == 0:
        return None
    return ranked.iloc[0]


def run_celltype_assignment_parameter_sweep(
    folder: Path,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    image_id: str,
    channels_cfg: Sequence[Dict[str, Any]],
    celltype_cfg: Sequence[Dict[str, Any]],
    labels: np.ndarray,
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    base_params: CelltypeAssignmentParams,
    sweep_values: Dict[str, Sequence[Any]],
    save_outputs: bool = True,
    parallel_workers: int = 1,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Dict[str, Any]:
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    ordered_values: Dict[str, List[Any]] = {}
    n_combinations = 1
    for field in CELLTYPE_PARAM_ORDER:
        values = list(sweep_values.get(field, [getattr(base_params, field)]))
        if field == "thresh_mode":
            norm_values = [str(v) for v in values if str(v).strip()]
            if not norm_values:
                norm_values = [str(getattr(base_params, field))]
        elif field in {"min_pos_object_size_px", "min_pos_pix"}:
            norm_values = [max(0, int(v)) for v in values]
        else:
            norm_values = [float(v) for v in values]
        seen = set()
        unique_values = []
        for v in norm_values:
            key = str(v) if field == "thresh_mode" else float(v)
            if key in seen:
                continue
            seen.add(key)
            unique_values.append(v)
        ordered_values[field] = unique_values
        n_combinations *= len(unique_values)

    defined_count_columns = [f"count::{ct['name']}" for ct in celltype_cfg]
    extra_count_columns = ["count::Unassigned", "count::Ambiguous"]
    count_columns = defined_count_columns + extra_count_columns

    combos = list(product(*[ordered_values[field] for field in CELLTYPE_PARAM_ORDER]))

    def evaluate_combo(combo_index: int, combo_values: Tuple[Any, ...]) -> Dict[str, Any]:
        overrides = {field: value for field, value in zip(CELLTYPE_PARAM_ORDER, combo_values)}
        params = replace(base_params, **overrides)
        row: Dict[str, Any] = {
            "combo_index": int(combo_index),
        }
        row.update({CELLTYPE_PARAM_LABELS[field]: getattr(params, field) for field in CELLTYPE_PARAM_ORDER})
        try:
            result = _run_celltype_assignment_impl(
                folder=folder,
                save_dir=save_dir,
                pixel_size_um=pixel_size_um,
                image_id=image_id,
                channels_cfg=channels_cfg,
                celltype_cfg=celltype_cfg,
                labels=labels,
                df_pixels=df_pixels,
                shapes=shapes,
                params=params,
                save_outputs=False,
                make_figures=False,
                native_threads=1,
                support_workers=1,
            )
            counts_map = result["counts"].set_index("celltype")["count"].to_dict() if len(result["counts"]) > 0 else {}
            row["n_cells"] = int(len(result["df_cells"]))
            for col in count_columns:
                ct_name = col.replace("count::", "")
                row[col] = int(counts_map.get(ct_name, 0))
            row["assigned_defined_total"] = int(sum(int(counts_map.get(ct["name"], 0)) for ct in celltype_cfg))
            row["error"] = ""
        except Exception as exc:
            row["n_cells"] = np.nan
            for col in count_columns:
                row[col] = np.nan
            row["assigned_defined_total"] = np.nan
            row["error"] = str(exc)
        return row

    # Run sequentially for stability. The progress callback is invoked after each
    # tested combination so the Streamlit UI can update a progress bar.
    records: List[Dict[str, Any]] = []
    total = len(combos)
    if progress_callback is not None:
        progress_callback(0, total)
    for done, (combo_index, combo_values) in enumerate(enumerate(combos, start=1), start=1):
        records.append(evaluate_combo(combo_index, combo_values))
        if progress_callback is not None:
            progress_callback(done, total)

    df_results = pd.DataFrame(records)
    csv_path = save_dir / "celltype_assignment_parameter_sweep_results.csv"
    json_path = save_dir / "celltype_assignment_parameter_sweep_grid.json"
    fig = make_celltype_assignment_parameter_sweep_figure(df_results, count_columns, celltype_cfg=celltype_cfg)
    svg_path = save_dir / "celltype_assignment_parameter_sweep.svg"
    png_path = save_dir / "celltype_assignment_parameter_sweep.png"

    if save_outputs:
        df_results.to_csv(csv_path, index=False)
        write_json(
            json_path,
            {
                "n_combinations": int(n_combinations),
                "base_params": base_params.to_dict(),
                "candidate_values": {
                    CELLTYPE_PARAM_LABELS[k]: [str(v) if k == "thresh_mode" else float(v) if k not in {"min_pos_object_size_px", "min_pos_pix"} else int(v) for v in vals]
                    for k, vals in ordered_values.items()
                },
                "count_columns": count_columns,
                "execution_mode": "sequential",
            },
        )
        fig.savefig(svg_path, dpi=300, bbox_inches="tight")
        fig.savefig(png_path, dpi=300, bbox_inches="tight")

    return {
        "results": df_results,
        "figure": fig,
        "n_combinations": int(n_combinations),
        "candidate_values": ordered_values,
        "count_columns": count_columns,
        "saved_paths": {
            "csv": csv_path,
            "json": json_path,
            "svg": svg_path,
            "png": png_path,
        },
    }


def rank_celltype_assignment_optimizer_results(
    df_results: pd.DataFrame,
    defined_celltype_names: Sequence[str] | None = None,
) -> pd.DataFrame:
    ranked = df_results.copy()
    if "error" in ranked.columns:
        ranked = ranked[ranked["error"].fillna("") == ""].copy()
    if len(ranked) == 0:
        return ranked

    if defined_celltype_names is None:
        defined_celltype_names = [
            col.replace("count::", "")
            for col in ranked.columns
            if str(col).startswith("count::") and col not in {"count::Unassigned", "count::Ambiguous"}
        ]
    defined_celltype_names = list(defined_celltype_names)

    if "assigned_defined_total" not in ranked.columns:
        total = np.zeros(len(ranked), dtype=float)
        for name in defined_celltype_names:
            col = f"count::{name}"
            if col in ranked.columns:
                total = total + pd.to_numeric(ranked[col], errors="coerce").fillna(0).to_numpy()
        ranked["assigned_defined_total"] = total

    ambiguous = (
        pd.to_numeric(ranked["count::Ambiguous"], errors="coerce").fillna(np.inf)
        if "count::Ambiguous" in ranked.columns
        else pd.Series(np.zeros(len(ranked), dtype=float), index=ranked.index)
    )
    unassigned = (
        pd.to_numeric(ranked["count::Unassigned"], errors="coerce").fillna(np.inf)
        if "count::Unassigned" in ranked.columns
        else pd.Series(np.zeros(len(ranked), dtype=float), index=ranked.index)
    )
    ranked["unresolved_total"] = ambiguous + unassigned

    sort_cols = ["unresolved_total"]
    ascending = [True]
    if "count::Ambiguous" in ranked.columns:
        sort_cols.append("count::Ambiguous")
        ascending.append(True)
    if "count::Unassigned" in ranked.columns:
        sort_cols.append("count::Unassigned")
        ascending.append(True)
    if "assigned_defined_total" in ranked.columns:
        sort_cols.append("assigned_defined_total")
        ascending.append(False)
    if "combo_index" in ranked.columns:
        sort_cols.append("combo_index")
        ascending.append(True)
    return ranked.sort_values(sort_cols, ascending=ascending).reset_index(drop=True)


def recommend_celltype_assignment_optimizer_result(
    df_results: pd.DataFrame,
    defined_celltype_names: Sequence[str] | None = None,
) -> pd.Series | None:
    ranked = rank_celltype_assignment_optimizer_results(
        df_results,
        defined_celltype_names=defined_celltype_names,
    )
    if len(ranked) == 0:
        return None
    return ranked.iloc[0]


def _assignment_decimal_places_from_step(step: float) -> int:
    text = f"{float(step):.10f}".rstrip("0").rstrip(".")
    if "." not in text:
        return 0
    return len(text.split(".")[-1])


def _assignment_value_key(value: Any) -> Any:
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    return str(value)


def _build_assignment_numeric_values(kind: str, lower: float, upper: float, step: float) -> List[Any]:
    kind = str(kind)
    lower = float(lower)
    upper = float(upper)
    step = float(step)
    if step <= 0:
        step = 1.0
    n_values = int(round((upper - lower) / step)) + 1
    decimals = _assignment_decimal_places_from_step(step)
    values: List[Any] = []
    for idx in range(max(1, n_values)):
        raw_value = lower + idx * step
        raw_value = min(max(raw_value, lower), upper)
        if kind == "int":
            values.append(int(round(raw_value)))
        else:
            values.append(float(round(raw_value, decimals + 2)))
    unique_values: List[Any] = []
    seen: set[Any] = set()
    for value in values:
        key = _assignment_value_key(value)
        if key in seen:
            continue
        seen.add(key)
        unique_values.append(value)
    return unique_values


def _build_celltype_assignment_search_space_specs(
    search_specs: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    built: Dict[str, Dict[str, Any]] = {}
    for field in CELLTYPE_OPTIMIZER_PARAM_ORDER:
        spec = dict(search_specs[field])
        kind = str(spec.get("kind", "float")).strip().lower()
        if kind in {"choice", "bool"}:
            raw_options = list(spec.get("options", []))
            options: List[Any] = []
            seen: set[Any] = set()
            for option in raw_options:
                value = bool(option) if kind == "bool" else option
                key = _assignment_value_key(value)
                if key in seen:
                    continue
                seen.add(key)
                options.append(value)
            if not options:
                raise ValueError(f"Search space for {field} has no valid options.")
            built[field] = {
                "kind": kind,
                "options": options,
                "values": options,
                "n_values": int(len(options)),
            }
            continue

        lower = float(spec["min"])
        upper = float(spec["max"])
        step = float(spec["step"])
        values = _build_assignment_numeric_values(kind, lower, upper, step)
        if not values:
            raise ValueError(f"Search space for {field} has no valid numeric values.")
        built[field] = {
            "kind": kind,
            "min": float(values[0]),
            "max": float(values[-1]),
            "step": float(step),
            "values": values,
            "n_values": int(len(values)),
        }
    return built


def _intersect_celltype_assignment_search_space_specs(
    full_search_space_specs: Dict[str, Dict[str, Any]],
    limited_search_specs: Dict[str, Dict[str, Any]] | None,
) -> Dict[str, Dict[str, Any]] | None:
    if not limited_search_specs:
        return None

    intersected: Dict[str, Dict[str, Any]] = {}
    for field in CELLTYPE_OPTIMIZER_PARAM_ORDER:
        full_spec = dict(full_search_space_specs[field])
        limited_spec = dict(limited_search_specs.get(field, {}))
        full_kind = str(full_spec.get("kind", "float")).strip().lower()
        limited_kind = str(limited_spec.get("kind", full_kind)).strip().lower()
        if full_kind != limited_kind:
            return None

        if full_kind in {"choice", "bool"}:
            limited_options = list(limited_spec.get("options", []))
            if full_kind == "bool":
                limited_options = [bool(option) for option in limited_options]
            limited_keys = {_assignment_value_key(option) for option in limited_options}
            options = [
                option
                for option in full_spec.get("options", [])
                if _assignment_value_key(option) in limited_keys
            ]
            if not options:
                return None
            intersected[field] = {
                "kind": full_kind,
                "options": options,
                "values": options,
                "n_values": int(len(options)),
            }
            continue

        values = list(full_spec.get("values", []))
        if not values:
            return None
        lower = float(limited_spec.get("min", values[0]))
        upper = float(limited_spec.get("max", values[-1]))
        filtered_values = [
            value
            for value in values
            if float(value) >= lower - 1e-12 and float(value) <= upper + 1e-12
        ]
        if not filtered_values:
            return None
        intersected[field] = {
            "kind": full_kind,
            "min": float(filtered_values[0]),
            "max": float(filtered_values[-1]),
            "step": float(full_spec.get("step", 1.0)),
            "values": filtered_values,
            "n_values": int(len(filtered_values)),
        }
    return intersected


def _celltype_assignment_search_space_n_combinations(
    search_space_specs: Dict[str, Dict[str, Any]],
) -> int:
    total = 1
    for field in CELLTYPE_OPTIMIZER_PARAM_ORDER:
        total *= int(search_space_specs[field]["n_values"])
    return int(total)


def _celltype_assignment_combo_from_params(params: CelltypeAssignmentParams) -> Tuple[Any, ...]:
    return tuple(getattr(params, field) for field in CELLTYPE_OPTIMIZER_PARAM_ORDER)


def _celltype_assignment_value_to_index(value: Any, spec: Dict[str, Any]) -> int:
    values = list(spec.get("values", []))
    if not values:
        return 0
    target_key = _assignment_value_key(value)
    for idx, candidate in enumerate(values):
        if _assignment_value_key(candidate) == target_key:
            return idx
    if str(spec.get("kind", "float")).strip().lower() in {"float", "int"}:
        numeric_value = float(value)
        return min(
            range(len(values)),
            key=lambda idx: abs(float(values[idx]) - numeric_value),
        )
    return 0


def _celltype_assignment_snap_value_to_search_space(value: Any, spec: Dict[str, Any]) -> Any:
    values = list(spec.get("values", []))
    if not values:
        return value
    return values[_celltype_assignment_value_to_index(value, spec)]


def _celltype_assignment_snap_combo_to_search_space(
    combo: Sequence[Any],
    search_space_specs: Dict[str, Dict[str, Any]],
) -> Tuple[Any, ...]:
    snapped: List[Any] = []
    for idx, field in enumerate(CELLTYPE_OPTIMIZER_PARAM_ORDER):
        snapped.append(_celltype_assignment_snap_value_to_search_space(combo[idx], search_space_specs[field]))
    return tuple(snapped)


def _generate_exhaustive_celltype_assignment_combo_values(
    search_space_specs: Dict[str, Dict[str, Any]],
) -> List[Tuple[Any, ...]]:
    ordered_values = [list(search_space_specs[field]["values"]) for field in CELLTYPE_OPTIMIZER_PARAM_ORDER]
    return [tuple(combo) for combo in product(*ordered_values)]


def _sample_global_celltype_assignment_candidates(
    search_space_specs: Dict[str, Dict[str, Any]],
    n_candidates: int,
    rng: np.random.Generator,
) -> List[Tuple[Any, ...]]:
    if n_candidates <= 0:
        return []
    combos: List[Tuple[Any, ...]] = []
    for _ in range(int(n_candidates)):
        combo: List[Any] = []
        for field in CELLTYPE_OPTIMIZER_PARAM_ORDER:
            values = list(search_space_specs[field]["values"])
            combo.append(values[int(rng.integers(0, len(values)))])
        combos.append(tuple(combo))
    return combos


def _sample_local_celltype_assignment_candidates(
    elite_rows: pd.DataFrame,
    search_space_specs: Dict[str, Dict[str, Any]],
    n_candidates: int,
    radius_fraction: float,
    rng: np.random.Generator,
) -> List[Tuple[Any, ...]]:
    if n_candidates <= 0 or len(elite_rows) == 0:
        return []

    combos: List[Tuple[Any, ...]] = []
    elite_indices = list(range(len(elite_rows)))
    for _ in range(int(n_candidates)):
        elite_row = elite_rows.iloc[int(rng.choice(elite_indices))]
        combo: List[Any] = []
        for field in CELLTYPE_OPTIMIZER_PARAM_ORDER:
            spec = search_space_specs[field]
            values = list(spec["values"])
            current_value = elite_row[CELLTYPE_OPTIMIZER_PARAM_LABELS[field]]
            current_index = _celltype_assignment_value_to_index(current_value, spec)
            if str(spec.get("kind", "float")).strip().lower() in {"choice", "bool"}:
                if len(values) == 1 or rng.random() < 0.8:
                    combo.append(values[current_index])
                else:
                    combo.append(values[int(rng.integers(0, len(values)))])
                continue
            radius_steps = max(1, int(math.ceil((len(values) - 1) * float(radius_fraction))))
            delta = int(rng.integers(-radius_steps, radius_steps + 1))
            candidate_index = min(max(current_index + delta, 0), len(values) - 1)
            combo.append(values[candidate_index])
        combos.append(tuple(combo))
    return combos


def _scaled_celltype_assignment_params_for_screening(
    params: CelltypeAssignmentParams,
    factor: int,
) -> CelltypeAssignmentParams:
    try:
        factor = max(1, int(factor))
    except Exception:
        factor = 1
    if factor <= 1:
        return params
    area_scale = max(1, int(factor) * int(factor))
    return replace(
        params,
        min_pos_object_size_px=max(0, int(round(float(params.min_pos_object_size_px) / float(area_scale)))),
        min_pos_pix=max(0, int(round(float(params.min_pos_pix) / float(area_scale)))),
    )


def _clamp_assignment_roi_bounds(
    center_x: float,
    center_y: float,
    roi_width: int,
    roi_height: int,
    full_width: int,
    full_height: int,
) -> Tuple[int, int, int, int]:
    roi_width = max(1, min(int(roi_width), int(full_width)))
    roi_height = max(1, min(int(roi_height), int(full_height)))

    x0 = int(round(float(center_x) - roi_width / 2.0))
    y0 = int(round(float(center_y) - roi_height / 2.0))
    x1 = x0 + roi_width
    y1 = y0 + roi_height

    if x0 < 0:
        x1 -= x0
        x0 = 0
    if y0 < 0:
        y1 -= y0
        y0 = 0
    if x1 > full_width:
        x0 -= (x1 - full_width)
        x1 = full_width
    if y1 > full_height:
        y0 -= (y1 - full_height)
        y1 = full_height

    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(full_width, x1)
    y1 = min(full_height, y1)
    return int(x0), int(y0), int(x1), int(y1)


def _build_fixed_five_roi_assignment_subset(
    *,
    labels: np.ndarray,
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    image_id: str,
    channels_cfg: Sequence[Dict[str, Any]],
    pixel_size_um: Tuple[float, float],
    roi_area_fraction: float = 0.02,
    gap_px: int = 16,
) -> Dict[str, Any]:
    full_height, full_width = labels.shape
    roi_fraction = max(1e-6, float(roi_area_fraction))
    roi_width = max(1, min(full_width, int(round(full_width * math.sqrt(roi_fraction)))))
    roi_height = max(1, min(full_height, int(round(full_height * math.sqrt(roi_fraction)))))
    gap_px = max(4, int(gap_px))

    anchor_layout = [
        ("upper_left", 0.25, 0.25, 0, 0),
        ("upper_right", 0.75, 0.25, 0, 1),
        ("center", 0.50, 0.50, 1, 0),
        ("lower_left", 0.25, 0.75, 2, 0),
        ("lower_right", 0.75, 0.75, 2, 1),
    ]

    # Fixed image-quarter anchors can all miss sparse or off-center tissue. Use
    # the same five spatial targets inside the detected-nuclei extent, then snap
    # each target to a different nucleus centroid. Every optimizer ROI therefore
    # contains labels while still sampling the tissue's corners and center.
    anchor_specs: List[Tuple[str, float, float, int, int]] = []
    region_properties = measure.regionprops_table(
        labels.astype(np.int32, copy=False),
        properties=("label", "centroid"),
    )
    centroid_x = np.asarray(region_properties.get("centroid-1", []), dtype=float)
    centroid_y = np.asarray(region_properties.get("centroid-0", []), dtype=float)
    if centroid_x.size:
        min_x, max_x = float(np.min(centroid_x)), float(np.max(centroid_x))
        min_y, max_y = float(np.min(centroid_y)), float(np.max(centroid_y))
        span_x = max(1.0, max_x - min_x)
        span_y = max(1.0, max_y - min_y)
        available = np.ones(centroid_x.shape, dtype=bool)
        for roi_name, x_fraction, y_fraction, row_idx, col_idx in anchor_layout:
            target_x = min_x + x_fraction * span_x
            target_y = min_y + y_fraction * span_y
            distances = ((centroid_x - target_x) / span_x) ** 2 + ((centroid_y - target_y) / span_y) ** 2
            if np.any(available):
                distances = np.where(available, distances, np.inf)
            selected_index = int(np.argmin(distances))
            available[selected_index] = False
            anchor_specs.append(
                (
                    roi_name,
                    float(centroid_x[selected_index]),
                    float(centroid_y[selected_index]),
                    row_idx,
                    col_idx,
                )
            )
    else:
        anchor_specs = [
            (
                roi_name,
                x_fraction * float(full_width),
                y_fraction * float(full_height),
                row_idx,
                col_idx,
            )
            for roi_name, x_fraction, y_fraction, row_idx, col_idx in anchor_layout
        ]

    mosaic_height = int(roi_height * 3 + gap_px * 2)
    mosaic_width = int(roi_width * 2 + gap_px)
    mosaic_labels = np.zeros((mosaic_height, mosaic_width), dtype=np.int32)
    roi_metadata: List[Dict[str, Any]] = []
    next_label_offset = 1

    for roi_name, center_x, center_y, row_idx, col_idx in anchor_specs:
        x0, y0, x1, y1 = _clamp_assignment_roi_bounds(
            center_x=center_x,
            center_y=center_y,
            roi_width=roi_width,
            roi_height=roi_height,
            full_width=full_width,
            full_height=full_height,
        )
        patch_labels = labels[y0:y1, x0:x1].astype(np.int32, copy=False)
        relabeled_patch = np.zeros_like(patch_labels, dtype=np.int32)
        positive_mask = patch_labels > 0
        if np.any(positive_mask):
            unique_labels = np.unique(patch_labels[positive_mask])
            relabeled_patch[positive_mask] = (
                np.searchsorted(unique_labels, patch_labels[positive_mask]).astype(np.int32)
                + next_label_offset
            )
            next_label_offset += int(len(unique_labels))

        target_y0 = int(row_idx * (roi_height + gap_px))
        target_x0 = int(col_idx * (roi_width + gap_px))
        target_y1 = target_y0 + (y1 - y0)
        target_x1 = target_x0 + (x1 - x0)
        mosaic_labels[target_y0:target_y1, target_x0:target_x1] = relabeled_patch

        roi_metadata.append(
            {
                "name": roi_name,
                "center_x_px": float(center_x),
                "center_y_px": float(center_y),
                "source_bounds_px": {
                    "x0": int(x0),
                    "y0": int(y0),
                    "x1": int(x1),
                    "y1": int(y1),
                },
                "mosaic_bounds_px": {
                    "x0": int(target_x0),
                    "y0": int(target_y0),
                    "x1": int(target_x1),
                    "y1": int(target_y1),
                },
                "area_px": int((x1 - x0) * (y1 - y0)),
                "area_fraction": float(((x1 - x0) * (y1 - y0)) / max(1, full_height * full_width)),
                "anchor_source": "nucleus_centroid" if centroid_x.size else "image_geometry",
            }
        )

    yy_idx, xx_idx = np.indices((mosaic_height, mosaic_width))
    has_um_coords = "x_um" in df_pixels.columns and "y_um" in df_pixels.columns
    mosaic_frames: List[pd.DataFrame] = []
    mosaic_shapes: Dict[Tuple[str, str], Tuple[int, int]] = {}
    channel_names = [str(channel_cfg["channel"]) for channel_cfg in channels_cfg]

    for channel_name in channel_names:
        full_img = to_image(df_pixels, shapes, image_id, channel_name).astype(np.float32, copy=False)
        mosaic_img = np.zeros((mosaic_height, mosaic_width), dtype=np.float32)
        for roi_spec in roi_metadata:
            src = roi_spec["source_bounds_px"]
            dst = roi_spec["mosaic_bounds_px"]
            mosaic_img[
                int(dst["y0"]):int(dst["y1"]),
                int(dst["x0"]):int(dst["x1"]),
            ] = full_img[
                int(src["y0"]):int(src["y1"]),
                int(src["x0"]):int(src["x1"]),
            ]

        frame = pd.DataFrame(
            {
                "image_id": image_id,
                "channel": channel_name,
                "y_px": yy_idx.ravel(order="C").astype(np.int32),
                "x_px": xx_idx.ravel(order="C").astype(np.int32),
                "value": mosaic_img.ravel(order="C"),
            }
        )
        if has_um_coords:
            frame["x_um"] = frame["x_px"].to_numpy(float) * float(pixel_size_um[0])
            frame["y_um"] = frame["y_px"].to_numpy(float) * float(pixel_size_um[1])
        mosaic_frames.append(frame)
        mosaic_shapes[(image_id, channel_name)] = (mosaic_height, mosaic_width)

    sampled_area_fraction = float(sum(float(spec["area_fraction"]) for spec in roi_metadata))
    return {
        "labels": mosaic_labels,
        "df_pixels": pd.concat(mosaic_frames, ignore_index=True),
        "shapes": mosaic_shapes,
        "roi_metadata": roi_metadata,
        "sampled_area_fraction": sampled_area_fraction,
        "mosaic_shape": (int(mosaic_height), int(mosaic_width)),
    }


def _build_vertical_band_assignment_subset(
    *,
    labels: np.ndarray,
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    image_id: str,
    channels_cfg: Sequence[Dict[str, Any]],
    pixel_size_um: Tuple[float, float],
    band_count: int = 10,
    selected_band_indices: Sequence[int] | None = None,
    selected_band_count: int = 5,
    gap_px: int = 16,
) -> Dict[str, Any]:
    full_height, full_width = labels.shape
    band_count = max(1, int(band_count))
    selected_band_count = max(1, int(selected_band_count))
    gap_px = max(4, int(gap_px))

    if selected_band_indices is None:
        auto_indices = {
            min(band_count - 1, int(math.floor(i * float(band_count) / float(selected_band_count))))
            for i in range(selected_band_count)
        }
        selected_band_indices = sorted(auto_indices)
    else:
        selected_band_indices = sorted(
            {
                int(idx)
                for idx in selected_band_indices
                if 0 <= int(idx) < band_count
            }
        )
        if not selected_band_indices:
            selected_band_indices = [0]

    band_bounds: List[Tuple[int, int]] = []
    for band_index in range(band_count):
        x0 = int(round(float(band_index) * float(full_width) / float(band_count)))
        x1 = int(round(float(band_index + 1) * float(full_width) / float(band_count)))
        x0 = max(0, min(full_width, x0))
        x1 = max(x0, min(full_width, x1))
        band_bounds.append((x0, x1))

    selected_widths = [max(0, band_bounds[idx][1] - band_bounds[idx][0]) for idx in selected_band_indices]
    mosaic_width = int(sum(selected_widths) + gap_px * max(0, len(selected_band_indices) - 1))
    mosaic_labels = np.zeros((int(full_height), int(mosaic_width)), dtype=np.int32)
    roi_metadata: List[Dict[str, Any]] = []
    next_label_offset = 1

    target_x0 = 0
    for band_index in selected_band_indices:
        x0, x1 = band_bounds[band_index]
        band_width = max(0, x1 - x0)
        patch_labels = labels[:, x0:x1].astype(np.int32, copy=False)
        relabeled_patch = np.zeros_like(patch_labels, dtype=np.int32)
        positive_mask = patch_labels > 0
        if np.any(positive_mask):
            unique_labels = np.unique(patch_labels[positive_mask])
            relabeled_patch[positive_mask] = (
                np.searchsorted(unique_labels, patch_labels[positive_mask]).astype(np.int32)
                + next_label_offset
            )
            next_label_offset += int(len(unique_labels))

        target_x1 = target_x0 + band_width
        if band_width > 0:
            mosaic_labels[:, target_x0:target_x1] = relabeled_patch

        roi_metadata.append(
            {
                "name": f"vertical_band_{band_index + 1}_of_{band_count}",
                "band_index_zero_based": int(band_index),
                "band_index_one_based": int(band_index + 1),
                "source_bounds_px": {
                    "x0": int(x0),
                    "y0": 0,
                    "x1": int(x1),
                    "y1": int(full_height),
                },
                "mosaic_bounds_px": {
                    "x0": int(target_x0),
                    "y0": 0,
                    "x1": int(target_x1),
                    "y1": int(full_height),
                },
                "area_px": int(band_width * full_height),
                "area_fraction": float((band_width * full_height) / max(1, full_height * full_width)),
            }
        )
        target_x0 = target_x1 + gap_px

    yy_idx, xx_idx = np.indices((full_height, mosaic_width))
    has_um_coords = "x_um" in df_pixels.columns and "y_um" in df_pixels.columns
    mosaic_frames: List[pd.DataFrame] = []
    mosaic_shapes: Dict[Tuple[str, str], Tuple[int, int]] = {}
    channel_names = [str(channel_cfg["channel"]) for channel_cfg in channels_cfg]

    for channel_name in channel_names:
        full_img = to_image(df_pixels, shapes, image_id, channel_name).astype(np.float32, copy=False)
        mosaic_img = np.zeros((int(full_height), int(mosaic_width)), dtype=np.float32)
        for roi_spec in roi_metadata:
            src = roi_spec["source_bounds_px"]
            dst = roi_spec["mosaic_bounds_px"]
            mosaic_img[
                int(dst["y0"]):int(dst["y1"]),
                int(dst["x0"]):int(dst["x1"]),
            ] = full_img[
                int(src["y0"]):int(src["y1"]),
                int(src["x0"]):int(src["x1"]),
            ]

        frame = pd.DataFrame(
            {
                "image_id": image_id,
                "channel": channel_name,
                "y_px": yy_idx.ravel(order="C").astype(np.int32),
                "x_px": xx_idx.ravel(order="C").astype(np.int32),
                "value": mosaic_img.ravel(order="C"),
            }
        )
        if has_um_coords:
            frame["x_um"] = frame["x_px"].to_numpy(float) * float(pixel_size_um[0])
            frame["y_um"] = frame["y_px"].to_numpy(float) * float(pixel_size_um[1])
        mosaic_frames.append(frame)
        mosaic_shapes[(image_id, channel_name)] = (int(full_height), int(mosaic_width))

    sampled_area_fraction = float(sum(float(spec["area_fraction"]) for spec in roi_metadata))
    return {
        "labels": mosaic_labels,
        "df_pixels": pd.concat(mosaic_frames, ignore_index=True),
        "shapes": mosaic_shapes,
        "roi_metadata": roi_metadata,
        "sampled_area_fraction": sampled_area_fraction,
        "mosaic_shape": (int(full_height), int(mosaic_width)),
        "band_count": int(band_count),
        "selected_band_indices": [int(idx) for idx in selected_band_indices],
    }


def _evaluate_celltype_assignment_combo_chunk(
    combo_chunk: Sequence[Tuple[int, Tuple[Any, ...]]],
    *,
    folder: Path,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    image_id: str,
    channels_cfg: Sequence[Dict[str, Any]],
    celltype_cfg: Sequence[Dict[str, Any]],
    labels: np.ndarray,
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    base_params: CelltypeAssignmentParams,
    native_threads: int | None = 1,
    support_workers: int | None = 1,
    screening_factor: int = 1,
) -> List[Dict[str, Any]]:
    defined_count_columns = [f"count::{ct['name']}" for ct in celltype_cfg]
    extra_count_columns = ["count::Unassigned", "count::Ambiguous"]
    count_columns = defined_count_columns + extra_count_columns
    rows: List[Dict[str, Any]] = []

    for combo_index, combo_values in combo_chunk:
        overrides = {
            field: value
            for field, value in zip(CELLTYPE_OPTIMIZER_PARAM_ORDER, combo_values)
        }
        params = replace(base_params, **overrides)
        eval_params = _scaled_celltype_assignment_params_for_screening(params, screening_factor)
        row: Dict[str, Any] = {"combo_index": int(combo_index)}
        for field in CELLTYPE_OPTIMIZER_PARAM_ORDER:
            row[CELLTYPE_OPTIMIZER_PARAM_LABELS[field]] = getattr(params, field)
        try:
            result = _run_celltype_assignment_impl(
                folder=folder,
                save_dir=save_dir,
                pixel_size_um=pixel_size_um,
                image_id=image_id,
                channels_cfg=channels_cfg,
                celltype_cfg=celltype_cfg,
                labels=labels,
                df_pixels=df_pixels,
                shapes=shapes,
                params=eval_params,
                save_outputs=False,
                make_figures=False,
                native_threads=native_threads,
                support_workers=support_workers,
            )
            counts_map = result["counts"].set_index("celltype")["count"].to_dict() if len(result["counts"]) > 0 else {}
            row["n_cells"] = int(len(result["df_cells"]))
            for col in count_columns:
                ct_name = col.replace("count::", "")
                row[col] = int(counts_map.get(ct_name, 0))
            row["assigned_defined_total"] = int(sum(int(counts_map.get(ct["name"], 0)) for ct in celltype_cfg))
            row["unresolved_total"] = int(row.get("count::Ambiguous", 0)) + int(row.get("count::Unassigned", 0))
            row["error"] = ""
        except Exception as exc:
            row["n_cells"] = np.nan
            for col in count_columns:
                row[col] = np.nan
            row["assigned_defined_total"] = np.nan
            row["unresolved_total"] = np.nan
            row["error"] = str(exc)
        rows.append(row)
    return rows


def _iter_assignment_combo_chunks(
    combo_records: Sequence[Tuple[int, Tuple[Any, ...]]],
    chunk_size: int,
) -> List[List[Tuple[int, Tuple[Any, ...]]]]:
    chunks: List[List[Tuple[int, Tuple[Any, ...]]]] = []
    chunk: List[Tuple[int, Tuple[Any, ...]]] = []
    for record in combo_records:
        chunk.append(record)
        if len(chunk) >= chunk_size:
            chunks.append(chunk)
            chunk = []
    if chunk:
        chunks.append(chunk)
    return chunks


def _evaluate_explicit_celltype_assignment_combo_records(
    combo_values: Sequence[Tuple[Any, ...]],
    *,
    combo_index_start: int,
    folder: Path,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    image_id: str,
    channels_cfg: Sequence[Dict[str, Any]],
    celltype_cfg: Sequence[Dict[str, Any]],
    labels: np.ndarray,
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    base_params: CelltypeAssignmentParams,
    parallel_workers: int = 1,
    parallel_backend: str = "loky",
    native_threads_per_worker: int | None = 1,
    support_workers_per_worker: int | None = 1,
    screening_factor: int = 1,
) -> List[Dict[str, Any]]:
    if not combo_values:
        return []

    combo_records = [
        (int(combo_index_start + idx), tuple(combo))
        for idx, combo in enumerate(combo_values)
    ]
    safe_backend = str(parallel_backend or "loky").strip().lower()
    if safe_backend not in {"loky", "threading"}:
        safe_backend = "loky"
    try:
        parallel_workers = max(1, int(parallel_workers))
    except Exception:
        parallel_workers = 1
    parallel_workers = min(parallel_workers, len(combo_records))

    if parallel_workers > 1 and Parallel is not None and delayed is not None:
        chunk_size = max(1, min(48, math.ceil(len(combo_records) / max(1, parallel_workers * 4))))
        # threadpoolctl changes process-wide native-library limits. Keep one
        # outer guard active for the complete threaded scan so sibling worker
        # contexts cannot restore a larger limit while another combo is still
        # running. Per-combo guards remain useful for process-based backends.
        outer_native_limit = 1 if safe_backend == "threading" else max(1, int(native_threads_per_worker or 1))
        with threadpool_limits(limits=outer_native_limit):
            chunk_results = Parallel(
                n_jobs=parallel_workers,
                backend=safe_backend,
                max_nbytes="32M",
                mmap_mode="r",
                batch_size=1,
                verbose=0,
            )(
                delayed(_evaluate_celltype_assignment_combo_chunk)(
                    combo_chunk,
                    folder=folder,
                    save_dir=save_dir,
                    pixel_size_um=pixel_size_um,
                    image_id=image_id,
                    channels_cfg=channels_cfg,
                    celltype_cfg=celltype_cfg,
                    labels=labels,
                    df_pixels=df_pixels,
                    shapes=shapes,
                    base_params=base_params,
                    native_threads=native_threads_per_worker,
                    support_workers=support_workers_per_worker,
                    screening_factor=screening_factor,
                )
                for combo_chunk in _iter_assignment_combo_chunks(combo_records, chunk_size)
            )
        return [row for chunk in chunk_results for row in chunk]

    return _evaluate_celltype_assignment_combo_chunk(
        combo_records,
        folder=folder,
        save_dir=save_dir,
        pixel_size_um=pixel_size_um,
        image_id=image_id,
        channels_cfg=channels_cfg,
        celltype_cfg=celltype_cfg,
        labels=labels,
        df_pixels=df_pixels,
        shapes=shapes,
        base_params=base_params,
        native_threads=native_threads_per_worker,
        support_workers=support_workers_per_worker,
        screening_factor=screening_factor,
    )


def _celltype_assignment_combo_key_from_result_row(row: pd.Series) -> Tuple[Any, ...]:
    return tuple(
        _assignment_value_key(row[CELLTYPE_OPTIMIZER_PARAM_LABELS[field]])
        for field in CELLTYPE_OPTIMIZER_PARAM_ORDER
    )


def _deduplicate_celltype_assignment_result_rows(df_results: pd.DataFrame) -> pd.DataFrame:
    if len(df_results) == 0:
        return df_results.copy()
    working = df_results.copy()
    working["_combo_key"] = [
        _celltype_assignment_combo_key_from_result_row(row)
        for _, row in working.iterrows()
    ]
    if "error" in working.columns:
        working["_error_rank"] = (working["error"].fillna("") != "").astype(int)
        working = working.sort_values(["_error_rank", "combo_index"], ascending=[True, True]).drop(columns=["_error_rank"])
    else:
        working = working.sort_values("combo_index")
    working = working.drop_duplicates("_combo_key", keep="first").drop(columns=["_combo_key"])
    return working.reset_index(drop=True)


def _prepare_celltype_assignment_screening_inputs(
    labels: np.ndarray,
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    image_id: str,
    pixel_size_um: Tuple[float, float],
    factor: int,
) -> Dict[str, Any] | None:
    try:
        factor = max(1, int(factor))
    except Exception:
        factor = 1
    if factor <= 1:
        return None

    labels_screen = np.ascontiguousarray(labels[::factor, ::factor]).astype(np.int32, copy=False)
    if min(labels_screen.shape[0], labels_screen.shape[1]) < 64:
        return None

    mask = (df_pixels["y_px"].to_numpy() % factor == 0) & (df_pixels["x_px"].to_numpy() % factor == 0)
    if not np.any(mask):
        return None
    df_screen = df_pixels.loc[mask].copy()
    df_screen["y_px"] = (df_screen["y_px"].to_numpy(np.int64) // factor).astype(np.int32)
    df_screen["x_px"] = (df_screen["x_px"].to_numpy(np.int64) // factor).astype(np.int32)
    if "x_um" in df_screen.columns:
        df_screen["x_um"] = df_screen["x_px"].to_numpy(float) * float(pixel_size_um[0]) * float(factor)
    if "y_um" in df_screen.columns:
        df_screen["y_um"] = df_screen["y_px"].to_numpy(float) * float(pixel_size_um[1]) * float(factor)

    shapes_screen: Dict[Tuple[str, str], Tuple[int, int]] = {}
    for key, shape in shapes.items():
        if key[0] != image_id:
            continue
        shapes_screen[key] = (
            int(math.ceil(int(shape[0]) / float(factor))),
            int(math.ceil(int(shape[1]) / float(factor))),
        )

    return {
        "factor": int(factor),
        "labels": labels_screen,
        "df_pixels": df_screen,
        "shapes": shapes_screen,
        "pixel_size_um": (float(pixel_size_um[0]) * factor, float(pixel_size_um[1]) * factor),
    }


def _select_celltype_screening_survivors(
    combo_pool: Sequence[Tuple[Any, ...]],
    ranked_df: pd.DataFrame,
    survivor_count: int,
) -> List[Tuple[Any, ...]]:
    survivor_count = max(1, min(int(survivor_count), len(combo_pool)))
    survivors: List[Tuple[Any, ...]] = []
    seen: set[Tuple[Any, ...]] = set()

    for _, row in ranked_df.iterrows():
        combo_idx = int(row["combo_index"]) - 1
        if combo_idx < 0 or combo_idx >= len(combo_pool):
            continue
        combo = tuple(combo_pool[combo_idx])
        combo_key = tuple(_assignment_value_key(value) for value in combo)
        if combo_key in seen:
            continue
        seen.add(combo_key)
        survivors.append(combo)
        if len(survivors) >= survivor_count:
            return survivors

    for combo in combo_pool:
        combo_key = tuple(_assignment_value_key(value) for value in combo)
        if combo_key in seen:
            continue
        seen.add(combo_key)
        survivors.append(tuple(combo))
        if len(survivors) >= survivor_count:
            break
    return survivors


def _screen_celltype_assignment_combo_pool_with_successive_halving(
    *,
    combo_pool: Sequence[Tuple[Any, ...]],
    survivor_count: int,
    folder: Path,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    image_id: str,
    channels_cfg: Sequence[Dict[str, Any]],
    celltype_cfg: Sequence[Dict[str, Any]],
    labels: np.ndarray,
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    base_params: CelltypeAssignmentParams,
    parallel_workers: int = 1,
    parallel_backend: str = "loky",
    native_threads_per_worker: int | None = 1,
    support_workers_per_worker: int | None = 1,
) -> Dict[str, Any]:
    working_pool = [tuple(combo) for combo in combo_pool]
    survivor_count = max(1, min(int(survivor_count), len(working_pool)))
    if len(working_pool) <= survivor_count:
        return {"survivors": working_pool, "n_screened": 0, "n_rounds": 0, "stage_factors": []}

    screening_specs: List[Dict[str, Any]] = []
    for factor in (4, 2):
        prepared = _prepare_celltype_assignment_screening_inputs(
            labels,
            df_pixels,
            shapes,
            image_id,
            pixel_size_um,
            factor,
        )
        if prepared is not None:
            screening_specs.append(prepared)
    if not screening_specs:
        return {
            "survivors": working_pool[:survivor_count],
            "n_screened": 0,
            "n_rounds": 0,
            "stage_factors": [],
        }

    screened_count = 0
    rounds_completed = 0
    stage_factors: List[int] = []
    current_pool = working_pool
    for spec in screening_specs:
        if len(current_pool) <= survivor_count:
            break
        stage_survivor_count = max(survivor_count, int(math.ceil(len(current_pool) / 2.0)))
        if stage_survivor_count >= len(current_pool):
            continue
        screen_rows = _evaluate_explicit_celltype_assignment_combo_records(
            current_pool,
            combo_index_start=1,
            folder=folder,
            save_dir=save_dir,
            pixel_size_um=spec["pixel_size_um"],
            image_id=image_id,
            channels_cfg=channels_cfg,
            celltype_cfg=celltype_cfg,
            labels=spec["labels"],
            df_pixels=spec["df_pixels"],
            shapes=spec["shapes"],
            base_params=base_params,
            parallel_workers=parallel_workers,
            parallel_backend=parallel_backend,
            native_threads_per_worker=native_threads_per_worker,
            support_workers_per_worker=support_workers_per_worker,
            screening_factor=int(spec["factor"]),
        )
        screened_count += len(current_pool)
        ranked_screen = rank_celltype_assignment_optimizer_results(
            pd.DataFrame(screen_rows),
            defined_celltype_names=[ct["name"] for ct in celltype_cfg],
        )
        current_pool = _select_celltype_screening_survivors(current_pool, ranked_screen, stage_survivor_count)
        rounds_completed += 1
        stage_factors.append(int(spec["factor"]))
    return {
        "survivors": current_pool[:survivor_count],
        "n_screened": int(screened_count),
        "n_rounds": int(rounds_completed),
        "stage_factors": stage_factors,
    }


def _mutate_celltype_assignment_combo(
    combo: Sequence[Any],
    search_space_specs: Dict[str, Dict[str, Any]],
    rng: np.random.Generator,
    *,
    mutation_rate: float = 0.35,
    mutation_scale: float = 0.12,
) -> Tuple[Any, ...]:
    mutated: List[Any] = []
    mutated_any = False
    for idx, field in enumerate(CELLTYPE_OPTIMIZER_PARAM_ORDER):
        spec = search_space_specs[field]
        values = list(spec["values"])
        current_index = _celltype_assignment_value_to_index(combo[idx], spec)
        kind = str(spec.get("kind", "float")).strip().lower()
        if len(values) <= 1 or rng.random() >= mutation_rate:
            mutated.append(values[current_index])
            continue
        if kind in {"choice", "bool"}:
            choice_indices = [i for i in range(len(values)) if i != current_index]
            if choice_indices:
                new_index = int(choice_indices[int(rng.integers(0, len(choice_indices)))])
            else:
                new_index = current_index
        else:
            radius_steps = max(1, int(math.ceil((len(values) - 1) * max(0.01, float(mutation_scale)))))
            delta = int(rng.integers(-radius_steps, radius_steps + 1))
            if delta == 0:
                delta = 1 if current_index < len(values) - 1 else -1
            new_index = min(max(current_index + delta, 0), len(values) - 1)
        mutated_any = mutated_any or (new_index != current_index)
        mutated.append(values[new_index])
    if not mutated_any and len(CELLTYPE_OPTIMIZER_PARAM_ORDER) > 0:
        forced_idx = int(rng.integers(0, len(CELLTYPE_OPTIMIZER_PARAM_ORDER)))
        field = CELLTYPE_OPTIMIZER_PARAM_ORDER[forced_idx]
        spec = search_space_specs[field]
        values = list(spec["values"])
        if len(values) > 1:
            current_index = _celltype_assignment_value_to_index(combo[forced_idx], spec)
            new_index = current_index + (1 if current_index < len(values) - 1 else -1)
            mutated[forced_idx] = values[new_index]
    return _celltype_assignment_snap_combo_to_search_space(mutated, search_space_specs)


def _crossover_celltype_assignment_combos(
    parent_a: Sequence[Any],
    parent_b: Sequence[Any],
    search_space_specs: Dict[str, Dict[str, Any]],
    rng: np.random.Generator,
) -> Tuple[Any, ...]:
    child: List[Any] = []
    for idx, field in enumerate(CELLTYPE_OPTIMIZER_PARAM_ORDER):
        spec = search_space_specs[field]
        values = list(spec["values"])
        kind = str(spec.get("kind", "float")).strip().lower()
        a_index = _celltype_assignment_value_to_index(parent_a[idx], spec)
        b_index = _celltype_assignment_value_to_index(parent_b[idx], spec)
        if kind in {"choice", "bool"}:
            if rng.random() < 0.5:
                child.append(values[a_index])
            else:
                child.append(values[b_index])
            continue
        if rng.random() < 0.35 and a_index != b_index:
            lower = min(a_index, b_index)
            upper = max(a_index, b_index)
            child.append(values[int(rng.integers(lower, upper + 1))])
        else:
            child.append(values[a_index] if rng.random() < 0.5 else values[b_index])
    return _celltype_assignment_snap_combo_to_search_space(child, search_space_specs)


def _run_budgeted_celltype_assignment_search_records(
    *,
    search_space_specs: Dict[str, Dict[str, Any]],
    max_evaluations: int,
    combo_index_start: int,
    folder: Path,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    image_id: str,
    channels_cfg: Sequence[Dict[str, Any]],
    celltype_cfg: Sequence[Dict[str, Any]],
    labels: np.ndarray,
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    base_params: CelltypeAssignmentParams,
    parallel_workers: int = 1,
    parallel_backend: str = "loky",
    native_threads_per_worker: int | None = 1,
    support_workers_per_worker: int | None = 1,
    random_seed: int = 42,
    seed_rows: pd.DataFrame | None = None,
) -> Dict[str, Any]:
    try:
        max_evaluations = max(0, int(max_evaluations))
    except Exception:
        max_evaluations = 0
    if max_evaluations <= 0:
        empty = pd.DataFrame()
        return {"results": empty, "ranked_results": empty, "n_evaluated": 0}

    rng = np.random.default_rng(int(random_seed))
    total_space_n_combinations = _celltype_assignment_search_space_n_combinations(search_space_specs)
    seen_combos: set[Tuple[Any, ...]] = set()
    pending_seed_combos: List[Tuple[Any, ...]] = []
    evaluated_rows: List[Dict[str, Any]] = []
    stage_index = 0
    search_batch_size = max(8, min(48, int(parallel_workers) * 4))
    radius_schedule = [0.25, 0.12, 0.06, 0.03]

    def queue_combo(combo: Sequence[Any]) -> None:
        snapped = _celltype_assignment_snap_combo_to_search_space(combo, search_space_specs)
        combo_key = tuple(_assignment_value_key(value) for value in snapped)
        if combo_key in seen_combos:
            return
        seen_combos.add(combo_key)
        pending_seed_combos.append(snapped)

    queue_combo(_celltype_assignment_combo_from_params(base_params))
    if seed_rows is not None and len(seed_rows) > 0:
        ranked_seed_rows = rank_celltype_assignment_optimizer_results(
            seed_rows,
            defined_celltype_names=[ct["name"] for ct in celltype_cfg],
        )
        for _, row in ranked_seed_rows.head(min(8, len(ranked_seed_rows))).iterrows():
            queue_combo(_celltype_assignment_combo_key_from_result_row(row))
            if len(pending_seed_combos) >= max_evaluations:
                break

    while len(evaluated_rows) < max_evaluations:
        remaining = max_evaluations - len(evaluated_rows)
        current_batch_size = min(search_batch_size, remaining)
        batch_combos: List[Tuple[Any, ...]] = []
        while pending_seed_combos and len(batch_combos) < current_batch_size:
            batch_combos.append(pending_seed_combos.pop(0))

        if len(batch_combos) < current_batch_size:
            oversample_n = max(current_batch_size * 6, 48)
            candidate_combos: List[Tuple[Any, ...]] = []
            if evaluated_rows:
                ranked_so_far = rank_celltype_assignment_optimizer_results(
                    pd.DataFrame(evaluated_rows),
                    defined_celltype_names=[ct["name"] for ct in celltype_cfg],
                )
                elite_rows = ranked_so_far.head(min(8, len(ranked_so_far)))
                radius_fraction = radius_schedule[min(stage_index, len(radius_schedule) - 1)]
                local_n = max(1, oversample_n // 2)
                global_n = max(1, oversample_n - local_n)
                candidate_combos.extend(
                    _sample_local_celltype_assignment_candidates(
                        elite_rows,
                        search_space_specs,
                        local_n,
                        radius_fraction,
                        rng,
                    )
                )
                candidate_combos.extend(
                    _sample_global_celltype_assignment_candidates(
                        search_space_specs,
                        global_n,
                        rng,
                    )
                )
            else:
                candidate_combos.extend(
                    _sample_global_celltype_assignment_candidates(
                        search_space_specs,
                        oversample_n,
                        rng,
                    )
                )

            for combo in candidate_combos:
                snapped = _celltype_assignment_snap_combo_to_search_space(combo, search_space_specs)
                combo_key = tuple(_assignment_value_key(value) for value in snapped)
                if combo_key in seen_combos:
                    continue
                seen_combos.add(combo_key)
                batch_combos.append(snapped)
                if len(batch_combos) >= current_batch_size:
                    break

        if not batch_combos and total_space_n_combinations <= max(2048, max_evaluations * 4):
            for combo in _generate_exhaustive_celltype_assignment_combo_values(search_space_specs):
                snapped = _celltype_assignment_snap_combo_to_search_space(combo, search_space_specs)
                combo_key = tuple(_assignment_value_key(value) for value in snapped)
                if combo_key in seen_combos:
                    continue
                seen_combos.add(combo_key)
                batch_combos.append(snapped)
                if len(batch_combos) >= current_batch_size:
                    break

        if not batch_combos:
            break

        batch_rows = _evaluate_explicit_celltype_assignment_combo_records(
            batch_combos,
            combo_index_start=combo_index_start + len(evaluated_rows),
            folder=folder,
            save_dir=save_dir,
            pixel_size_um=pixel_size_um,
            image_id=image_id,
            channels_cfg=channels_cfg,
            celltype_cfg=celltype_cfg,
            labels=labels,
            df_pixels=df_pixels,
            shapes=shapes,
            base_params=base_params,
            parallel_workers=parallel_workers,
            parallel_backend=parallel_backend,
            native_threads_per_worker=native_threads_per_worker,
            support_workers_per_worker=support_workers_per_worker,
        )
        evaluated_rows.extend(batch_rows)
        stage_index += 1

    df_results = _deduplicate_celltype_assignment_result_rows(pd.DataFrame(evaluated_rows))
    if len(df_results) > 0:
        df_results = df_results.sort_values("combo_index").reset_index(drop=True)
    ranked_results = rank_celltype_assignment_optimizer_results(
        df_results,
        defined_celltype_names=[ct["name"] for ct in celltype_cfg],
    )
    return {
        "results": df_results,
        "ranked_results": ranked_results,
        "n_evaluated": int(len(df_results)),
    }


def _run_evolutionary_celltype_assignment_search_records(
    *,
    search_space_specs: Dict[str, Dict[str, Any]],
    max_evaluations: int,
    combo_index_start: int,
    folder: Path,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    image_id: str,
    channels_cfg: Sequence[Dict[str, Any]],
    celltype_cfg: Sequence[Dict[str, Any]],
    labels: np.ndarray,
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    base_params: CelltypeAssignmentParams,
    parallel_workers: int = 1,
    parallel_backend: str = "loky",
    native_threads_per_worker: int | None = 1,
    support_workers_per_worker: int | None = 1,
    random_seed: int = 42,
    seed_rows: pd.DataFrame | None = None,
) -> Dict[str, Any]:
    try:
        max_evaluations = max(0, int(max_evaluations))
    except Exception:
        max_evaluations = 0
    if max_evaluations <= 0:
        empty = pd.DataFrame()
        return {
            "results": empty,
            "ranked_results": empty,
            "n_evaluated": 0,
            "n_generations": 0,
            "population_size": 0,
            "n_screened_candidates": 0,
            "n_screening_rounds": 0,
            "screening_stage_factors": [],
        }

    rng = np.random.default_rng(int(random_seed))
    total_space_n_combinations = _celltype_assignment_search_space_n_combinations(search_space_specs)
    population_size = max(10, min(48, int(max_evaluations), int(parallel_workers) * 10))
    candidate_pool_size = max(population_size, min(128, population_size * 4))
    elite_size = max(2, min(10, population_size // 4))
    immigrant_quota = max(2, min(8, population_size // 5))
    mutation_schedule = [0.20, 0.14, 0.09, 0.06]

    seen_combos: set[Tuple[Any, ...]] = set()
    evaluated_rows: List[Dict[str, Any]] = []
    generation_index = 0
    population: List[Tuple[Any, ...]] = []
    screened_candidate_count = 0
    screening_round_count = 0
    screening_stage_factors: set[int] = set()
    ranked_seed_rows = (
        rank_celltype_assignment_optimizer_results(
            seed_rows,
            defined_celltype_names=[ct["name"] for ct in celltype_cfg],
        )
        if seed_rows is not None and len(seed_rows) > 0
        else pd.DataFrame()
    )

    def add_unique_combo(target: List[Tuple[Any, ...]], combo: Sequence[Any]) -> bool:
        snapped = _celltype_assignment_snap_combo_to_search_space(combo, search_space_specs)
        combo_key = tuple(_assignment_value_key(value) for value in snapped)
        if combo_key in seen_combos:
            return False
        seen_combos.add(combo_key)
        target.append(snapped)
        return True

    add_unique_combo(population, _celltype_assignment_combo_from_params(base_params))
    for _, row in ranked_seed_rows.head(min(12, len(ranked_seed_rows))).iterrows():
        add_unique_combo(population, _celltype_assignment_combo_key_from_result_row(row))
        if len(population) >= candidate_pool_size:
            break

    if len(population) < candidate_pool_size and len(ranked_seed_rows) > 0:
        seed_local_candidates = _sample_local_celltype_assignment_candidates(
            ranked_seed_rows.head(min(8, len(ranked_seed_rows))),
            search_space_specs,
            max(candidate_pool_size * 2, 32),
            0.20,
            rng,
        )
        for combo in seed_local_candidates:
            add_unique_combo(population, combo)
            if len(population) >= candidate_pool_size:
                break

    if len(population) < candidate_pool_size:
        global_candidates = _sample_global_celltype_assignment_candidates(
            search_space_specs,
            max(candidate_pool_size * 3, 64),
            rng,
        )
        for combo in global_candidates:
            add_unique_combo(population, combo)
            if len(population) >= candidate_pool_size:
                break

    if len(population) < candidate_pool_size and total_space_n_combinations <= max(2048, max_evaluations * 6):
        for combo in _generate_exhaustive_celltype_assignment_combo_values(search_space_specs):
            add_unique_combo(population, combo)
            if len(population) >= candidate_pool_size:
                break

    while len(evaluated_rows) < max_evaluations and population:
        batch_size = min(population_size, max_evaluations - len(evaluated_rows))
        screen_result = _screen_celltype_assignment_combo_pool_with_successive_halving(
            combo_pool=population,
            survivor_count=batch_size,
            folder=folder,
            save_dir=save_dir,
            pixel_size_um=pixel_size_um,
            image_id=image_id,
            channels_cfg=channels_cfg,
            celltype_cfg=celltype_cfg,
            labels=labels,
            df_pixels=df_pixels,
            shapes=shapes,
            base_params=base_params,
            parallel_workers=parallel_workers,
            parallel_backend=parallel_backend,
            native_threads_per_worker=native_threads_per_worker,
            support_workers_per_worker=support_workers_per_worker,
        )
        screened_candidate_count += int(screen_result.get("n_screened", 0))
        screening_round_count += int(screen_result.get("n_rounds", 0))
        screening_stage_factors.update(int(v) for v in screen_result.get("stage_factors", []))
        batch_combos = [tuple(combo) for combo in screen_result.get("survivors", [])][:batch_size]
        if not batch_combos:
            break
        batch_rows = _evaluate_explicit_celltype_assignment_combo_records(
            batch_combos,
            combo_index_start=combo_index_start + len(evaluated_rows),
            folder=folder,
            save_dir=save_dir,
            pixel_size_um=pixel_size_um,
            image_id=image_id,
            channels_cfg=channels_cfg,
            celltype_cfg=celltype_cfg,
            labels=labels,
            df_pixels=df_pixels,
            shapes=shapes,
            base_params=base_params,
            parallel_workers=parallel_workers,
            parallel_backend=parallel_backend,
            native_threads_per_worker=native_threads_per_worker,
            support_workers_per_worker=support_workers_per_worker,
        )
        evaluated_rows.extend(batch_rows)
        generation_index += 1

        if len(evaluated_rows) >= max_evaluations:
            break

        ranked_all = rank_celltype_assignment_optimizer_results(
            pd.DataFrame(evaluated_rows),
            defined_celltype_names=[ct["name"] for ct in celltype_cfg],
        )
        elite_rows = ranked_all.head(min(elite_size, len(ranked_all)))
        elite_combos = [_celltype_assignment_combo_key_from_result_row(row) for _, row in elite_rows.iterrows()]

        next_population: List[Tuple[Any, ...]] = []
        mutation_scale = mutation_schedule[min(generation_index - 1, len(mutation_schedule) - 1)]
        remaining_budget = max_evaluations - len(evaluated_rows)
        next_pool_target = max(
            min(population_size, remaining_budget),
            min(candidate_pool_size, max(min(population_size, remaining_budget) * 4, 24)),
        )

        for combo in elite_combos:
            if len(next_population) >= next_pool_target:
                break
            add_unique_combo(
                next_population,
                _mutate_celltype_assignment_combo(
                    combo,
                    search_space_specs,
                    rng,
                    mutation_rate=0.55,
                    mutation_scale=mutation_scale,
                ),
            )

        breeding_pool = elite_combos.copy()
        breeding_pool.extend(
            [_celltype_assignment_combo_key_from_result_row(row) for _, row in ranked_seed_rows.head(min(6, len(ranked_seed_rows))).iterrows()]
        )
        if not breeding_pool and len(ranked_all) > 0:
            breeding_pool = [_celltype_assignment_combo_key_from_result_row(row) for _, row in ranked_all.head(min(8, len(ranked_all))).iterrows()]

        attempts = 0
        crossover_target = max(0, next_pool_target - immigrant_quota)
        while len(next_population) < crossover_target and attempts < next_pool_target * 40 and breeding_pool:
            parent_a = breeding_pool[int(rng.integers(0, len(breeding_pool)))]
            parent_b = breeding_pool[int(rng.integers(0, len(breeding_pool)))]
            child = _crossover_celltype_assignment_combos(parent_a, parent_b, search_space_specs, rng)
            if rng.random() < 0.9:
                child = _mutate_celltype_assignment_combo(
                    child,
                    search_space_specs,
                    rng,
                    mutation_rate=0.35,
                    mutation_scale=mutation_scale,
                )
            add_unique_combo(next_population, child)
            attempts += 1

        if len(next_population) < next_pool_target and len(elite_rows) > 0:
            local_candidates = _sample_local_celltype_assignment_candidates(
                elite_rows,
                search_space_specs,
                max((next_pool_target - len(next_population)) * 4, 16),
                max(0.03, mutation_scale),
                rng,
            )
            for combo in local_candidates:
                add_unique_combo(next_population, combo)
                if len(next_population) >= next_pool_target:
                    break

        if len(next_population) < next_pool_target:
            immigrant_candidates = _sample_global_celltype_assignment_candidates(
                search_space_specs,
                max((next_pool_target - len(next_population)) * 6, 24),
                rng,
            )
            for combo in immigrant_candidates:
                add_unique_combo(next_population, combo)
                if len(next_population) >= next_pool_target:
                    break

        if len(next_population) < next_pool_target and total_space_n_combinations <= max(2048, max_evaluations * 6):
            for combo in _generate_exhaustive_celltype_assignment_combo_values(search_space_specs):
                if add_unique_combo(next_population, combo) and len(next_population) >= next_pool_target:
                    break

        population = next_population[: max(0, next_pool_target)]

    df_results = _deduplicate_celltype_assignment_result_rows(pd.DataFrame(evaluated_rows))
    if len(df_results) > 0:
        df_results = df_results.sort_values("combo_index").reset_index(drop=True)
    ranked_results = rank_celltype_assignment_optimizer_results(
        df_results,
        defined_celltype_names=[ct["name"] for ct in celltype_cfg],
    )
    return {
        "results": df_results,
        "ranked_results": ranked_results,
        "n_evaluated": int(len(df_results)),
        "n_generations": int(generation_index),
        "population_size": int(population_size),
        "n_screened_candidates": int(screened_candidate_count),
        "n_screening_rounds": int(screening_round_count),
        "screening_stage_factors": sorted(screening_stage_factors),
    }


def run_celltype_assignment_parameter_optimizer(
    *,
    folder: Path,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    image_id: str,
    channels_cfg: Sequence[Dict[str, Any]],
    celltype_cfg: Sequence[Dict[str, Any]],
    labels: np.ndarray,
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    base_params: CelltypeAssignmentParams,
    search_specs: Dict[str, Dict[str, Any]],
    priority_search_specs: Dict[str, Dict[str, Any]] | None = None,
    save_outputs: bool = True,
    max_evaluations: int = 512,
    priority_target_evaluations: int | None = None,
    expansion_target_evaluations: int | None = None,
    exhaustive_limit: int = 4096,
    parallel_workers: int = 1,
    parallel_backend: str = "loky",
    native_threads_per_worker: int | None = 1,
    support_workers_per_worker: int | None = 1,
    random_seed: int = 42,
    output_prefix: str = "celltype_assignment_auto_optimizer",
    use_fixed_roi_subset: bool = False,
    roi_area_fraction_per_roi: float = 0.02,
    use_vertical_band_subset: bool = False,
    vertical_band_count: int = 10,
    vertical_band_selection_count: int = 5,
    vertical_band_indices: Sequence[int] | None = None,
) -> Dict[str, Any]:
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    safe_output_prefix = str(output_prefix or "celltype_assignment_auto_optimizer").strip() or "celltype_assignment_auto_optimizer"

    search_space_specs = _build_celltype_assignment_search_space_specs(search_specs)
    priority_space_specs = _intersect_celltype_assignment_search_space_specs(
        search_space_specs,
        priority_search_specs,
    )
    optimizer_labels = labels
    optimizer_df_pixels = df_pixels
    optimizer_shapes = shapes
    roi_mode = "full_image"
    roi_metadata: List[Dict[str, Any]] = []
    roi_sampled_area_fraction = 1.0
    roi_mosaic_shape = tuple(int(v) for v in labels.shape)
    selected_band_indices_out: List[int] = []
    band_count_out = 0
    if use_vertical_band_subset:
        roi_subset = _build_vertical_band_assignment_subset(
            labels=labels,
            df_pixels=df_pixels,
            shapes=shapes,
            image_id=image_id,
            channels_cfg=channels_cfg,
            pixel_size_um=pixel_size_um,
            band_count=vertical_band_count,
            selected_band_indices=vertical_band_indices,
            selected_band_count=vertical_band_selection_count,
        )
        optimizer_labels = roi_subset["labels"]
        optimizer_df_pixels = roi_subset["df_pixels"]
        optimizer_shapes = roi_subset["shapes"]
        roi_mode = "vertical_bands"
        roi_metadata = list(roi_subset.get("roi_metadata", []))
        roi_sampled_area_fraction = float(roi_subset.get("sampled_area_fraction", 1.0))
        roi_mosaic_shape = tuple(int(v) for v in roi_subset.get("mosaic_shape", labels.shape))
        selected_band_indices_out = [int(v) for v in roi_subset.get("selected_band_indices", [])]
        band_count_out = int(roi_subset.get("band_count", 0))
    elif use_fixed_roi_subset:
        roi_subset = _build_fixed_five_roi_assignment_subset(
            labels=labels,
            df_pixels=df_pixels,
            shapes=shapes,
            image_id=image_id,
            channels_cfg=channels_cfg,
            pixel_size_um=pixel_size_um,
            roi_area_fraction=roi_area_fraction_per_roi,
        )
        optimizer_labels = roi_subset["labels"]
        optimizer_df_pixels = roi_subset["df_pixels"]
        optimizer_shapes = roi_subset["shapes"]
        roi_mode = "fixed_five_2pct_anchors"
        roi_metadata = list(roi_subset.get("roi_metadata", []))
        roi_sampled_area_fraction = float(roi_subset.get("sampled_area_fraction", 1.0))
        roi_mosaic_shape = tuple(int(v) for v in roi_subset.get("mosaic_shape", labels.shape))
    full_space_n_combinations = _celltype_assignment_search_space_n_combinations(search_space_specs)
    priority_space_n_combinations = (
        _celltype_assignment_search_space_n_combinations(priority_space_specs)
        if priority_space_specs is not None
        else 0
    )

    try:
        max_evaluations = max(0, int(max_evaluations))
    except Exception:
        max_evaluations = 0
    try:
        priority_target_evaluations = (
            max_evaluations if priority_target_evaluations is None else max(0, int(priority_target_evaluations))
        )
    except Exception:
        priority_target_evaluations = max_evaluations
    try:
        expansion_target_evaluations = (
            0 if expansion_target_evaluations is None else max(0, int(expansion_target_evaluations))
        )
    except Exception:
        expansion_target_evaluations = 0

    search_mode = "adaptive_global_search"
    evaluated_priority_combinations = 0
    evaluated_expansion_combinations = 0
    expansion_generation_count = 0
    expansion_population_size = 0
    expansion_screened_candidate_count = 0
    expansion_screening_round_count = 0
    expansion_screening_stage_factors: List[int] = []

    if full_space_n_combinations <= exhaustive_limit:
        search_mode = "exhaustive_full_grid"
        all_combos = list(dict.fromkeys(_generate_exhaustive_celltype_assignment_combo_values(search_space_specs)))
        evaluated_rows = _evaluate_explicit_celltype_assignment_combo_records(
            all_combos,
            combo_index_start=1,
            folder=folder,
            save_dir=save_dir,
            pixel_size_um=pixel_size_um,
            image_id=image_id,
            channels_cfg=channels_cfg,
            celltype_cfg=celltype_cfg,
            labels=optimizer_labels,
            df_pixels=optimizer_df_pixels,
            shapes=optimizer_shapes,
            base_params=base_params,
            parallel_workers=parallel_workers,
            parallel_backend=parallel_backend,
            native_threads_per_worker=native_threads_per_worker,
            support_workers_per_worker=support_workers_per_worker,
        )
        df_results = _deduplicate_celltype_assignment_result_rows(pd.DataFrame(evaluated_rows))
        if len(df_results) > 0:
            df_results = df_results.sort_values("combo_index").reset_index(drop=True)
        evaluated_unique_combinations = int(len(df_results))
    else:
        search_parts: List[pd.DataFrame] = []
        next_combo_index = 1
        priority_ranked_results = pd.DataFrame()

        if priority_space_specs is not None and priority_target_evaluations > 0:
            search_mode = "core_first_expanded_search"
            priority_search_result = _run_budgeted_celltype_assignment_search_records(
                search_space_specs=priority_space_specs,
                max_evaluations=int(priority_target_evaluations),
                combo_index_start=next_combo_index,
                folder=folder,
                save_dir=save_dir,
                pixel_size_um=pixel_size_um,
                image_id=image_id,
                channels_cfg=channels_cfg,
                celltype_cfg=celltype_cfg,
                labels=optimizer_labels,
                df_pixels=optimizer_df_pixels,
                shapes=optimizer_shapes,
                base_params=base_params,
                parallel_workers=parallel_workers,
                parallel_backend=parallel_backend,
                native_threads_per_worker=native_threads_per_worker,
                support_workers_per_worker=support_workers_per_worker,
                random_seed=int(random_seed),
            )
            priority_results = priority_search_result["results"]
            priority_ranked_results = priority_search_result["ranked_results"]
            evaluated_priority_combinations = int(priority_search_result["n_evaluated"])
            if len(priority_results) > 0:
                search_parts.append(priority_results)
                next_combo_index += len(priority_results)

        if expansion_target_evaluations > 0:
            search_mode = "core_first_genetic_successive_halving_expansion"
            expansion_search_result = _run_evolutionary_celltype_assignment_search_records(
                search_space_specs=search_space_specs,
                max_evaluations=int(expansion_target_evaluations),
                combo_index_start=next_combo_index,
                folder=folder,
                save_dir=save_dir,
                pixel_size_um=pixel_size_um,
                image_id=image_id,
                channels_cfg=channels_cfg,
                celltype_cfg=celltype_cfg,
                labels=optimizer_labels,
                df_pixels=optimizer_df_pixels,
                shapes=optimizer_shapes,
                base_params=base_params,
                parallel_workers=parallel_workers,
                parallel_backend=parallel_backend,
                native_threads_per_worker=native_threads_per_worker,
                support_workers_per_worker=support_workers_per_worker,
                random_seed=int(random_seed) + 1,
                seed_rows=priority_ranked_results,
            )
            expansion_results = expansion_search_result["results"]
            evaluated_expansion_combinations = int(expansion_search_result["n_evaluated"])
            expansion_generation_count = int(expansion_search_result.get("n_generations", 0))
            expansion_population_size = int(expansion_search_result.get("population_size", 0))
            expansion_screened_candidate_count = int(expansion_search_result.get("n_screened_candidates", 0))
            expansion_screening_round_count = int(expansion_search_result.get("n_screening_rounds", 0))
            expansion_screening_stage_factors = [int(v) for v in expansion_search_result.get("screening_stage_factors", [])]
            if len(expansion_results) > 0:
                search_parts.append(expansion_results)

        if not search_parts:
            fallback_search_result = _run_budgeted_celltype_assignment_search_records(
                search_space_specs=search_space_specs,
                max_evaluations=int(max_evaluations),
                combo_index_start=1,
                folder=folder,
                save_dir=save_dir,
                pixel_size_um=pixel_size_um,
                image_id=image_id,
                channels_cfg=channels_cfg,
                celltype_cfg=celltype_cfg,
                labels=optimizer_labels,
                df_pixels=optimizer_df_pixels,
                shapes=optimizer_shapes,
                base_params=base_params,
                parallel_workers=parallel_workers,
                parallel_backend=parallel_backend,
                native_threads_per_worker=native_threads_per_worker,
                support_workers_per_worker=support_workers_per_worker,
                random_seed=int(random_seed),
            )
            df_results = fallback_search_result["results"]
            evaluated_expansion_combinations = int(fallback_search_result["n_evaluated"])
        else:
            df_results = _deduplicate_celltype_assignment_result_rows(pd.concat(search_parts, ignore_index=True))
            if len(df_results) > 0:
                df_results = df_results.sort_values("combo_index").reset_index(drop=True)
        evaluated_unique_combinations = int(len(df_results))

    defined_count_columns = [f"count::{ct['name']}" for ct in celltype_cfg]
    count_columns = defined_count_columns + ["count::Unassigned", "count::Ambiguous"]
    results_csv_path = save_dir / f"{safe_output_prefix}_results.csv"
    json_path = save_dir / f"{safe_output_prefix}_grid.json"
    fig = make_celltype_assignment_parameter_sweep_figure(df_results, count_columns, celltype_cfg=celltype_cfg)
    svg_path = save_dir / f"{safe_output_prefix}.svg"
    png_path = save_dir / f"{safe_output_prefix}.png"

    if save_outputs:
        df_results.to_csv(results_csv_path, index=False)
        write_json(
            json_path,
            {
                "search_mode": search_mode,
                "roi_mode": roi_mode,
                "roi_sampled_area_fraction": float(roi_sampled_area_fraction),
                "roi_mosaic_shape": [int(v) for v in roi_mosaic_shape],
                "roi_metadata": roi_metadata,
                "vertical_band_count": int(band_count_out),
                "selected_vertical_band_indices": [int(v) for v in selected_band_indices_out],
                "full_space_n_combinations": int(full_space_n_combinations),
                "priority_space_n_combinations": int(priority_space_n_combinations),
                "max_evaluations": int(max_evaluations),
                "priority_target_evaluations": int(priority_target_evaluations),
                "expansion_target_evaluations": int(expansion_target_evaluations),
                "evaluated_priority_combinations": int(evaluated_priority_combinations),
                "evaluated_expansion_combinations": int(evaluated_expansion_combinations),
                "expansion_generation_count": int(expansion_generation_count),
                "expansion_population_size": int(expansion_population_size),
                "expansion_screened_candidate_count": int(expansion_screened_candidate_count),
                "expansion_screening_round_count": int(expansion_screening_round_count),
                "expansion_screening_stage_factors": [int(v) for v in expansion_screening_stage_factors],
                "evaluated_unique_combinations": int(evaluated_unique_combinations),
                "base_params": base_params.to_dict(),
                "search_space": {
                    CELLTYPE_OPTIMIZER_PARAM_LABELS[field]: {
                        "kind": str(search_space_specs[field]["kind"]),
                        "values": [
                            bool(value) if isinstance(value, (np.bool_, bool)) else int(value) if isinstance(value, (np.integer, int)) else float(value) if isinstance(value, (np.floating, float)) else str(value)
                            for value in search_space_specs[field]["values"]
                        ],
                        "n_values": int(search_space_specs[field]["n_values"]),
                    }
                    for field in CELLTYPE_OPTIMIZER_PARAM_ORDER
                },
                "priority_search_space": None
                if priority_space_specs is None
                else {
                    CELLTYPE_OPTIMIZER_PARAM_LABELS[field]: {
                        "kind": str(priority_space_specs[field]["kind"]),
                        "values": [
                            bool(value) if isinstance(value, (np.bool_, bool)) else int(value) if isinstance(value, (np.integer, int)) else float(value) if isinstance(value, (np.floating, float)) else str(value)
                            for value in priority_space_specs[field]["values"]
                        ],
                        "n_values": int(priority_space_specs[field]["n_values"]),
                    }
                    for field in CELLTYPE_OPTIMIZER_PARAM_ORDER
                },
                "optimizer_config": {
                    "random_seed": int(random_seed),
                    "exhaustive_limit": int(exhaustive_limit),
                    "parallel_workers": int(parallel_workers),
                    "parallel_backend": str(parallel_backend),
                    "expansion_search_strategy": "evolutionary_genetic_successive_halving",
                    "objective": "minimize_unresolved_total_then_ambiguous_then_unassigned",
                },
                "parallel_config": {
                    "parallel_workers": int(parallel_workers),
                    "parallel_backend": str(parallel_backend),
                    "native_threads_per_worker": None
                    if native_threads_per_worker is None
                    else int(native_threads_per_worker),
                    "support_workers_per_worker": None
                    if support_workers_per_worker is None
                    else int(support_workers_per_worker),
                    "joblib_available": bool(Parallel is not None and delayed is not None),
                    "threadpoolctl_available": bool(threadpool_limits is not None),
                    "cpu_count": int(os.cpu_count() or 1),
                },
            },
        )
        fig.savefig(svg_path, dpi=300, bbox_inches="tight")
        fig.savefig(png_path, dpi=300, bbox_inches="tight")

    return {
        "results": df_results,
        "figure": fig,
        "search_mode": search_mode,
        "roi_mode": roi_mode,
        "roi_sampled_area_fraction": float(roi_sampled_area_fraction),
        "roi_mosaic_shape": [int(v) for v in roi_mosaic_shape],
        "roi_metadata": roi_metadata,
        "vertical_band_count": int(band_count_out),
        "selected_vertical_band_indices": [int(v) for v in selected_band_indices_out],
        "full_space_n_combinations": int(full_space_n_combinations),
        "priority_space_n_combinations": int(priority_space_n_combinations),
        "max_evaluations": int(max_evaluations),
        "priority_target_evaluations": int(priority_target_evaluations),
        "expansion_target_evaluations": int(expansion_target_evaluations),
        "evaluated_priority_combinations": int(evaluated_priority_combinations),
        "evaluated_expansion_combinations": int(evaluated_expansion_combinations),
        "expansion_generation_count": int(expansion_generation_count),
        "expansion_population_size": int(expansion_population_size),
        "expansion_screened_candidate_count": int(expansion_screened_candidate_count),
        "expansion_screening_round_count": int(expansion_screening_round_count),
        "expansion_screening_stage_factors": [int(v) for v in expansion_screening_stage_factors],
        "evaluated_unique_combinations": int(evaluated_unique_combinations),
        "count_columns": count_columns,
        "search_space": search_space_specs,
        "priority_search_space": priority_space_specs,
        "parallel_config": {
            "parallel_workers": int(parallel_workers),
            "parallel_backend": str(parallel_backend),
            "native_threads_per_worker": None
            if native_threads_per_worker is None
            else int(native_threads_per_worker),
            "support_workers_per_worker": None
            if support_workers_per_worker is None
            else int(support_workers_per_worker),
            "joblib_available": bool(Parallel is not None and delayed is not None),
            "threadpoolctl_available": bool(threadpool_limits is not None),
            "cpu_count": int(os.cpu_count() or 1),
        },
        "saved_paths": {
            "csv": results_csv_path,
            "json": json_path,
            "svg": svg_path,
            "png": png_path,
        },
    }
