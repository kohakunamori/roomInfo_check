$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$candidates = @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv\bin\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe")
)

$python = $null
foreach ($c in $candidates) {
    if (Test-Path -LiteralPath $c) {
        $python = $c
        break
    }
}
if (-not $python) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { $python = "py" }
}
if (-not $python) {
    throw "No Python found to run package_release.py"
}

if ($python -eq "py") {
    & py -3 (Join-Path $projectRoot "package_release.py") @args
} else {
    & $python (Join-Path $projectRoot "package_release.py") @args
}
exit $LASTEXITCODE
