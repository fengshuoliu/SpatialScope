from __future__ import annotations

import argparse
import json
from pathlib import Path

from native_engine_smoke import EngineProcess, prepare_smoke_output_folder


WINDOWS_DIR = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify configure -> Composite Preview against a frozen native engine."
    )
    parser.add_argument("--engine-executable", type=Path, required=True)
    parser.add_argument(
        "--input-folder",
        type=Path,
        default=WINDOWS_DIR / "build" / "smoke-output" / "synthetic_input",
    )
    parser.add_argument(
        "--output-folder",
        type=Path,
        default=WINDOWS_DIR / "build" / "native-overlay-smoke",
    )
    args = parser.parse_args()

    engine_executable = args.engine_executable.resolve()
    input_folder = args.input_folder.resolve()
    output_folder = prepare_smoke_output_folder(args.output_folder)

    engine = EngineProcess([str(engine_executable)])
    try:
        hello = engine.request("hello", {})
        configured = engine.request(
            "configure",
            {
                "inputFolder": str(input_folder),
                "outputFolder": str(output_folder),
                "pixelSizeUm": [1.0, 1.0],
                "imageId": "FrozenOverlaySmoke",
                "whiteChannel": "DAPI",
                "whiteWeight": 0.25,
            },
        )
        overlay = engine.request("overlay", {"clipHighPercentile": 99.8})
        previews = {name: Path(value) for name, value in overlay["previewPaths"].items()}
        missing = [str(path) for path in previews.values() if not path.is_file()]
        if missing:
            raise AssertionError(f"Composite Preview did not create preview files: {missing}")
        svg_artifacts = [
            artifact
            for artifact in overlay["artifacts"]
            if str(artifact.get("relativePath", "")).lower().endswith(".svg")
        ]
        if len(svg_artifacts) < 2:
            raise AssertionError("Composite Preview did not export both overlay SVG files.")
        for artifact in svg_artifacts:
            svg_path = Path(str(artifact["absolutePath"]))
            if not svg_path.is_file() or svg_path.stat().st_size == 0:
                raise AssertionError(f"Composite Preview SVG is missing or empty: {svg_path}")
            if b"<svg" not in svg_path.read_bytes()[:4096]:
                raise AssertionError(f"Composite Preview artifact is not valid SVG: {svg_path}")
        if engine.max_protocol_line_bytes >= 2_000_000:
            raise AssertionError(
                f"Composite Preview protocol line is too large: {engine.max_protocol_line_bytes} bytes"
            )

        report = {
            "status": "passed",
            "protocolVersion": hello["protocolVersion"],
            "channelCount": len(configured["channels"]),
            "svgArtifacts": len(svg_artifacts),
            "previewFiles": {name: str(path) for name, path in previews.items()},
            "maxProtocolLineBytes": engine.max_protocol_line_bytes,
        }
        (output_folder / "native_overlay_smoke_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2))
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
