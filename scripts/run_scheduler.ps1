$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

Push-Location $root
try {
    & $python "main.py" "run-scheduler"
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
