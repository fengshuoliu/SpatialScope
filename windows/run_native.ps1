param(
    [ValidateSet("setup", "run", "test")]
    [string]$Command = "run"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$WindowsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendRoot = Join-Path $WindowsRoot "backend"
$TestsRoot = Join-Path $WindowsRoot "tests"
$VenvRoot = Join-Path $WindowsRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$RequirementsPath = Join-Path $BackendRoot "requirements-native.txt"
$RequirementsStamp = Join-Path $VenvRoot "requirements-native.sha256"
$ProjectPath = Join-Path $WindowsRoot "native\src\SpatialScope.App\SpatialScope.App.csproj"
$UpdaterTestProject = Join-Path $WindowsRoot "native\tests\SpatialScope.Updater.ContractTests\SpatialScope.Updater.ContractTests.csproj"
$SmokeRoot = Join-Path $WindowsRoot "build\smoke-output"
$SyntheticInput = Join-Path $SmokeRoot "synthetic_input"

function Assert-Success([string]$Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE"
    }
}

function Find-Python311 {
    $Candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        "C:\Program Files\Python311\python.exe"
    )
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        $Candidates += $PythonCommand.Source
    }

    foreach ($Candidate in $Candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $Candidate)) {
            continue
        }
        $Version = & $Candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $Version -eq "3.11") {
            return $Candidate
        }
    }
    throw "Python 3.11 was not found. Install it with: winget install --id Python.Python.3.11 --exact"
}

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "The .NET 10 SDK was not found. Install it with: winget install --id Microsoft.DotNet.SDK.10 --exact"
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $BootstrapPython = Find-Python311
    Write-Host "Creating the SpatialScope Python 3.11 environment..."
    & $BootstrapPython -m venv $VenvRoot
    Assert-Success "Python virtual environment creation"
}

$RequirementsHash = (Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
$InstalledHash = if (Test-Path -LiteralPath $RequirementsStamp) {
    (Get-Content -LiteralPath $RequirementsStamp -Raw).Trim()
} else {
    ""
}
if ($InstalledHash -ne $RequirementsHash) {
    Write-Host "Installing SpatialScope native scientific dependencies..."
    & $VenvPython -m pip install --upgrade pip
    Assert-Success "pip upgrade"
    & $VenvPython -m pip install -r $RequirementsPath
    Assert-Success "native dependency installation"
    [System.IO.File]::WriteAllText($RequirementsStamp, "$RequirementsHash`n", [System.Text.Encoding]::ASCII)
}

if ($Command -eq "setup") {
    Write-Host "SpatialScope native Windows development setup is ready."
    exit 0
}

if ($Command -eq "test") {
    & $VenvPython -m compileall -q $BackendRoot $TestsRoot
    Assert-Success "native Python compile check"
    & $VenvPython -m unittest discover -s $TestsRoot -p "test_compute_runtime.py" -v
    Assert-Success "CPU and OpenCL compute parity tests"
    & $VenvPython (Join-Path $BackendRoot "native_engine.py") --smoke-test
    Assert-Success "source Matplotlib renderer smoke test"
    dotnet run --project $UpdaterTestProject --configuration Release
    Assert-Success "native updater contract tests"
    dotnet build $ProjectPath --configuration Release
    Assert-Success "native WPF build"

    if (-not (Test-Path -LiteralPath $SyntheticInput)) {
        & $VenvPython (Join-Path $TestsRoot "smoke_pipeline.py") --output-root $SmokeRoot
        Assert-Success "synthetic analysis fixture generation"
    }
    & $VenvPython (Join-Path $TestsRoot "native_engine_smoke.py") `
        --python $VenvPython `
        --input-folder $SyntheticInput `
        --output-folder (Join-Path $WindowsRoot "build\native-source-smoke")
    Assert-Success "source nine-step scientific workflow smoke test"
    exit 0
}

dotnet run --project $ProjectPath
exit $LASTEXITCODE
