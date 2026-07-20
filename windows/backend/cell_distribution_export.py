#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["svg.image_inline"] = True
matplotlib.rcParams["svg.fonttype"] = "path"

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch
from PIL import ImageColor
from scipy import ndimage as ndi


RUNTIME_SUPPORT_DIR = Path(__file__).resolve().parent / "runtime_support"
if RUNTIME_SUPPORT_DIR.exists() and str(RUNTIME_SUPPORT_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SUPPORT_DIR))

from src.spatialscope_analysis.compute_runtime import get_compute_runtime  # noqa: E402
from src.spatialscope_analysis.io import files_to_long_df, load_any_tiff, safe_name, valid_pixel_size, write_json  # noqa: E402
from src.spatialscope_analysis.visualization import overlay_multi_channels  # noqa: E402


CELL_DISTRIBUTION_BG_BLEND = 0.45
CELL_DISTRIBUTION_BAND_ALPHA = 0.48
CELL_DISTRIBUTION_CHANNEL_ALPHA = 0.72
CELL_DISTRIBUTION_CONTOUR_LINEWIDTH = 1.0
CELL_DISTRIBUTION_CONTOUR_ALPHA = 0.95
CELL_DISTRIBUTION_INSIDE_CMAP = "viridis"
CELL_DISTRIBUTION_OUTSIDE_CMAP = "magma"
CELL_DISTRIBUTION_DISPLAY_ORIGIN = "upper"
CELL_DISTRIBUTION_STRUCTURE = ndi.generate_binary_structure(2, 2)

CELL_DENSITY_PLOT_BACKGROUND_ALPHA = 0.22
CELL_DENSITY_PLOT_LINEWIDTH = 2.3
CELL_DENSITY_PLOT_MARKERSIZE = 4.0


@dataclass
class NativeChannel:
    file: str
    channel: str
    color_hex: str

    def to_dict(self) -> Dict[str, str]:
        return {"file": self.file, "channel": self.channel, "color_hex": self.color_hex}


@dataclass
class NativePipelineConfig:
    folder: Path
    save_dir: Path
    pixel_size_um: tuple[float, float] | None
    image_id: str
    channels: List[NativeChannel]
    overlay_channels: List[str]
    white_channel: str | None
    white_weight: float


def _format_float(value: float) -> str:
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def _band_token(band_width_um: float) -> str:
    return _format_float(float(band_width_um)).replace(".", "p")


def _normalise_channel_payload(row: Dict[str, Any]) -> NativeChannel:
    return NativeChannel(
        file=str(row.get("file", "")),
        channel=str(row.get("channel", "")),
        color_hex=str(row.get("color_hex") or row.get("colorHex") or "#ffffff"),
    )


def _read_config(output_folder: Path) -> NativePipelineConfig:
    config_path = output_folder / "00_config" / "pipeline_config.json"
    if not config_path.exists():
        raise RuntimeError(f"Missing pipeline config: {config_path}")
    payload = json.loads(config_path.read_text())
    raw_pixel_size = payload.get("pixel_size_um") or payload.get("pixelSizeUm") or []
    pixel_size: tuple[float, float] | None = None
    if isinstance(raw_pixel_size, Sequence) and len(raw_pixel_size) >= 2:
        pixel_size = (float(raw_pixel_size[0]), float(raw_pixel_size[1]))

    channels = [_normalise_channel_payload(row) for row in payload.get("channels", [])]
    overlay_channels = [str(v) for v in (payload.get("overlay_channels") or payload.get("overlayChannels") or [])]
    return NativePipelineConfig(
        folder=Path(payload.get("folder", "")).expanduser(),
        save_dir=Path(payload.get("save_dir") or payload.get("saveDir") or str(output_folder)).expanduser(),
        pixel_size_um=pixel_size,
        image_id=str(payload.get("image_id") or payload.get("imageID") or "FieldA"),
        channels=channels,
        overlay_channels=overlay_channels,
        white_channel=payload.get("white_channel") or payload.get("whiteChannel"),
        white_weight=float(payload.get("white_weight") or payload.get("whiteWeight") or 0.0),
    )


def _cell_distribution_output_dirs(config: NativePipelineConfig) -> Dict[str, Path]:
    root_dir = config.save_dir / "10_cell_distribution_analysis"
    region_masks_dir = root_dir / "01_region_masks"
    density_dir = root_dir / "02_cell_density"
    cluster_dir = root_dir / "03_cell_cluster_distribution"
    for path in (region_masks_dir, density_dir, cluster_dir):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "root": root_dir,
        "region_masks": region_masks_dir,
        "cell_density": density_dir,
        "cell_cluster_distribution": cluster_dir,
    }


