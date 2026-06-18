$ErrorActionPreference = "Stop"

Write-Host "Starting Exampulse status..." -ForegroundColor Cyan

Set-Location -LiteralPath $PSScriptRoot

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "ERROR: Project virtual environment was not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Run these setup commands from the project root:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv"
    Write-Host "  .\.venv\Scripts\python.exe -m pip install -U pip"
    Write-Host "  .\.venv\Scripts\python.exe -m pip install -e ."
    exit 1
}

& $Python -c "import typer" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Dependencies are missing. Installing Exampulse into .venv..." -ForegroundColor Yellow
    & $Python -m pip install -U pip
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $Python -m pip install -e .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot ".env"))) {
    $EnvExample = Join-Path $PSScriptRoot ".env.example"
    if (Test-Path -LiteralPath $EnvExample) {
        Write-Host "Creating .env from .env.example..." -ForegroundColor Yellow
        Copy-Item -LiteralPath $EnvExample -Destination (Join-Path $PSScriptRoot ".env")
    }
    else {
        Write-Host "ERROR: .env was not found." -ForegroundColor Red
        exit 1
    }
}

function Invoke-Exampulse {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments
    )

    & $Python -m app.cli.main @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Write-Host "`n[1/4] Importing exams..." -ForegroundColor Yellow
$ExamsPath = Join-Path $PSScriptRoot "exams.json"
if (Test-Path -LiteralPath $ExamsPath) {
    Invoke-Exampulse -Arguments @("exams", "import", "exams.json", "--replace")
}
else {
    Write-Host "No exams.json found. Skipping exam import." -ForegroundColor Yellow
}

Write-Host "`n[2/4] Syncing WHOOP data..." -ForegroundColor Yellow
& $Python -m app.cli.main sync --days 30
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "WHOOP sync failed. Continuing with local data." -ForegroundColor Yellow
    Write-Host "If this is your first real sync, run: .\.venv\Scripts\python.exe -m app.cli.main auth" -ForegroundColor Yellow
}

Write-Host "`n[3/4] Today's status..." -ForegroundColor Yellow
Invoke-Exampulse -Arguments @("today", "--compact")

Write-Host "`n[4/4] Full report..." -ForegroundColor Yellow
Invoke-Exampulse -Arguments @("report", "--compact")

Write-Host "`nDone." -ForegroundColor Green
