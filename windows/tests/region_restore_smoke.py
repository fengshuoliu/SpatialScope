from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image

from native_engine_smoke import ENGINE_SCRIPT, EngineProcess, _assert_png_backed_svg


def _require_path(value: Any, label: str) -> Path:
    path = Path(str(value or "")).expanduser().resolve()
    if not path.is_file():
        raise AssertionError(f"{label} was not created: {path}")
    return path


def _assert_preview_contract(result: dict[str, Any], *, exact_primary: bool) -> None:
    width = int(result.get("width", 0))
    height = int(result.get("height", 0))
    if width <= 0 or height <= 0:
        raise AssertionError(f"Invalid source dimensions: {width} x {height}")

    overlay = _require_path(result.get("overlayPreviewPath"), "Overlay preview")
    mask = _require_path(result.get("maskPreviewPath"), "Cell-type mask preview")
    comparison = _require_path(result.get("comparisonPreviewPath"), "Comparison preview")
    primary = _require_path(result.get("previewPath"), "Primary preview")
    with Image.open(overlay) as image:
        if image.size != (width, height):
            raise AssertionError(f"Overlay size {image.size} does not match {(width, height)}")
    with Image.open(mask) as image:
        if image.size != (width, height):
            raise AssertionError(f"Mask size {image.size} does not match {(width, height)}")
    with Image.open(comparison) as image:
        expected = (width * 2 + 24, height)
        if image.size != expected:
            raise AssertionError(f"Comparison size {image.size} does not match {expected}")
    expected_primary = mask if exact_primary else comparison
    if primary != expected_primary:
        raise AssertionError(f"Primary preview {primary} does not match {expected_primary}")


def _preferred_seed_type(regions: Sequence[dict[str, Any]], cell_types: Sequence[str]) -> str:
    totals = {name: 0 for name in cell_types}
    for region in regions:
        counts = region.get("countsByType")
        if not isinstance(counts, dict):
            continue
        for name in totals:
            totals[name] += int(counts.get(name, 0) or 0)
    return max(cell_types, key=lambda name: totals[name])


