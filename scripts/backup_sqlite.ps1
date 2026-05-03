$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$dbPath = Join-Path $root "data\law_monitor.db"
$backupDir = Join-Path $root "data\backups"

if (-not (Test-Path $dbPath)) {
    throw "SQLite database not found: $dbPath"
}

New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupPath = Join-Path $backupDir "law_monitor_$timestamp.db"

Copy-Item -LiteralPath $dbPath -Destination $backupPath -Force
Write-Output "Backup created: $backupPath"
