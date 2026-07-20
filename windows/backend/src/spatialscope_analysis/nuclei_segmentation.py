from __future__ import annotations

import json
import math
import os
import random
from contextlib import nullcontext
from functools import lru_cache
from itertools import product
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from joblib import Parallel, delayed
except Exception:  # pragma: no cover - optional runtime dependency guard
    Parallel = None
    delayed = None

try:
    from threadpoolctl import threadpool_limits
except Exception:  # pragma: no cover - optional runtime dependency guard
    threadpool_limits = None

from matplotlib.colors import ListedColormap
from scipy import ndimage as ndi
try:
    from scipy.stats import qmc
except Exception:  # pragma: no cover - optional runtime dependency guard
    qmc = None
from skimage import feature, filters, measure, morphology, segmentation
from skimage.measure import find_contours

from .compute_runtime import get_compute_runtime
from .io import save_uint16_tiff, to_image, write_json
from .models import NucleiParams
from .visualization import norm_clip

SWEEP_PARAM_ORDER: List[str] = [
    "min_diam_um",
    "max_diam_um",
    "tophat_radius_um",
    "gauss_sigma_um",
    "local_win_um",
    "local_offset",
    "h_maxima_um",
    "seed_min_dist_um",
    "watershed_compactness",
    "post_resplit_mult",
]

SWEEP_PARAM_LABELS: Dict[str, str] = {
    "min_diam_um": "MIN_DIAM_UM",
    "max_diam_um": "MAX_DIAM_UM",
    "tophat_radius_um": "TOPHAT_RADIUS_UM",
    "gauss_sigma_um": "GAUSS_SIGMA_UM",
    "local_win_um": "LOCAL_WIN_UM",
    "local_offset": "LOCAL_OFFSET",
    "h_maxima_um": "H_MAXIMA_UM",
    "seed_min_dist_um": "SEED_MIN_DIST_UM",
    "watershed_compactness": "WATERSHED_COMPACTNESS",
    "post_resplit_mult": "POST_RESPLIT_MULT",
}


DEFAULT_CPU_COUNT = os.cpu_count() or 1


CANONICAL_NUCLEI_PARAM_FIELDS: List[str] = ["nucleus_channel", *SWEEP_PARAM_ORDER]


def _canonical_nuclei_param_key(key: Any) -> str | None:
    text = str(key).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in CANONICAL_NUCLEI_PARAM_FIELDS:
        return lowered
    if lowered == "nucleus_channel":
        return "nucleus_channel"
    for field, label in SWEEP_PARAM_LABELS.items():
        if lowered == label.lower():
            return field
    alias_map = {
        "nucleuschannel": "nucleus_channel",
        "nucleus-channel": "nucleus_channel",
    }
    return alias_map.get(lowered)


def _coerce_nuclei_params(params_like: Any, overrides: Dict[str, Any] | None = None) -> NucleiParams:
    raw: Dict[str, Any] = {}
    if isinstance(params_like, dict):
        raw.update(params_like)
    elif hasattr(params_like, "to_dict") and callable(getattr(params_like, "to_dict")):
        try:
            raw.update(params_like.to_dict())
        except Exception:
            pass
    for field in CANONICAL_NUCLEI_PARAM_FIELDS:
        if hasattr(params_like, field):
            raw[field] = getattr(params_like, field)

    if overrides:
        raw.update(overrides)

    clean: Dict[str, Any] = {}
    for key, value in raw.items():
        canonical = _canonical_nuclei_param_key(key)
        if canonical is None:
            continue
        clean[canonical] = value

    if "nucleus_channel" not in clean or clean["nucleus_channel"] in (None, ""):
        raise RuntimeError("Nucleus channel is missing from nuclei segmentation parameters.")

    for field in SWEEP_PARAM_ORDER:
        if field in clean:
            clean[field] = float(clean[field])

    return NucleiParams(**{field: clean[field] for field in CANONICAL_NUCLEI_PARAM_FIELDS if field in clean})


def _validate_nuclei_params(params: NucleiParams) -> None:
    if float(params.min_diam_um) > float(params.max_diam_um):
        raise ValueError(
            f"MIN_DIAM_UM ({float(params.min_diam_um):.3f}) cannot be larger than "
            f"MAX_DIAM_UM ({float(params.max_diam_um):.3f})."
        )


def _thread_limit_context(n_threads: int | None):
    if threadpool_limits is None or n_threads is None:
        return nullcontext()
    try:
        n_threads = int(n_threads)
    except Exception:
        return nullcontext()
    if n_threads < 1:
        return nullcontext()
    return threadpool_limits(limits=n_threads)


def _iter_combo_chunks(
    ordered_values: Dict[str, List[float]],
    chunk_size: int,
) -> Iterable[List[Tuple[int, Tuple[float, ...]]]]:
    chunk: List[Tuple[int, Tuple[float, ...]]] = []
    for combo_index, combo in enumerate(product(*[ordered_values[field] for field in SWEEP_PARAM_ORDER]), start=1):
        chunk.append((combo_index, tuple(float(v) for v in combo)))
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _iter_explicit_combo_chunks(
    combo_records: Sequence[Tuple[int, Tuple[float, ...]]],
    chunk_size: int,
) -> Iterable[List[Tuple[int, Tuple[float, ...]]]]:
    chunk: List[Tuple[int, Tuple[float, ...]]] = []
    for record in combo_records:
        chunk.append(record)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _decimal_places_from_step(step: float) -> int:
    text = f"{float(step):.10f}".rstrip("0").rstrip(".")
    if "." not in text:
        return 0
    return len(text.split(".")[-1])


def _snap_value_to_search_space(value: float, lower: float, upper: float, step: float) -> float:
    lower = float(lower)
    upper = float(upper)
    step = float(step)
    clipped = min(max(float(value), lower), upper)
    if step <= 0:
        return clipped
    index = round((clipped - lower) / step)
    snapped = lower + index * step
    snapped = min(max(snapped, lower), upper)
    decimals = _decimal_places_from_step(step)
    return float(round(snapped, decimals + 2))


def _grid_value_count(lower: float, upper: float, step: float) -> int:
    lower = float(lower)
    upper = float(upper)
    step = float(step)
    if step <= 0:
        return 1
    return int(round((upper - lower) / step)) + 1


def _search_space_n_combinations(search_space_specs: Dict[str, Dict[str, float]]) -> int:
    total = 1
    for field in SWEEP_PARAM_ORDER:
        total *= int(search_space_specs[field]["n_values"])
    return int(total)


