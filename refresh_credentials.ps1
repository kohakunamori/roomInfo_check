$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-PythonHasPlaywright {
    param([string]$PythonExe)
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        return $false
    }

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $null = & $PythonExe -c "from playwright.sync_api import sync_playwright" 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

$candidates = New-Object System.Collections.Generic.List[string]
foreach ($path in @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv\bin\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
)) {
    if ($path) {
        [void]$candidates.Add($path)
    }
}

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $pyLauncher) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $pyList = & py -0p 2>&1
        foreach ($line in $pyList) {
            if ($line -match ':\s*(.+\.exe)\s*$') {
                [void]$candidates.Add($Matches[1].Trim())
            }
        }
    } finally {
        $ErrorActionPreference = $previous
    }
}

$selectedPython = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (Test-PythonHasPlaywright -PythonExe $candidate) {
        $selectedPython = $candidate
        break
    }
}

if (-not $selectedPython) {
    $fallbackA = Join-Path $projectRoot ".venv\Scripts\python.exe"
    $fallbackB = Join-Path $projectRoot ".venv\bin\python.exe"
    if (Test-Path -LiteralPath $fallbackA) {
        $selectedPython = $fallbackA
    } elseif (Test-Path -LiteralPath $fallbackB) {
        $selectedPython = $fallbackB
    }
}

if (-not $selectedPython) {
    throw "No Python found for refresh_credentials.py"
}

Write-Host "Using Python: $selectedPython"
& $selectedPython (Join-Path $projectRoot "refresh_credentials.py") @args
exit $LASTEXITCODE
