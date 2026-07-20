"""Optional, deterministic CPU + OpenCL compute runtime for SpatialScope.

The native Windows engine processes one JSON-lines request at a time, while an
optimizer may fan work out to joblib threads.  A :class:`ComputeRuntime` therefore
keeps one process-visible active request (guarded by a lock) instead of relying on
thread-local state.  Every operation remains usable without PyOpenCL and produces
the same NumPy result when OpenCL is unavailable or a device fails.

Only real OpenCL GPU devices are reported.  Win32 display-adapter names are never
treated as evidence that a compute backend is available.
"""

from __future__ import annotations

import atexit
import copy
import math
import os
import threading
import warnings
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence

import numpy as np

try:  # PyOpenCL is deliberately optional; the packaged app keeps a CPU path.
    import pyopencl as _cl  # type: ignore
except Exception as _pyopencl_error:  # pragma: no cover - depends on installation
    _cl = None
    _PYOPENCL_IMPORT_ERROR = f"{type(_pyopencl_error).__name__}: {_pyopencl_error}"
else:  # pragma: no cover - exercised only on hosts with PyOpenCL
    _PYOPENCL_IMPORT_ERROR = ""


_OPENCL_SOURCE = r"""
__kernel void equal_scalar_i32(
    __global const int *values,
    const int scalar,
    __global uchar *output)
{
    const size_t i = get_global_id(0);
    output[i] = values[i] == scalar ? (uchar)1 : (uchar)0;
}

__kernel void lookup_labels_i32(
    __global const int *labels,
    __global const int *lookup,
    const int lookup_size,
    const int default_value,
    __global int *output)
{
    const size_t i = get_global_id(0);
    const int label = labels[i];
    output[i] = (label >= 0 && label < lookup_size) ? lookup[label] : default_value;
}

__kernel void labels_in_set_lookup_i32(
    __global const int *labels,
    __global const uchar *lookup,
    const int lookup_minimum,
    const int lookup_size,
    __global uchar *output)
{
    const size_t i = get_global_id(0);
    const long offset = (long)labels[i] - (long)lookup_minimum;
    output[i] = (offset >= 0 && offset < (long)lookup_size)
        ? lookup[(size_t)offset]
        : (uchar)0;
}

__kernel void labels_in_set_sorted_i32(
    __global const int *labels,
    __global const int *accepted,
    const int accepted_size,
    __global uchar *output)
{
    const size_t i = get_global_id(0);
    const int label = labels[i];
    int low = 0;
    int high = accepted_size;
    while (low < high) {
        const int middle = low + (high - low) / 2;
        if (accepted[middle] < label) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    output[i] = (low < accepted_size && accepted[low] == label)
        ? (uchar)1
        : (uchar)0;
}

__kernel void assignment_base_u16(
    __global const int *labels,
    __global const uchar *positive,
    __global const uchar *voronoi,
    __global const uchar *buffer_zone,
    __global const int *nearest,
    __global ushort *output)
{
    const size_t i = get_global_id(0);
    ushort value = (ushort)0;
    if (positive[i]) {
        if (labels[i] > 0) {
            value = (ushort)labels[i];
        } else if (voronoi[i] && !buffer_zone[i]) {
            value = (ushort)nearest[i];
        }
    }
    output[i] = value;
}

__kernel void expand_tile_grid_i32(
    __global const int *tile_ids,
    const int tile_rows,
    const int tile_cols,
    const int width,
    const int tile_height,
    const int tile_width,
    const int output_offset,
    __global int *output)
{
    const int local_i = (int)get_global_id(0);
    const int i = output_offset + local_i;
    const int y = i / width;
    const int x = i - y * width;
    const int tile_y = min(y / tile_height, tile_rows - 1);
    const int tile_x = min(x / tile_width, tile_cols - 1);
    output[local_i] = tile_ids[tile_y * tile_cols + tile_x];
}

__kernel void normalize_clip_f32(
    __global const float *values,
    const float low,
    const float inverse_span,
    __global float *output)
{
    const size_t i = get_global_id(0);
    const float value = values[i];
    if (isnan(value)) {
        output[i] = value;
    } else {
        output[i] = clamp((value - low) * inverse_span, 0.0f, 1.0f);
    }
}

__kernel void threshold_ge_f32(
    __global const float *values,
    const float threshold,
    __global uchar *output)
{
    const size_t i = get_global_id(0);
    output[i] = values[i] >= threshold ? (uchar)1 : (uchar)0;
}

__kernel void composite_rgb_f32(
    __global const float *base_rgb,
    __global const float *intensity,
    const float color_r,
    const float color_g,
    const float color_b,
    const float weight,
    __global float *output)
{
    const size_t i = get_global_id(0);
    const float scaled = intensity[i] * weight;
    const float colors[3] = {color_r, color_g, color_b};
    for (int channel = 0; channel < 3; ++channel) {
        const size_t index = i * 3 + channel;
        const float layer = clamp(scaled * colors[channel], 0.0f, 1.0f);
        const float combined = 1.0f - (1.0f - base_rgb[index]) * (1.0f - layer);
        output[index] = clamp(combined, 0.0f, 1.0f);
    }
}

__kernel void band_index_f32(
    __global const float *distance,
    __global const uchar *mask,
    const float band_width,
    __global int *output)
{
    const size_t i = get_global_id(0);
    const float value = distance[i];
    output[i] = (mask[i] && isfinite(value))
        ? (int)floor(fmax(value, 0.0f) / band_width)
        : -1;
}
"""


# A byte lookup is substantially faster than a search for dense label IDs, but
# an untrusted/sparse ID range must never cause a multi-gigabyte allocation.
_LABEL_SET_LOOKUP_MAX_BYTES = 8 * 1024 * 1024
_LABEL_SET_LOOKUP_MAX_SPARSITY = 8
_LABEL_SET_LOOKUP_SMALL_RANGE = 4096


def _prepare_label_membership(
    accepted_ids: Iterable[int],
) -> tuple[str, np.ndarray, int]:
    """Return a bounded lookup table or unique sorted IDs for exact membership.

    The returned tuple is ``(mode, values, minimum)``.  ``minimum`` is used only
    for lookup mode.  Sparse or very wide int32 ranges always use binary search,
    so memory is O(number of accepted IDs), never O(ID range).
    """

    if isinstance(accepted_ids, np.ndarray):
        accepted = np.ascontiguousarray(
            np.asarray(accepted_ids, dtype=np.int32)
        ).reshape(-1)
    else:
        accepted = np.ascontiguousarray(
            np.fromiter(accepted_ids, dtype=np.int32)
        ).reshape(-1)
    if accepted.size == 0:
        return "sorted", accepted, 0

    accepted = np.ascontiguousarray(np.unique(accepted), dtype=np.int32)
    minimum = int(accepted[0])
    span = int(accepted[-1]) - minimum + 1
    dense_limit = max(
        _LABEL_SET_LOOKUP_SMALL_RANGE,
        int(accepted.size) * _LABEL_SET_LOOKUP_MAX_SPARSITY,
    )
    if span <= _LABEL_SET_LOOKUP_MAX_BYTES and span <= dense_limit:
        lookup = np.zeros(span, dtype=np.uint8)
        offsets = accepted.astype(np.int64) - np.int64(minimum)
        lookup[offsets] = np.uint8(1)
        return "lookup", np.ascontiguousarray(lookup), minimum

    return "sorted", accepted, 0


