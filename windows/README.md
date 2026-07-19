# SpatialScope for Windows

SpatialScope 2.0 is a native Windows desktop application. Its interface is WPF/.NET, its analysis engine runs as a private local process, and it does not use Streamlit or a browser.

## Architecture

- `native/src/SpatialScope.App/` is the Windows WPF application and native folder-picker UI.
- `backend/native_engine.py` exposes the scientific workflows over a compact JSON-lines protocol.
- `backend/SpatialScopeEngine.spec` freezes the engine with both Matplotlib Agg and SVG backends.
- `tests/native_overlay_smoke.py` reproduces configuration through Composite Preview (Step 2).
- `tests/native_engine_smoke.py` exercises the complete nine-step workflow.
- `run_native.ps1` prepares and runs the source application for local development.
- `build_native.ps1` produces the self-contained portable Windows package.

The previous Electron/Streamlit implementation remains in `desktop/` and `backend/app.py` for compatibility and reference only. It is not used by the native 2.0 package.

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

Run the source renderer, WPF build, and complete nine-step scientific smoke test with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\run_native.ps1 test
```

## Build a portable release

After source testing, create and validate the self-contained Windows package:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\build_native.ps1 -FullSmoke
```

Once dependencies are already installed, add `-SkipDependencies` to save time. The build checks the frozen Matplotlib renderer, runs Step 2 against the frozen engine, optionally runs all nine workflow stages, and then writes:

- `native/dist/SpatialScope-Windows-x64-Portable-2.0.0.zip`
- `native/dist/SHA256SUMS-Windows.txt`

Unzip the archive and run `SpatialScope.exe`; Python, Node.js, Electron, Streamlit, and the .NET SDK are not required on the test machine. The executable is currently unsigned, so Windows SmartScreen may require **More info > Run anyway** the first time.

Do not distribute or test the older 1.2.0 package: that freeze omitted `matplotlib.backends.backend_svg`, which Step 2 imports when it saves SVG files. The native 2.0 build explicitly bundles and smoke-tests that backend.
