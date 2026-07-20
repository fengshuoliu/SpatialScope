"""Apply fixed-parameter selections to optimizer search specifications.

The Windows UI sends ``fixedParameterKeys`` separately from the current
parameter values.  Keeping the values in the normal ``parameters`` payload
gives the engine one authoritative value for both the final run and the
screening lock.

This module operates on the raw search specifications consumed by
``run_nuclei_parameter_optimizer`` and
``run_celltype_assignment_parameter_optimizer``.  It deliberately does not
import either optimizer module, which keeps the request-normalization path
small and avoids circular imports in ``native_engine``.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


NUCLEI_FIXED_PARAMETER_KEYS = frozenset({"min_diam_um", "max_diam_um"})
ASSIGNMENT_FIXED_PARAMETER_KEYS = frozenset({"r_voronoi_um", "r_buffer_um"})


@dataclass(frozen=True)
class FixedParameterSearchSpecs:
    """Normalized search specifications and the exact values held fixed."""

    search_specs: dict[str, dict[str, Any]]
    fixed_parameters: dict[str, float]
    fixed_parameter_keys: tuple[str, ...]


def apply_nuclei_fixed_parameter_keys(
    search_specs: Mapping[str, Mapping[str, Any]],
    parameters: Mapping[str, Any] | object,
    fixed_parameter_keys: Sequence[str] | None,
) -> FixedParameterSearchSpecs:
    """Overlay optional nuclei locks while preserving valid diameter pairs.

    When only one diameter is fixed, the other diameter's search domain is
    constrained so the nuclei optimizer's min/max repair cannot swap the
    fixed value away:

    * fixed minimum -> every searched maximum is at least that minimum;
    * fixed maximum -> every searched minimum is at most that maximum.

    Omitting ``fixed_parameter_keys`` (or passing an empty sequence) returns a
    deep copy of ``search_specs`` without otherwise changing legacy behavior.
    """

    normalized_keys = _normalize_fixed_parameter_keys(
        fixed_parameter_keys,
        allowed_keys=NUCLEI_FIXED_PARAMETER_KEYS,
        optimizer_name="Nuclei optimizer",
    )
    cloned_specs = _copy_search_specs(search_specs)
    if not normalized_keys:
        return FixedParameterSearchSpecs(cloned_specs, {}, ())

    fixed_values = _fixed_values_from_parameters(
        parameters,
        normalized_keys,
        optimizer_name="Nuclei optimizer",
    )
    fixed_minimum = fixed_values.get("min_diam_um")
    fixed_maximum = fixed_values.get("max_diam_um")
    if (
        fixed_minimum is not None
        and fixed_maximum is not None
        and fixed_minimum > fixed_maximum
    ):
        raise ValueError(
            "Nuclei optimizer fixed minimum diameter "
            f"({fixed_minimum:g}) cannot be larger than the fixed maximum diameter "
            f"({fixed_maximum:g})."
        )

    for key, value in fixed_values.items():
        _set_numeric_singleton(
            cloned_specs,
            key,
            value,
            optimizer_name="Nuclei optimizer",
        )

    if fixed_minimum is not None and fixed_maximum is None:
        _constrain_numeric_lower_bound(
            cloned_specs,
            "max_diam_um",
            fixed_minimum,
            optimizer_name="Nuclei optimizer",
        )
    elif fixed_maximum is not None and fixed_minimum is None:
        _constrain_numeric_upper_bound(
            cloned_specs,
            "min_diam_um",
            fixed_maximum,
            optimizer_name="Nuclei optimizer",
        )

    return FixedParameterSearchSpecs(cloned_specs, fixed_values, normalized_keys)


def apply_assignment_fixed_parameter_keys(
    search_specs: Mapping[str, Mapping[str, Any]],
    parameters: Mapping[str, Any] | object,
    fixed_parameter_keys: Sequence[str] | None,
) -> FixedParameterSearchSpecs:
    """Overlay optional Voronoi/buffer locks on assignment search specs.

    Omitting ``fixed_parameter_keys`` (or passing an empty sequence) retains
    the complete legacy search domain.
    """

    normalized_keys = _normalize_fixed_parameter_keys(
        fixed_parameter_keys,
        allowed_keys=ASSIGNMENT_FIXED_PARAMETER_KEYS,
        optimizer_name="Assignment optimizer",
    )
    cloned_specs = _copy_search_specs(search_specs)
    if not normalized_keys:
        return FixedParameterSearchSpecs(cloned_specs, {}, ())

    fixed_values = _fixed_values_from_parameters(
        parameters,
        normalized_keys,
        optimizer_name="Assignment optimizer",
    )
    for key, value in fixed_values.items():
        _set_numeric_singleton(
            cloned_specs,
            key,
            value,
            optimizer_name="Assignment optimizer",
        )
    return FixedParameterSearchSpecs(cloned_specs, fixed_values, normalized_keys)


def _normalize_fixed_parameter_keys(
    raw_keys: Sequence[str] | None,
    *,
    allowed_keys: frozenset[str],
    optimizer_name: str,
) -> tuple[str, ...]:
    if raw_keys is None:
        return ()
    if isinstance(raw_keys, (str, bytes, bytearray)) or not isinstance(raw_keys, Sequence):
        raise ValueError(f"{optimizer_name} fixedParameterKeys must be an array of parameter names.")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_key in raw_keys:
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError(f"{optimizer_name} fixedParameterKeys must contain nonempty strings.")
        key = raw_key.strip()
        if key not in allowed_keys:
            allowed = ", ".join(sorted(allowed_keys))
            raise ValueError(
                f"{optimizer_name} cannot fix unknown parameter {key!r}; allowed keys: {allowed}."
            )
        if key not in seen:
            seen.add(key)
            normalized.append(key)
    return tuple(normalized)


def _copy_search_specs(
    search_specs: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(search_specs, Mapping):
        raise ValueError("Optimizer searchSpecs must be an object.")
    cloned: dict[str, dict[str, Any]] = {}
    for key, spec in search_specs.items():
        if not isinstance(key, str) or not isinstance(spec, Mapping):
            raise ValueError("Optimizer searchSpecs entries must map parameter names to objects.")
        cloned[key] = copy.deepcopy(dict(spec))
    return cloned


def _fixed_values_from_parameters(
    parameters: Mapping[str, Any] | object,
    keys: Sequence[str],
    *,
    optimizer_name: str,
) -> dict[str, float]:
    fixed_values: dict[str, float] = {}
    for key in keys:
        if isinstance(parameters, Mapping):
            if key not in parameters:
                raise ValueError(f"{optimizer_name} parameters are missing fixed parameter {key!r}.")
            raw_value = parameters[key]
        elif hasattr(parameters, key):
            raw_value = getattr(parameters, key)
        else:
            raise ValueError(f"{optimizer_name} parameters are missing fixed parameter {key!r}.")

        if isinstance(raw_value, bool):
            raise ValueError(f"{optimizer_name} fixed parameter {key!r} must be a finite number.")
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{optimizer_name} fixed parameter {key!r} must be a finite number."
            ) from exc
        if not math.isfinite(value):
            raise ValueError(f"{optimizer_name} fixed parameter {key!r} must be a finite number.")
        fixed_values[key] = value
    return fixed_values


def _numeric_search_bounds(
    search_specs: dict[str, dict[str, Any]],
    key: str,
    *,
    optimizer_name: str,
) -> tuple[dict[str, Any], float, float]:
    spec = search_specs.get(key)
    if spec is None:
        raise ValueError(f"{optimizer_name} searchSpecs are missing parameter {key!r}.")
    try:
        lower = float(spec["min"])
        upper = float(spec["max"])
        step = float(spec["step"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"{optimizer_name} searchSpecs for {key!r} must define numeric min, max, and step."
        ) from exc
    if not all(math.isfinite(value) for value in (lower, upper, step)) or step <= 0 or lower > upper:
        raise ValueError(
            f"{optimizer_name} searchSpecs for {key!r} require finite min <= max and step > 0."
        )
    return spec, lower, upper


def _set_numeric_singleton(
    search_specs: dict[str, dict[str, Any]],
    key: str,
    value: float,
    *,
    optimizer_name: str,
) -> None:
    spec, _, _ = _numeric_search_bounds(search_specs, key, optimizer_name=optimizer_name)
    spec["min"] = value
    spec["max"] = value
    # These keys belong to already-built specs and would be stale after an
    # overlay.  The optimizer builders recreate them from min/max/step.
    spec.pop("values", None)
    spec.pop("n_values", None)


def _constrain_numeric_lower_bound(
    search_specs: dict[str, dict[str, Any]],
    key: str,
    minimum: float,
    *,
    optimizer_name: str,
) -> None:
    spec, lower, upper = _numeric_search_bounds(search_specs, key, optimizer_name=optimizer_name)
    constrained_lower = max(lower, minimum)
    spec["min"] = constrained_lower
    spec["max"] = max(upper, constrained_lower)
    spec.pop("values", None)
    spec.pop("n_values", None)


def _constrain_numeric_upper_bound(
    search_specs: dict[str, dict[str, Any]],
    key: str,
    maximum: float,
    *,
    optimizer_name: str,
) -> None:
    spec, lower, upper = _numeric_search_bounds(search_specs, key, optimizer_name=optimizer_name)
    constrained_upper = min(upper, maximum)
    spec["min"] = min(lower, constrained_upper)
    spec["max"] = constrained_upper
    spec.pop("values", None)
    spec.pop("n_values", None)