def _clean_error(exc: BaseException, limit: int = 320) -> str:
    message = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {message}"[:limit]


def _device_attr(device: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(device, name)
    except Exception:
        return default


def _platform_attr(platform: Any, name: str, default: str = "") -> str:
    try:
        return str(getattr(platform, name) or default).strip()
    except Exception:
        return default


def _discover_opencl_gpu_records() -> tuple[list[dict[str, Any]], list[str]]:
    if _cl is None:
        return [], [f"PyOpenCL is unavailable: {_PYOPENCL_IMPORT_ERROR}"]

    records: list[dict[str, Any]] = []
    reasons: list[str] = []
    try:
        platforms = list(_cl.get_platforms())
    except Exception as exc:  # pragma: no cover - driver dependent
        return [], [f"OpenCL platform discovery failed: {_clean_error(exc)}"]

    for platform_index, platform in enumerate(platforms):
        platform_name = _platform_attr(platform, "name", f"Platform {platform_index}")
        try:
            devices = list(platform.get_devices(device_type=_cl.device_type.GPU))
        except Exception as exc:  # CL_DEVICE_NOT_FOUND is normal on a CPU-only ICD.
            error_code = getattr(exc, "code", None)
            device_not_found = getattr(getattr(_cl, "status_code", None), "DEVICE_NOT_FOUND", None)
            if error_code != device_not_found:
                reasons.append(
                    f"OpenCL GPU discovery failed on {platform_name}: {_clean_error(exc)}"
                )
            continue

        for device_index, device in enumerate(devices):
            name = str(_device_attr(device, "name", f"GPU {device_index}")).strip()
            available = bool(_device_attr(device, "available", True))
            compiler_available = bool(_device_attr(device, "compiler_available", True))
            descriptor = {
                "id": f"opencl:{platform_index}:{device_index}",
                "backend": "OpenCL",
                "deviceType": "GPU",
                "name": name,
                "vendor": str(_device_attr(device, "vendor", "")).strip(),
                "driverVersion": str(_device_attr(device, "driver_version", "")).strip(),
                "openclVersion": str(_device_attr(device, "version", "")).strip(),
                "platformName": platform_name,
                "platformVendor": _platform_attr(platform, "vendor"),
                "globalMemoryBytes": int(_device_attr(device, "global_mem_size", 0) or 0),
                "maxComputeUnits": int(_device_attr(device, "max_compute_units", 0) or 0),
                "available": available,
                "compilerAvailable": compiler_available,
                "compatible": available and compiler_available,
                "compatibilityError": "",
            }
            if not available:
                descriptor["compatibilityError"] = "The OpenCL device is not available."
            elif not compiler_available:
                descriptor["compatibilityError"] = "The OpenCL device compiler is unavailable."
            records.append({"descriptor": descriptor, "device": device})
    return records, reasons


def enumerate_opencl_gpus() -> list[dict[str, Any]]:
    """Return descriptors for GPU devices exposed by actual OpenCL platforms.

    This read-only discovery function does not create contexts or claim that a
    kernel was successfully built.  ``ComputeRuntime.device_descriptors()`` adds
    that compatibility probe.
    """

    records, _ = _discover_opencl_gpu_records()
    return [copy.deepcopy(record["descriptor"]) for record in records]


def _readonly_buffer(context: Any, values: np.ndarray) -> Any:
    return _cl.Buffer(  # type: ignore[union-attr]
        context,
        _cl.mem_flags.READ_ONLY | _cl.mem_flags.COPY_HOST_PTR,  # type: ignore[union-attr]
        hostbuf=np.ascontiguousarray(values),
    )


class _OpenCLWorker:
    """One serial executor owns one device context, queue, and compiled program."""

    def __init__(self, descriptor: dict[str, Any], device: Any):
        self.descriptor = descriptor
        self._device = device
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"spatialscope-{descriptor['id'].replace(':', '-')}",
        )
        self._state: tuple[Any, Any, Any] | None = None
        self._kernels: dict[str, Any] = {}
        self._closed = False

    def _ensure_state(self) -> tuple[Any, Any, Any]:
        if self._state is None:
            context = _cl.Context(devices=[self._device])  # type: ignore[union-attr]
            queue = _cl.CommandQueue(context, device=self._device)  # type: ignore[union-attr]
            # Some Windows ICDs print a non-empty informational build log even
            # when compilation succeeds.  Preserve build exceptions, but keep
            # that PyOpenCL-only warning out of the JSON-lines engine stderr.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", _cl.CompilerWarning)  # type: ignore[union-attr]
                program = _cl.Program(context, _OPENCL_SOURCE).build()  # type: ignore[union-attr]
            self._state = (context, queue, program)
        return self._state

    def probe(self) -> None:
        payload = {"values": np.array([0, 1], dtype=np.int32), "scalar": 1}
        result = self.submit("equalScalar", payload).result()
        if not np.array_equal(result, np.array([False, True])):
            raise RuntimeError("OpenCL compatibility probe returned an invalid result.")

    def submit(self, operation: str, payload: Mapping[str, Any]) -> Future[np.ndarray]:
        if self._closed:
            raise RuntimeError("OpenCL worker is closed.")
        return self._executor.submit(self._execute, operation, dict(payload))

    def _execute(self, operation: str, payload: dict[str, Any]) -> np.ndarray:
        context, queue, program = self._ensure_state()
        memory_flags = _cl.mem_flags  # type: ignore[union-attr]

        def kernel(name: str) -> Any:
            cached = self._kernels.get(name)
            if cached is None:
                cached = _cl.Kernel(program, name)  # type: ignore[union-attr]
                self._kernels[name] = cached
            return cached

        def output_buffer(result: np.ndarray) -> Any:
            return _cl.Buffer(context, memory_flags.WRITE_ONLY, result.nbytes)  # type: ignore[union-attr]

        if operation == "equalScalar":
            values = np.ascontiguousarray(payload["values"], dtype=np.int32)
            result = np.empty(values.size, dtype=np.uint8)
            out = output_buffer(result)
            kernel("equal_scalar_i32")(
                queue, (values.size,), None, _readonly_buffer(context, values),
                np.int32(payload["scalar"]), out,
            )
        elif operation == "lookupLabels":
            labels = np.ascontiguousarray(payload["labels"], dtype=np.int32)
            lookup = np.ascontiguousarray(payload["lookup"], dtype=np.int32)
            lookup_buffer = lookup if lookup.size else np.zeros(1, dtype=np.int32)
            result = np.empty(labels.size, dtype=np.int32)
            out = output_buffer(result)
            kernel("lookup_labels_i32")(
                queue, (labels.size,), None, _readonly_buffer(context, labels),
                _readonly_buffer(context, lookup_buffer), np.int32(lookup.size),
                np.int32(payload["default"]), out,
            )
        elif operation == "labelsInSet":
            labels = np.ascontiguousarray(payload["labels"], dtype=np.int32)
            result = np.empty(labels.size, dtype=np.uint8)
            out = output_buffer(result)
            membership_mode = str(payload["membershipMode"])
            if membership_mode == "lookup":
                lookup = np.ascontiguousarray(payload["membership"], dtype=np.uint8)
                kernel("labels_in_set_lookup_i32")(
                    queue, (labels.size,), None, _readonly_buffer(context, labels),
                    _readonly_buffer(context, lookup), np.int32(payload["membershipMinimum"]),
                    np.int32(lookup.size), out,
                )
            elif membership_mode == "sorted":
                accepted = np.ascontiguousarray(payload["membership"], dtype=np.int32)
                # OpenCL buffers cannot have zero bytes.  The size argument
                # preserves empty-set behavior while this allocation stays inert.
                accepted_buffer = accepted if accepted.size else np.zeros(1, dtype=np.int32)
                kernel("labels_in_set_sorted_i32")(
                    queue, (labels.size,), None, _readonly_buffer(context, labels),
                    _readonly_buffer(context, accepted_buffer), np.int32(accepted.size), out,
                )
            else:  # pragma: no cover - payloads are prepared by labels_in_set
                raise ValueError(f"Unsupported label membership mode: {membership_mode}")
        elif operation == "assignmentBase":
            labels = np.ascontiguousarray(payload["labels"], dtype=np.int32)
            positive = np.ascontiguousarray(payload["positive"], dtype=np.uint8)
            voronoi = np.ascontiguousarray(payload["voronoi"], dtype=np.uint8)
            buffer_zone = np.ascontiguousarray(payload["buffer"], dtype=np.uint8)
            nearest = np.ascontiguousarray(payload["nearest"], dtype=np.int32)
            result = np.empty(labels.size, dtype=np.uint16)
            out = output_buffer(result)
            kernel("assignment_base_u16")(
                queue, (labels.size,), None,
                _readonly_buffer(context, labels), _readonly_buffer(context, positive),
                _readonly_buffer(context, voronoi), _readonly_buffer(context, buffer_zone),
                _readonly_buffer(context, nearest), out,
            )
        elif operation == "expandTileGrid":
            tile_ids = np.ascontiguousarray(payload["tileIds"], dtype=np.int32)
            size = int(payload["size"])
            result = np.empty(size, dtype=np.int32)
            out = output_buffer(result)
            kernel("expand_tile_grid_i32")(
                queue, (size,), None, _readonly_buffer(context, tile_ids),
                np.int32(payload["tileRows"]), np.int32(payload["tileCols"]),
                np.int32(payload["width"]), np.int32(payload["tileHeight"]),
                np.int32(payload["tileWidth"]), np.int32(payload["offset"]), out,
            )
        elif operation == "normalizeClip":
            values = np.ascontiguousarray(payload["values"], dtype=np.float32)
            result = np.empty(values.size, dtype=np.float32)
            out = output_buffer(result)
            kernel("normalize_clip_f32")(
                queue, (values.size,), None, _readonly_buffer(context, values),
                np.float32(payload["low"]), np.float32(payload["inverseSpan"]), out,
            )
        elif operation == "thresholdGe":
            values = np.ascontiguousarray(payload["values"], dtype=np.float32)
            result = np.empty(values.size, dtype=np.uint8)
            out = output_buffer(result)
            kernel("threshold_ge_f32")(
                queue, (values.size,), None, _readonly_buffer(context, values),
                np.float32(payload["threshold"]), out,
            )
        elif operation == "compositeRgb":
            base = np.ascontiguousarray(payload["base"], dtype=np.float32)
            intensity = np.ascontiguousarray(payload["intensity"], dtype=np.float32)
            color = np.asarray(payload["color"], dtype=np.float32)
            result = np.empty_like(base, dtype=np.float32)
            out = output_buffer(result)
            kernel("composite_rgb_f32")(
                queue, (intensity.size,), None, _readonly_buffer(context, base),
                _readonly_buffer(context, intensity), np.float32(color[0]),
                np.float32(color[1]), np.float32(color[2]),
                np.float32(payload["weight"]), out,
            )
        elif operation == "bandIndex":
            distance = np.ascontiguousarray(payload["distance"], dtype=np.float32)
            mask = np.ascontiguousarray(payload["mask"], dtype=np.uint8)
            result = np.empty(distance.size, dtype=np.int32)
            out = output_buffer(result)
            kernel("band_index_f32")(
                queue, (distance.size,), None, _readonly_buffer(context, distance),
                _readonly_buffer(context, mask), np.float32(payload["bandWidth"]), out,
            )
        else:  # pragma: no cover - guarded by public methods
            raise KeyError(f"Unsupported OpenCL operation: {operation}")

        _cl.enqueue_copy(queue, result, out).wait()  # type: ignore[union-attr]
        if operation in {"equalScalar", "labelsInSet", "thresholdGe"}:
            return result.astype(bool)
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._state = None
        self._kernels.clear()


