from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import tempfile
from pathlib import Path


def bundled_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SpatialScope Windows analysis runtime")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--session-root", type=Path)
    parser.add_argument("--settings-path", type=Path)
    parser.add_argument("--desktop-paths-path", type=Path)
    parser.add_argument("--system-language", default="en")
    parser.add_argument("--smoke-test", action="store_true")
    return parser.parse_args()


def configure_environment(args: argparse.Namespace) -> None:
    runtime_root = Path(args.session_root or (Path(tempfile.gettempdir()) / "SpatialScope" / "sessions"))
    runtime_root.mkdir(parents=True, exist_ok=True)
    matplotlib_root = runtime_root.parent / "matplotlib"
    matplotlib_root.mkdir(parents=True, exist_ok=True)

    os.environ["SPATIALSCOPE_SESSION_ROOT"] = str(runtime_root)
    os.environ["SPATIALSCOPE_SYSTEM_LANGUAGE"] = str(args.system_language)
    os.environ["MPLCONFIGDIR"] = str(matplotlib_root)
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    os.environ.setdefault("STREAMLIT_CLIENT_TOOLBAR_MODE", "minimal")
    if args.settings_path is not None:
        args.settings_path.parent.mkdir(parents=True, exist_ok=True)
        os.environ["SPATIALSCOPE_SETTINGS_PATH"] = str(args.settings_path)
    if args.desktop_paths_path is not None:
        args.desktop_paths_path.parent.mkdir(parents=True, exist_ok=True)
        os.environ["SPATIALSCOPE_DESKTOP_PATHS_PATH"] = str(args.desktop_paths_path)


def run_smoke_test() -> int:
    import app
    import matplotlib
    import numpy
    import pandas
    import scipy
    import skimage
    import streamlit

    versions = {
        "app": str(Path(app.__file__).name),
        "matplotlib": matplotlib.__version__,
        "numpy": numpy.__version__,
        "pandas": pandas.__version__,
        "scipy": scipy.__version__,
        "skimage": skimage.__version__,
        "streamlit": streamlit.__version__,
    }
    print("SpatialScope backend smoke test passed", versions, flush=True)
    return 0


def main() -> int:
    multiprocessing.freeze_support()
    args = parse_args()
    configure_environment(args)
    if args.smoke_test:
        return run_smoke_test()

    app_path = bundled_root() / "app.py"
    if not app_path.exists():
        raise FileNotFoundError(f"Bundled SpatialScope app not found: {app_path}")

    os.chdir(bundled_root())
    from streamlit.web import cli as streamlit_cli

    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        "--server.headless=true",
        "--server.address=127.0.0.1",
        f"--server.port={args.port}",
        "--server.enableXsrfProtection=true",
        "--browser.gatherUsageStats=false",
        "--client.toolbarMode=minimal",
        "--client.showErrorDetails=false",
    ]
    return int(streamlit_cli.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
