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
$ProjectPath = Join-Path $NativeRoot "src\SpatialScope.App\SpatialScope.App.csproj"
$BuildRoot = Join-Path $WindowsRoot "build\native-release"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller-work"
$EngineDistRoot = Join-Path $BuildRoot "engine-dist"
$PublishRoot = Join-Path $BuildRoot "SpatialScope-Windows-x64"
$EngineStage = Join-Path $PublishRoot "engine"
$DistRoot = Join-Path $NativeRoot "dist"
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
        & $PythonExe (Join-Path $TestsRoot "native_engine_smoke.py") `
            --engine-executable (Join-Path $EngineStage "SpatialScopeEngine.exe") `
            --input-folder $SyntheticInput `
            --output-folder (Join-Path $BuildRoot "staged-full-smoke")
        Assert-Success "staged full scientific workflow smoke test"
    }

    [xml]$ProjectXml = Get-Content -LiteralPath $ProjectPath -Raw
    $Version = [string]$ProjectXml.Project.PropertyGroup.Version
    $PortableZip = Join-Path $DistRoot "SpatialScope-Windows-x64-Portable-$Version.zip"
    Compress-Archive -Path (Join-Path $PublishRoot "*") -DestinationPath $PortableZip -CompressionLevel Optimal -Force
    $Hash = (Get-FileHash -LiteralPath $PortableZip -Algorithm SHA256).Hash.ToLowerInvariant()
    $HashPath = Join-Path $DistRoot "SHA256SUMS-Windows.txt"
    [System.IO.File]::WriteAllText(
        $HashPath,
        "$Hash  $(Split-Path -Leaf $PortableZip)`n",
        [System.Text.Encoding]::ASCII
    )

    Write-Host "Native SpatialScope $Version is ready."
    Write-Host "Run: $(Join-Path $PublishRoot 'SpatialScope.exe')"
    Write-Host "Portable: $PortableZip"
}
finally {
    Pop-Location
}
