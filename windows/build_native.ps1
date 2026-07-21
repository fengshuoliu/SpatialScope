param(
    [switch]$SkipDependencies,
    [switch]$FullSmoke,
    [switch]$RequireGpuParity
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
$AppContractTestProject = Join-Path $NativeRoot "tests\SpatialScope.App.ContractTests\SpatialScope.App.ContractTests.csproj"
$UpdaterTestProject = Join-Path $NativeRoot "tests\SpatialScope.Updater.ContractTests\SpatialScope.Updater.ContractTests.csproj"
$BuildRoot = Join-Path $WindowsRoot "build\native-release"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller-work"
$EngineDistRoot = Join-Path $BuildRoot "engine-dist"
$PublishRoot = Join-Path $BuildRoot "SpatialScope-Windows-x64"
$EngineStage = Join-Path $PublishRoot "engine"
$DistRoot = Join-Path $NativeRoot "dist"
$InstallerSmokeRoot = Join-Path $BuildRoot "installer-smoke"
$PythonExe = Join-Path $WindowsRoot ".venv\Scripts\python.exe"
$SyntheticInput = Join-Path $WindowsRoot "build\smoke-output\synthetic_input"

if ($RequireGpuParity -and -not $FullSmoke) {
    throw "-RequireGpuParity must be used together with -FullSmoke."
}

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

function Stop-ProcessesAtExactPaths([string[]]$ExecutablePaths) {
    $ExpectedPaths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase)
    foreach ($ExecutablePath in $ExecutablePaths) {
        if ($ExecutablePath) {
            [void]$ExpectedPaths.Add([System.IO.Path]::GetFullPath($ExecutablePath))
        }
    }

    foreach ($Process in Get-Process -ErrorAction SilentlyContinue) {
        try {
            $ProcessPath = $Process.Path
            if ($ProcessPath -and $ExpectedPaths.Contains([System.IO.Path]::GetFullPath($ProcessPath))) {
                Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
            }
        }
        catch {
            # Access to unrelated system processes can be denied; exact-path
            # cleanup remains best-effort and never targets a process by name.
        }
        finally {
            $Process.Dispose()
        }
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
    & $PythonExe -m unittest discover -s $TestsRoot -p "test_nuclei_optimizer_grouping.py" -v
    Assert-Success "exact nuclei optimizer grouping tests"
    & $PythonExe -m unittest discover -s $TestsRoot -p "test_optimizer_fixed_parameters.py" -v
    Assert-Success "optimizer fixed-parameter contract tests"
    & $PythonExe -m unittest discover -s $TestsRoot -p "test_celltype_optimizer_recommendation.py" -v
    Assert-Success "cell-type optimizer recommendation objective tests"
    & $PythonExe -m unittest discover -s $TestsRoot -p "test_assignment_parameter_parity.py" -v
    Assert-Success "assignment parameter parity tests"
    & $PythonExe -m unittest discover -s $TestsRoot -p "test_celltype_vectorized_rules.py" -v
    Assert-Success "exact vectorized cell-type rule tests"
    & $PythonExe -m unittest discover -s $TestsRoot -p "test_neighborhood_outputs.py" -v
    Assert-Success "separate neighborhood map and cluster-key tests"
    & $PythonExe -m unittest discover -s $TestsRoot -p "test_region_overlay_filtering.py" -v
    Assert-Success "Region overlay filtering tests"
    & $PythonExe -m unittest discover -s $TestsRoot -p "test_region_output_filenames.py" -v
    Assert-Success "human-readable Region output filename tests"
    & $PythonExe (Join-Path $TestsRoot "region_registry_rerun_smoke.py")
    Assert-Success "Region registry rerun smoke test"
    & $PythonExe (Join-Path $TestsRoot "distribution_preview_contract_smoke.py")
    Assert-Success "cell distribution preview contract smoke test"
    & $PythonExe (Join-Path $TestsRoot "optimizer_acceleration_smoke.py") `
        --work-root (Join-Path $WindowsRoot "build\optimizer-acceleration-smoke") `
        --minimum-speedup 2.0
    Assert-Success "exact optimizer parity and acceleration smoke test"
    & $PythonExe (Join-Path $BackendRoot "native_engine.py") --smoke-test
    Assert-Success "source Matplotlib renderer smoke test"
    dotnet run --project $UpdaterTestProject --configuration Release
    Assert-Success "native updater contract tests"
    dotnet run --project $AppContractTestProject --configuration Release
    Assert-Success "native WPF live-preview contract tests"
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

        if ($RequireGpuParity) {
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
    Assert-ChildPath $InstalledApp $InstallerSmokeRoot
    Assert-ChildPath $InstalledEngine $InstallerSmokeRoot

    # Exercise the updater handoff itself with the installed app as the real
    # blocker: NSIS must wait for its PID and named mutex, replace only the
    # owned installation, then relaunch the updated app.
    $PreUpdateCapturePath = Join-Path $InstallerSmokeRoot "pre-update-app.png"
    $UpdateCapturePath = Join-Path $InstallerSmokeRoot "updated-app.png"
    $UpdatePreviousCapturePath = $env:SPATIALSCOPE_CAPTURE_PATH
    $UpdatePreviousCaptureExit = $env:SPATIALSCOPE_CAPTURE_EXIT
    $UpdatePreviousExitDelay = $env:SPATIALSCOPE_QA_EXIT_DELAY_MS
    $UpdateHadCheckSetting = Test-Path -LiteralPath Env:SPATIALSCOPE_DISABLE_UPDATE_CHECK
    $UpdatePreviousCheckSetting = $env:SPATIALSCOPE_DISABLE_UPDATE_CHECK
    $HadQaMutexSetting = Test-Path -LiteralPath Env:SPATIALSCOPE_QA_INSTANCE_MUTEX
    $PreviousQaMutexSetting = $env:SPATIALSCOPE_QA_INSTANCE_MUTEX
    $SmokeInstanceMutexName = "Local\SpatialScope.Windows.Application.QA.$([Guid]::NewGuid().ToString('N'))"
    $env:SPATIALSCOPE_QA_INSTANCE_MUTEX = $SmokeInstanceMutexName
    $PriorInstalledProcess = $null
    $DuplicateInstalledProcess = $null
    $UpdateInstallerProcess = $null
    try {
        $env:SPATIALSCOPE_CAPTURE_PATH = $PreUpdateCapturePath
        $env:SPATIALSCOPE_CAPTURE_EXIT = "1"
        $env:SPATIALSCOPE_QA_EXIT_DELAY_MS = "5000"
        $env:SPATIALSCOPE_DISABLE_UPDATE_CHECK = "1"
        [System.IO.File]::WriteAllText($InstallMarker, "waiting for prior process", [System.Text.Encoding]::ASCII)
        $PriorInstalledProcess = Start-Process `
            -FilePath $InstalledApp `
            -ArgumentList "--qa-smoke" `
            -WindowStyle Hidden `
            -PassThru
        $PreUpdateCaptureDeadline = [DateTime]::UtcNow.AddSeconds(30)
        while (-not (Test-Path -LiteralPath $PreUpdateCapturePath) -and
            -not $PriorInstalledProcess.HasExited -and
            [DateTime]::UtcNow -lt $PreUpdateCaptureDeadline) {
            Start-Sleep -Milliseconds 200
        }
        if (-not (Test-Path -LiteralPath $PreUpdateCapturePath) -or $PriorInstalledProcess.HasExited) {
            throw "The installed app did not enter the updater PID-wait smoke-test state."
        }

        $DuplicateCapturePath = Join-Path $InstallerSmokeRoot "duplicate-app.png"
        $env:SPATIALSCOPE_CAPTURE_PATH = $DuplicateCapturePath
        $env:SPATIALSCOPE_QA_EXIT_DELAY_MS = "0"
        $DuplicateInstalledProcess = Start-Process `
            -FilePath $InstalledApp `
            -ArgumentList "--qa-smoke" `
            -WindowStyle Hidden `
            -PassThru
        if (-not $DuplicateInstalledProcess.WaitForExit(5000)) {
            throw "A duplicate SpatialScope instance did not exit while the primary instance held the mutex."
        }
        if (Test-Path -LiteralPath $DuplicateCapturePath) {
            throw "A duplicate SpatialScope instance opened a window instead of respecting the single-instance mutex."
        }

        # The installer inherits these values for the app it relaunches; the
        # already-running old process retains its original delayed-exit values.
        $env:SPATIALSCOPE_CAPTURE_PATH = $UpdateCapturePath
        $env:SPATIALSCOPE_QA_EXIT_DELAY_MS = "0"
        $UpdateInstallerProcess = Start-Process `
            -FilePath $SetupExe `
            -ArgumentList @("/S", "/SMOKETEST", "/UPDATEPID=$($PriorInstalledProcess.Id)", "/D=$InstalledRoot") `
            -WindowStyle Hidden `
            -PassThru
        Start-Sleep -Milliseconds 1000
        if ($UpdateInstallerProcess.HasExited) {
            throw "The update installer did not wait for the previous application PID."
        }
        if ((Get-Content -LiteralPath $InstallMarker -Raw) -ne "waiting for prior process") {
            throw "The update installer modified files before the previous application PID exited."
        }
        if (-not $PriorInstalledProcess.WaitForExit(20000)) {
            throw "The old installed app did not exit during the updater handoff smoke test."
        }
        if (-not $UpdateInstallerProcess.WaitForExit(60000) -or $UpdateInstallerProcess.ExitCode -ne 0) {
            throw "The update installer did not finish successfully after the previous PID exited."
        }
        if ((Get-Content -LiteralPath $InstallMarker -Raw).Trim() -ne "SpatialScope $Version") {
            throw "The update installer did not replace the owned installation after the previous PID exited."
        }
        $UpdateCaptureDeadline = [DateTime]::UtcNow.AddSeconds(60)
        while (-not (Test-Path -LiteralPath $UpdateCapturePath) -and [DateTime]::UtcNow -lt $UpdateCaptureDeadline) {
            Start-Sleep -Milliseconds 200
        }
        if (-not (Test-Path -LiteralPath $UpdateCapturePath)) {
            throw "The update installer did not relaunch the updated SpatialScope app."
        }

        # Do not start the following launch smoke until the relaunched app has
        # released the single-instance mutex and completed engine shutdown.
        $MutexExitDeadline = [DateTime]::UtcNow.AddSeconds(30)
        $MutexStillExists = $true
        while ($MutexStillExists -and [DateTime]::UtcNow -lt $MutexExitDeadline) {
            $MutexProbe = $null
            try {
                $MutexProbe = [System.Threading.Mutex]::OpenExisting($SmokeInstanceMutexName)
                $MutexStillExists = $true
            }
            catch [System.Threading.WaitHandleCannotBeOpenedException] {
                $MutexStillExists = $false
            }
            finally {
                if ($MutexProbe) { $MutexProbe.Dispose() }
            }
            if ($MutexStillExists) { Start-Sleep -Milliseconds 200 }
        }
        if ($MutexStillExists) {
            throw "The relaunched updated app did not complete a clean shutdown."
        }
    }
    finally {
        if ($PriorInstalledProcess -and -not $PriorInstalledProcess.HasExited) {
            Stop-Process -Id $PriorInstalledProcess.Id -Force -ErrorAction SilentlyContinue
        }
        if ($DuplicateInstalledProcess -and -not $DuplicateInstalledProcess.HasExited) {
            Stop-Process -Id $DuplicateInstalledProcess.Id -Force -ErrorAction SilentlyContinue
        }
        if ($UpdateInstallerProcess -and -not $UpdateInstallerProcess.HasExited) {
            Stop-Process -Id $UpdateInstallerProcess.Id -Force -ErrorAction SilentlyContinue
        }
        Stop-ProcessesAtExactPaths @($InstalledApp, $InstalledEngine)
        $env:SPATIALSCOPE_CAPTURE_PATH = $UpdatePreviousCapturePath
        $env:SPATIALSCOPE_CAPTURE_EXIT = $UpdatePreviousCaptureExit
        $env:SPATIALSCOPE_QA_EXIT_DELAY_MS = $UpdatePreviousExitDelay
        if ($UpdateHadCheckSetting) {
            $env:SPATIALSCOPE_DISABLE_UPDATE_CHECK = $UpdatePreviousCheckSetting
        } else {
            Remove-Item -LiteralPath Env:SPATIALSCOPE_DISABLE_UPDATE_CHECK -ErrorAction SilentlyContinue
        }
        if ($HadQaMutexSetting) {
            $env:SPATIALSCOPE_QA_INSTANCE_MUTEX = $PreviousQaMutexSetting
        } else {
            Remove-Item -LiteralPath Env:SPATIALSCOPE_QA_INSTANCE_MUTEX -ErrorAction SilentlyContinue
        }
    }

    $CapturePath = Join-Path $InstallerSmokeRoot "installed-app.png"
    $PreviousCapturePath = $env:SPATIALSCOPE_CAPTURE_PATH
    $PreviousCaptureExit = $env:SPATIALSCOPE_CAPTURE_EXIT
    $HadUpdateCheckSetting = Test-Path -LiteralPath Env:SPATIALSCOPE_DISABLE_UPDATE_CHECK
    $PreviousUpdateCheckSetting = $env:SPATIALSCOPE_DISABLE_UPDATE_CHECK
    $InstalledProcess = $null
    try {
        $env:SPATIALSCOPE_QA_INSTANCE_MUTEX = $SmokeInstanceMutexName
        $env:SPATIALSCOPE_CAPTURE_PATH = $CapturePath
        $env:SPATIALSCOPE_CAPTURE_EXIT = "1"
        $env:SPATIALSCOPE_DISABLE_UPDATE_CHECK = "1"
        $InstalledProcess = Start-Process `
            -FilePath $InstalledApp `
            -ArgumentList "--qa-smoke" `
            -WindowStyle Hidden `
            -PassThru
        if (-not $InstalledProcess.WaitForExit(30000)) {
            Stop-Process -Id $InstalledProcess.Id -Force -ErrorAction SilentlyContinue
            throw "The installed SpatialScope app did not finish its launch smoke test."
        }
    }
    finally {
        if ($InstalledProcess -and -not $InstalledProcess.HasExited) {
            Stop-Process -Id $InstalledProcess.Id -Force -ErrorAction SilentlyContinue
        }
        Stop-ProcessesAtExactPaths @($InstalledApp, $InstalledEngine)
        $env:SPATIALSCOPE_CAPTURE_PATH = $PreviousCapturePath
        $env:SPATIALSCOPE_CAPTURE_EXIT = $PreviousCaptureExit
        if ($HadUpdateCheckSetting) {
            $env:SPATIALSCOPE_DISABLE_UPDATE_CHECK = $PreviousUpdateCheckSetting
        } else {
            Remove-Item -LiteralPath Env:SPATIALSCOPE_DISABLE_UPDATE_CHECK -ErrorAction SilentlyContinue
        }
        if ($HadQaMutexSetting) {
            $env:SPATIALSCOPE_QA_INSTANCE_MUTEX = $PreviousQaMutexSetting
        } else {
            Remove-Item -LiteralPath Env:SPATIALSCOPE_QA_INSTANCE_MUTEX -ErrorAction SilentlyContinue
        }
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
