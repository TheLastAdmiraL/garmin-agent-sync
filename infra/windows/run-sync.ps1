$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repoRoot "functions\.venv\Scripts\python.exe"
$runner = Join-Path $repoRoot "functions\local_run.py"
$logDirectory = Join-Path $env:LOCALAPPDATA "GarminAgentSync\logs"
$logFile = Join-Path $logDirectory "sync.log"
$rotatedLog = Join-Path $logDirectory "sync.log.1"

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
if ((Test-Path -LiteralPath $logFile) -and (Get-Item -LiteralPath $logFile).Length -ge 5MB) {
    Remove-Item -LiteralPath $rotatedLog -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $logFile -Destination $rotatedLog
}

"[$(Get-Date -Format o)] Sync started" | Add-Content -LiteralPath $logFile -Encoding UTF8
& $python $runner sync 2>&1 | ForEach-Object {
    Add-Content -LiteralPath $logFile -Value ([string]$_) -Encoding UTF8
}
$syncExitCode = $LASTEXITCODE
"[$(Get-Date -Format o)] Sync exited with code $syncExitCode" | Add-Content -LiteralPath $logFile -Encoding UTF8
exit $syncExitCode
