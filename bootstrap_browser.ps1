$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-PythonHasPlaywright {
    param([string]$PythonExe)
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        return $false
    }

    # 探测失败不应因为 stderr 触发 $ErrorActionPreference=Stop。
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

# 项目 .venv 若是 MSYS2/MinGW Python，通常装不上 Playwright 官方 wheel。
# 额外探测 py launcher 列出的本机官方 CPython。
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
    throw "No Python with Playwright found. Install with official CPython (not MSYS2): python -m pip install playwright. If you use a local proxy, set HTTPS_PROXY=http://127.0.0.1:7890"
}

Write-Host "Using Python: $selectedPython"
& $selectedPython (Join-Path $projectRoot "bootstrap_browser.py") @args
exit $LASTEXITCODE
