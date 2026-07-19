from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import tifffile

from native_engine_smoke import EngineProcess


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _snake_nuclei_parameters(raw: dict[str, Any], nucleus_channel: str) -> dict[str, Any]:
    return {
        "nucleus_channel": nucleus_channel,
        "min_diam_um": float(raw["minDiamUm"]),
        "max_diam_um": float(raw["maxDiamUm"]),
        "tophat_radius_um": float(raw["tophatRadiusUm"]),
        "gauss_sigma_um": float(raw["gaussSigmaUm"]),
        "local_win_um": float(raw["localWinUm"]),
        "local_offset": float(raw["localOffset"]),
        "h_maxima_um": float(raw["hMaximaUm"]),
        "seed_min_dist_um": float(raw["seedMinDistUm"]),
        "watershed_compactness": float(raw["watershedCompactness"]),
        "post_resplit_mult": float(raw["postResplitMult"]),
    }


def _snake_assignment_parameters(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "r_voronoi_um": float(raw["rVoronoiUm"]),
        "r_buffer_um": float(raw["rBufferUm"]),
        "r_vote_um": float(raw["rVoteUm"]),
        "tophat_r_um": float(raw["tophatRUm"]),
        "gauss_sigma_um": float(raw["gaussSigmaUm"]),
        "thresh_mode": str(raw["threshMode"]),
        "min_pos_object_size_px": int(raw["minPosObjectSizePx"]),
        "min_pos_pix": int(raw["minPosPix"]),
        "resolve_ambiguous": bool(raw["resolveAmbiguous"]),
        "ambiguous_min_probability": float(raw["ambiguousMinProbability"]),
        "ambiguous_min_gap": float(raw["ambiguousMinGap"]),
    }


def _normalize_marker(value: Any) -> str:
    marker = str(value)
    return "nucleus" if marker.casefold() == "nucleus" else marker


def _cell_types(raw: Any) -> list[dict[str, Any]]:
    definitions = raw.get("cell_types", []) if isinstance(raw, dict) else raw
    if not isinstance(definitions, list) or not definitions:
        raise ValueError("The reference cell type configuration is empty.")
    normalized: list[dict[str, Any]] = []
    for definition in definitions:
        normalized.append(
            {
                "name": str(definition["name"]),
                "color_hex": str(definition.get("colorHex") or definition.get("color_hex")),
                "mode": str(definition.get("mode") or "simple"),
                "all_pos": [_normalize_marker(value) for value in definition.get("allPositiveMarkers", definition.get("all_pos", []))],
                "all_neg": [_normalize_marker(value) for value in definition.get("allNegativeMarkers", definition.get("all_neg", []))],
                "any_pos_groups": [
                    [_normalize_marker(value) for value in group]
                    for group in definition.get("anyPositiveGroups", definition.get("any_pos_groups", []))
                ],
            }
        )
    return normalized


def _require_files(paths: Sequence[str | Path], label: str) -> None:
    missing = [str(path) for path in paths if not Path(path).is_file()]
    if missing:
        raise AssertionError(f"{label} did not create required files: {missing}")


def _require_exact_reference_labels(native_path: Path, reference_path: Path) -> int:
    reference = _load_json(reference_path)
    width = int(reference["width"])
    height = int(reference["height"])
    expected = np.asarray(reference["labels"], dtype=np.uint16).reshape((height, width))
    actual = np.asarray(tifffile.imread(native_path), dtype=np.uint16)
    if actual.shape != expected.shape:
        raise AssertionError(
            "Native nuclei label map shape does not match the macOS reference: "
            f"{actual.shape} versus {expected.shape}."
        )
    mismatch_count = int(np.count_nonzero(actual != expected))
    if mismatch_count:
        raise AssertionError(
            "Native nuclei label map does not match the macOS reference: "
            f"{mismatch_count:,} pixels differ."
        )
    return mismatch_count