def _save_figure_both_formats(
    fig,
    path: Path,
    dpi: int = 300,
    bbox_inches: str = "tight",
    pad_inches: float = 0.0,
    facecolor: str = "white",
) -> tuple[Path, Path]:
    base = path.with_suffix("") if path.suffix.lower() in {".png", ".svg", ".tiff"} else path
    base.parent.mkdir(parents=True, exist_ok=True)
    png_path = base.with_suffix(".png")
    svg_path = base.with_suffix(".svg")
    fig.savefig(str(png_path), dpi=dpi, bbox_inches=bbox_inches, pad_inches=pad_inches, facecolor=facecolor)
    fig.savefig(str(svg_path), dpi=dpi, bbox_inches=bbox_inches, pad_inches=pad_inches, facecolor=facecolor)
    return png_path, svg_path


def _boundary_seed_mask(boundary_mask: np.ndarray) -> np.ndarray:
    from skimage import segmentation

    boundary_mask = np.asarray(boundary_mask, dtype=bool)
    if not np.any(boundary_mask):
        return np.zeros_like(boundary_mask, dtype=bool)

    def without_image_frame(mask: np.ndarray) -> np.ndarray:
        cleaned = np.asarray(mask, dtype=bool).copy()
        if cleaned.size:
            cleaned[0, :] = False
            cleaned[-1, :] = False
            cleaned[:, 0] = False
            cleaned[:, -1] = False
        return cleaned

    seed_mask = segmentation.find_boundaries(boundary_mask, mode="thick")
    seed_mask = without_image_frame(seed_mask)
    if np.any(seed_mask):
        return seed_mask.astype(bool)
    fallback = boundary_mask ^ ndi.binary_erosion(boundary_mask, structure=CELL_DISTRIBUTION_STRUCTURE)
    fallback = without_image_frame(fallback)
    return fallback.astype(bool)


