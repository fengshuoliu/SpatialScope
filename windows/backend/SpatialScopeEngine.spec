from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

project_root = Path(SPECPATH)
datas = []
datas += collect_data_files(
    "scipy",
    includes=["stats/_sobol_direction_numbers.npz"],
)
hiddenimports = [
    # Matplotlib chooses output renderers dynamically from savefig suffixes.
    # Keep both renderers explicit so Step 2 can export PNG and SVG after the
    # Python engine is frozen by PyInstaller.
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_svg",
    "openpyxl",
    "tifffile",
    "xlsxwriter",
]

analysis = Analysis(
    [str(project_root / "native_engine.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={
        "matplotlib": {
            "backends": ["Agg", "SVG"],
        },
    },
    runtime_hooks=[],
    excludes=["plotly", "pyarrow", "streamlit", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="SpatialScopeEngine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # The WPF client redirects stdin/stdout for the JSON-lines protocol and
    # starts this process with CreateNoWindow=true, so no console is shown.
    # PyInstaller must still attach standard streams inside the executable.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SpatialScopeEngine",
)
