#!/usr/bin/env bash
set -euo pipefail

# Keep Conda/Miniforge toolchains from shadowing Apple's system tools.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${BUILD_ROOT:-$ROOT_DIR/build/cell-distribution-runtime-build}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT_DIR/build/cell-distribution-runtime}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
PYINSTALLER_VERSION="${PYINSTALLER_VERSION:-6.21.0}"

for architecture in arm64 x86_64; do
    destination="$OUTPUT_ROOT/$architecture"
    if [[ -x "$destination/cell_distribution_exporter" && "${FORCE_REBUILD:-0}" != "1" ]]; then
        echo "Using existing Cell Distribution runtime for $architecture"
        continue
    fi

    architecture_root="$BUILD_ROOT/$architecture"
    venv="$architecture_root/venv"
    mkdir -p "$architecture_root" "$OUTPUT_ROOT"

    if [[ ! -x "$venv/bin/python" ]]; then
        arch -"$architecture" "$PYTHON_BIN" -m venv "$venv"
    fi

    arch -"$architecture" "$venv/bin/python" -m pip install --upgrade pip setuptools wheel
    arch -"$architecture" "$venv/bin/python" -m pip install \
        -r "$ROOT_DIR/script/cell_distribution_runtime_requirements.txt" \
        "pyinstaller==$PYINSTALLER_VERSION"

    arch -"$architecture" "$venv/bin/python" -m PyInstaller \
        --noconfirm \
        --clean \
        --onedir \
        --target-architecture "$architecture" \
        --name cell_distribution_exporter \
        --paths "$ROOT_DIR/script/runtime_support" \
        --hidden-import skimage.segmentation \
        --hidden-import matplotlib.backends.backend_svg \
        --distpath "$architecture_root/dist" \
        --workpath "$architecture_root/work" \
        --specpath "$architecture_root/spec" \
        "$ROOT_DIR/script/cell_distribution_streamlit_export.py"

    mkdir -p "$destination"
    /usr/bin/ditto "$architecture_root/dist/cell_distribution_exporter" "$destination"
    arch -"$architecture" "$destination/cell_distribution_exporter" --help >/dev/null
done

echo "Cell Distribution runtimes are ready in $OUTPUT_ROOT"
