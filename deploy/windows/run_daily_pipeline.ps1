# Windows Task Scheduler entry point for the daily pipeline (dev-machine
# stand-in for deploy/systemd/stock-daily-pipeline.* on a real host).
# job_runs (app/jobs/_tracking.py) already records structured status for
# every step; this log only catches what job_runs can't -- a crash before
# any row gets written.
#
# Redirection goes through cmd /c rather than PowerShell's own *>> --
# python's logging module writes INFO lines to stderr, and PowerShell 5.1
# wraps every redirected stderr line from a native exe in a terminating
# NativeCommandError, which aborts the script on the first log line.
Set-Location "D:\stock application"

$logDir = "D:\stock application\deploy\windows\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "daily_pipeline.log"

Add-Content -Path $logFile -Value "=== $(Get-Date -Format o) ==="
cmd /c "`"D:\stock application\.venv\Scripts\python.exe`" -m app.jobs.daily_pipeline >> `"$logFile`" 2>&1"