def run_restore_smoke(engine_command: Sequence[str], output_folder: Path) -> dict[str, Any]:
    engine = EngineProcess(engine_command)
    try:
        restored = engine.request("restore", {"outputFolder": str(output_folder)})
        if not restored.get("restored"):
            raise AssertionError(f"No saved analysis was restored from {output_folder}")
        workflow = restored.get("workflow") or {}
        if not workflow.get("region"):
            raise AssertionError("The restored output does not contain a completed Region analysis.")
        cell_types = [str(value) for value in restored.get("resolvedCellTypes") or [] if str(value)]
        regions = [value for value in restored.get("regions") or [] if isinstance(value, dict)]
        if not cell_types or not regions:
            raise AssertionError("The restored output has no cell types or Region catalog.")
        boundary_labels = [str(region.get("label") or "") for region in regions]
        boundary_labels = [value for value in boundary_labels if value]

        display_payload = {
            "selectedBoundaryLabels": boundary_labels,
            "selectedCellTypes": cell_types,
            "lineWidth": 2,
            "lineStyle": "-",
            "boundaryColor": "#A1D99B",
            "useTypeColors": True,
        }
        content_preview = engine.request(
            "region_preview",
            {**display_payload, "previewKey": "real_data_display", "boundaryCellTypeMode": "content"},
        )
        _assert_preview_contract(content_preview, exact_primary=False)
        summary = content_preview.get("summary") or {}
        if int(summary.get("boundaryCount", -1)) != int(content_preview.get("boundaryCount", -2)):
            raise AssertionError("Region preview boundary counts disagree.")
        if int(summary.get("cellTypeCount", 0)) != len(cell_types):
            raise AssertionError("Region preview cell-type count is incorrect.")
        if int(summary.get("cellCount", -1)) != int(content_preview.get("cellCount", -2)):
            raise AssertionError("Region preview cell counts disagree.")

        source_preview = engine.request(
            "region_preview",
            {**display_payload, "previewKey": "real_data_manual_editor", "boundaryCellTypeMode": "source"},
        )
        _assert_preview_contract(source_preview, exact_primary=True)

        width = int(source_preview["width"])
        height = int(source_preview["height"])
        seed_type = _preferred_seed_type(regions, cell_types)
        polygon = [
            {"x": 1.0, "y": 1.0},
            {"x": float(max(1, width - 2)), "y": 1.0},
            {"x": float(max(1, width - 2)), "y": float(max(1, height - 2))},
            {"x": 1.0, "y": float(max(1, height - 2))},
        ]
        manual_payload = {
            "mode": "create",
            "targetBoundaryLabel": None,
            "polygons": [polygon],
            "seedCellTypes": [seed_type],
            "selectedCellTypes": [seed_type],
            "previewKey": "real_data_manual_adjusted",
            "closeUm": 2,
            "dilateUm": 0,
            "minAreaUm2": 0,
            "minCells": 1,
            "contourDownsample": 1,
            "lineWidth": 2,
            "lineStyle": "-",
            "boundaryColor": "#A1D99B",
            "useTypeColors": True,
        }
        manual_preview = engine.request("region_manual_preview", manual_payload)
        primary_manual = _require_path(manual_preview.get("previewPath"), "Manual adjusted preview")
        with Image.open(primary_manual) as image:
            if image.size != (width, height):
                raise AssertionError("Manual preview is not in exact source coordinates.")

        saved = engine.request(
            "region_manual_save",
            {**manual_payload, "displayName": "Windows final validation ROI"},
        )
        saved_summary = saved.get("summary") or {}
        saved_label = str(saved_summary.get("savedRegionLabel") or "")
        saved_regions = [value for value in saved.get("regions") or [] if isinstance(value, dict)]
        if not saved_label or not any(str(region.get("label") or "") == saved_label for region in saved_regions):
            raise AssertionError("The manual ROI was not added to the Region catalog.")

        customized = engine.request(
            "region_custom_export",
            {
                "selectedBoundaryLabels": [str(region.get("label") or "") for region in saved_regions],
                "selectedCellTypes": cell_types,
                "previewKey": "real_data_customized_export",
                "boundaryCellTypeMode": "content",
                "lineWidth": 2,
                "lineStyle": "-",
                "boundaryColor": "#A1D99B",
                "useTypeColors": True,
            },
        )
        _assert_preview_contract(customized, exact_primary=False)
        if not customized.get("artifacts"):
            raise AssertionError("Customized Region export returned no artifacts.")
        for comparison_key in ("comparisonPreviewPath", "originalComparisonPreviewPath"):
            primary_png = _require_path(customized.get(comparison_key), comparison_key)
            _assert_png_backed_svg(primary_png, primary_png.with_suffix(".svg"))
    finally:
        engine.close()

    verification_engine = EngineProcess(engine_command)
    try:
        verified = verification_engine.request("restore", {"outputFolder": str(output_folder)})
        verified_labels = {
            str(region.get("label") or "")
            for region in verified.get("regions") or []
            if isinstance(region, dict)
        }
        if saved_label not in verified_labels:
            raise AssertionError("The saved manual ROI did not survive a fresh restore.")
    finally:
        verification_engine.close()

    report = {
        "status": "passed",
        "outputFolder": str(output_folder),
        "sourceWidth": width,
        "sourceHeight": height,
        "cellTypes": cell_types,
        "initialRegionCount": len(regions),
        "savedRegionLabel": saved_label,
        "restoredRegionCount": len(verified_labels),
        "contentBoundaryCount": int(content_preview.get("boundaryCount", 0)),
        "selectedCellCount": int(content_preview.get("cellCount", 0)),
        "customizedArtifactCount": len(customized.get("artifacts") or []),
    }
    report_path = output_folder / "windows_region_redesign_real_data_test_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["reportPath"] = str(report_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Region restore/edit/export against saved real data.")
    parser.add_argument("--output-folder", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--engine-executable", type=Path)
    args = parser.parse_args()
    output_folder = args.output_folder.expanduser().resolve()
    if not output_folder.is_dir():
        raise FileNotFoundError(output_folder)
    engine_command = (
        [str(args.engine_executable.expanduser().resolve())]
        if args.engine_executable is not None
        else [str(args.python), str(ENGINE_SCRIPT)]
    )
    report = run_restore_smoke(engine_command, output_folder)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