def _build_search_space_specs(search_specs: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    built: Dict[str, Dict[str, float]] = {}
    for field in SWEEP_PARAM_ORDER:
        spec = search_specs[field]
        lower = float(spec["min"])
        upper = float(spec["max"])
        step = float(spec["step"])
        built[field] = {
            "min": lower,
            "max": upper,
            "step": step,
            "n_values": int(_grid_value_count(lower, upper, step)),
        }
    return built


def _intersect_search_space_specs(
    full_search_space_specs: Dict[str, Dict[str, float]],
    limited_search_specs: Dict[str, Dict[str, float]] | None,
) -> Dict[str, Dict[str, float]] | None:
    if not limited_search_specs:
        return None

    intersected: Dict[str, Dict[str, float]] = {}
    for field in SWEEP_PARAM_ORDER:
        full_spec = full_search_space_specs[field]
        limited_spec = limited_search_specs.get(field)
        if limited_spec is None:
            return None

        lower = max(float(full_spec["min"]), float(limited_spec["min"]))
        upper = min(float(full_spec["max"]), float(limited_spec["max"]))
        step = float(full_spec["step"])
        if lower > upper:
            return None

        lower = _snap_value_to_search_space(lower, float(full_spec["min"]), float(full_spec["max"]), step)
        upper = _snap_value_to_search_space(upper, float(full_spec["min"]), float(full_spec["max"]), step)
        if lower > upper:
            return None

        intersected[field] = {
            "min": float(lower),
            "max": float(upper),
            "step": float(step),
            "n_values": int(_grid_value_count(lower, upper, step)),
        }
    return intersected


def _generate_exhaustive_combo_values(search_space_specs: Dict[str, Dict[str, float]]) -> List[Tuple[float, ...]]:
    ordered_values = {
        field: [
            _snap_value_to_search_space(
                float(search_space_specs[field]["min"]) + idx * float(search_space_specs[field]["step"]),
                float(search_space_specs[field]["min"]),
                float(search_space_specs[field]["max"]),
                float(search_space_specs[field]["step"]),
            )
            for idx in range(int(search_space_specs[field]["n_values"]))
        ]
        for field in SWEEP_PARAM_ORDER
    }
    return [
        _repair_combo_constraints(combo)
        for combo in product(*[ordered_values[field] for field in SWEEP_PARAM_ORDER])
    ]


def _repair_combo_constraints(combo: Sequence[float]) -> Tuple[float, ...]:
    combo_list = [float(value) for value in combo]
    min_idx = SWEEP_PARAM_ORDER.index("min_diam_um")
    max_idx = SWEEP_PARAM_ORDER.index("max_diam_um")
    if combo_list[min_idx] > combo_list[max_idx]:
        combo_list[min_idx], combo_list[max_idx] = combo_list[max_idx], combo_list[min_idx]
    return tuple(combo_list)


def _combo_from_params(params: NucleiParams) -> Tuple[float, ...]:
    return tuple(float(getattr(params, field)) for field in SWEEP_PARAM_ORDER)


def _sample_global_search_candidates(
    search_space_specs: Dict[str, Dict[str, float]],
    n_candidates: int,
    rng: np.random.Generator,
) -> List[Tuple[float, ...]]:
    if n_candidates <= 0:
        return []
    dim = len(SWEEP_PARAM_ORDER)
    if qmc is not None:
        sampler = qmc.Sobol(d=dim, scramble=True, seed=int(rng.integers(0, 2**32 - 1)))
        power = max(0, int(math.ceil(math.log2(max(1, n_candidates)))))
        sample = sampler.random_base2(power)[:n_candidates]
    else:  # pragma: no cover - fallback when scipy.stats.qmc is unavailable
        sample = rng.random((n_candidates, dim))

    combos: List[Tuple[float, ...]] = []
    for row in sample:
        combo: List[float] = []
        for idx, field in enumerate(SWEEP_PARAM_ORDER):
            spec = search_space_specs[field]
            raw_value = float(spec["min"]) + float(row[idx]) * (float(spec["max"]) - float(spec["min"]))
            combo.append(
                _snap_value_to_search_space(
                    raw_value,
                    float(spec["min"]),
                    float(spec["max"]),
                    float(spec["step"]),
                )
            )
        combos.append(_repair_combo_constraints(combo))
    return combos


def _sample_local_search_candidates(
    elite_rows: pd.DataFrame,
    search_space_specs: Dict[str, Dict[str, float]],
    n_candidates: int,
    radius_fraction: float,
    rng: np.random.Generator,
) -> List[Tuple[float, ...]]:
    if n_candidates <= 0 or len(elite_rows) == 0:
        return []

    combos: List[Tuple[float, ...]] = []
    elite_indices = list(range(len(elite_rows)))
    for _ in range(n_candidates):
        elite_row = elite_rows.iloc[int(rng.choice(elite_indices))]
        combo: List[float] = []
        for field in SWEEP_PARAM_ORDER:
            spec = search_space_specs[field]
            center = float(elite_row[SWEEP_PARAM_LABELS[field]])
            max_index = max(0, int(spec["n_values"]) - 1)
            center_index = int(round((center - float(spec["min"])) / float(spec["step"])))
            center_index = min(max(center_index, 0), max_index)
            radius_steps = max(1, int(math.ceil(max_index * float(radius_fraction))))
            delta = int(rng.integers(-radius_steps, radius_steps + 1))
            candidate_index = min(max(center_index + delta, 0), max_index)
            combo.append(
                _snap_value_to_search_space(
                    float(spec["min"]) + candidate_index * float(spec["step"]),
                    float(spec["min"]),
                    float(spec["max"]),
                    float(spec["step"]),
                )
            )
        combos.append(_repair_combo_constraints(combo))
    return combos


def _evaluate_explicit_combo_records(
    combo_values: Sequence[Tuple[float, ...]],
    combo_index_start: int,
    base_params: NucleiParams,
    dapi: np.ndarray,
    dapi_norm: np.ndarray,
    pixel_size_um: Tuple[float, float],
    parallel_workers: int = 1,
    parallel_backend: str = "loky",
    native_threads_per_worker: int | None = 1,
    compute_telemetry_state: Dict[str, bool] | None = None,
) -> List[Dict[str, Any]]:
    if not combo_values:
        return []

    combo_records = [
        (int(combo_index_start + idx), tuple(float(value) for value in combo))
        for idx, combo in enumerate(combo_values)
    ]

    safe_backend = str(parallel_backend or "loky").strip().lower()
    if safe_backend not in {"loky", "threading"}:
        safe_backend = "loky"

    try:
        parallel_workers = int(parallel_workers)
    except Exception:
        parallel_workers = 1
    parallel_workers = max(1, min(parallel_workers, len(combo_records)))

    if native_threads_per_worker is not None:
        try:
            native_threads_per_worker = max(1, int(native_threads_per_worker))
        except Exception:
            native_threads_per_worker = 1

    # The two image-preparation radii are aggressively quantized to integer
    # pixels by the macOS-compatible algorithm.  Grouping by top-hat radius and
    # then Gaussian radius lets every identical intermediate be computed once,
    # while the component metrics remain independent for every candidate.
    grouped_records = _group_optimizer_combo_records(
        combo_records,
        pixel_size_um=pixel_size_um,
    )
    telemetry_combo_index: int | None = None
    if compute_telemetry_state is not None and not bool(compute_telemetry_state.get("recorded", False)):
        telemetry_combo_index = min(record[0] for record in combo_records)
        # Search phases call this function sequentially.  Reserve the single
        # request-level compute operation before worker threads are started.
        compute_telemetry_state["recorded"] = True

    group_workers = max(1, min(parallel_workers, len(grouped_records)))
    if group_workers > 1 and Parallel is not None and delayed is not None:
        group_results = Parallel(
            n_jobs=group_workers,
            backend=safe_backend,
            max_nbytes="16M",
            mmap_mode="r",
            batch_size=1,
            verbose=0,
        )(
            delayed(_evaluate_optimizer_tophat_group)(
                tophat_radius_px,
                records,
                base_params,
                dapi_norm,
                pixel_size_um,
                native_threads=native_threads_per_worker,
                telemetry_combo_index=telemetry_combo_index,
            )
            for tophat_radius_px, records in grouped_records
        )
        rows = [row for group in group_results for row in group]
    else:
        rows = []
        for tophat_radius_px, records in grouped_records:
            rows.extend(
                _evaluate_optimizer_tophat_group(
                    tophat_radius_px,
                    records,
                    base_params,
                    dapi_norm,
                    pixel_size_um,
                    native_threads=native_threads_per_worker,
                    telemetry_combo_index=telemetry_combo_index,
                )
            )

    # Parallel groups finish out of combo order; the pre-optimization API
    # returned candidate order and downstream adaptive search relies on it.
    return sorted(rows, key=lambda row: int(row["combo_index"]))

def _combo_from_linear_index(
    linear_index: int,
    search_space_specs: Dict[str, Dict[str, float]],
) -> Tuple[float, ...]:
    remaining = int(linear_index)
    reversed_values: List[float] = []
    for field in reversed(SWEEP_PARAM_ORDER):
        spec = search_space_specs[field]
        n_values = int(spec["n_values"])
        value_index = remaining % n_values
        remaining //= n_values
        reversed_values.append(
            _snap_value_to_search_space(
                float(spec["min"]) + value_index * float(spec["step"]),
                float(spec["min"]),
                float(spec["max"]),
                float(spec["step"]),
            )
        )
    return _repair_combo_constraints(list(reversed(reversed_values)))


def _draw_random_unique_combos(
    *,
    total_combinations: int,
    n_draws: int,
    search_space_specs: Dict[str, Dict[str, float]],
    rng: random.Random,
    seen_combos: set[Tuple[float, ...]],
) -> List[Tuple[float, ...]]:
    if n_draws <= 0 or total_combinations <= 0:
        return []

    out: List[Tuple[float, ...]] = []
    attempts = 0
    max_attempts = max(1000, n_draws * 50)
    while len(out) < n_draws and attempts < max_attempts:
        combo = _combo_from_linear_index(rng.randrange(total_combinations), search_space_specs)
        attempts += 1
        if combo in seen_combos:
            continue
        seen_combos.add(combo)
        out.append(combo)
    return out


def _pick_better_nuclei_row(current_best: pd.Series | None, candidate: pd.Series | None) -> pd.Series | None:
    if candidate is None:
        return current_best
    if current_best is None:
        return candidate.copy()
    ranked = rank_nuclei_parameter_sweep_results(
        pd.DataFrame([current_best.to_dict(), candidate.to_dict()])
    )
    if len(ranked) == 0:
        return current_best
    return ranked.iloc[0].copy()


def make_nuclei_batch_optimizer_figure(df_batches: pd.DataFrame) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(8.6, 4.2), constrained_layout=True)
    if len(df_batches) > 0:
        ax.plot(
            df_batches["batch_index"],
            df_batches["global_best_n_nuclei"],
            marker="o",
            linewidth=1.4,
            label="Global best after each batch",
        )
        ax.plot(
            df_batches["batch_index"],
            df_batches["batch_best_n_nuclei"],
            marker=".",
            linewidth=1.0,
            alpha=0.5,
            label="Best inside batch",
        )
        best_idx = df_batches["global_best_n_nuclei"].astype(float).idxmax()
        best_row = df_batches.loc[best_idx]
        ax.annotate(
            f"best batch #{int(best_row['batch_index'])}: {int(best_row['global_best_n_nuclei'])} nuclei",
            xy=(best_row["batch_index"], best_row["global_best_n_nuclei"]),
            xytext=(10, 10),
            textcoords="offset points",
        )
        ax.legend()
    ax.tick_params(axis="both", labelsize=11)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", rotation_mode="anchor")
    ax.set_xlabel("Batch index")
    ax.set_ylabel("Segmented nuclei count")
    ax.set_title("Batched random nuclei optimizer")
    ax.grid(alpha=0.3)
    return fig


def _snap_combo_to_search_space(
    combo: Sequence[float],
    search_space_specs: Dict[str, Dict[str, float]],
) -> Tuple[float, ...]:
    snapped: List[float] = []
    for field, value in zip(SWEEP_PARAM_ORDER, combo):
        spec = search_space_specs[field]
        snapped.append(
            _snap_value_to_search_space(
                float(value),
                float(spec["min"]),
                float(spec["max"]),
                float(spec["step"]),
            )
        )
    return _repair_combo_constraints(snapped)


def _combo_key_from_result_row(row: pd.Series) -> Tuple[float, ...]:
    return tuple(float(row[SWEEP_PARAM_LABELS[field]]) for field in SWEEP_PARAM_ORDER)


def _deduplicate_nuclei_result_rows(df_results: pd.DataFrame) -> pd.DataFrame:
    if len(df_results) == 0:
        return df_results.copy()

    working = df_results.copy()
    working["_combo_key"] = [
        tuple(float(row[SWEEP_PARAM_LABELS[field]]) for field in SWEEP_PARAM_ORDER)
        for _, row in working.iterrows()
    ]
    if "error" in working.columns:
        working["_error_rank"] = (working["error"].fillna("") != "").astype(int)
        working = working.sort_values(["_error_rank", "combo_index"], ascending=[True, True])
        working = working.drop(columns=["_error_rank"])
    else:
        working = working.sort_values("combo_index")
    working = working.drop_duplicates("_combo_key", keep="first")
    working = working.drop(columns=["_combo_key"])
    return working.reset_index(drop=True)


