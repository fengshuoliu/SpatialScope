param(
    [switch]$SkipDependencies,
    [switch]$FullSmoke
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$WindowsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepositoryRoot = Split-Path -Parent $WindowsRoot
$BackendRoot = Join-Path $WindowsRoot "backend"
$TestsRoot = Join-Path $WindowsRoot "tests"
$NativeRoot = Join-Path $WindowsRoot "native"
$InstallerScript = Join-Path $WindowsRoot "installer\SpatialScope.nsi"
$ProjectPath = Join-Path $NativeRoot "src\SpatialScope.App\SpatialScope.App.csproj"
$BuildRoot = Join-Path $WindowsRoot "build\native-release"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller-work"
$EngineDistRoot = Join-Path $BuildRoot "engine-dist"
$PublishRoot = Join-Path $BuildRoot "SpatialScope-Windows-x64"
$EngineStage = Join-Path $PublishRoot "engine"
$DistRoot = Join-Path $NativeRoot "dist"
$InstallerSmokeRoot = Join-Path $BuildRoot "installer-smoke"
$PythonExe = Join-Path $WindowsRoot ".venv\Scripts\python.exe"
$SyntheticInput = Join-Path $WindowsRoot "build\smoke-output\synthetic_input"

function Assert-Success([string]$Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE"
    }
}

