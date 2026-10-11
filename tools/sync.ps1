[CmdletBinding()]
param(
    [string]$Config = "config.local.json",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
try {
    & $Python (Join-Path $PSScriptRoot "sync_records.py") --config $Config
    exit $LASTEXITCODE
} catch {
    Write-Error $_
    exit 1
}