def _run_budgeted_search_records(
    *,
    search_space_specs: Dict[str, Dict[str, float]],
    max_evaluations: int,
    combo_index_start: int,
    base_params: NucleiParams,
    dapi: np.ndarray,
    dapi_norm: np.ndarray,
    pixel_size_um: Tuple[float, float],
    parallel_workers: int = 1,
    parallel_backend: str = "loky",
    native_threads_per_worker: int | None = 1,
    random_seed: int = 42,
    seed_rows: pd.DataFrame | None = None,
    compute_telemetry_state: Dict[str, bool] | None = None,
) -> Dict[str, Any]:
    try:
        max_evaluations = max(0, int(max_evaluations))
    except Exception:
        max_evaluations = 0
    if max_evaluations <= 0:
        empty = pd.DataFrame()
        return {"results": empty, "ranked_results": empty, "n_evaluated": 0}

    rng = np.random.default_rng(int(random_seed))
    total_space_n_combinations = _search_space_n_combinations(search_space_specs)
    seen_combos: set[Tuple[float, ...]] = set()
    pending_seed_combos: List[Tuple[float, ...]] = []
    evaluated_rows: List[Dict[str, Any]] = []
    stage_index = 0
    search_batch_size = max(16, min(128, int(parallel_workers) * 8))
    radius_schedule = [0.25, 0.12, 0.06, 0.03]

    def queue_combo(combo: Sequence[float]) -> None:
        snapped = _snap_combo_to_search_space(combo, search_space_specs)
        if snapped in seen_combos:
            return
        seen_combos.add(snapped)
        pending_seed_combos.append(snapped)

    queue_combo(_combo_from_params(base_params))
    if seed_rows is not None and len(seed_rows) > 0:
        ranked_seed_rows = rank_nuclei_parameter_sweep_results(seed_rows)
        for _, row in ranked_seed_rows.head(min(8, len(ranked_seed_rows))).iterrows():
            queue_combo(_combo_key_from_result_row(row))
            if len(pending_seed_combos) >= max_evaluations:
                break

    while len(evaluated_rows) < max_evaluations:
        remaining = max_evaluations - len(evaluated_rows)
        current_batch_size = min(search_batch_size, remaining)
        batch_combos: List[Tuple[float, ...]] = []

        while pending_seed_combos and len(batch_combos) < current_batch_size:
            batch_combos.append(pending_seed_combos.pop(0))

        if len(batch_combos) < current_batch_size:
            oversample_n = max(current_batch_size * 6, 64)
            candidate_combos: List[Tuple[float, ...]] = []
            if evaluated_rows:
                ranked_so_far = rank_nuclei_parameter_sweep_results(pd.DataFrame(evaluated_rows))
                elite_rows = ranked_so_far.head(min(8, len(ranked_so_far)))
                radius_fraction = radius_schedule[min(stage_index, len(radius_schedule) - 1)]
                local_n = max(1, oversample_n // 2)
                global_n = max(1, oversample_n - local_n)
                candidate_combos.extend(
                    _sample_local_search_candidates(
                        elite_rows,
                        search_space_specs,
                        local_n,
                        radius_fraction,
                        rng,
                    )
                )
                candidate_combos.extend(
                    _sample_global_search_candidates(
                        search_space_specs,
                        global_n,
                        rng,
                    )
                )
            else:
                candidate_combos.extend(
                    _sample_global_search_candidates(
                        search_space_specs,
                        oversample_n,
                        rng,
                    )
                )

            for combo in candidate_combos:
                snapped = _snap_combo_to_search_space(combo, search_space_specs)
                if snapped in seen_combos:
                    continue
                seen_combos.add(snapped)
                batch_combos.append(snapped)
                if len(batch_combos) >= current_batch_size:
                    break

        if not batch_combos and total_space_n_combinations <= max(4096, max_evaluations * 4):
            for combo in _generate_exhaustive_combo_values(search_space_specs):
                snapped = _snap_combo_to_search_space(combo, search_space_specs)
                if snapped in seen_combos:
                    continue
                seen_combos.add(snapped)
                batch_combos.append(snapped)
                if len(batch_combos) >= current_batch_size:
                    break

        if not batch_combos:
            break

        batch_rows = _evaluate_explicit_combo_records(
            batch_combos,
            combo_index_start=combo_index_start + len(evaluated_rows),
            base_params=base_params,
            dapi=dapi,
            dapi_norm=dapi_norm,
            pixel_size_um=pixel_size_um,
            parallel_workers=parallel_workers,
            parallel_backend=parallel_backend,
            native_threads_per_worker=native_threads_per_worker,
            compute_telemetry_state=compute_telemetry_state,
        )
        evaluated_rows.extend(batch_rows)
        stage_index += 1

    df_results = _deduplicate_nuclei_result_rows(pd.DataFrame(evaluated_rows))
    if len(df_results) > 0:
        df_results = df_results.sort_values("combo_index").reset_index(drop=True)
    ranked_results = rank_nuclei_parameter_sweep_results(df_results)
    return {
        "results": df_results,
        "ranked_results": ranked_results,
        "n_evaluated": int(len(df_results)),
    }


def _prepare_screening_inputs(
    dapi: np.ndarray,
    dapi_norm: np.ndarray,
    pixel_size_um: Tuple[float, float],
    factor: int,
) -> Dict[str, Any] | None:
    try:
        factor = max(1, int(factor))
    except Exception:
        factor = 1
    if factor <= 1:
        return None

    min_dim = min(int(dapi.shape[0]), int(dapi.shape[1]))
    max_safe_factor = max(1, min_dim // 96)
    factor = min(factor, max_safe_factor)
    if factor <= 1:
        return None

    dapi_screen = np.ascontiguousarray(dapi[::factor, ::factor])
    dapi_norm_screen = np.ascontiguousarray(dapi_norm[::factor, ::factor])
    if min(dapi_screen.shape[0], dapi_screen.shape[1]) < 64:
        return None

    return {
        "factor": int(factor),
        "dapi": dapi_screen,
        "dapi_norm": dapi_norm_screen,
        "pixel_size_um": (float(pixel_size_um[0]) * factor, float(pixel_size_um[1]) * factor),
    }


def _select_screening_survivors(
    combo_pool: Sequence[Tuple[float, ...]],
    ranked_df: pd.DataFrame,
    survivor_count: int,
) -> List[Tuple[float, ...]]:
    survivor_count = max(1, min(int(survivor_count), len(combo_pool)))
    survivors: List[Tuple[float, ...]] = []
    seen: set[Tuple[float, ...]] = set()

    for _, row in ranked_df.iterrows():
        combo_idx = int(row["combo_index"]) - 1
        if combo_idx < 0 or combo_idx >= len(combo_pool):
            continue
        combo = tuple(float(v) for v in combo_pool[combo_idx])
        if combo in seen:
            continue
        seen.add(combo)
        survivors.append(combo)
        if len(survivors) >= survivor_count:
            return survivors

    for combo in combo_pool:
        combo_key = tuple(float(v) for v in combo)
        if combo_key in seen:
            continue
        seen.add(combo_key)
        survivors.append(combo_key)
        if len(survivors) >= survivor_count:
            break

    return survivors


def _screen_combo_pool_with_successive_halving(
    *,
    combo_pool: Sequence[Tuple[float, ...]],
    survivor_count: int,
    base_params: NucleiParams,
    dapi: np.ndarray,
    dapi_norm: np.ndarray,
    pixel_size_um: Tuple[float, float],
    parallel_workers: int = 1,
    parallel_backend: str = "loky",
    native_threads_per_worker: int | None = 1,
    compute_telemetry_state: Dict[str, bool] | None = None,
) -> Dict[str, Any]:
    working_pool = [tuple(float(v) for v in combo) for combo in combo_pool]
    survivor_count = max(1, min(int(survivor_count), len(working_pool)))
    if len(working_pool) <= survivor_count:
        return {
            "survivors": working_pool,
            "n_screened": 0,
            "n_rounds": 0,
            "stage_factors": [],
        }

    screening_specs: List[Dict[str, Any]] = []
    for factor in (4, 2):
        prepared = _prepare_screening_inputs(dapi, dapi_norm, pixel_size_um, factor)
        if prepared is None:
            continue
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

        screen_rows = _evaluate_explicit_combo_records(
            current_pool,
            combo_index_start=1,
            base_params=base_params,
            dapi=spec["dapi"],
            dapi_norm=spec["dapi_norm"],
            pixel_size_um=spec["pixel_size_um"],
            parallel_workers=parallel_workers,
            parallel_backend=parallel_backend,
            native_threads_per_worker=native_threads_per_worker,
            compute_telemetry_state=compute_telemetry_state,
        )
        screened_count += len(current_pool)
        ranked_screen = rank_nuclei_parameter_sweep_results(pd.DataFrame(screen_rows))
        current_pool = _select_screening_survivors(current_pool, ranked_screen, stage_survivor_count)
        rounds_completed += 1
        stage_factors.append(int(spec["factor"]))

    return {
        "survivors": current_pool[:survivor_count],
        "n_screened": int(screened_count),
        "n_rounds": int(rounds_completed),
        "stage_factors": stage_factors,
    }


def _mutate_nuclei_combo(
    combo: Sequence[float],
    search_space_specs: Dict[str, Dict[str, float]],
    rng: np.random.Generator,
    *,
    mutation_rate: float = 0.35,
    mutation_scale: float = 0.12,
) -> Tuple[float, ...]:
    mutated: List[float] = []
    mutated_any = False

    for idx, field in enumerate(SWEEP_PARAM_ORDER):
        spec = search_space_specs[field]
        max_index = max(0, int(spec["n_values"]) - 1)
        current_index = int(round((float(combo[idx]) - float(spec["min"])) / float(spec["step"])))
        current_index = min(max(current_index, 0), max_index)

        if rng.random() < mutation_rate and max_index > 0:
            if rng.random() < 0.15:
                new_index = int(rng.integers(0, max_index + 1))
            else:
                radius_steps = max(1, int(math.ceil(max_index * max(0.01, float(mutation_scale)))))
                delta = int(rng.integers(-radius_steps, radius_steps + 1))
                if delta == 0:
                    delta = 1 if current_index < max_index else -1
                new_index = min(max(current_index + delta, 0), max_index)
            mutated_any = mutated_any or new_index != current_index
            value = float(spec["min"]) + new_index * float(spec["step"])
        else:
            value = float(combo[idx])

        mutated.append(
            _snap_value_to_search_space(
                value,
                float(spec["min"]),
                float(spec["max"]),
                float(spec["step"]),
            )
        )

    if not mutated_any and len(SWEEP_PARAM_ORDER) > 0:
        forced_idx = int(rng.integers(0, len(SWEEP_PARAM_ORDER)))
        field = SWEEP_PARAM_ORDER[forced_idx]
        spec = search_space_specs[field]
        max_index = max(0, int(spec["n_values"]) - 1)
        if max_index > 0:
            current_index = int(round((float(combo[forced_idx]) - float(spec["min"])) / float(spec["step"])))
            current_index = min(max(current_index, 0), max_index)
            delta = 1 if current_index < max_index else -1
            new_index = min(max(current_index + delta, 0), max_index)
            mutated[forced_idx] = _snap_value_to_search_space(
                float(spec["min"]) + new_index * float(spec["step"]),
                float(spec["min"]),
                float(spec["max"]),
                float(spec["step"]),
            )

    return _repair_combo_constraints(mutated)


def _crossover_nuclei_combos(
    parent_a: Sequence[float],
    parent_b: Sequence[float],
    search_space_specs: Dict[str, Dict[str, float]],
    rng: np.random.Generator,
) -> Tuple[float, ...]:
    child: List[float] = []
    for idx, field in enumerate(SWEEP_PARAM_ORDER):
        spec = search_space_specs[field]
        a_val = float(parent_a[idx])
        b_val = float(parent_b[idx])
        if rng.random() < 0.35 and abs(a_val - b_val) > 1e-12:
            lower = min(a_val, b_val)
            upper = max(a_val, b_val)
            candidate = float(rng.uniform(lower, upper))
        else:
            candidate = a_val if rng.random() < 0.5 else b_val
        child.append(
            _snap_value_to_search_space(
                candidate,
                float(spec["min"]),
                float(spec["max"]),
                float(spec["step"]),
            )
        )
    return _repair_combo_constraints(child)


def _run_evolutionary_search_records(
    *,
    search_space_specs: Dict[str, Dict[str, float]],
    max_evaluations: int,
    combo_index_start: int,
    base_params: NucleiParams,
    dapi: np.ndarray,
    dapi_norm: np.ndarray,
    pixel_size_um: Tuple[float, float],
    parallel_workers: int = 1,
    parallel_backend: str = "loky",
    native_threads_per_worker: int | None = 1,
    random_seed: int = 42,
    seed_rows: pd.DataFrame | None = None,
    compute_telemetry_state: Dict[str, bool] | None = None,
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
    total_space_n_combinations = _search_space_n_combinations(search_space_specs)
    population_size = max(12, min(64, int(max_evaluations), int(parallel_workers) * 12))
    candidate_pool_size = max(population_size, min(160, population_size * 4))
    elite_size = max(2, min(12, population_size // 4))
    immigrant_quota = max(2, min(10, population_size // 5))
    mutation_schedule = [0.18, 0.12, 0.08, 0.05]

    seen_combos: set[Tuple[float, ...]] = set()
    evaluated_rows: List[Dict[str, Any]] = []
    generation_index = 0
    population: List[Tuple[float, ...]] = []
    screened_candidate_count = 0
    screening_round_count = 0
    screening_stage_factors: set[int] = set()
    ranked_seed_rows = (
        rank_nuclei_parameter_sweep_results(seed_rows) if seed_rows is not None and len(seed_rows) > 0 else pd.DataFrame()
    )

    def add_unique_combo(target: List[Tuple[float, ...]], combo: Sequence[float]) -> bool:
        snapped = _snap_combo_to_search_space(combo, search_space_specs)
        if snapped in seen_combos:
            return False
        seen_combos.add(snapped)
        target.append(snapped)
        return True

    add_unique_combo(population, _combo_from_params(base_params))
    for _, row in ranked_seed_rows.head(min(12, len(ranked_seed_rows))).iterrows():
        add_unique_combo(population, _combo_key_from_result_row(row))
        if len(population) >= candidate_pool_size:
            break

    if len(population) < candidate_pool_size and len(ranked_seed_rows) > 0:
        seed_local_candidates = _sample_local_search_candidates(
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
        global_candidates = _sample_global_search_candidates(
            search_space_specs,
            max(candidate_pool_size * 3, 64),
            rng,
        )
        for combo in global_candidates:
            add_unique_combo(population, combo)
            if len(population) >= candidate_pool_size:
                break

    if len(population) < candidate_pool_size and total_space_n_combinations <= max(4096, max_evaluations * 6):
        for combo in _generate_exhaustive_combo_values(search_space_specs):
            add_unique_combo(population, combo)
            if len(population) >= candidate_pool_size:
                break

    while len(evaluated_rows) < max_evaluations and population:
        batch_size = min(population_size, max_evaluations - len(evaluated_rows))
        screen_result = _screen_combo_pool_with_successive_halving(
            combo_pool=population,
            survivor_count=batch_size,
            base_params=base_params,
            dapi=dapi,
            dapi_norm=dapi_norm,
            pixel_size_um=pixel_size_um,
            parallel_workers=parallel_workers,
            parallel_backend=parallel_backend,
            native_threads_per_worker=native_threads_per_worker,
            compute_telemetry_state=compute_telemetry_state,
        )
        screened_candidate_count += int(screen_result.get("n_screened", 0))
        screening_round_count += int(screen_result.get("n_rounds", 0))
        screening_stage_factors.update(int(v) for v in screen_result.get("stage_factors", []))
        batch_combos = [tuple(float(v) for v in combo) for combo in screen_result.get("survivors", [])][:batch_size]
        if not batch_combos:
            break
        batch_rows = _evaluate_explicit_combo_records(
            batch_combos,
            combo_index_start=combo_index_start + len(evaluated_rows),
            base_params=base_params,
            dapi=dapi,
            dapi_norm=dapi_norm,
            pixel_size_um=pixel_size_um,
            parallel_workers=parallel_workers,
            parallel_backend=parallel_backend,
            native_threads_per_worker=native_threads_per_worker,
            compute_telemetry_state=compute_telemetry_state,
        )
        evaluated_rows.extend(batch_rows)
        generation_index += 1

        if len(evaluated_rows) >= max_evaluations:
            break

        ranked_all = rank_nuclei_parameter_sweep_results(pd.DataFrame(evaluated_rows))
        elite_rows = ranked_all.head(min(elite_size, len(ranked_all)))
        elite_combos = [_combo_key_from_result_row(row) for _, row in elite_rows.iterrows()]

        next_population: List[Tuple[float, ...]] = []
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
                _mutate_nuclei_combo(
                    combo,
                    search_space_specs,
                    rng,
                    mutation_rate=0.55,
                    mutation_scale=mutation_scale,
                ),
            )

        breeding_pool = elite_combos.copy()
        breeding_pool.extend(
            [_combo_key_from_result_row(row) for _, row in ranked_seed_rows.head(min(6, len(ranked_seed_rows))).iterrows()]
        )
        if not breeding_pool and len(ranked_all) > 0:
            breeding_pool = [_combo_key_from_result_row(row) for _, row in ranked_all.head(min(8, len(ranked_all))).iterrows()]

        attempts = 0
        crossover_target = max(0, next_pool_target - immigrant_quota)
        while len(next_population) < crossover_target and attempts < next_pool_target * 40 and breeding_pool:
            parent_a = breeding_pool[int(rng.integers(0, len(breeding_pool)))]
            parent_b = breeding_pool[int(rng.integers(0, len(breeding_pool)))]
            child = _crossover_nuclei_combos(parent_a, parent_b, search_space_specs, rng)
            if rng.random() < 0.9:
                child = _mutate_nuclei_combo(
                    child,
                    search_space_specs,
                    rng,
                    mutation_rate=0.35,
                    mutation_scale=mutation_scale,
                )
            add_unique_combo(next_population, child)
            attempts += 1

        if len(next_population) < next_pool_target and len(elite_rows) > 0:
            local_candidates = _sample_local_search_candidates(
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
            immigrant_candidates = _sample_global_search_candidates(
                search_space_specs,
                max((next_pool_target - len(next_population)) * 6, 24),
                rng,
            )
            for combo in immigrant_candidates:
                add_unique_combo(next_population, combo)
                if len(next_population) >= next_pool_target:
                    break

        if len(next_population) < next_pool_target and total_space_n_combinations <= max(4096, max_evaluations * 6):
            for combo in _generate_exhaustive_combo_values(search_space_specs):
                if add_unique_combo(next_population, combo) and len(next_population) >= next_pool_target:
                    break

        population = next_population[: max(0, next_pool_target)]

    df_results = _deduplicate_nuclei_result_rows(pd.DataFrame(evaluated_rows))
    if len(df_results) > 0:
        df_results = df_results.sort_values("combo_index").reset_index(drop=True)
    ranked_results = rank_nuclei_parameter_sweep_results(df_results)
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


def _optimizer_combo_preprocess_radii(
    combo: Sequence[float],
    pixel_size_um: Tuple[float, float],
) -> Tuple[int, int]:
    iso_scale_um = float(np.sqrt(float(pixel_size_um[0]) * float(pixel_size_um[1])))
    safe_scale = max(1e-12, iso_scale_um)
    tophat_index = SWEEP_PARAM_ORDER.index("tophat_radius_um")
    sigma_index = SWEEP_PARAM_ORDER.index("gauss_sigma_um")
    tophat_radius_px = min(
        _swift_round_nonnegative(float(combo[tophat_index]) / safe_scale),
        30,
    )
    sigma_radius_px = _swift_round_nonnegative(float(combo[sigma_index]) / safe_scale)
    if sigma_radius_px > 0:
        sigma_radius_px = min(max(1, sigma_radius_px), 12)
    return int(tophat_radius_px), int(sigma_radius_px)


def _group_optimizer_combo_records(
    combo_records: Sequence[Tuple[int, Tuple[float, ...]]],
    *,
    pixel_size_um: Tuple[float, float],
) -> List[Tuple[int, List[Tuple[int, Tuple[float, ...]]]]]:
    grouped: Dict[int, List[Tuple[int, Tuple[float, ...]]]] = {}
    for combo_index, combo in combo_records:
        tophat_radius_px, _ = _optimizer_combo_preprocess_radii(combo, pixel_size_um)
        grouped.setdefault(tophat_radius_px, []).append((int(combo_index), combo))
    return list(grouped.items())


def _nuclei_optimizer_result_row(
    combo_index: int,
    params: NucleiParams,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "combo_index": int(combo_index),
        "nucleus_channel": params.nucleus_channel,
    }
    row.update({SWEEP_PARAM_LABELS[field]: float(getattr(params, field)) for field in SWEEP_PARAM_ORDER})
    return row


def _evaluate_optimizer_tophat_group(
    tophat_radius_px: int,
    combo_records: Sequence[Tuple[int, Tuple[float, ...]]],
    base_params: NucleiParams,
    dapi_norm: np.ndarray,
    pixel_size_um: Tuple[float, float],
    native_threads: int | None = 1,
    telemetry_combo_index: int | None = None,
) -> List[Dict[str, Any]]:
    prepared_records: List[Tuple[int, NucleiParams, int]] = []
    for combo_index, combo in combo_records:
        overrides = {field: float(value) for field, value in zip(SWEEP_PARAM_ORDER, combo)}
        params = _coerce_nuclei_params(base_params, overrides)
        _validate_nuclei_params(params)
        _, sigma_radius_px = _optimizer_combo_preprocess_radii(combo, pixel_size_um)
        prepared_records.append((int(combo_index), params, int(sigma_radius_px)))

    sigma_groups: Dict[int, List[Tuple[int, NucleiParams]]] = {}
    for combo_index, params, sigma_radius_px in prepared_records:
        sigma_groups.setdefault(sigma_radius_px, []).append((combo_index, params))

    rows: List[Dict[str, Any]] = []
    with _thread_limit_context(native_threads):
        base_work = dapi_norm.astype(np.float64, copy=True)
        if tophat_radius_px > 0:
            background = _truncated_box_blur(base_work, int(tophat_radius_px))
            base_work = np.maximum(0.0, base_work - background)

        for sigma_radius_px, records in sigma_groups.items():
            work = (
                base_work
                if sigma_radius_px <= 0
                else _truncated_box_blur(base_work, int(sigma_radius_px))
            )
            work_mean = float(np.mean(work))
            work_std = float(np.std(work))

            for combo_index, params in records:
                row = _nuclei_optimizer_result_row(combo_index, params)
                try:
                    component_labels, component_areas, valid_components = _nuclei_components_from_work(
                        work,
                        pixel_size_um,
                        params,
                        work_mean=work_mean,
                        work_std=work_std,
                    )
                    n_nuclei = int(valid_components.size)
                    if combo_index == telemetry_combo_index:
                        # One exact, scientifically relevant membership pass per
                        # optimizer request keeps CPU/OpenCL execution observable
                        # without rebuilding every candidate's full label image.
                        positive_mask = get_compute_runtime().labels_in_set(
                            component_labels,
                            valid_components,
                        )
                        positive_px = int(np.count_nonzero(positive_mask))
                    else:
                        positive_px = int(component_areas[valid_components].sum())
                    row["n_nuclei"] = n_nuclei
                    row["positive_pixel_fraction"] = (
                        float(positive_px / component_labels.size) if component_labels.size > 0 else 0.0
                    )
                    row["mean_pixels_per_nucleus"] = (
                        float(positive_px / n_nuclei) if n_nuclei > 0 else 0.0
                    )
                    row["error"] = ""
                except Exception as exc:  # pragma: no cover - defensive UI guard
                    row["n_nuclei"] = np.nan
                    row["positive_pixel_fraction"] = np.nan
                    row["mean_pixels_per_nucleus"] = np.nan
                    row["error"] = str(exc)
                rows.append(row)
    return rows


def _evaluate_sweep_combo_chunk(
    combo_chunk: Sequence[Tuple[int, Tuple[float, ...]]],
    base_params: NucleiParams,
    dapi: np.ndarray,
    dapi_norm: np.ndarray,
    pixel_size_um: Tuple[float, float],
    native_threads: int | None = 1,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with _thread_limit_context(native_threads):
        for combo_index, combo in combo_chunk:
            overrides = {field: float(value) for field, value in zip(SWEEP_PARAM_ORDER, combo)}
            params = _coerce_nuclei_params(base_params, overrides)
            _validate_nuclei_params(params)
            row = _nuclei_optimizer_result_row(combo_index, params)
            try:
                labels = segment_nuclei_from_prepared_images(dapi, dapi_norm, pixel_size_um, params)
                n_nuclei = int(labels.max())
                positive_px = int((labels > 0).sum())
                row["n_nuclei"] = n_nuclei
                row["positive_pixel_fraction"] = float(positive_px / labels.size) if labels.size > 0 else 0.0
                row["mean_pixels_per_nucleus"] = float(positive_px / n_nuclei) if n_nuclei > 0 else 0.0
                row["error"] = ""
            except Exception as exc:  # pragma: no cover - defensive UI guard
                row["n_nuclei"] = np.nan
                row["positive_pixel_fraction"] = np.nan
                row["mean_pixels_per_nucleus"] = np.nan
                row["error"] = str(exc)
            rows.append(row)
    return rows



def pick_nucleus_channel(channels: Sequence[str]) -> str | None:
    if not channels:
        return None
    preferred_exact = ["DAPI", "HOECHST", "HOECHST33342", "H33342", "H342", "NUCLEUS", "NUCLEAR"]
    upper = {channel.upper(): channel for channel in channels}
    for preferred in preferred_exact:
        if preferred in upper:
            return upper[preferred]
    preferred_sub = ["DAPI", "HOECHST", "H333", "NUC"]
    for channel in channels:
        upper_channel = channel.upper()
        if any(token in upper_channel for token in preferred_sub):
            return channel
    return channels[0]


def load_nucleus_channel_image(
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    image_id: str,
    nucleus_channel: str,
) -> np.ndarray:
    return to_image(df_pixels, shapes, image_id, nucleus_channel).astype(np.float32)


def normalize_nucleus_image(dapi: np.ndarray) -> np.ndarray:
    high = float(np.nanpercentile(dapi, 99.8))
    normalized = np.clip(dapi.astype(np.float64, copy=False) / max(1e-6, high), 0, 1)
    return np.nan_to_num(normalized, nan=0.0, posinf=1.0, neginf=0.0)


def _clamp_nuclei_roi_bounds(
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


def _build_fixed_five_roi_nuclei_subset(
    dapi: np.ndarray,
    *,
    roi_area_fraction: float = 0.02,
    gap_px: int = 16,
) -> Dict[str, Any]:
    full_height, full_width = dapi.shape
    roi_fraction = max(1e-6, float(roi_area_fraction))
    roi_width = max(1, min(full_width, int(round(full_width * math.sqrt(roi_fraction)))))
    roi_height = max(1, min(full_height, int(round(full_height * math.sqrt(roi_fraction)))))
    gap_px = max(4, int(gap_px))

    anchor_specs = [
        ("upper_left", float(full_width) / 4.0, float(full_height) / 4.0, 0, 0),
        ("upper_right", 3.0 * float(full_width) / 4.0, float(full_height) / 4.0, 0, 1),
        ("center", float(full_width) / 2.0, float(full_height) / 2.0, 1, 0),
        ("lower_left", float(full_width) / 4.0, 3.0 * float(full_height) / 4.0, 2, 0),
        ("lower_right", 3.0 * float(full_width) / 4.0, 3.0 * float(full_height) / 4.0, 2, 1),
    ]

    mosaic_height = int(roi_height * 3 + gap_px * 2)
    mosaic_width = int(roi_width * 2 + gap_px)
    mosaic_dapi = np.zeros((mosaic_height, mosaic_width), dtype=np.float32)
    roi_metadata: List[Dict[str, Any]] = []

    for roi_name, center_x, center_y, row_idx, col_idx in anchor_specs:
        x0, y0, x1, y1 = _clamp_nuclei_roi_bounds(
            center_x=center_x,
            center_y=center_y,
            roi_width=roi_width,
            roi_height=roi_height,
            full_width=full_width,
            full_height=full_height,
        )
        target_y0 = int(row_idx * (roi_height + gap_px))
        target_x0 = int(col_idx * (roi_width + gap_px))
        target_y1 = target_y0 + (y1 - y0)
        target_x1 = target_x0 + (x1 - x0)
        mosaic_dapi[target_y0:target_y1, target_x0:target_x1] = dapi[y0:y1, x0:x1]
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
            }
        )

    return {
        "dapi": mosaic_dapi,
        "roi_metadata": roi_metadata,
        "sampled_area_fraction": float(sum(float(spec["area_fraction"]) for spec in roi_metadata)),
        "mosaic_shape": (int(mosaic_height), int(mosaic_width)),
    }


def _build_vertical_band_nuclei_subset(
    dapi: np.ndarray,
    *,
    band_count: int = 10,
    selected_band_indices: Sequence[int] | None = None,
    selected_band_count: int = 5,
    gap_px: int = 16,
) -> Dict[str, Any]:
    full_height, full_width = dapi.shape
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
    mosaic_dapi = np.zeros((int(full_height), int(mosaic_width)), dtype=np.float32)
    roi_metadata: List[Dict[str, Any]] = []

    target_x0 = 0
    for band_index in selected_band_indices:
        x0, x1 = band_bounds[band_index]
        band_width = max(0, x1 - x0)
        target_x1 = target_x0 + band_width
        if band_width > 0:
            mosaic_dapi[:, target_x0:target_x1] = dapi[:, x0:x1]
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

    return {
        "dapi": mosaic_dapi,
        "roi_metadata": roi_metadata,
        "sampled_area_fraction": float(sum(float(spec["area_fraction"]) for spec in roi_metadata)),
        "mosaic_shape": (int(full_height), int(mosaic_width)),
        "band_count": int(band_count),
        "selected_band_indices": [int(idx) for idx in selected_band_indices],
    }


def _swift_round_nonnegative(value: float) -> int:
    """Match Swift's `.rounded()` for the nonnegative pixel values used here."""
    return int(math.floor(max(0.0, float(value)) + 0.5))


def _pixel_converters(pixel_size_um: Tuple[float, float]):
    px_um_x, px_um_y = float(pixel_size_um[0]), float(pixel_size_um[1])

    def um_to_px_x(value_um: float) -> int:
        return max(1, _swift_round_nonnegative(value_um / px_um_x))

    def um_to_px_y(value_um: float) -> int:
        return max(1, _swift_round_nonnegative(value_um / px_um_y))

    def um_to_px_iso(value_um: float) -> int:
        return max(
            1,
            _swift_round_nonnegative(value_um / np.sqrt(px_um_x * px_um_y)),
        )

    return px_um_x, px_um_y, um_to_px_x, um_to_px_y, um_to_px_iso


@lru_cache(maxsize=256)
def _truncated_box_blur_edge_counts(length: int, radius: int) -> np.ndarray:
    """Cache the exact edge divisor used by SciPy's constant-mode box blur."""
    size = int(radius) * 2 + 1
    counts = ndi.uniform_filter1d(
        np.ones(int(length), dtype=np.float64),
        size=size,
        mode="constant",
        cval=0.0,
    ) * float(size)
    counts.setflags(write=False)
    return counts


def _truncated_box_blur(image: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return image.astype(np.float64, copy=False)
    size = int(radius) * 2 + 1
    values = image.astype(np.float64, copy=False)
    horizontal_sum = ndi.uniform_filter1d(
        values,
        size=size,
        axis=1,
        mode="constant",
        cval=0.0,
    ) * float(size)
    horizontal_count = _truncated_box_blur_edge_counts(values.shape[1], int(radius))
    horizontal = np.divide(
        horizontal_sum,
        horizontal_count[np.newaxis, :],
        out=np.zeros_like(horizontal_sum),
        where=horizontal_count[np.newaxis, :] > 0,
    )
    vertical_sum = ndi.uniform_filter1d(
        horizontal,
        size=size,
        axis=0,
        mode="constant",
        cval=0.0,
    ) * float(size)
    vertical_count = _truncated_box_blur_edge_counts(values.shape[0], int(radius))
    return np.divide(
        vertical_sum,
        vertical_count[:, np.newaxis],
        out=np.zeros_like(vertical_sum),
        where=vertical_count[:, np.newaxis] > 0,
    )


def _prepare_nuclei_work_for_radii(
    dapi_norm: np.ndarray,
    tophat_radius_px: int,
    sigma_radius_px: int,
) -> np.ndarray:
    work = dapi_norm.astype(np.float64, copy=True)
    if tophat_radius_px > 0:
        background = _truncated_box_blur(work, min(int(tophat_radius_px), 30))
        work = np.maximum(0.0, work - background)
    if sigma_radius_px > 0:
        work = _truncated_box_blur(work, min(max(1, int(sigma_radius_px)), 12))
    return work


def _nuclei_components_from_work(
    work: np.ndarray,
    pixel_size_um: Tuple[float, float],
    params: NucleiParams,
    *,
    work_mean: float | None = None,
    work_std: float | None = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    px_um_x, px_um_y, _, _, _ = _pixel_converters(pixel_size_um)
    iso_scale_um = np.sqrt(px_um_x * px_um_y)
    mean = float(np.mean(work)) if work_mean is None else float(work_mean)
    std = float(np.std(work)) if work_std is None else float(work_std)
    threshold_factor = (
        0.56
        + float(params.local_offset) * 2.0
        + float(params.h_maxima_um) * 0.18
        + float(params.seed_min_dist_um) * 0.012
        + float(params.watershed_compactness) * 0.018
        - float(params.post_resplit_mult) * 0.010
    )
    threshold = min(max(mean + std * threshold_factor, 0.01), 0.98)
    mask = work >= threshold

    min_radius_px = max(0.5, float(params.min_diam_um) / max(1e-12, iso_scale_um) / 2.0)
    max_radius_px = max(
        min_radius_px,
        float(params.max_diam_um) / max(1e-12, iso_scale_um) / 2.0,
    )
    min_area_px = max(1, int(np.pi * min_radius_px * min_radius_px * 0.35))
    max_area_px = max(min_area_px, int(np.pi * max_radius_px * max_radius_px * 1.75))

    component_labels, _ = ndi.label(mask, structure=ndi.generate_binary_structure(2, 1))
    component_areas = np.bincount(component_labels.ravel())
    valid_components = np.flatnonzero(
        (component_areas >= min_area_px) & (component_areas <= max_area_px)
    )
    valid_components = valid_components[valid_components > 0]
    return component_labels, component_areas, valid_components


def segment_nuclei_from_prepared_images(
    dapi: np.ndarray,
    dapi_norm: np.ndarray,
    pixel_size_um: Tuple[float, float],
    params: NucleiParams,
) -> np.ndarray:
    del dapi  # The macOS implementation operates on the normalized channel.
    px_um_x, px_um_y, _, _, _ = _pixel_converters(pixel_size_um)
    iso_scale_um = np.sqrt(px_um_x * px_um_y)

    # Match SpatialScope/Services/NucleiSegmenter.swift so the same saved
    # parameters produce comparable labels on macOS and Windows.
    tophat_px = _swift_round_nonnegative(
        float(params.tophat_radius_um) / max(1e-12, iso_scale_um)
    )
    sigma_px = _swift_round_nonnegative(
        float(params.gauss_sigma_um) / max(1e-12, iso_scale_um)
    )
    work = _prepare_nuclei_work_for_radii(
        dapi_norm,
        min(tophat_px, 30),
        min(max(1, sigma_px), 12) if sigma_px > 0 else 0,
    )
    component_labels, component_areas, valid_components = _nuclei_components_from_work(
        work,
        pixel_size_um,
        params,
    )
    remap = np.zeros(component_areas.shape[0], dtype=np.int32)
    remap[valid_components] = np.arange(1, valid_components.size + 1, dtype=np.int32)
    # Canonical relabeling is an exact integer lookup, so every compatible GPU
    # and the CPU pool can share this real segmentation work without changing
    # component identities or the macOS-compatible algorithm.
    return get_compute_runtime().lookup_labels(component_labels, remap).astype(np.int32, copy=False)


def summarize_nuclei_labels(
    labels: np.ndarray,
    dapi: np.ndarray,
    pixel_size_um: Tuple[float, float],
) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    px_um_x, px_um_y, _, _, _ = _pixel_converters(pixel_size_um)
    px_area_um2 = px_um_x * px_um_y
    n_nuclei = int(labels.max())

    props = measure.regionprops_table(
        labels,
        intensity_image=dapi,
        properties=(
            "label",
            "area",
            "perimeter",
            "eccentricity",
            "solidity",
            "centroid",
            "bbox",
            "mean_intensity",
            "max_intensity",
        ),
    )
    df_props = pd.DataFrame(props)
    if len(df_props) > 0:
        df_props.rename(
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
        df_props["centroid_x_um"] = df_props["centroid_x_px"] * px_um_x
        df_props["centroid_y_um"] = df_props["centroid_y_px"] * px_um_y
        df_props["area_um2"] = df_props["area"] * px_area_um2
        df_props["perimeter_um"] = df_props["perimeter"] * np.sqrt(px_um_x * px_um_y)

    boundaries: List[Dict[str, Any]] = []
    for label_id in range(1, n_nuclei + 1):
        mask_i = labels == label_id
        contours = find_contours(mask_i.astype(float), 0.5)
        if not contours:
            continue
        contour = max(contours, key=lambda arr: arr.shape[0])
        xy_px = np.column_stack([contour[:, 1], contour[:, 0]])
        xy_um = xy_px * np.array([px_um_x, px_um_y])
        boundaries.append(
            {
                "label": int(label_id),
                "boundary_px": xy_px.tolist(),
                "boundary_um": xy_um.tolist(),
            }
        )

    return df_props, boundaries


def make_nuclei_segmentation_figure(
    dapi: np.ndarray,
    labels_u16: np.ndarray,
    image_id: str,
    pixel_size_um: Tuple[float, float],
    params: NucleiParams,
) -> plt.Figure:
    px_um_x, px_um_y, _, _, _ = _pixel_converters(pixel_size_um)
    n_nuclei = int(labels_u16.max())

    rng = np.random.default_rng(42)
    palette = np.vstack([[0, 0, 0], rng.random((max(n_nuclei, 1), 3))])
    cmap = ListedColormap(palette)

    h, w = labels_u16.shape
    extent = [0, w * px_um_x, h * px_um_y, 0]

    fig, axes = plt.subplots(1, 2, figsize=(12, 6), constrained_layout=True)
    axes[0].imshow(norm_clip(dapi, hi_percentile=99.8), cmap="gray", origin="upper", extent=extent)
    axes[0].set_title(f"{image_id} — {params.nucleus_channel}")
    axes[0].set_xlabel("x (µm)")
    axes[0].set_ylabel("y (µm)")

    axes[1].imshow(labels_u16, cmap=cmap, origin="upper", extent=extent, interpolation="nearest")
    axes[1].set_title("Nuclei (random colors)")
    axes[1].set_xlabel("x (µm)")
    axes[1].set_ylabel("y (µm)")
    return fig


def run_nuclei_segmentation(
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    image_id: str,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    params: NucleiParams,
    save_outputs: bool = True,
    native_threads: int | None = None,
) -> Dict[str, Any]:
    """
    Port of the notebook's nuclei segmentation logic with minimal changes.
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    params = _coerce_nuclei_params(params)
    _validate_nuclei_params(params)

    dapi = load_nucleus_channel_image(df_pixels, shapes, image_id, params.nucleus_channel)
    dapi_norm = normalize_nucleus_image(dapi)
    with _thread_limit_context(native_threads):
        labels = segment_nuclei_from_prepared_images(dapi, dapi_norm, pixel_size_um, params)
        n_nuclei = int(labels.max())
        df_props, boundaries = summarize_nuclei_labels(labels, dapi_norm, pixel_size_um)
    labels_u16 = labels.astype(np.uint16)

    summary_csv = save_dir / "nuclei_summary.csv"
    boundaries_json = save_dir / "nuclei_boundaries.json"
    tiff_path = save_dir / "nuclei_labels_uint16.tiff"
    params_json = save_dir / "nuclei_params.json"

    if save_outputs:
        df_props.to_csv(summary_csv, index=False)
        boundaries_json.write_text(json.dumps(boundaries))
        save_uint16_tiff(tiff_path, labels_u16)
        write_json(params_json, params.to_dict())

    fig = make_nuclei_segmentation_figure(dapi, labels_u16, image_id, pixel_size_um, params)

    panel_svg = save_dir / "nuclei_segmentation_panel.svg"
    panel_png = save_dir / "nuclei_segmentation_panel.png"
    panel_tiff = save_dir / "nuclei_segmentation_panel.tiff"
    if save_outputs:
        fig.savefig(panel_svg, bbox_inches="tight")
        fig.savefig(panel_png, dpi=300, bbox_inches="tight")
        fig.savefig(panel_tiff, dpi=300, bbox_inches="tight")

    return {
        "labels": labels,
        "labels_u16": labels_u16,
        "n_nuclei": n_nuclei,
        "df_props": df_props,
        "boundaries": boundaries,
        "params": params.to_dict(),
        "figure": fig,
        "saved_paths": {
            "summary_csv": summary_csv,
            "boundaries_json": boundaries_json,
            "labels_tiff": tiff_path,
            "panel_svg": panel_svg,
            "panel_png": panel_png,
            "panel_tiff": panel_tiff,
            "params_json": params_json,
        },
    }


def make_nuclei_parameter_sweep_figure(df_results: pd.DataFrame) -> plt.Figure:
    plot_df = df_results.copy()
    if "combo_index" in plot_df.columns:
        plot_df = plot_df.sort_values("combo_index")

    fig, ax = plt.subplots(figsize=(8.6, 4.2), constrained_layout=True)
    if len(plot_df) > 0:
        ax.plot(plot_df["combo_index"], plot_df["n_nuclei"], marker="o", linewidth=1)
        best_idx = plot_df["n_nuclei"].astype(float).idxmax()
        best_row = plot_df.loc[best_idx]
        ax.annotate(
            f"best #{int(best_row['combo_index'])}: {int(best_row['n_nuclei'])} nuclei",
            xy=(best_row["combo_index"], best_row["n_nuclei"]),
            xytext=(10, 10),
            textcoords="offset points",
        )
    ax.set_xlabel("Parameter combination index")
    ax.set_ylabel("Segmented nuclei count")
    ax.set_title("Nuclei parameter sweep")
    ax.grid(alpha=0.3)
    ax.tick_params(axis="both", labelsize=11)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", rotation_mode="anchor")
    return fig



def rank_nuclei_parameter_sweep_results(df_results: pd.DataFrame) -> pd.DataFrame:
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


def recommend_nuclei_parameter_sweep_result(df_results: pd.DataFrame) -> pd.Series | None:
    ranked = rank_nuclei_parameter_sweep_results(df_results)
    if len(ranked) == 0:
        return None
    return ranked.iloc[0]


def run_nuclei_parameter_sweep(
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    image_id: str,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    base_params: NucleiParams,
    sweep_values: Dict[str, Sequence[float]],
    save_outputs: bool = True,
    max_combinations: int | None = None,
    parallel_workers: int = 1,
    parallel_backend: str = "loky",
    native_threads_per_worker: int | None = 1,
    output_prefix: str = "nuclei_parameter_sweep",
) -> Dict[str, Any]:
    """
    Run a nuclei-parameter sweep.

    Notes
    -----
    - `max_combinations` is kept only for backward compatibility with older app.py files.
      It is no longer enforced as a hard cap.
    - `parallel_workers` controls how many parameter combinations are evaluated concurrently.
    - `native_threads_per_worker` caps the BLAS/OpenMP threads used inside each worker to
      avoid oversubscription during parallel scans.
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    base_params = _coerce_nuclei_params(base_params)
    _validate_nuclei_params(base_params)

    safe_output_prefix = str(output_prefix or "nuclei_parameter_sweep").strip() or "nuclei_parameter_sweep"

    ordered_values: Dict[str, List[float]] = {}
    n_combinations = 1
    for field in SWEEP_PARAM_ORDER:
        values = sweep_values.get(field, [getattr(base_params, field)])
        values = [float(v) for v in values]
        if not values:
            values = [float(getattr(base_params, field))]
        ordered_values[field] = values
        n_combinations *= len(values)

    dapi = load_nucleus_channel_image(df_pixels, shapes, image_id, base_params.nucleus_channel)
    dapi_norm = normalize_nucleus_image(dapi)

    safe_backend = str(parallel_backend or "loky").strip().lower()
    if safe_backend not in {"loky", "threading"}:
        safe_backend = "loky"

    try:
        parallel_workers = int(parallel_workers)
    except Exception:
        parallel_workers = 1
    parallel_workers = max(1, min(parallel_workers, int(n_combinations)))

    if native_threads_per_worker is not None:
        try:
            native_threads_per_worker = max(1, int(native_threads_per_worker))
        except Exception:
            native_threads_per_worker = 1

    if parallel_workers > 1 and Parallel is not None and delayed is not None:
        chunk_size = max(1, min(128, math.ceil(n_combinations / max(1, parallel_workers * 4))))
        chunk_results = Parallel(
            n_jobs=parallel_workers,
            backend=safe_backend,
            max_nbytes="16M",
            mmap_mode="r",
            batch_size=1,
            verbose=0,
        )(
            delayed(_evaluate_sweep_combo_chunk)(
                combo_chunk,
                base_params,
                dapi,
                dapi_norm,
                pixel_size_um,
                native_threads=native_threads_per_worker,
            )
            for combo_chunk in _iter_combo_chunks(ordered_values, chunk_size)
        )
        records = [row for chunk in chunk_results for row in chunk]
    else:
        records = _evaluate_sweep_combo_chunk(
            list(_iter_combo_chunks(ordered_values, max(1, int(n_combinations))))[0] if n_combinations > 0 else [],
            base_params,
            dapi,
            dapi_norm,
            pixel_size_um,
            native_threads=native_threads_per_worker,
        )

    df_results = pd.DataFrame(records)
    if len(df_results) > 0 and "combo_index" in df_results.columns:
        df_results = df_results.sort_values("combo_index").reset_index(drop=True)

    csv_path = save_dir / f"{safe_output_prefix}_results.csv"
    json_path = save_dir / f"{safe_output_prefix}_grid.json"
    fig = make_nuclei_parameter_sweep_figure(df_results[df_results["error"].fillna("") == ""])
    svg_path = save_dir / f"{safe_output_prefix}.svg"
    png_path = save_dir / f"{safe_output_prefix}.png"

    if save_outputs:
        df_results.to_csv(csv_path, index=False)
        write_json(
            json_path,
            {
                "nucleus_channel": base_params.nucleus_channel,
                "n_combinations": int(n_combinations),
                "base_params": base_params.to_dict(),
                "candidate_values": {SWEEP_PARAM_LABELS[k]: [float(v) for v in vals] for k, vals in ordered_values.items()},
                "parallel_config": {
                    "parallel_workers": int(parallel_workers),
                    "parallel_backend": safe_backend,
                    "native_threads_per_worker": None if native_threads_per_worker is None else int(native_threads_per_worker),
                    "joblib_available": bool(Parallel is not None and delayed is not None),
                    "threadpoolctl_available": bool(threadpool_limits is not None),
                    "cpu_count": int(DEFAULT_CPU_COUNT),
                },
            },
        )
        fig.savefig(svg_path, dpi=300, bbox_inches="tight")
        fig.savefig(png_path, dpi=300, bbox_inches="tight")

    return {
        "results": df_results,
        "figure": fig,
        "n_combinations": int(n_combinations),
        "candidate_values": {field: [float(v) for v in vals] for field, vals in ordered_values.items()},
        "parallel_config": {
            "parallel_workers": int(parallel_workers),
            "parallel_backend": safe_backend,
            "native_threads_per_worker": None if native_threads_per_worker is None else int(native_threads_per_worker),
            "joblib_available": bool(Parallel is not None and delayed is not None),
            "threadpoolctl_available": bool(threadpool_limits is not None),
            "cpu_count": int(DEFAULT_CPU_COUNT),
        },
        "saved_paths": {
            "csv": csv_path,
            "json": json_path,
            "svg": svg_path,
            "png": png_path,
        },
    }


def run_nuclei_parameter_optimizer(
    df_pixels: pd.DataFrame,
    shapes: Dict[Tuple[str, str], Tuple[int, int]],
    image_id: str,
    save_dir: Path,
    pixel_size_um: Tuple[float, float],
    base_params: NucleiParams,
    search_specs: Dict[str, Dict[str, float]],
    priority_search_specs: Dict[str, Dict[str, float]] | None = None,
    save_outputs: bool = True,
    max_evaluations: int = 512,
    priority_target_evaluations: int | None = None,
    expansion_target_evaluations: int | None = None,
    exhaustive_limit: int = 4096,
    parallel_workers: int = 1,
    parallel_backend: str = "loky",
    native_threads_per_worker: int | None = 1,
    random_seed: int = 42,
    output_prefix: str = "nuclei_auto_optimizer",
    use_fixed_roi_subset: bool = False,
    roi_area_fraction_per_roi: float = 0.02,
    use_vertical_band_subset: bool = False,
    vertical_band_count: int = 10,
    vertical_band_selection_count: int = 5,
    vertical_band_indices: Sequence[int] | None = None,
) -> Dict[str, Any]:
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    base_params = _coerce_nuclei_params(base_params)
    _validate_nuclei_params(base_params)

    search_space_specs = _build_search_space_specs(search_specs)
    safe_output_prefix = str(output_prefix or "nuclei_auto_optimizer").strip() or "nuclei_auto_optimizer"

    full_space_n_combinations = _search_space_n_combinations(search_space_specs)
    priority_space_specs = _intersect_search_space_specs(search_space_specs, priority_search_specs)
    priority_space_n_combinations = (
        _search_space_n_combinations(priority_space_specs) if priority_space_specs is not None else 0
    )

    try:
        exhaustive_limit = max(1, int(exhaustive_limit))
    except Exception:
        exhaustive_limit = 4096
    try:
        max_evaluations = max(1, int(max_evaluations))
    except Exception:
        max_evaluations = 512
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

    dapi = load_nucleus_channel_image(df_pixels, shapes, image_id, base_params.nucleus_channel)
    roi_mode = "full_image"
    roi_metadata: List[Dict[str, Any]] = []
    roi_sampled_area_fraction = 1.0
    roi_mosaic_shape = tuple(int(v) for v in dapi.shape)
    selected_band_indices_out: List[int] = []
    band_count_out = 0
    if use_vertical_band_subset:
        roi_subset = _build_vertical_band_nuclei_subset(
            dapi,
            band_count=vertical_band_count,
            selected_band_indices=vertical_band_indices,
            selected_band_count=vertical_band_selection_count,
        )
        dapi = roi_subset["dapi"]
        roi_mode = "vertical_bands"
        roi_metadata = list(roi_subset.get("roi_metadata", []))
        roi_sampled_area_fraction = float(roi_subset.get("sampled_area_fraction", 1.0))
        roi_mosaic_shape = tuple(int(v) for v in roi_subset.get("mosaic_shape", dapi.shape))
        selected_band_indices_out = [int(v) for v in roi_subset.get("selected_band_indices", [])]
        band_count_out = int(roi_subset.get("band_count", 0))
    elif use_fixed_roi_subset:
        roi_subset = _build_fixed_five_roi_nuclei_subset(
            dapi,
            roi_area_fraction=roi_area_fraction_per_roi,
        )
        dapi = roi_subset["dapi"]
        roi_mode = "fixed_five_2pct_anchors"
        roi_metadata = list(roi_subset.get("roi_metadata", []))
        roi_sampled_area_fraction = float(roi_subset.get("sampled_area_fraction", 1.0))
        roi_mosaic_shape = tuple(int(v) for v in roi_subset.get("mosaic_shape", dapi.shape))
    dapi_norm = normalize_nucleus_image(dapi)
    compute_telemetry_state = {"recorded": False}

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
        all_combos = list(dict.fromkeys(_generate_exhaustive_combo_values(search_space_specs)))
        evaluated_rows = _evaluate_explicit_combo_records(
            all_combos,
            combo_index_start=1,
            base_params=base_params,
            dapi=dapi,
            dapi_norm=dapi_norm,
            pixel_size_um=pixel_size_um,
            parallel_workers=parallel_workers,
            parallel_backend=parallel_backend,
            native_threads_per_worker=native_threads_per_worker,
            compute_telemetry_state=compute_telemetry_state,
        )
        df_results = _deduplicate_nuclei_result_rows(pd.DataFrame(evaluated_rows))
        if len(df_results) > 0:
            df_results = df_results.sort_values("combo_index").reset_index(drop=True)
        evaluated_unique_combinations = int(len(df_results))
    else:
        search_parts: List[pd.DataFrame] = []
        next_combo_index = 1
        priority_ranked_results = pd.DataFrame()

        if priority_space_specs is not None and priority_target_evaluations > 0:
            search_mode = "core_first_expanded_search"
            priority_search_result = _run_budgeted_search_records(
                search_space_specs=priority_space_specs,
                max_evaluations=int(priority_target_evaluations),
                combo_index_start=next_combo_index,
                base_params=base_params,
                dapi=dapi,
                dapi_norm=dapi_norm,
                pixel_size_um=pixel_size_um,
                parallel_workers=parallel_workers,
                parallel_backend=parallel_backend,
                native_threads_per_worker=native_threads_per_worker,
                random_seed=int(random_seed),
                compute_telemetry_state=compute_telemetry_state,
            )
            priority_results = priority_search_result["results"]
            priority_ranked_results = priority_search_result["ranked_results"]
            evaluated_priority_combinations = int(priority_search_result["n_evaluated"])
            if len(priority_results) > 0:
                search_parts.append(priority_results)
                next_combo_index += len(priority_results)

        if expansion_target_evaluations > 0:
            search_mode = "core_first_genetic_successive_halving_expansion"
            expansion_search_result = _run_evolutionary_search_records(
                search_space_specs=search_space_specs,
                max_evaluations=int(expansion_target_evaluations),
                combo_index_start=next_combo_index,
                base_params=base_params,
                dapi=dapi,
                dapi_norm=dapi_norm,
                pixel_size_um=pixel_size_um,
                parallel_workers=parallel_workers,
                parallel_backend=parallel_backend,
                native_threads_per_worker=native_threads_per_worker,
                random_seed=int(random_seed) + 1,
                seed_rows=priority_ranked_results,
                compute_telemetry_state=compute_telemetry_state,
            )
            expansion_results = expansion_search_result["results"]
            evaluated_expansion_combinations = int(expansion_search_result["n_evaluated"])
            expansion_generation_count = int(expansion_search_result.get("n_generations", 0))
            expansion_population_size = int(expansion_search_result.get("population_size", 0))
            expansion_screened_candidate_count = int(expansion_search_result.get("n_screened_candidates", 0))
            expansion_screening_round_count = int(expansion_search_result.get("n_screening_rounds", 0))
            expansion_screening_stage_factors = [
                int(v) for v in expansion_search_result.get("screening_stage_factors", [])
            ]
            if len(expansion_results) > 0:
                search_parts.append(expansion_results)

        if not search_parts:
            fallback_search_result = _run_budgeted_search_records(
                search_space_specs=search_space_specs,
                max_evaluations=int(max_evaluations),
                combo_index_start=1,
                base_params=base_params,
                dapi=dapi,
                dapi_norm=dapi_norm,
                pixel_size_um=pixel_size_um,
                parallel_workers=parallel_workers,
                parallel_backend=parallel_backend,
                native_threads_per_worker=native_threads_per_worker,
                random_seed=int(random_seed),
                compute_telemetry_state=compute_telemetry_state,
            )
            df_results = fallback_search_result["results"]
            evaluated_expansion_combinations = int(fallback_search_result["n_evaluated"])
        else:
            df_results = _deduplicate_nuclei_result_rows(pd.concat(search_parts, ignore_index=True))
            if len(df_results) > 0:
                df_results = df_results.sort_values("combo_index").reset_index(drop=True)
        evaluated_unique_combinations = int(len(df_results))

    results_csv_path = save_dir / f"{safe_output_prefix}_results.csv"
    json_path = save_dir / f"{safe_output_prefix}_grid.json"
    fig = make_nuclei_parameter_sweep_figure(df_results)
    svg_path = save_dir / f"{safe_output_prefix}.svg"
    png_path = save_dir / f"{safe_output_prefix}.png"

    if save_outputs:
        df_results.to_csv(results_csv_path, index=False)
        write_json(
            json_path,
            {
                "nucleus_channel": base_params.nucleus_channel,
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
                    SWEEP_PARAM_LABELS[field]: {
                        "min": float(search_space_specs[field]["min"]),
                        "max": float(search_space_specs[field]["max"]),
                        "step": float(search_space_specs[field]["step"]),
                        "n_values": int(search_space_specs[field]["n_values"]),
                    }
                    for field in SWEEP_PARAM_ORDER
                },
                "priority_search_space": None
                if priority_space_specs is None
                else {
                    SWEEP_PARAM_LABELS[field]: {
                        "min": float(priority_space_specs[field]["min"]),
                        "max": float(priority_space_specs[field]["max"]),
                        "step": float(priority_space_specs[field]["step"]),
                        "n_values": int(priority_space_specs[field]["n_values"]),
                    }
                    for field in SWEEP_PARAM_ORDER
                },
                "optimizer_config": {
                    "random_seed": int(random_seed),
                    "exhaustive_limit": int(exhaustive_limit),
                    "parallel_workers": int(parallel_workers),
                    "parallel_backend": str(parallel_backend),
                    "expansion_search_strategy": "evolutionary_genetic_successive_halving",
                },
                "parallel_config": {
                    "parallel_workers": int(parallel_workers),
                    "parallel_backend": str(parallel_backend),
                    "native_threads_per_worker": None if native_threads_per_worker is None else int(native_threads_per_worker),
                    "joblib_available": bool(Parallel is not None and delayed is not None),
                    "threadpoolctl_available": bool(threadpool_limits is not None),
                    "cpu_count": int(DEFAULT_CPU_COUNT),
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
        "search_space": {
            field: {
                "min": float(search_space_specs[field]["min"]),
                "max": float(search_space_specs[field]["max"]),
                "step": float(search_space_specs[field]["step"]),
                "n_values": int(search_space_specs[field]["n_values"]),
            }
            for field in SWEEP_PARAM_ORDER
        },
        "saved_paths": {
            "results_csv": results_csv_path,
            "csv": results_csv_path,
            "json": json_path,
            "svg": svg_path,
            "png": png_path,
        },
    }
