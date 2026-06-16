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

Write-Host "`n[1/3] Syncing WHOOP data..." -ForegroundColor Yellow
Invoke-Exampulse -Arguments @("sync", "--days", "30")

Write-Host "`n[2/3] Today's status..." -ForegroundColor Yellow
Invoke-Exampulse -Arguments @("today", "--compact")

Write-Host "`n[3/3] Full report..." -ForegroundColor Yellow
Invoke-Exampulse -Arguments @("report", "--compact")

Write-Host "`nDone." -ForegroundColor Green
