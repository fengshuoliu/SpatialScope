# SpatialScope for Windows

SpatialScope 1.2.1 is a native Windows desktop application. Its interface is WPF/.NET, its analysis engine runs as a private local process, and it does not use Streamlit or a browser.

## Architecture

- `native/src/SpatialScope.App/` is the Windows WPF application and native folder-picker UI.
- `backend/native_engine.py` exposes the scientific workflows over a compact JSON-lines protocol.
- `backend/src/spatialscope_analysis/compute_runtime.py` schedules exact array work across every logical CPU and every compatible OpenCL GPU, with automatic CPU fallback.
- `backend/SpatialScopeEngine.spec` freezes the engine with both Matplotlib Agg and SVG backends.
- `tests/native_overlay_smoke.py` reproduces configuration through Composite Preview (Step 2).
- `tests/native_engine_smoke.py` exercises the complete nine-step workflow.
- `run_native.ps1` prepares and runs the source application for local development.
- `build_native.ps1` produces the self-contained Windows setup program.

The previous Electron/Streamlit implementation remains in `desktop/` and `backend/app.py` for compatibility and reference only. It is not used by the native 1.2.1 installer.

## Test and adjust locally

Visual Studio Community is the closest Windows equivalent to Xcode. Install the **.NET desktop development** workload, or use Visual Studio Code with the C# Dev Kit. The project targets 64-bit Windows 10/11 and .NET 10; Python 3.11 is used for source-level scientific development.

Open this project in Visual Studio:

```text
windows/native/src/SpatialScope.App/SpatialScope.App.csproj
```

First prepare the isolated Python environment:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\run_native.ps1 setup
```

Then press **F5** in Visual Studio, or launch from a terminal:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\run_native.ps1 run
```

The source application automatically starts `windows/backend/native_engine.py` from the isolated environment. Input and output path fields open the native Windows folder browser when clicked.

SpatialScope probes real OpenCL compute devices instead of treating display-adapter names as proof of GPU support. In the default `auto` mode, every compatible Intel, NVIDIA, or AMD OpenCL GPU receives returned workflow work alongside all logical CPU lanes. Each CPU lane has a persistent worker thread, and request telemetry counts a worker only after that thread completes output used by the workflow. The header reports the backend and GPU count; hover it to see exact device names. If OpenCL is missing or a device fails, the same operations fall back to the CPU and the engine records the reason in request telemetry.

Run the source renderer, WPF build, and complete nine-step scientific smoke test with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\run_native.ps1 test
```

Run the focused CPU/OpenCL parity suite with:

```powershell
.\windows\.venv\Scripts\python.exe -m unittest -v windows.tests.test_compute_runtime
```

`windows/tests/native_gpu_parity.py` additionally runs the full workflow once on CPU and once with OpenCL required, then compares its scientific masks, tables, distance-band arrays, and preview pixels. It is intended for a Windows machine with at least one OpenCL GPU.

```powershell
.\windows\.venv\Scripts\python.exe .\windows\tests\native_gpu_parity.py
```

## Build an installer release

After source testing, create and validate the self-contained Windows package:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\build_native.ps1 -FullSmoke -RequireGpuParity
```

Once dependencies are already installed, add `-SkipDependencies` to save time. The build checks the frozen Matplotlib renderer and runs Step 2 against the frozen engine. `-FullSmoke` runs all nine workflow stages; adding `-RequireGpuParity` also runs exact CPU-versus-required-OpenCL parity against the staged frozen engine, so that release-only gate requires a compatible OpenCL GPU. GPU-less CI can retain the complete CPU workflow smoke by using `-FullSmoke` alone. The build then writes:

- `native/dist/SpatialScope-Windows-x64-Setup.exe`
- `native/dist/SHA256SUMS-Windows.txt`

Run the setup program and launch SpatialScope from the Start menu or desktop shortcut. Python, PyOpenCL, Node.js, Electron, Streamlit, and the .NET SDK are not required on the test machine; the private engine and OpenCL binding are bundled. A compatible graphics driver is still required for GPU execution. The installer is currently unsigned, so Windows SmartScreen may require **More info > Run anyway** the first time.

The native 1.2.1 build explicitly bundles and smoke-tests `matplotlib.backends.backend_svg`, which Step 2 uses when it saves SVG files.