def _distance_bands_from_boundary(
    boundary_mask: np.ndarray,
    seed_mask: np.ndarray,
    band_width_um: float,
    pixel_size_um: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    boundary_mask = np.asarray(boundary_mask, dtype=bool)
    seed_mask = np.asarray(seed_mask, dtype=bool)
    if not np.any(boundary_mask):
        raise RuntimeError("The selected boundary mask is empty.")
    if not np.any(seed_mask):
        raise RuntimeError("The selected boundary mask does not contain a valid drawable edge.")
    if float(band_width_um) <= 0:
        raise RuntimeError("Band width must be > 0 um.")

    runtime = get_compute_runtime()
    inside_mask = runtime.equal_scalar(boundary_mask.astype(np.uint8), 1).astype(bool, copy=False)
    outside_mask = runtime.equal_scalar(boundary_mask.astype(np.uint8), 0).astype(bool, copy=False)
    distance_input = runtime.equal_scalar(seed_mask.astype(np.uint8), 0).astype(bool, copy=False)

    distance_um = ndi.distance_transform_edt(
        distance_input,
        sampling=(float(pixel_size_um[1]), float(pixel_size_um[0])),
    ).astype(np.float32, copy=False)
    inside_distance_um = distance_um.copy()
    inside_distance_um[outside_mask] = np.nan
    outside_distance_um = distance_um.copy()
    outside_distance_um[inside_mask] = np.nan

    inside_band_index = runtime.band_index(distance_um, inside_mask, float(band_width_um))
    outside_band_index = runtime.band_index(distance_um, outside_mask, float(band_width_um))
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


def _channel_color_hex_from_config(config: NativePipelineConfig, channel_name: str) -> str:
    for channel in config.channels:
        if str(channel.channel) == str(channel_name):
            return str(channel.color_hex)
    return "#ffffff"


def _channel_image_from_data(data_result: Dict[str, Any], config: NativePipelineConfig, channel_name: str) -> np.ndarray:
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
    return get_compute_runtime().normalize_clip(arr, lo_v, hi_v)


def _channel_rgba_for_distribution(
    data_result: Dict[str, Any],
    config: NativePipelineConfig,
    channel_name: str,
) -> np.ndarray:
    img = _channel_image_from_data(data_result, config, channel_name)
    intensity = _norm_clip_local(img, hi_percentile=99.8)
    rgb = np.array(ImageColor.getrgb(_channel_color_hex_from_config(config, channel_name)), dtype=float) / 255.0
    rgba = np.zeros(intensity.shape + (4,), dtype=float)
    rgba[..., :3] = rgb
    rgba[..., 3] = CELL_DISTRIBUTION_CHANNEL_ALPHA * intensity
    return rgba


def _build_overlay_rgb_for_region_ui(config: NativePipelineConfig, data_result: Dict[str, Any]) -> np.ndarray:
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
        if overlay_fig is not None:
            plt.close(overlay_fig)


def _render_region_band_map(
    *,
    config: NativePipelineConfig,
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
        Patch(
            facecolor=plt.get_cmap(CELL_DISTRIBUTION_INSIDE_CMAP)(0.75),
            edgecolor="none",
            alpha=CELL_DISTRIBUTION_BAND_ALPHA,
            label=f"{inside_name} {float(band_width_um):g} um bands",
        ),
        Patch(
            facecolor=plt.get_cmap(CELL_DISTRIBUTION_OUTSIDE_CMAP)(0.75),
            edgecolor="none",
            alpha=CELL_DISTRIBUTION_BAND_ALPHA,
            label=f"{outside_name} {float(band_width_um):g} um bands",
        ),
    ]
    title = f"{boundary_label} - {float(band_width_um):g} um boundary bands"
    if extra_channel_name is not None and extra_channel_rgba is not None:
        ax.imshow(np.clip(np.asarray(extra_channel_rgba), 0.0, 1.0), origin=CELL_DISTRIBUTION_DISPLAY_ORIGIN)
        handles.append(
            Patch(
                facecolor=_channel_color_hex_from_config(config, extra_channel_name),
                edgecolor="none",
                alpha=0.85,
                label=f"{extra_channel_name} overlay",
            )
        )
        title = f"{title} + {extra_channel_name}"

    ax.legend(handles=handles, loc="upper right", frameon=True)
    ax.set_title(title)
    ax.set_axis_off()
    fig.subplots_adjust(left=0, right=1, bottom=0, top=0.96)
    return fig


def _load_celltype_config(output_folder: Path) -> List[Dict[str, Any]]:
    for path in (
        output_folder / "05_cell_type_assignment" / "celltype_config.json",
        output_folder / "03_cell_type_definition" / "celltype_config.json",
    ):
        if not path.exists():
            continue
        rows = json.loads(path.read_text())
        out = []
        for row in rows:
            out.append(
                {
                    "name": str(row.get("name", "")),
                    "color_hex": str(row.get("color_hex") or row.get("colorHex") or "#ffffff"),
                }
            )
        return out
    return []


def _celltype_color_map(celltype_cfg: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    return {str(ct.get("name")): str(ct.get("color_hex", "#ffffff")) for ct in celltype_cfg}


def _load_assignment_cells(output_folder: Path) -> pd.DataFrame:
    csv_path = output_folder / "05_cell_type_assignment" / "celltype_assignments.csv"
    if not csv_path.exists():
        raise RuntimeError(f"Missing cell-type assignment CSV: {csv_path}")
    df = pd.read_csv(csv_path)
    if "celltype" not in df.columns and "assigned_type" in df.columns:
        df["celltype"] = df["assigned_type"].astype(str)
    required = {"celltype", "centroid_x_px", "centroid_y_px"}
    if not required.issubset(df.columns):
        raise RuntimeError(f"Cell-type assignment table is missing required columns: {sorted(required)}")
    return df


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


def _resolve_pixel_size(config: NativePipelineConfig, region_inputs: Dict[str, Any] | None = None) -> tuple[float, float]:
    if valid_pixel_size(config.pixel_size_um):
        return (float(config.pixel_size_um[0]), float(config.pixel_size_um[1]))
    raw = (region_inputs or {}).get("pixel_size_um") or []
    if len(raw) >= 2 and float(raw[0]) > 0 and float(raw[1]) > 0:
        config.pixel_size_um = (float(raw[0]), float(raw[1]))
        return config.pixel_size_um
    raise RuntimeError("Valid pixel size is required before running Cell distribution analysis.")


def _data_result(config: NativePipelineConfig) -> Dict[str, Any]:
    df_pixels, shapes = files_to_long_df(
        folder=config.folder,
        channels_cfg=[channel.to_dict() for channel in config.channels],
        image_id=config.image_id,
        pixel_size_um=config.pixel_size_um,
    )
    return {"df_pixels": df_pixels, "shapes": shapes}


def _load_boundary_mask(mask_path: str | Path | None, arrays_npz_path: Path | None = None) -> np.ndarray:
    if mask_path:
        path = Path(mask_path).expanduser()
        if path.exists():
            return np.asarray(load_any_tiff(path), dtype=bool)
    if arrays_npz_path is not None and arrays_npz_path.exists():
        with np.load(arrays_npz_path) as arrays:
            if "boundary_mask" in arrays:
                return np.asarray(arrays["boundary_mask"], dtype=bool)
    raise RuntimeError("A boundary mask path or existing arrays NPZ is required.")


def _discover_region_inputs(output_folder: Path) -> List[Dict[str, Any]]:
    region_dir = output_folder / "10_cell_distribution_analysis" / "01_region_masks"
    items: List[Dict[str, Any]] = []
    input_paths = sorted(
        region_dir.glob("region_bands__*__inputs.json"),
        key=lambda path: (path.stat().st_mtime if path.exists() else 0.0, path.name),
        reverse=True,
    )
    for inputs_path in input_paths:
        try:
            payload = json.loads(inputs_path.read_text())
        except Exception:
            continue
        boundary_label = str(payload.get("boundary_label") or "")
        band_width = float(payload.get("band_width_um") or 10.0)
        base_name = f"region_bands__{safe_name(boundary_label, 'boundary')}__{_band_token(band_width)}um"
        payload["_inputs_path"] = str(inputs_path)
        payload["_arrays_npz"] = str(region_dir / f"{base_name}__arrays.npz")
        items.append(payload)
    return items


def _registry_boundary_paths(output_folder: Path) -> Dict[str, Path]:
    registry_path = output_folder / "07_region_analysis" / "boundary_mask_registry.json"
    if not registry_path.exists():
        return {}
    payload = json.loads(registry_path.read_text())
    region_dir = output_folder / "07_region_analysis"
    paths: Dict[str, Path] = {}
    for entry in payload.get("entries", []):
        raw_path = Path(str(entry.get("mask_path", "")))
        mask_path = raw_path if raw_path.is_absolute() else region_dir / raw_path
        for key in ("display_name", "mask_key", "group_name"):
            value = str(entry.get(key, "")).strip()
            if value:
                paths[value] = mask_path
    return paths


def run_region_mask_band_analysis(
    *,
    config: NativePipelineConfig,
    data_result: Dict[str, Any],
    boundary_label: str,
    boundary_mask_path: Path | str | None,
    arrays_npz_path: Path | None,
    band_width_um: float,
    overlay_channels: Sequence[str],
) -> Dict[str, Any]:
    pixel_size = _resolve_pixel_size(config)
    boundary_mask = _load_boundary_mask(boundary_mask_path, arrays_npz_path=arrays_npz_path)
    if boundary_mask.ndim != 2:
        raise RuntimeError("The selected boundary mask is not a 2D image.")
    if not np.any(boundary_mask):
        raise RuntimeError("The selected boundary mask is empty.")

    seed_mask = _boundary_seed_mask(boundary_mask)
    inside_distance_um, outside_distance_um, inside_band_index, outside_band_index = _distance_bands_from_boundary(
        boundary_mask=boundary_mask,
        seed_mask=seed_mask,
        band_width_um=float(band_width_um),
        pixel_size_um=pixel_size,
    )

    pixel_area_um2 = float(pixel_size[0]) * float(pixel_size[1])
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
    base_name = f"region_bands__{safe_name(boundary_label, 'boundary')}__{_band_token(float(band_width_um))}um"
    region_masks_dir = output_dirs["region_masks"]
    for old_overlay in region_masks_dir.glob(f"{base_name}__overlay__*"):
        old_overlay.unlink(missing_ok=True)
    summary_csv = region_masks_dir / f"{base_name}__summary.csv"
    summary_json = region_masks_dir / f"{base_name}__summary.json"
    arrays_npz = region_masks_dir / f"{base_name}__arrays.npz"
    inputs_json = region_masks_dir / f"{base_name}__inputs.json"
    base_png_path, base_svg_path = _save_figure_both_formats(
        base_fig,
        region_masks_dir / f"{base_name}__band_map",
        dpi=300,
        bbox_inches="tight",
        pad_inches=0,
        facecolor="white",
    )
    plt.close(base_fig)

    band_summary_df.to_csv(summary_csv, index=False)
    resolved_mask_path = ""
    if boundary_mask_path:
        candidate = Path(boundary_mask_path).expanduser()
        if candidate.exists():
            resolved_mask_path = str(candidate.resolve())
    write_json(
        inputs_json,
        {
            "image_id": str(config.image_id),
            "boundary_label": str(boundary_label),
            "boundary_mask_path": resolved_mask_path,
            "pixel_size_um": [float(pixel_size[0]), float(pixel_size[1])],
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
        _save_figure_both_formats(
            overlay_fig,
            region_masks_dir / f"{base_name}__overlay__{safe_name(channel_name, 'channel')}",
            dpi=300,
            bbox_inches="tight",
            pad_inches=0,
            facecolor="white",
        )
        plt.close(overlay_fig)

    return {
        "boundary_label": str(boundary_label),
        "band_width_um": float(band_width_um),
        "saved_paths": {
            "png": str(base_png_path),
            "svg": str(base_svg_path),
            "summary_csv": str(summary_csv),
            "summary_json": str(summary_json),
            "arrays_npz": str(arrays_npz),
            "inputs_json": str(inputs_json),
        },
    }


def run_cell_density_analysis(
    *,
    config: NativePipelineConfig,
    assignment_df: pd.DataFrame,
    celltype_cfg: Sequence[Dict[str, Any]],
    region_masks_result: Dict[str, Any],
    selected_celltypes: Sequence[str],
) -> Dict[str, Any]:
    saved_paths = region_masks_result.get("saved_paths", {}) if isinstance(region_masks_result, dict) else {}
    arrays_npz_path = saved_paths.get("arrays_npz")
    inputs_json_path = saved_paths.get("inputs_json")
    if not arrays_npz_path or not Path(arrays_npz_path).exists():
        raise RuntimeError("Region mask arrays were not found. Generate Region masks first.")

    inputs_payload: Dict[str, Any] = {}
    if inputs_json_path and Path(inputs_json_path).exists():
        inputs_payload = json.loads(Path(inputs_json_path).read_text())
    pixel_size = _resolve_pixel_size(config, inputs_payload)

    band_arrays = np.load(arrays_npz_path)
    inside_band_index = np.asarray(band_arrays["inside_band_index"]).astype(np.int32)
    outside_band_index = np.asarray(band_arrays["outside_band_index"]).astype(np.int32)
    inside_mask = inside_band_index >= 0
    outside_mask = outside_band_index >= 0
    boundary_label = str(region_masks_result.get("boundary_label") or inputs_payload.get("boundary_label") or "Selected boundary")
    band_width_um = float(region_masks_result.get("band_width_um", inputs_payload.get("band_width_um", 10.0)) or 10.0)

    selected_celltypes = [str(name) for name in selected_celltypes if str(name).strip()]
    if not selected_celltypes:
        selected_celltypes = sorted(
            {
                str(value)
                for value in assignment_df["celltype"].astype(str).tolist()
                if str(value) not in {"Unassigned", "Ambiguous", ""}
            }
        )
    if not selected_celltypes:
        raise RuntimeError("Select at least one cell type.")

    pixel_area_um2 = float(pixel_size[0]) * float(pixel_size[1])
    df_cells = assignment_df.copy()
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
            "side": "inside",
        },
        {
            "key": "outside",
            "name": f"Outside {boundary_label}",
            "band_index": outside_band_index,
            "mask": outside_mask,
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
    celltype_colors = _celltype_color_map(celltype_cfg)
    fig, ax = plt.subplots(figsize=(9.2, 4.3), facecolor="white")
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
        Patch(
            facecolor=plt.get_cmap(CELL_DISTRIBUTION_INSIDE_CMAP)(0.75),
            edgecolor="none",
            alpha=CELL_DENSITY_PLOT_BACKGROUND_ALPHA,
            label=f"Inside {boundary_label} bands",
        ),
        Patch(
            facecolor=plt.get_cmap(CELL_DISTRIBUTION_OUTSIDE_CMAP)(0.75),
            edgecolor="none",
            alpha=CELL_DENSITY_PLOT_BACKGROUND_ALPHA,
            label=f"Outside {boundary_label} bands",
        ),
    ]
    line_handles, line_labels = ax.get_legend_handles_labels()
    ax.legend(background_handles + line_handles, [h.get_label() for h in background_handles] + line_labels, loc="upper left", frameon=True, fontsize=9)
    ax.set_xlabel("Distance across inside -> outside (um)", fontsize=12)
    ax.set_ylabel("Cell density (cells / mm²)", fontsize=12)
    ax.set_title(f"{config.image_id} - Cell density by band", fontsize=13)
    ax.tick_params(axis="x", labelrotation=45, labelsize=11)
    ax.tick_params(axis="y", labelsize=11)
    ax.set_ylim(0.0, ymax)
    ax.grid(axis="y", alpha=0.20, zorder=1)

    output_dirs = _cell_distribution_output_dirs(config)
    density_dir = output_dirs["cell_density"]
    # This directory stores the latest density run. Keep the boundary and band
    # width because they are meaningful to a reader, but do not expose an
    # implementation hash in every exported filename.
    base_name = (
        f"cell_density_by_boundary_distance__{safe_name(boundary_label, 'boundary')}"
        f"__{_band_token(float(band_width_um))}um"
    )

    csv_band_wide = density_dir / f"{base_name}__wide.csv"
    csv_band_long = density_dir / f"{base_name}__long.csv"
    csv_region = density_dir / f"{base_name}__region.csv"
    csv_inputs = density_dir / f"{base_name}__inputs.csv"
    plot_png_path, plot_svg_path = _save_figure_both_formats(
        fig,
        density_dir / f"{base_name}__plot",
        dpi=300,
        bbox_inches="tight",
        pad_inches=0.05,
        facecolor="white",
    )
    plt.close(fig)

    band_metrics_df.sort_values(["plot_x_center_um"]).to_csv(csv_band_wide, index=False)
    band_metrics_long_df.sort_values(["plot_x_center_um", "celltype"]).to_csv(csv_band_long, index=False)
    region_metrics_df.to_csv(csv_region, index=False)
    inputs_df = pd.DataFrame(
        [
            {
                "image_id": str(config.image_id),
                "boundary_label": str(boundary_label),
                "band_width_um": float(band_width_um),
                "pixel_size_x_um": float(pixel_size[0]),
                "pixel_size_y_um": float(pixel_size[1]),
                "inside_label": f"Inside {boundary_label}",
                "outside_label": f"Outside {boundary_label}",
                "region_mask_arrays_npz": str(Path(arrays_npz_path).resolve()),
                "selected_celltypes": ", ".join(selected_celltypes),
            }
        ]
    )
    inputs_df.to_csv(csv_inputs, index=False)

    return {
        "boundary_label": str(boundary_label),
        "band_width_um": float(band_width_um),
        "selected_celltypes": list(selected_celltypes),
        "saved_paths": {
            "png": str(plot_png_path),
            "svg": str(plot_svg_path),
            "csv_band_wide": str(csv_band_wide),
            "csv_band_long": str(csv_band_long),
            "csv_region": str(csv_region),
            "csv_inputs": str(csv_inputs),
        },
    }


def _boundary_candidates(output_folder: Path) -> List[tuple[str, Path]]:
    registry = _registry_boundary_paths(output_folder)
    candidates: List[tuple[str, Path]] = []
    seen_paths: set[str] = set()
    for label, path in registry.items():
        resolved = Path(path).expanduser()
        if not resolved.exists():
            continue
        key = str(resolved.resolve())
        candidates.append((str(label), resolved))
        seen_paths.add(key)

    region_dir = output_folder / "07_region_analysis"
    for path in sorted(region_dir.glob("*region_mask_uint8.tiff")):
        key = str(path.resolve())
        if key in seen_paths:
            continue
        label = path.name.split("__", 1)[0] or path.stem
        candidates.append((label, path))
        seen_paths.add(key)
    return candidates


def _load_neighborhood_result(output_folder: Path) -> Dict[str, Any]:
    neighborhood_dir = output_folder / "06_neighborhood_analysis"
    cluster_summary_path = neighborhood_dir / "neighborhood_cluster_summary.csv"
    tile_assignments_path = neighborhood_dir / "neighborhood_tile_assignments.csv"
    cluster_mask_path = neighborhood_dir / "neighborhood_cluster_mask_uint16.tiff"
    if not cluster_summary_path.exists():
        raise RuntimeError(f"Missing neighborhood cluster summary: {cluster_summary_path}")
    if not tile_assignments_path.exists():
        raise RuntimeError(f"Missing neighborhood tile assignments: {tile_assignments_path}")
    if not cluster_mask_path.exists():
        raise RuntimeError(f"Missing neighborhood cluster mask: {cluster_mask_path}")

    grid_size_um = 20.0
    for params_path in (neighborhood_dir / "neighborhood_params.json", neighborhood_dir / "neighborhood_parameters.json"):
        if not params_path.exists():
            continue
        try:
            params = json.loads(params_path.read_text())
        except Exception:
            continue
        grid_size_um = float(params.get("grid_size_um") or params.get("gridSizeUm") or grid_size_um)
        break

    return {
        "cluster_summary": pd.read_csv(cluster_summary_path),
        "tile_assignments": pd.read_csv(tile_assignments_path),
        "cluster_mask": load_any_tiff(cluster_mask_path),
        "grid_size_um": grid_size_um,
    }


def run_cell_cluster_distribution_analysis(
    *,
    config: NativePipelineConfig,
    neighborhood_result: Dict[str, Any],
    boundary_candidates: Sequence[tuple[str, Path]],
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
        selected_boundary_labels = [label for label, _ in list(boundary_candidates)[:3]]
    if not selected_boundary_labels:
        raise RuntimeError("Select at least one Region analysis boundary or ROI.")

    cluster_summary = cluster_summary_raw.copy()
    if "cluster_label" not in cluster_summary.columns:
        raise RuntimeError("Neighborhood cluster summary is missing the cluster_label column.")
    if "cluster_id" not in cluster_summary.columns:
        cluster_summary["cluster_id"] = np.arange(1, len(cluster_summary) + 1, dtype=int)
    if "cluster_key" not in cluster_summary.columns:
        cluster_summary["cluster_key"] = cluster_summary["cluster_label"].astype(str)

    available_cluster_labels = cluster_summary["cluster_label"].astype(str).tolist()
    selected_cluster_labels = [str(label) for label in selected_cluster_labels if str(label).strip()]
    if not selected_cluster_labels:
        selected_cluster_labels = available_cluster_labels
    selected_cluster_labels = [label for label in selected_cluster_labels if label in set(available_cluster_labels)]
    if not selected_cluster_labels:
        raise RuntimeError("None of the selected neighborhood clusters are available in the current neighborhood-analysis result.")

    tile_assignments = tile_assignments_raw.copy()
    cluster_mask = np.asarray(cluster_mask_raw).astype(np.uint16)
    height, width = cluster_mask.shape

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
    fig_width = max(9.0, 3.5 + 1.15 * n_regions)
    fig_height = max(4.8, 1.4 + 0.38 * n_clusters)
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
        ax.set_xticklabels(matrix_df.columns.tolist(), rotation=45, ha="right", fontsize=10)
        ax.set_yticks(np.arange(len(matrix_df.index)))
        ax.set_yticklabels(matrix_df.index.tolist(), fontsize=9)
        ax.set_xlabel("Region from Region analysis", fontsize=11)
        if ax is axes[0]:
            ax.set_ylabel("Neighborhood cluster", fontsize=11)
        if annotate_values:
            max_value = np.nanmax(matrix_values) if matrix_values.size else 0
            for row_idx in range(matrix_values.shape[0]):
                for col_idx in range(matrix_values.shape[1]):
                    ax.text(
                        col_idx,
                        row_idx,
                        f"{int(matrix_values[row_idx, col_idx])}",
                        ha="center",
                        va="center",
                        fontsize=9,
                        color="white" if max_value > 0 and matrix_values[row_idx, col_idx] > 0.55 * max_value else "black",
                    )
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    fig.suptitle(f"{config.image_id} - Neighborhood clusters by Region-analysis mask", fontsize=15)

    output_dirs = _cell_distribution_output_dirs(config)
    cluster_dir = output_dirs["cell_cluster_distribution"]
    for old_output in cluster_dir.glob("cell_cluster_distribution__*"):
        old_output.unlink(missing_ok=True)
    base_name = "neighborhood_clusters_by_region"

    csv_cluster_region = cluster_dir / f"{base_name}__cluster_region.csv"
    csv_region = cluster_dir / f"{base_name}__region.csv"
    csv_tiles = cluster_dir / f"{base_name}__tiles.csv"
    csv_tile_matrix = cluster_dir / f"{base_name}__tile_matrix.csv"
    csv_cell_matrix = cluster_dir / f"{base_name}__cell_matrix.csv"
    csv_inputs = cluster_dir / f"{base_name}__inputs.csv"
    plot_png_path, plot_svg_path = _save_figure_both_formats(
        fig,
        cluster_dir / f"{base_name}__heatmap",
        dpi=300,
        bbox_inches="tight",
        pad_inches=0.05,
        facecolor="white",
    )
    plt.close(fig)

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

    return {
        "selected_boundaries": list(selected_boundary_labels),
        "selected_clusters": list(selected_cluster_labels),
        "saved_paths": {
            "png": str(plot_png_path),
            "svg": str(plot_svg_path),
            "csv_cluster_region": str(csv_cluster_region),
            "csv_region": str(csv_region),
            "csv_tiles": str(csv_tiles),
            "csv_tile_matrix": str(csv_tile_matrix),
            "csv_cell_matrix": str(csv_cell_matrix),
            "csv_inputs": str(csv_inputs),
        },
    }


def _prepare_region_requests(args: argparse.Namespace, output_folder: Path) -> List[Dict[str, Any]]:
    registry_paths = _registry_boundary_paths(output_folder)
    labels = list(args.boundary_label or [])
    mask_paths = list(args.boundary_mask_path or [])
    requests: List[Dict[str, Any]] = []
    for idx, label in enumerate(labels):
        explicit_path = mask_paths[idx] if idx < len(mask_paths) else None
        requests.append(
            {
                "boundary_label": str(label),
                "boundary_mask_path": explicit_path or registry_paths.get(str(label)),
                "band_width_um": float(args.band_width_um),
                "_arrays_npz": None,
            }
        )
    if requests:
        return requests

    discovered = _discover_region_inputs(output_folder)
    for item in discovered:
        label = str(item.get("boundary_label") or "")
        mask_path = item.get("boundary_mask_path") or registry_paths.get(label)
        requests.append(
            {
                "boundary_label": label,
                "boundary_mask_path": mask_path,
                "band_width_um": float(item.get("band_width_um") or args.band_width_um),
                "_arrays_npz": item.get("_arrays_npz"),
                "_pixel_size_um": item.get("pixel_size_um"),
                "_overlay_channels": [],
            }
        )
    return requests


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export SpatialScope Distribution outputs using the original Streamlit plotting contract.")
    parser.add_argument("--output-folder", required=True)
    parser.add_argument(
        "--mode",
        choices=["region-masks", "cell-density", "region-masks-and-density", "cell-cluster-distribution"],
        required=True,
    )
    parser.add_argument("--boundary-label", action="append", default=[])
    parser.add_argument("--boundary-mask-path", action="append", default=[])
    parser.add_argument("--band-width-um", type=float, default=10.0)
    parser.add_argument("--selected-celltype", action="append", default=[])
    parser.add_argument("--selected-cluster", action="append", default=[])
    parser.add_argument("--manifest", default="")
    args = parser.parse_args(argv)

    output_folder = Path(args.output_folder).expanduser().resolve()
    config = _read_config(output_folder)
    if config.save_dir.resolve() != output_folder:
        config.save_dir = output_folder

    region_requests = _prepare_region_requests(args, output_folder)
    region_results: List[Dict[str, Any]] = []
    if args.mode in {"region-masks", "region-masks-and-density"}:
        if not region_requests:
            raise RuntimeError("No region mask requests were found.")
        data_result = _data_result(config)
        for request in region_requests:
            if not valid_pixel_size(config.pixel_size_um):
                raw_pixel_size = request.get("_pixel_size_um") or []
                if len(raw_pixel_size) >= 2:
                    config.pixel_size_um = (float(raw_pixel_size[0]), float(raw_pixel_size[1]))
            result = run_region_mask_band_analysis(
                config=config,
                data_result=data_result,
                boundary_label=str(request["boundary_label"]),
                boundary_mask_path=request.get("boundary_mask_path"),
                arrays_npz_path=Path(request["_arrays_npz"]) if request.get("_arrays_npz") else None,
                band_width_um=float(request["band_width_um"]),
                overlay_channels=[str(value) for value in (request.get("_overlay_channels") or [])],
            )
            region_results.append(result)

    if args.mode in {"cell-density", "region-masks-and-density"}:
        if not region_results:
            discovered = _discover_region_inputs(output_folder)
            if not discovered:
                raise RuntimeError("Region mask arrays were not found. Generate Region masks first.")
            first = discovered[0]
            region_results = [
                {
                    "boundary_label": str(first.get("boundary_label") or "Selected boundary"),
                    "band_width_um": float(first.get("band_width_um") or args.band_width_um),
                    "saved_paths": {
                        "arrays_npz": str(first.get("_arrays_npz")),
                        "inputs_json": str(first.get("_inputs_path")),
                    },
                }
            ]
        assignment_df = _load_assignment_cells(output_folder)
        celltype_cfg = _load_celltype_config(output_folder)
        run_cell_density_analysis(
            config=config,
            assignment_df=assignment_df,
            celltype_cfg=celltype_cfg,
            region_masks_result=region_results[0],
            selected_celltypes=args.selected_celltype,
        )

    if args.mode == "cell-cluster-distribution":
        boundary_candidates = _boundary_candidates(output_folder)
        selected_boundaries = [str(label) for label in args.boundary_label if str(label).strip()]
        selected_clusters = [str(label) for label in args.selected_cluster if str(label).strip()]
        run_cell_cluster_distribution_analysis(
            config=config,
            neighborhood_result=_load_neighborhood_result(output_folder),
            boundary_candidates=boundary_candidates,
            selected_boundary_labels=selected_boundaries,
            selected_cluster_labels=selected_clusters,
        )

    if args.manifest:
        write_json(
            Path(args.manifest).expanduser(),
            {
                "mode": args.mode,
                "output_folder": str(output_folder),
                "region_results": region_results,
                "selected_celltypes": list(args.selected_celltype or []),
                "selected_clusters": list(args.selected_cluster or []),
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
