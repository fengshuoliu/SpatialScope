param(
    [switch]$SkipDependencies,
    [switch]$SkipAnalysisSmoke
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$WindowsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepositoryRoot = Split-Path -Parent $WindowsRoot
$BackendRoot = Join-Path $WindowsRoot "backend"
$DesktopRoot = Join-Path $WindowsRoot "desktop"
$BuildRoot = Join-Path $WindowsRoot "build"
$BackendDist = Join-Path $BuildRoot "SpatialScopeBackend"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller-work"
$SmokeRoot = Join-Path $BuildRoot "smoke-output"
$BackendSmokeRoot = Join-Path $BuildRoot "backend-smoke"

function Assert-NativeSuccess([string]$Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE"
    }
}

Push-Location $RepositoryRoot
try {
    if (-not $SkipDependencies) {
        python -m pip install --upgrade pip
        Assert-NativeSuccess "pip upgrade"
        python -m pip install -r (Join-Path $BackendRoot "requirements.txt")
        Assert-NativeSuccess "backend dependency installation"
        python -m pip install "pyinstaller==6.21.0"
        Assert-NativeSuccess "PyInstaller installation"
    }

    python -m compileall -q $BackendRoot (Join-Path $WindowsRoot "tests")
    Assert-NativeSuccess "Python compile check"
    node --check (Join-Path $DesktopRoot "main.js")
    Assert-NativeSuccess "Electron main-process syntax check"
    node --check (Join-Path $DesktopRoot "preload.js")
    Assert-NativeSuccess "Electron preload syntax check"

    if (-not $SkipAnalysisSmoke) {
        python (Join-Path $WindowsRoot "tests/smoke_pipeline.py") --output-root $SmokeRoot
        Assert-NativeSuccess "end-to-end analysis smoke test"
    }

    Remove-Item -Recurse -Force $BackendDist -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $PyInstallerWork -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $BuildRoot | Out-Null

    python -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $BuildRoot `
        --workpath $PyInstallerWork `
        (Join-Path $BackendRoot "SpatialScopeBackend.spec")
    Assert-NativeSuccess "PyInstaller backend build"

    $BackendExe = Join-Path $BackendDist "SpatialScopeBackend.exe"
    if (-not (Test-Path $BackendExe)) {
        throw "PyInstaller did not produce $BackendExe"
    }
    & $BackendExe `
        --port 18768 `
        --session-root $BackendSmokeRoot `
        --settings-path (Join-Path $BackendSmokeRoot "settings.json") `
        --desktop-paths-path (Join-Path $BackendSmokeRoot "desktop-paths.json") `
        --system-language en `
        --smoke-test
    Assert-NativeSuccess "frozen backend smoke test"

    Push-Location $DesktopRoot
    try {
        if (-not $SkipDependencies) {
            npm ci
            Assert-NativeSuccess "npm dependency installation"
        }
        npm run dist
        Assert-NativeSuccess "Electron Windows packaging"
    }
    finally {
        Pop-Location
    }

    $DesktopDist = Join-Path $DesktopRoot "dist"
    $Version = (Get-Content (Join-Path $DesktopRoot "package.json") | ConvertFrom-Json).version
    $SetupExe = Join-Path $DesktopDist "SpatialScope-Windows-x64-Setup-$Version.exe"
    $PortableExe = Join-Path $DesktopDist "SpatialScope-Windows-x64-Portable-$Version.exe"
    $UpdateMetadata = Join-Path $DesktopDist "latest.yml"
    foreach ($RequiredPath in @($SetupExe, $PortableExe, $UpdateMetadata)) {
        if (-not (Test-Path $RequiredPath)) {
            throw "Missing Windows release artifact: $RequiredPath"
        }
    }

    $HashPath = Join-Path $DesktopDist "SHA256SUMS-Windows.txt"
    $HashLines = foreach ($ArtifactPath in @($SetupExe, $PortableExe)) {
        $Hash = (Get-FileHash -Algorithm SHA256 $ArtifactPath).Hash.ToLowerInvariant()
        "$Hash  $(Split-Path -Leaf $ArtifactPath)"
    }
    Set-Content -Path $HashPath -Value $HashLines -Encoding ascii

    Write-Host "SpatialScope Windows $Version is ready in $DesktopDist"
}
finally {
    Pop-Location
}
