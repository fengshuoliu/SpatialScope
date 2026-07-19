from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata


project_root = Path(SPECPATH)
datas = [
    (str(project_root / "app.py"), "."),
    (str(project_root / "cell_distribution_export.py"), "."),
    (str(project_root / "streamlit_drawable_canvas.py"), "."),
    (str(project_root / "assets"), "assets"),
    (str(project_root / "src"), "src"),
]
binaries = []
hiddenimports = [
    "app",
    "cell_distribution_export",
    "streamlit_drawable_canvas",
    "matplotlib.backends.backend_agg",
    "openpyxl",
    "plotly",
    "tifffile",
    "xlsxwriter",
]

for package in ("matplotlib", "plotly", "skimage", "streamlit"):
    datas += collect_data_files(package)
datas += collect_data_files("streamlit_drawable_canvas", include_py_files=True)

for distribution in ("streamlit", "streamlit-drawable-canvas"):
    datas += copy_metadata(distribution, recursive=True)

analysis = Analysis(
    [str(project_root / "launcher.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="SpatialScopeBackend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    upx=True,
    upx_exclude=[],
    name="SpatialScopeBackend",
)
