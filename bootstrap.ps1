$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$candidates = @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv\bin\python.exe")
)
$venvPython = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $venvPython) {
    throw "Project virtual environment not found. Run: python -m venv .venv"
}

& $venvPython (Join-Path $projectRoot "bootstrap_session.py") @args
exit $LASTEXITCODE