function Assert-ChildPath([string]$Candidate, [string]$ExpectedParent) {
    $ResolvedCandidate = [System.IO.Path]::GetFullPath($Candidate).TrimEnd('\')
    $ResolvedParent = [System.IO.Path]::GetFullPath($ExpectedParent).TrimEnd('\')
    if (-not $ResolvedCandidate.StartsWith("$ResolvedParent\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside $ResolvedParent`: $ResolvedCandidate"
    }
}

function Resolve-MakeNsis {
    $Candidates = @()
    if ($env:MAKENSIS_PATH) {
        $Candidates += $env:MAKENSIS_PATH
    }
    $Command = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($Command) {
        $Candidates += $Command.Source
    }
    $Candidates += @(
        "C:\Program Files\NSIS\makensis.exe",
        "C:\Program Files (x86)\NSIS\makensis.exe"
    )
    $ElectronBuilderCache = Join-Path $env:LOCALAPPDATA "electron-builder\Cache"
    if (Test-Path -LiteralPath $ElectronBuilderCache) {
        $Candidates += Get-ChildItem -LiteralPath $ElectronBuilderCache -Recurse -Filter makensis.exe -File -ErrorAction SilentlyContinue |
            Sort-Object Length -Descending |
            Select-Object -ExpandProperty FullName
    }
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }
    throw "NSIS is missing. Install it with: winget install --id NSIS.NSIS --exact"
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "The Windows Python environment is missing. Run .\windows\run_native.ps1 setup first."
}
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "The .NET SDK is missing. Install it with: winget install --id Microsoft.DotNet.SDK.10 --exact"
}
if (-not (Test-Path -LiteralPath $SyntheticInput)) {
    & $PythonExe (Join-Path $TestsRoot "smoke_pipeline.py") --output-root (Join-Path $WindowsRoot "build\smoke-output")
    Assert-Success "synthetic analysis fixture generation"
}

Push-Location $RepositoryRoot
try {
    if (-not $SkipDependencies) {
        & $PythonExe -m pip install -r (Join-Path $BackendRoot "requirements-native.txt")
        Assert-Success "native engine dependency installation"
    }

    & $PythonExe -m compileall -q $BackendRoot $TestsRoot
    Assert-Success "native Python compile check"
    & $PythonExe -m unittest discover -s $TestsRoot -p "test_compute_runtime.py" -v
    Assert-Success "CPU and OpenCL compute parity tests"
    & $PythonExe (Join-Path $BackendRoot "native_engine.py") --smoke-test
    Assert-Success "source Matplotlib renderer smoke test"
    dotnet build $ProjectPath --configuration Release
    Assert-Success "native WPF build"

    Assert-ChildPath $BuildRoot (Join-Path $WindowsRoot "build")
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $BuildRoot, $DistRoot | Out-Null

    & $PythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $EngineDistRoot `
        --workpath $PyInstallerWork `
        (Join-Path $BackendRoot "SpatialScopeEngine.spec")
    Assert-Success "native engine freeze"

    $FrozenEngine = Join-Path $EngineDistRoot "SpatialScopeEngine\SpatialScopeEngine.exe"
    if (-not (Test-Path -LiteralPath $FrozenEngine)) {
        throw "PyInstaller did not produce $FrozenEngine"
    }
    & $FrozenEngine --smoke-test
    Assert-Success "frozen Matplotlib renderer smoke test"
    & $PythonExe (Join-Path $TestsRoot "native_overlay_smoke.py") `
        --engine-executable $FrozenEngine `
        --input-folder $SyntheticInput `
        --output-folder (Join-Path $BuildRoot "frozen-overlay-smoke")
    Assert-Success "frozen configure-to-overlay smoke test"

    dotnet publish $ProjectPath `
        --configuration Release `
        --runtime win-x64 `
        --self-contained true `
        -p:PublishSingleFile=true `
        -p:IncludeNativeLibrariesForSelfExtract=true `
        -p:DebugType=None `
        -p:DebugSymbols=false `
        --output $PublishRoot
    Assert-Success "self-contained WPF publish"

    New-Item -ItemType Directory -Force $EngineStage | Out-Null
    Copy-Item -Path (Join-Path $EngineDistRoot "SpatialScopeEngine\*") -Destination $EngineStage -Recurse -Force
    if (-not (Test-Path -LiteralPath (Join-Path $EngineStage "SpatialScopeEngine.exe"))) {
        throw "The staged WPF package is missing SpatialScopeEngine.exe"
    }

    & $PythonExe (Join-Path $TestsRoot "native_overlay_smoke.py") `
        --engine-executable (Join-Path $EngineStage "SpatialScopeEngine.exe") `
        --input-folder $SyntheticInput `
        --output-folder (Join-Path $BuildRoot "staged-overlay-smoke")
    Assert-Success "staged configure-to-overlay smoke test"

    if ($FullSmoke) {
        $StagedEngine = Join-Path $EngineStage "SpatialScopeEngine.exe"
        & $PythonExe (Join-Path $TestsRoot "native_engine_smoke.py") `
            --engine-executable $StagedEngine `
            --input-folder $SyntheticInput `
            --output-folder (Join-Path $BuildRoot "staged-full-smoke")
        Assert-Success "staged full scientific workflow smoke test"

        $HadGpuMode = Test-Path -LiteralPath Env:SPATIALSCOPE_GPU_MODE
        $PreviousGpuMode = $env:SPATIALSCOPE_GPU_MODE
        try {
            $env:SPATIALSCOPE_GPU_MODE = "require"
            & $PythonExe (Join-Path $TestsRoot "native_gpu_parity.py") `
                --engine-executable $StagedEngine `
                --input-folder $SyntheticInput `
                --output-root (Join-Path $BuildRoot "staged-gpu-parity")
            Assert-Success "staged required-GPU scientific parity test"
        }
        finally {
            if ($HadGpuMode) {
                $env:SPATIALSCOPE_GPU_MODE = $PreviousGpuMode
            }
            else {
                Remove-Item -LiteralPath Env:SPATIALSCOPE_GPU_MODE -ErrorAction SilentlyContinue
            }
        }
    }

    [xml]$ProjectXml = Get-Content -LiteralPath $ProjectPath -Raw
    $Version = [string]$ProjectXml.Project.PropertyGroup.Version
    $MakeNsis = Resolve-MakeNsis
    Assert-ChildPath $DistRoot $NativeRoot
    Get-ChildItem -LiteralPath $DistRoot -Filter "SpatialScope-Windows-x64-Portable-*.zip" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force
    $SetupExe = Join-Path $DistRoot "SpatialScope-Windows-x64-Setup.exe"
    Remove-Item -LiteralPath $SetupExe -Force -ErrorAction SilentlyContinue
    & $MakeNsis `
        "/DAPP_VERSION=$Version" `
        "/DSOURCE_DIR=$PublishRoot" `
        "/DOUTPUT_DIR=$DistRoot" `
        "/DICON_PATH=$(Join-Path $WindowsRoot 'desktop\assets\SpatialScope.ico')" `
        $InstallerScript
    Assert-Success "NSIS installer build"
    if (-not (Test-Path -LiteralPath $SetupExe)) {
        throw "NSIS did not produce $SetupExe"
    }

    Assert-ChildPath $InstallerSmokeRoot $BuildRoot
    Remove-Item -LiteralPath $InstallerSmokeRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $InstallerSmokeRoot | Out-Null

    # Prove that a user-selected non-empty directory without the installer
    # ownership marker is rejected without changing its contents.
    $ProtectedRoot = Join-Path $InstallerSmokeRoot "protected"
    $ProtectedEngine = Join-Path $ProtectedRoot "engine"
    $ProtectedSentinel = Join-Path $ProtectedEngine "keep.txt"
    New-Item -ItemType Directory -Force $ProtectedEngine | Out-Null
    [System.IO.File]::WriteAllText($ProtectedSentinel, "user-owned sentinel", [System.Text.Encoding]::UTF8)
    $ProtectedInstallerProcess = Start-Process `
        -FilePath $SetupExe `
        -ArgumentList @("/S", "/SMOKETEST", "/D=$ProtectedRoot") `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($ProtectedInstallerProcess.ExitCode -eq 0) {
        throw "The installer accepted a non-empty directory without a SpatialScope ownership marker."
    }
    if (-not (Test-Path -LiteralPath $ProtectedSentinel) -or
        (Get-Content -LiteralPath $ProtectedSentinel -Raw) -ne "user-owned sentinel") {
        throw "The installer modified a protected user-owned directory."
    }
    if ((Test-Path -LiteralPath (Join-Path $ProtectedRoot ".spatialscope-install")) -or
        (Test-Path -LiteralPath (Join-Path $ProtectedRoot "SpatialScope.exe"))) {
        throw "The rejected installer run left SpatialScope files in the protected directory."
    }

    $InstalledRoot = Join-Path $InstallerSmokeRoot "installed"
    $InstallerProcess = Start-Process `
        -FilePath $SetupExe `
        -ArgumentList @("/S", "/SMOKETEST", "/D=$InstalledRoot") `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($InstallerProcess.ExitCode -ne 0) {
        throw "silent installer smoke test failed with exit code $($InstallerProcess.ExitCode)"
    }
    $InstalledApp = Join-Path $InstalledRoot "SpatialScope.exe"
    $InstalledEngine = Join-Path $InstalledRoot "engine\SpatialScopeEngine.exe"
    $InstallMarker = Join-Path $InstalledRoot ".spatialscope-install"
    $SmokeMarker = Join-Path $InstalledRoot ".spatialscope-smoke"
    if (-not (Test-Path -LiteralPath $InstalledApp) -or
        -not (Test-Path -LiteralPath $InstalledEngine) -or
        -not (Test-Path -LiteralPath $InstallMarker) -or
        -not (Test-Path -LiteralPath $SmokeMarker)) {
        throw "The installer smoke test did not install the app, engine, and safety markers."
    }
    $CapturePath = Join-Path $InstallerSmokeRoot "installed-app.png"
    $PreviousCapturePath = $env:SPATIALSCOPE_CAPTURE_PATH
    $PreviousCaptureExit = $env:SPATIALSCOPE_CAPTURE_EXIT
    try {
        $env:SPATIALSCOPE_CAPTURE_PATH = $CapturePath
        $env:SPATIALSCOPE_CAPTURE_EXIT = "1"
        $InstalledProcess = Start-Process -FilePath $InstalledApp -WindowStyle Hidden -PassThru
        if (-not $InstalledProcess.WaitForExit(30000)) {
            Stop-Process -Id $InstalledProcess.Id -Force -ErrorAction SilentlyContinue
            throw "The installed SpatialScope app did not finish its launch smoke test."
        }
    }
    finally {
        $env:SPATIALSCOPE_CAPTURE_PATH = $PreviousCapturePath
        $env:SPATIALSCOPE_CAPTURE_EXIT = $PreviousCaptureExit
    }
    if (-not (Test-Path -LiteralPath $CapturePath)) {
        throw "The installed SpatialScope app did not render its launch capture."
    }
    $Uninstaller = Join-Path $InstalledRoot "Uninstall SpatialScope.exe"
    $UninstallProcess = Start-Process -FilePath $Uninstaller -ArgumentList "/S" -WindowStyle Hidden -Wait -PassThru
    if ($UninstallProcess.ExitCode -ne 0 -or (Test-Path -LiteralPath $InstalledApp)) {
        throw "silent uninstaller smoke test failed."
    }

    $Hash = (Get-FileHash -LiteralPath $SetupExe -Algorithm SHA256).Hash.ToLowerInvariant()
    $HashPath = Join-Path $DistRoot "SHA256SUMS-Windows.txt"
    [System.IO.File]::WriteAllText(
        $HashPath,
        "$Hash  $(Split-Path -Leaf $SetupExe)`n",
        [System.Text.Encoding]::ASCII
    )

    Write-Host "Native SpatialScope $Version is ready."
    Write-Host "Run: $(Join-Path $PublishRoot 'SpatialScope.exe')"
    Write-Host "Installer: $SetupExe"
}
finally {
    Pop-Location
}