def run_real_data_smoke(
    engine_executable: Path,
    input_folder: Path,
    output_folder: Path,
    reference_output: Path,
) -> dict[str, Any]:
    if not input_folder.is_dir():
        raise FileNotFoundError(f"Input folder was not found: {input_folder}")
    if not reference_output.is_dir():
        raise FileNotFoundError(f"Reference output folder was not found: {reference_output}")
    if output_folder.exists():
        raise FileExistsError(
            f"Refusing to overwrite an existing real-data output folder: {output_folder}"
        )

    pipeline = _load_json(reference_output / "00_config" / "pipeline_config.json")
    nuclei_reference = _load_json(
        reference_output / "02_nuclei_segmentation" / "nuclei_segmentation_parameters.json"
    )
    assignment_reference = _load_json(
        reference_output / "05_cell_type_assignment" / "celltype_assignment_parameters.json"
    )
    celltype_reference = _load_json(
        reference_output / "03_cell_type_definition" / "celltype_config.json"
    )
    cell_types = _cell_types(celltype_reference)
    nucleus_channel = str(pipeline["nucleusChannel"])
    nuclei_parameters = _snake_nuclei_parameters(nuclei_reference, nucleus_channel)
    assignment_parameters = _snake_assignment_parameters(assignment_reference)
    reference_nuclei_summary = (
        reference_output / "02_nuclei_segmentation" / "nuclei_summary.csv"
    )
    with reference_nuclei_summary.open("r", encoding="utf-8-sig") as handle:
        reference_nuclei_count = max(0, sum(1 for _ in handle) - 1)

    channels = [
        {
            "file": str(channel["file"]),
            "channel": str(channel["channel"]),
            "colorHex": str(channel["colorHex"]),
            "includeOverlay": str(channel["channel"]) in set(pipeline.get("overlayChannels", [])),
        }
        for channel in pipeline["channels"]
    ]

    engine = EngineProcess([str(engine_executable)])
    completed_commands: list[str] = []
    try:
        hello = engine.request("hello", {})
        if hello.get("protocolVersion") != 1:
            raise AssertionError(f"Unexpected engine handshake: {hello}")

        configured = engine.request(
            "configure",
            {
                "inputFolder": str(input_folder),
                "outputFolder": str(output_folder),
                "pixelSizeUm": pipeline.get("pixelSizeUm", [1.0, 1.0]),
                "imageId": str(pipeline.get("imageID") or "FieldA"),
                "whiteChannel": pipeline.get("whiteChannel"),
                "whiteWeight": float(pipeline.get("whiteWeight", 0.0)),
                "channels": channels,
            },
        )
        completed_commands.append("configure")
        if len(configured["channels"]) != len(channels):
            raise AssertionError(
                f"Expected {len(channels)} CSV channels, discovered {len(configured['channels'])}."
            )

        overlay = engine.request("overlay", {"clipHighPercentile": 99.8})
        completed_commands.append("overlay")
        _require_files(overlay["previewPaths"].values(), "Composite Preview")
        svg_artifacts = [
            item for item in overlay["artifacts"]
            if str(item.get("relativePath", "")).lower().endswith(".svg")
        ]
        if len(svg_artifacts) < 2:
            raise AssertionError("Composite Preview did not create both SVG exports.")
        _require_files([item["absolutePath"] for item in svg_artifacts], "Composite Preview SVG")

        nuclei_optimizer = engine.request(
            "nuclei_optimizer",
            {
                "parameters": nuclei_parameters,
                "maxEvaluations": 2,
                "parallelWorkers": 1,
                "parallelBackend": "threading",
                "useFixedRoiSubset": True,
            },
        )
        completed_commands.append("nuclei_optimizer")
        if not nuclei_optimizer["recommendedParameters"]:
            raise AssertionError("Nuclei optimizer did not return a recommendation.")

        nuclei = engine.request(
            "nuclei",
            {"parameters": nuclei_parameters, "nativeThreads": 1},
        )
        completed_commands.append("nuclei")
        nuclei_count = int(nuclei["summary"]["nNuclei"])
        if nuclei_count <= 0:
            raise AssertionError("Nuclei segmentation did not find any nuclei.")
        if reference_nuclei_count and nuclei_count != reference_nuclei_count:
            raise AssertionError(
                "Native nuclei result does not match the reference macOS result: "
                f"{nuclei_count} versus {reference_nuclei_count}."
            )
        _require_files([nuclei["previewPath"]], "Nuclei Segmentation")
        label_map_mismatch_count = _require_exact_reference_labels(
            output_folder / "02_nuclei_segmentation" / "nuclei_labels_uint16.tiff",
            reference_output / "02_nuclei_segmentation" / "nuclei_label_map.json",
        )

        assignment_optimizer = engine.request(
            "celltype_optimizer",
            {
                "cellTypes": cell_types,
                "parameters": assignment_parameters,
                "maxEvaluations": 2,
                "parallelWorkers": 1,
                "parallelBackend": "threading",
                "useFixedRoiSubset": True,
            },
        )
        completed_commands.append("celltype_optimizer")
        if not assignment_optimizer["recommendedParameters"]:
            raise AssertionError("Cell assignment optimizer did not return a recommendation.")

        assignment = engine.request(
            "celltype_assignment",
            {
                "nucleusChannel": nucleus_channel,
                "cellTypes": cell_types,
                "parameters": assignment_parameters,
                "nativeThreads": 1,
                "supportWorkers": 1,
            },
        )
        completed_commands.append("celltype_assignment")
        cell_counts = {
            str(name): int(count) for name, count in assignment["summary"]["cellCounts"].items()
        }
        resolved_types = [
            name for name, count in cell_counts.items()
            if name not in {"Unassigned", "Ambiguous"} and count > 0
        ]
        if len(resolved_types) < 2:
            raise AssertionError(f"Fewer than two cell types were assigned: {cell_counts}")

        neighborhood = engine.request("neighborhood", {"gridSizeUm": 20.0})
        completed_commands.append("neighborhood")
        if int(neighborhood["summary"]["clusterCount"]) <= 0:
            raise AssertionError("Neighborhood analysis did not create any clusters.")

        preferred_region_type = "GFP tumor" if cell_counts.get("GFP tumor", 0) > 0 else max(
            resolved_types, key=lambda name: cell_counts[name]
        )
        region = engine.request(
            "region",
            {
                "selectedTypes": [preferred_region_type],
                "closeUm": 2.0,
                "dilateUm": 0.0,
                "minAreaUm2": 0.0,
                "minCells": 1,
                "contourDownsample": 1,
                "lineWidth": 2.0,
                "lineStyle": "-",
                "boundaryColor": "#A1D99B",
                "useTypeColors": True,
            },
        )
        completed_commands.append("region")
        if not region["boundaries"]:
            raise AssertionError(f"Region analysis produced no boundary for {preferred_region_type}.")
        boundary_label = str(region["boundaries"][0]["label"])

        distribution = engine.request(
            "cell_distribution",
            {
                "boundaryLabel": boundary_label,
                "bandWidthUm": 10.0,
                "selectedCellTypes": resolved_types,
            },
        )
        completed_commands.append("cell_distribution")
        if not distribution["artifacts"]:
            raise AssertionError("Cell Distribution did not create any artifacts.")

        distance_queries = resolved_types[1:]
        nearest = engine.request(
            "distance",
            {
                "mode": "nearest",
                "targetType": resolved_types[0],
                "queryTypes": distance_queries,
            },
        )
        completed_commands.append("distance_nearest")
        if not nearest["artifacts"]:
            raise AssertionError("Nearest-neighbor analysis did not create any artifacts.")

        boundary_distance = engine.request(
            "distance",
            {
                "mode": "boundary",
                "targetType": resolved_types[0],
                "queryTypes": resolved_types,
                "boundaryLabel": boundary_label,
                "regionFilter": "all",
            },
        )
        completed_commands.append("distance_boundary")
        if not boundary_distance["artifacts"]:
            raise AssertionError("Cell-to-boundary analysis did not create any artifacts.")

        outputs = engine.request("outputs", {})
        completed_commands.append("outputs")
        if len(outputs["files"]) < 12:
            raise AssertionError("The real-data output manifest is unexpectedly small.")
        serialized_outputs = json.dumps(outputs)
        if "data:image" in serialized_outputs or "base64" in serialized_outputs.casefold():
            raise AssertionError("The native protocol embedded image data instead of file paths.")
        if engine.max_protocol_line_bytes >= 2_000_000:
            raise AssertionError(
                f"The largest protocol line was {engine.max_protocol_line_bytes} bytes."
            )

        report = {
            "status": "passed",
            "engine": str(engine_executable),
            "input_folder": str(input_folder),
            "output_folder": str(output_folder),
            "reference_output": str(reference_output),
            "channel_count": len(configured["channels"]),
            "nuclei_count": nuclei_count,
            "reference_nuclei_count": reference_nuclei_count,
            "label_map_mismatch_count": label_map_mismatch_count,
            "cell_counts": cell_counts,
            "resolved_cell_types": resolved_types,
            "neighborhood_clusters": int(neighborhood["summary"]["clusterCount"]),
            "region_type": preferred_region_type,
            "boundary_label": boundary_label,
            "optimizer_checks": 2,
            "distance_modes": ["nearest", "boundary"],
            "output_files": len(outputs["files"]),
            "max_protocol_line_bytes": engine.max_protocol_line_bytes,
            "completed_commands": completed_commands,
        }
        report_path = output_folder / "native_real_data_smoke_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        engine.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the complete frozen native workflow on real SpatialScope data without overwriting prior results."
    )
    parser.add_argument("--engine-executable", type=Path, required=True)
    parser.add_argument("--input-folder", type=Path, required=True)
    parser.add_argument("--output-folder", type=Path, required=True)
    parser.add_argument("--reference-output", type=Path, required=True)
    args = parser.parse_args()
    report = run_real_data_smoke(
        args.engine_executable.resolve(),
        args.input_folder.resolve(),
        args.output_folder.resolve(),
        args.reference_output.resolve(),
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