class _CpuWorker:
    """One persistent thread owns one logical CPU worker lane.

    A shared :class:`ThreadPoolExecutor` may execute several submitted chunks on
    one fast thread while its other threads never start.  Keeping one serial
    executor per lane makes lane participation deterministic: a completed chunk
    identifies the lane and the native thread that actually produced it.
    """

    def __init__(self, lane_id: int):
        self.lane_id = int(lane_id)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"spatialscope-cpu-{self.lane_id}",
        )
        self._closed = False

    def submit(
        self,
        operation: str,
        payload: Mapping[str, Any],
    ) -> Future[tuple[np.ndarray, int, int, str]]:
        if self._closed:
            raise RuntimeError("CPU worker lane is closed.")
        return self._executor.submit(self._execute, operation, dict(payload))

    def _execute(
        self,
        operation: str,
        payload: Mapping[str, Any],
    ) -> tuple[np.ndarray, int, int, str]:
        result = _cpu_operation(operation, payload)
        current = threading.current_thread()
        return result, self.lane_id, threading.get_native_id(), current.name

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)


def _cpu_operation(operation: str, payload: Mapping[str, Any]) -> np.ndarray:
    if operation == "equalScalar":
        return np.asarray(payload["values"], dtype=np.int32) == np.int32(payload["scalar"])
    if operation == "lookupLabels":
        labels = np.asarray(payload["labels"], dtype=np.int32)
        lookup = np.asarray(payload["lookup"], dtype=np.int32)
        result = np.full(labels.shape, np.int32(payload["default"]), dtype=np.int32)
        valid = (labels >= 0) & (labels < lookup.size)
        result[valid] = lookup[labels[valid]]
        return result
    if operation == "labelsInSet":
        labels = np.asarray(payload["labels"], dtype=np.int32)
        membership_mode = str(payload["membershipMode"])
        membership = np.asarray(payload["membership"])
        result = np.zeros(labels.shape, dtype=bool)
        if membership_mode == "lookup":
            minimum = int(payload["membershipMinimum"])
            maximum = minimum + int(membership.size) - 1
            valid = (labels >= minimum) & (labels <= maximum)
            if np.any(valid):
                indexes = labels[valid].astype(np.int64) - np.int64(minimum)
                result[valid] = np.asarray(membership, dtype=np.uint8)[indexes] != 0
            return result
        if membership_mode == "sorted":
            accepted = np.asarray(membership, dtype=np.int32)
            if accepted.size:
                indexes = np.searchsorted(accepted, labels)
                valid = indexes < accepted.size
                result[valid] = accepted[indexes[valid]] == labels[valid]
            return result
        raise ValueError(f"Unsupported label membership mode: {membership_mode}")
    if operation == "assignmentBase":
        labels = np.asarray(payload["labels"], dtype=np.int32)
        positive = np.asarray(payload["positive"], dtype=bool)
        voronoi = np.asarray(payload["voronoi"], dtype=bool)
        buffer_zone = np.asarray(payload["buffer"], dtype=bool)
        nearest = np.asarray(payload["nearest"], dtype=np.int32)
        result = np.zeros(labels.shape, dtype=np.uint16)
        inside = positive & (labels > 0)
        result[inside] = labels[inside].astype(np.uint16)
        nonbuffer = positive & (labels <= 0) & voronoi & ~buffer_zone
        result[nonbuffer] = nearest[nonbuffer].astype(np.uint16)
        return result
    if operation == "expandTileGrid":
        tile_ids = np.asarray(payload["tileIds"], dtype=np.int32)
        size = int(payload["size"])
        width = int(payload["width"])
        offset = int(payload["offset"])
        flat_indices = np.arange(offset, offset + size, dtype=np.int64)
        ys = flat_indices // width
        xs = flat_indices - ys * width
        tile_ys = np.minimum(ys // int(payload["tileHeight"]), int(payload["tileRows"]) - 1)
        tile_xs = np.minimum(xs // int(payload["tileWidth"]), int(payload["tileCols"]) - 1)
        return tile_ids[tile_ys, tile_xs].astype(np.int32, copy=False)
    if operation == "normalizeClip":
        values = np.asarray(payload["values"], dtype=np.float32)
        result = (values - np.float32(payload["low"])) * np.float32(payload["inverseSpan"])
        return np.clip(result, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)
    if operation == "thresholdGe":
        return np.asarray(payload["values"], dtype=np.float32) >= np.float32(payload["threshold"])
    if operation == "compositeRgb":
        base = np.asarray(payload["base"], dtype=np.float32)
        intensity = np.asarray(payload["intensity"], dtype=np.float32)
        color = np.asarray(payload["color"], dtype=np.float32)
        scaled = intensity[:, None] * np.float32(payload["weight"])
        layer = np.clip(scaled * color[None, :], np.float32(0.0), np.float32(1.0))
        result = np.float32(1.0) - (np.float32(1.0) - base) * (np.float32(1.0) - layer)
        return np.clip(result, np.float32(0.0), np.float32(1.0)).astype(np.float32, copy=False)
    if operation == "bandIndex":
        distance = np.asarray(payload["distance"], dtype=np.float32)
        mask = np.asarray(payload["mask"], dtype=bool)
        result = np.full(distance.shape, -1, dtype=np.int32)
        valid = mask & np.isfinite(distance)
        result[valid] = np.floor(
            np.maximum(distance[valid], np.float32(0.0)) / np.float32(payload["bandWidth"])
        ).astype(np.int32)
        return result
    raise KeyError(f"Unsupported CPU operation: {operation}")


def _chunk_counts(total: int, lane_weights: Sequence[int]) -> list[int]:
    """Allocate deterministic non-empty chunks, then distribute weighted remainder."""

    if total <= 0 or not lane_weights:
        return []
    active_weights = list(lane_weights[: min(total, len(lane_weights))])
    counts = [1] * len(active_weights)
    remaining = total - len(active_weights)
    if remaining <= 0:
        return counts
    weight_sum = float(sum(active_weights))
    exact = [remaining * weight / weight_sum for weight in active_weights]
    floors = [int(math.floor(value)) for value in exact]
    counts = [count + extra for count, extra in zip(counts, floors)]
    leftover = remaining - sum(floors)
    order = sorted(
        range(len(active_weights)),
        key=lambda index: (-(exact[index] - floors[index]), index),
    )
    for index in order[:leftover]:
        counts[index] += 1
    return counts


def _normalise_parity_mode(value: str | None) -> str:
    mode = str(value or "off").strip().lower()
    if mode not in {"off", "sample", "full"}:
        raise ValueError("parity_mode must be 'off', 'sample', or 'full'.")
    return mode


class ComputeRequest(AbstractContextManager["ComputeRequest"]):
    """Request-scoped operations and bounded, thread-safe usage telemetry."""

    _MAX_FALLBACK_REASONS = 32

    def __init__(self, runtime: "ComputeRuntime", request_id: str, parity_mode: str):
        self.runtime = runtime
        self.request_id = str(request_id)
        self.parity_mode = _normalise_parity_mode(parity_mode)
        self._lock = threading.RLock()
        self._closed = False
        self._entered = False
        self._operations: dict[str, dict[str, Any]] = {}
        self._devices: dict[str, dict[str, Any]] = {}
        self._fallback_reasons: list[str] = []
        for reason in runtime._base_fallback_reasons:
            self._add_fallback(reason)

    def __enter__(self) -> "ComputeRequest":
        if not self._entered:
            self.runtime._activate(self)
            self._entered = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _add_fallback(self, reason: str) -> None:
        reason = str(reason).strip()
        if not reason:
            return
        with self._lock:
            if reason not in self._fallback_reasons and len(self._fallback_reasons) < self._MAX_FALLBACK_REASONS:
                self._fallback_reasons.append(reason)

    def _record(
        self,
        operation: str,
        element_count: int,
        stats: Mapping[str, Any],
        parity_checked: bool,
        parity_passed: bool | None,
        max_abs_error: float | None,
        gpu_output_used: bool,
    ) -> None:
        with self._lock:
            aggregate = self._operations.setdefault(
                operation,
                {
                    "calls": 0,
                    "elements": 0,
                    "cpuWorkUnits": 0,
                    "cpuElements": 0,
                    "cpuWorkers": {},
                    "gpuWorkUnits": 0,
                    "gpuElements": 0,
                    "gpuOutputElements": 0,
                    "parityChecks": 0,
                    "parityFailures": 0,
                    "maxAbsError": 0.0,
                },
            )
            aggregate["calls"] += 1
            aggregate["elements"] += int(element_count)
            aggregate["cpuWorkUnits"] += int(stats.get("cpuWorkUnits", 0))
            aggregate["cpuElements"] += int(stats.get("cpuElements", 0))
            for worker_key, values in dict(stats.get("cpuWorkers", {})).items():
                worker = aggregate["cpuWorkers"].setdefault(
                    worker_key,
                    {
                        "laneId": int(values["laneId"]),
                        "threadId": int(values["threadId"]),
                        "threadName": str(values["threadName"]),
                        "workUnits": 0,
                        "elements": 0,
                    },
                )
                worker["workUnits"] += int(values.get("workUnits", 0))
                worker["elements"] += int(values.get("elements", 0))
            if parity_checked:
                aggregate["parityChecks"] += 1
                if parity_passed is False:
                    aggregate["parityFailures"] += 1
                if max_abs_error is not None and np.isfinite(max_abs_error):
                    aggregate["maxAbsError"] = max(float(aggregate["maxAbsError"]), float(max_abs_error))

            for device_id, values in dict(stats.get("devices", {})).items():
                work_units = int(values.get("workUnits", 0))
                elements = int(values.get("elements", 0))
                attempted_units = int(values.get("attemptedWorkUnits", work_units))
                attempted_elements = int(values.get("attemptedElements", elements))
                aggregate["gpuWorkUnits"] += work_units
                aggregate["gpuElements"] += elements
                if gpu_output_used:
                    aggregate["gpuOutputElements"] += elements
                descriptor = values.get("descriptor", {})
                device = self._devices.setdefault(
                    device_id,
                    {
                        "id": device_id,
                        "name": descriptor.get("name", device_id),
                        "vendor": descriptor.get("vendor", ""),
                        "platformName": descriptor.get("platformName", ""),
                        "workUnits": 0,
                        "elements": 0,
                        "outputElements": 0,
                        "attemptedWorkUnits": 0,
                        "attemptedElements": 0,
                    },
                )
                device["workUnits"] += work_units
                device["elements"] += elements
                device["attemptedWorkUnits"] += attempted_units
                device["attemptedElements"] += attempted_elements
                if gpu_output_used:
                    device["outputElements"] += elements

            for reason in stats.get("fallbackReasons", []):
                self._add_fallback(str(reason))

    def _execute(
        self,
        operation: str,
        element_count: int,
        output_tail: tuple[int, ...],
        output_dtype: np.dtype[Any],
        payload_builder: Callable[[int, int], Mapping[str, Any]],
        full_cpu_result: Callable[[], np.ndarray],
        output_shape: tuple[int, ...],
        float_tolerance: tuple[float, float] = (0.0, 0.0),
    ) -> np.ndarray:
        if self._closed:
            raise RuntimeError("Compute request is closed.")
        result, stats = self.runtime._schedule(
            operation=operation,
            element_count=element_count,
            output_tail=output_tail,
            output_dtype=output_dtype,
            payload_builder=payload_builder,
        )
        result = result.reshape(output_shape)
        gpu_elements = sum(int(value.get("elements", 0)) for value in stats["devices"].values())
        parity_checked = gpu_elements > 0 and self.parity_mode != "off"
        parity_passed: bool | None = None
        max_abs_error: float | None = None
        gpu_output_used = gpu_elements > 0

        if parity_checked:
            reference = np.asarray(full_cpu_result()).reshape(output_shape)
            actual_for_check = result
            reference_for_check = reference
            if self.parity_mode == "sample" and result.size > 4096:
                indexes = np.linspace(0, result.size - 1, 4096, dtype=np.int64)
                actual_for_check = result.reshape(-1)[indexes]
                reference_for_check = reference.reshape(-1)[indexes]
            if np.issubdtype(result.dtype, np.floating):
                atol, rtol = float_tolerance
                parity_passed = bool(
                    np.allclose(actual_for_check, reference_for_check, atol=atol, rtol=rtol, equal_nan=True)
                )
                finite = np.isfinite(actual_for_check) & np.isfinite(reference_for_check)
                if np.any(finite):
                    max_abs_error = float(
                        np.max(np.abs(actual_for_check[finite] - reference_for_check[finite]))
                    )
                else:
                    max_abs_error = 0.0
            else:
                parity_passed = bool(np.array_equal(actual_for_check, reference_for_check))
                max_abs_error = 0.0 if parity_passed else None
            if not parity_passed:
                result = reference
                gpu_output_used = False
                self._add_fallback(
                    f"{operation} OpenCL parity check failed; returned the exact CPU result."
                )

        self._record(
            operation,
            element_count,
            stats,
            parity_checked,
            parity_passed,
            max_abs_error,
            gpu_output_used,
        )
        return result

    def equal_scalar(self, label_image: np.ndarray, scalar: int) -> np.ndarray:
        values = np.ascontiguousarray(np.asarray(label_image, dtype=np.int32)).reshape(-1)
        shape = np.shape(label_image)
        build = lambda start, end: {"values": values[start:end], "scalar": int(scalar)}
        return self._execute(
            "equalScalar", values.size, (), np.dtype(bool), build,
            lambda: _cpu_operation("equalScalar", build(0, values.size)), shape,
        )

    def lookup_labels(
        self,
        labels: np.ndarray,
        lookup: Sequence[int] | np.ndarray,
        default: int = 0,
    ) -> np.ndarray:
        flat = np.ascontiguousarray(np.asarray(labels, dtype=np.int32)).reshape(-1)
        lookup_array = np.ascontiguousarray(np.asarray(lookup, dtype=np.int32)).reshape(-1)
        shape = np.shape(labels)
        build = lambda start, end: {
            "labels": flat[start:end], "lookup": lookup_array, "default": int(default)
        }
        return self._execute(
            "lookupLabels", flat.size, (), np.dtype(np.int32), build,
            lambda: _cpu_operation("lookupLabels", build(0, flat.size)), shape,
        )

    def labels_in_set(self, labels: np.ndarray, accepted_ids: Iterable[int]) -> np.ndarray:
        flat = np.ascontiguousarray(np.asarray(labels, dtype=np.int32)).reshape(-1)
        membership_mode, membership, membership_minimum = _prepare_label_membership(accepted_ids)
        shape = np.shape(labels)
        build = lambda start, end: {
            "labels": flat[start:end],
            "membershipMode": membership_mode,
            "membership": membership,
            "membershipMinimum": membership_minimum,
        }
        return self._execute(
            "labelsInSet", flat.size, (), np.dtype(bool), build,
            lambda: _cpu_operation("labelsInSet", build(0, flat.size)), shape,
        )

    def assignment_base(
        self,
        labels: np.ndarray,
        positive_mask: np.ndarray,
        voronoi_band: np.ndarray,
        buffer_zone: np.ndarray,
        nearest_labels: np.ndarray,
    ) -> np.ndarray:
        shape = np.shape(labels)
        arrays = [np.asarray(value) for value in (positive_mask, voronoi_band, buffer_zone, nearest_labels)]
        if any(array.shape != shape for array in arrays):
            raise ValueError("assignment_base inputs must all have the same shape.")
        flat_labels = np.ascontiguousarray(np.asarray(labels, dtype=np.int32)).reshape(-1)
        positive = np.ascontiguousarray(np.asarray(positive_mask, dtype=np.uint8)).reshape(-1)
        voronoi = np.ascontiguousarray(np.asarray(voronoi_band, dtype=np.uint8)).reshape(-1)
        buffer_values = np.ascontiguousarray(np.asarray(buffer_zone, dtype=np.uint8)).reshape(-1)
        nearest = np.ascontiguousarray(np.asarray(nearest_labels, dtype=np.int32)).reshape(-1)
        build = lambda start, end: {
            "labels": flat_labels[start:end], "positive": positive[start:end],
            "voronoi": voronoi[start:end], "buffer": buffer_values[start:end],
            "nearest": nearest[start:end],
        }
        return self._execute(
            "assignmentBase", flat_labels.size, (), np.dtype(np.uint16), build,
            lambda: _cpu_operation("assignmentBase", build(0, flat_labels.size)), shape,
        )

    def expand_tile_grid(
        self,
        tile_ids: np.ndarray,
        height: int,
        width: int,
        tile_h: int,
        tile_w: int,
    ) -> np.ndarray:
        tiles = np.ascontiguousarray(np.asarray(tile_ids, dtype=np.int32))
        if tiles.ndim != 2 or tiles.shape[0] <= 0 or tiles.shape[1] <= 0:
            raise ValueError("tile_ids must be a non-empty 2D array.")
        height, width, tile_h, tile_w = map(int, (height, width, tile_h, tile_w))
        if min(height, width, tile_h, tile_w) <= 0:
            raise ValueError("height, width, tile_h, and tile_w must be positive.")
        expected_rows = int(math.ceil(height / float(tile_h)))
        expected_cols = int(math.ceil(width / float(tile_w)))
        if tiles.shape[0] < expected_rows or tiles.shape[1] < expected_cols:
            raise ValueError(
                f"tile_ids shape {tiles.shape} cannot cover {height}x{width} pixels "
                f"with {tile_h}x{tile_w} tiles."
            )
        element_count = height * width

        def build(start: int, end: int) -> Mapping[str, Any]:
            return {
                "tileIds": tiles,
                "tileRows": int(tiles.shape[0]),
                "tileCols": int(tiles.shape[1]),
                "height": height,
                "width": width,
                "tileHeight": tile_h,
                "tileWidth": tile_w,
                "offset": start,
                "size": end - start,
            }

        return self._execute(
            "expandTileGrid", element_count, (), np.dtype(np.int32), build,
            lambda: _cpu_operation("expandTileGrid", build(0, element_count)), (height, width),
        )

    def normalize_clip(self, values: np.ndarray, low_value: float, high_value: float) -> np.ndarray:
        flat = np.ascontiguousarray(np.asarray(values, dtype=np.float32)).reshape(-1)
        shape = np.shape(values)
        low = np.float32(low_value)
        high = np.float32(high_value)
        if not np.isfinite(low) or not np.isfinite(high) or high <= low:
            result = np.zeros(shape, dtype=np.float32)
            self._add_fallback("normalizeClip used zeros because its finite high value was not above low.")
            return result
        inverse_span = np.float32(1.0) / np.float32(high - low)
        build = lambda start, end: {
            "values": flat[start:end], "low": low, "inverseSpan": inverse_span
        }
        return self._execute(
            "normalizeClip", flat.size, (), np.dtype(np.float32), build,
            lambda: _cpu_operation("normalizeClip", build(0, flat.size)), shape,
            float_tolerance=(2e-6, 2e-6),
        )

    def threshold_ge(self, values: np.ndarray, threshold: float) -> np.ndarray:
        flat = np.ascontiguousarray(np.asarray(values, dtype=np.float32)).reshape(-1)
        shape = np.shape(values)
        build = lambda start, end: {"values": flat[start:end], "threshold": np.float32(threshold)}
        return self._execute(
            "thresholdGe", flat.size, (), np.dtype(bool), build,
            lambda: _cpu_operation("thresholdGe", build(0, flat.size)), shape,
        )

    def composite_rgb(
        self,
        base_rgb: np.ndarray,
        intensity: np.ndarray,
        color_rgb: Sequence[float],
        weight: float = 1.0,
    ) -> np.ndarray:
        base = np.asarray(base_rgb, dtype=np.float32)
        image = np.asarray(intensity, dtype=np.float32)
        if base.shape != image.shape + (3,):
            raise ValueError("base_rgb must have shape intensity.shape + (3,).")
        color = np.asarray(color_rgb, dtype=np.float32).reshape(-1)
        if color.size != 3:
            raise ValueError("color_rgb must contain exactly three values.")
        base_pixels = np.ascontiguousarray(base).reshape(-1, 3)
        flat_image = np.ascontiguousarray(image).reshape(-1)
        build = lambda start, end: {
            "base": base_pixels[start:end], "intensity": flat_image[start:end],
            "color": color, "weight": np.float32(weight),
        }
        return self._execute(
            "compositeRgb", flat_image.size, (3,), np.dtype(np.float32), build,
            lambda: _cpu_operation("compositeRgb", build(0, flat_image.size)), base.shape,
            float_tolerance=(3e-6, 3e-6),
        )

    def band_index(
        self,
        distance: np.ndarray,
        mask: np.ndarray,
        band_width: float,
    ) -> np.ndarray:
        if np.shape(distance) != np.shape(mask):
            raise ValueError("distance and mask must have the same shape.")
        if not np.isfinite(band_width) or float(band_width) <= 0:
            raise ValueError("band_width must be finite and greater than zero.")
        flat_distance = np.ascontiguousarray(np.asarray(distance, dtype=np.float32)).reshape(-1)
        flat_mask = np.ascontiguousarray(np.asarray(mask, dtype=np.uint8)).reshape(-1)
        shape = np.shape(distance)
        build = lambda start, end: {
            "distance": flat_distance[start:end], "mask": flat_mask[start:end],
            "bandWidth": np.float32(band_width),
        }
        return self._execute(
            "bandIndex", flat_distance.size, (), np.dtype(np.int32), build,
            lambda: _cpu_operation("bandIndex", build(0, flat_distance.size)), shape,
        )

    def telemetry(self) -> dict[str, Any]:
        with self._lock:
            operations = copy.deepcopy(self._operations)
            devices = sorted(copy.deepcopy(list(self._devices.values())), key=lambda value: value["id"])
            cpu_work_units = sum(int(value["cpuWorkUnits"]) for value in operations.values())
            cpu_elements = sum(int(value["cpuElements"]) for value in operations.values())
            cpu_workers: dict[str, dict[str, Any]] = {}
            for operation in operations.values():
                operation_workers = operation.pop("cpuWorkers", {})
                for worker_key, values in operation_workers.items():
                    worker = cpu_workers.setdefault(
                        worker_key,
                        {
                            "laneId": int(values["laneId"]),
                            "threadId": int(values["threadId"]),
                            "threadName": str(values["threadName"]),
                            "workUnits": 0,
                            "elements": 0,
                        },
                    )
                    worker["workUnits"] += int(values.get("workUnits", 0))
                    worker["elements"] += int(values.get("elements", 0))
                worker_details = sorted(
                    copy.deepcopy(list(operation_workers.values())),
                    key=lambda value: (int(value["laneId"]), int(value["threadId"])),
                )
                operation["cpuWorkersUsed"] = len(worker_details)
                operation["cpuWorkerDetails"] = worker_details
            cpu_worker_details = sorted(
                cpu_workers.values(),
                key=lambda value: (int(value["laneId"]), int(value["threadId"])),
            )
            gpu_output_elements = sum(int(value["outputElements"]) for value in devices)
            return {
                "requestId": self.request_id,
                "backend": "OpenCL+CPU" if gpu_output_elements > 0 else "CPU",
                "parityMode": self.parity_mode,
                "cpuWorkersConfigured": self.runtime.cpu_workers,
                "cpuWorkersUsed": len(cpu_worker_details),
                "cpuWorkerDetails": cpu_worker_details,
                "cpuWorkUnits": cpu_work_units,
                "cpuElements": cpu_elements,
                "gpuDevicesDetected": self.runtime.device_descriptors(),
                "gpuDevicesUsed": devices,
                "gpuOutputElements": gpu_output_elements,
                "operations": operations,
                "fallbackReasons": list(self._fallback_reasons),
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._entered:
            self.runtime._deactivate(self)
        self.runtime._publish_telemetry(self.telemetry())


class ComputeRuntime:
    """Own CPU workers and one safe serial worker for every compatible OpenCL GPU."""

    def __init__(
        self,
        cpu_workers: int | None = None,
        enable_gpu: bool = True,
        parity_mode: str = "off",
        probe_devices: bool = True,
    ):
        logical_cpus = int(os.cpu_count() or 1)
        self.cpu_workers = logical_cpus if cpu_workers is None else max(1, min(int(cpu_workers), logical_cpus))
        self.enable_gpu = bool(enable_gpu)
        self.parity_mode = _normalise_parity_mode(parity_mode)
        self._cpu_workers = [_CpuWorker(index) for index in range(self.cpu_workers)]
        self._cpu_fallback_lock = threading.Lock()
        self._cpu_fallback_index = 0
        self._gpu_workers: list[_OpenCLWorker] = []
        self._device_descriptors: list[dict[str, Any]] = []
        self._base_fallback_reasons: list[str] = []
        self._active_lock = threading.RLock()
        self._active_request: ComputeRequest | None = None
        self._last_telemetry: dict[str, Any] | None = None
        self._closed = False

        if not self.enable_gpu:
            self._base_fallback_reasons.append("GPU execution is disabled by configuration.")
            return

        records, discovery_reasons = _discover_opencl_gpu_records()
        self._base_fallback_reasons.extend(discovery_reasons)
        if not records and not discovery_reasons:
            self._base_fallback_reasons.append("No OpenCL GPU devices were found.")

        for record in records:
            descriptor = record["descriptor"]
            self._device_descriptors.append(descriptor)
            if not descriptor["compatible"]:
                self._base_fallback_reasons.append(
                    f"{descriptor['name']} is not OpenCL-compatible: {descriptor['compatibilityError']}"
                )
                continue
            worker = _OpenCLWorker(descriptor, record["device"])
            if probe_devices:
                try:
                    worker.probe()
                except Exception as exc:  # pragma: no cover - driver dependent
                    descriptor["compatible"] = False
                    descriptor["compatibilityError"] = _clean_error(exc)
                    self._base_fallback_reasons.append(
                        f"{descriptor['name']} failed its OpenCL kernel probe: {_clean_error(exc)}"
                    )
                    worker.close()
                    continue
            self._gpu_workers.append(worker)

        if records and not self._gpu_workers:
            self._base_fallback_reasons.append("No detected OpenCL GPU passed the compatibility probe.")

    def _activate(self, request: ComputeRequest) -> None:
        with self._active_lock:
            if self._active_request is not None and self._active_request is not request:
                raise RuntimeError("Another compute request is already active.")
            self._active_request = request

    def _deactivate(self, request: ComputeRequest) -> None:
        with self._active_lock:
            if self._active_request is request:
                self._active_request = None

    def _publish_telemetry(self, telemetry: dict[str, Any]) -> None:
        with self._active_lock:
            self._last_telemetry = copy.deepcopy(telemetry)

    def request_scope(self, request_id: str, parity_mode: str | None = None) -> ComputeRequest:
        if self._closed:
            raise RuntimeError("Compute runtime is closed.")
        return ComputeRequest(self, request_id, parity_mode or self.parity_mode)

    begin_request = request_scope

    def active_request(self) -> ComputeRequest | None:
        with self._active_lock:
            return self._active_request

    def last_telemetry(self) -> dict[str, Any] | None:
        with self._active_lock:
            return copy.deepcopy(self._last_telemetry)

    def device_descriptors(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._device_descriptors)

    def capabilities(self) -> dict[str, Any]:
        compatible = [value for value in self._device_descriptors if value.get("compatible")]
        return {
            "backend": "OpenCL" if compatible else None,
            "cpuWorkers": self.cpu_workers,
            "gpuDevices": self.device_descriptors(),
            "compatibleGpuCount": len(compatible),
            "fallbackReasons": list(self._base_fallback_reasons),
        }

    def _schedule(
        self,
        operation: str,
        element_count: int,
        output_tail: tuple[int, ...],
        output_dtype: np.dtype[Any],
        payload_builder: Callable[[int, int], Mapping[str, Any]],
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if self._closed:
            raise RuntimeError("Compute runtime is closed.")
        result = np.empty((element_count,) + output_tail, dtype=output_dtype)
        stats: dict[str, Any] = {
            "cpuWorkUnits": 0,
            "cpuElements": 0,
            "cpuWorkers": {},
            "devices": {},
            "fallbackReasons": [],
        }
        if element_count == 0:
            return result, stats

        # GPU lanes come first so every compatible GPU receives work even when a
        # tiny array has fewer elements than the total number of available lanes.
        lanes: list[tuple[str, Any, int]] = []
        gpu_weight = max(4, self.cpu_workers * 4)
        lanes.extend(("gpu", worker, gpu_weight) for worker in self._gpu_workers)
        lanes.extend(("cpu", worker, 1) for worker in self._cpu_workers)
        counts = _chunk_counts(element_count, [lane[2] for lane in lanes])
        active_lanes = lanes[: len(counts)]

        futures: dict[Future[Any], tuple[str, Any, int, int, Mapping[str, Any]]] = {}
        start = 0
        for (kind, lane, _), count in zip(active_lanes, counts):
            end = start + count
            payload = payload_builder(start, end)
            try:
                if kind == "gpu":
                    future = lane.submit(operation, payload)
                else:
                    future = lane.submit(operation, payload)
            except Exception as exc:
                if kind != "gpu":
                    raise
                cpu_worker = self._next_cpu_fallback_worker()
                future = cpu_worker.submit(operation, payload)
                stats["fallbackReasons"].append(
                    f"{lane.descriptor['name']} could not start {operation}; CPU fallback used: {_clean_error(exc)}"
                )
                lane = (lane, cpu_worker)
                kind = "gpuFallback"
            futures[future] = (kind, lane, start, end, payload)
            start = end

        for future in as_completed(futures):
            kind, lane, start, end, payload = futures[future]
            count = end - start
            if kind == "gpu":
                descriptor = lane.descriptor
                device_stats = stats["devices"].setdefault(
                    descriptor["id"],
                    {
                        "descriptor": descriptor,
                        "workUnits": 0,
                        "elements": 0,
                        "attemptedWorkUnits": 0,
                        "attemptedElements": 0,
                    },
                )
                device_stats["attemptedWorkUnits"] += 1
                device_stats["attemptedElements"] += count
                try:
                    chunk = future.result()
                except Exception as exc:
                    stats["fallbackReasons"].append(
                        f"{descriptor['name']} failed {operation}; CPU fallback used: {_clean_error(exc)}"
                    )
                    cpu_result = self._next_cpu_fallback_worker().submit(operation, payload).result()
                    chunk = self._record_cpu_completion(stats, cpu_result, count)
                else:
                    device_stats["workUnits"] += 1
                    device_stats["elements"] += count
            else:
                cpu_result = future.result()
                chunk = self._record_cpu_completion(stats, cpu_result, count)
                if kind == "gpuFallback":
                    gpu_worker, _ = lane
                    descriptor = gpu_worker.descriptor
                    device_stats = stats["devices"].setdefault(
                        descriptor["id"],
                        {
                            "descriptor": descriptor,
                            "workUnits": 0,
                            "elements": 0,
                            "attemptedWorkUnits": 1,
                            "attemptedElements": count,
                        },
                    )
            result[start:end] = np.asarray(chunk, dtype=output_dtype).reshape((count,) + output_tail)

        return result, stats

    def _next_cpu_fallback_worker(self) -> _CpuWorker:
        with self._cpu_fallback_lock:
            worker = self._cpu_workers[self._cpu_fallback_index % len(self._cpu_workers)]
            self._cpu_fallback_index += 1
            return worker

    @staticmethod
    def _record_cpu_completion(
        stats: dict[str, Any],
        completed: tuple[np.ndarray, int, int, str],
        element_count: int,
    ) -> np.ndarray:
        chunk, lane_id, thread_id, thread_name = completed
        worker_key = f"{int(lane_id)}:{int(thread_id)}"
        worker = stats["cpuWorkers"].setdefault(
            worker_key,
            {
                "laneId": int(lane_id),
                "threadId": int(thread_id),
                "threadName": str(thread_name),
                "workUnits": 0,
                "elements": 0,
            },
        )
        worker["workUnits"] += 1
        worker["elements"] += int(element_count)
        stats["cpuWorkUnits"] += 1
        stats["cpuElements"] += int(element_count)
        return chunk

    def _delegate(self, method_name: str, *args: Any, **kwargs: Any) -> np.ndarray:
        with self._active_lock:
            active = self._active_request
        if active is not None:
            return getattr(active, method_name)(*args, **kwargs)
        with self.request_scope(f"standalone:{method_name}") as request:
            return getattr(request, method_name)(*args, **kwargs)

    def equal_scalar(self, *args: Any, **kwargs: Any) -> np.ndarray:
        return self._delegate("equal_scalar", *args, **kwargs)

    def lookup_labels(self, *args: Any, **kwargs: Any) -> np.ndarray:
        return self._delegate("lookup_labels", *args, **kwargs)

    def labels_in_set(self, *args: Any, **kwargs: Any) -> np.ndarray:
        return self._delegate("labels_in_set", *args, **kwargs)

    def assignment_base(self, *args: Any, **kwargs: Any) -> np.ndarray:
        return self._delegate("assignment_base", *args, **kwargs)

    def expand_tile_grid(self, *args: Any, **kwargs: Any) -> np.ndarray:
        return self._delegate("expand_tile_grid", *args, **kwargs)

    def normalize_clip(self, *args: Any, **kwargs: Any) -> np.ndarray:
        return self._delegate("normalize_clip", *args, **kwargs)

    def threshold_ge(self, *args: Any, **kwargs: Any) -> np.ndarray:
        return self._delegate("threshold_ge", *args, **kwargs)

    def composite_rgb(self, *args: Any, **kwargs: Any) -> np.ndarray:
        return self._delegate("composite_rgb", *args, **kwargs)

    def band_index(self, *args: Any, **kwargs: Any) -> np.ndarray:
        return self._delegate("band_index", *args, **kwargs)

    def close(self) -> None:
        if self._closed:
            return
        with self._active_lock:
            active = self._active_request
        if active is not None:
            active.close()
        self._closed = True
        for worker in self._gpu_workers:
            worker.close()
        self._gpu_workers.clear()
        for worker in self._cpu_workers:
            worker.close()
        self._cpu_workers.clear()

    def __enter__(self) -> "ComputeRuntime":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


_DEFAULT_RUNTIME_LOCK = threading.RLock()
_DEFAULT_RUNTIME: ComputeRuntime | None = None


def _default_runtime_from_environment() -> ComputeRuntime:
    """Create a process-local runtime that honors the engine's GPU controls.

    Joblib's ``loky`` backend imports this module in child processes, where the
    engine cannot install its already-configured runtime.  Reading the same
    environment controls here prevents a CPU-only request from silently probing
    and using every GPU in each child.
    """

    gpu_mode = str(os.environ.get("SPATIALSCOPE_GPU_MODE", "auto")).strip().lower()
    gpu_enabled = gpu_mode not in {"0", "false", "off", "cpu", "disabled"}
    parity_mode = str(os.environ.get("SPATIALSCOPE_GPU_PARITY_MODE", "off")).strip().lower()
    runtime = ComputeRuntime(enable_gpu=gpu_enabled, parity_mode=parity_mode)
    capabilities = runtime.capabilities()
    if gpu_mode in {"require", "required"} and int(capabilities.get("compatibleGpuCount", 0)) == 0:
        reasons = "; ".join(capabilities.get("fallbackReasons", []))
        runtime.close()
        raise RuntimeError(
            f"OpenCL GPU execution was required but no compatible GPU was found. {reasons}".strip()
        )
    return runtime


def get_default_runtime() -> ComputeRuntime:
    global _DEFAULT_RUNTIME
    with _DEFAULT_RUNTIME_LOCK:
        if _DEFAULT_RUNTIME is None:
            _DEFAULT_RUNTIME = _default_runtime_from_environment()
        return _DEFAULT_RUNTIME


def get_compute_runtime() -> ComputeRuntime:
    """Return the process-wide runtime shared by the native engine and analyses."""

    return get_default_runtime()


def select_workflow_parallel_backend(
    requested_backend: Any,
    *,
    shared_gpu_runtime_enabled: bool,
) -> str:
    """Choose a joblib backend without duplicating a process-wide GPU runtime.

    A loky worker is a separate process, so each worker can discover the same
    OpenCL devices and create its own queues.  Keep GPU-backed workflow scans in
    this process where they share the engine runtime.  When no compatible GPU is
    active, preserve the caller's CPU-only backend selection.
    """

    selected = str(requested_backend or "threading")
    return "threading" if shared_gpu_runtime_enabled else selected


def set_default_runtime(runtime: ComputeRuntime | None) -> None:
    """Replace the module default, closing the previous runtime if necessary."""

    global _DEFAULT_RUNTIME
    with _DEFAULT_RUNTIME_LOCK:
        previous = _DEFAULT_RUNTIME
        _DEFAULT_RUNTIME = runtime
    if previous is not None and previous is not runtime:
        previous.close()


def set_compute_runtime(runtime: ComputeRuntime | None) -> None:
    """Install the process-wide runtime used by analysis-module helper calls."""

    set_default_runtime(runtime)


def _close_default_runtime() -> None:  # pragma: no cover - interpreter shutdown
    global _DEFAULT_RUNTIME
    with _DEFAULT_RUNTIME_LOCK:
        runtime = _DEFAULT_RUNTIME
        _DEFAULT_RUNTIME = None
    if runtime is not None:
        runtime.close()


atexit.register(_close_default_runtime)


__all__ = [
    "ComputeRequest",
    "ComputeRuntime",
    "enumerate_opencl_gpus",
    "get_compute_runtime",
    "get_default_runtime",
    "select_workflow_parallel_backend",
    "set_compute_runtime",
    "set_default_runtime",
]
