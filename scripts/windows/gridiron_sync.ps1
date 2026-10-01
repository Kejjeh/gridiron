<#
.SYNOPSIS
  What Task Scheduler runs every five minutes. One Sleeper sync, then exit.

.DESCRIPTION
  A thin wrapper, on purpose: all of the logic is in Python and is tested
  offline. This file exists only to supply the things a scheduled task needs
  and a Python script should not hardcode — which interpreter, which checkout,
  a hard wall-clock timeout, and a log that cannot grow without bound.

  NO AI, NO TOKENS. This starts a plain python.exe. Nothing here wakes Claude,
  Codex or any other assistant, and a run costs nothing but a few HTTP GETs.

  READ-ONLY. The Python it calls uses a GET-only Sleeper client. No lineup,
  claim, trade or message is ever submitted.

.PARAMETER RepoRoot
  The gridiron checkout to run from.

.PARAMETER Python
  python.exe to use. Any interpreter with the project's dependencies works.

.PARAMETER TimeoutSeconds
  Hard wall for one sync. Well under the five-minute interval so a hung run is
  killed before the next one is due, rather than the two of them overlapping.

.NOTES
  Proof that the wrapper cannot report a false success — run on the desktop:

    .\scripts\windows\gridiron_sync.ps1 -Python .\scripts\windows\selftest\fake_python_exit7.cmd
    $LASTEXITCODE          # must be 7, and the log line must read exit=7
#>
[CmdletBinding()]
param(
  [string] $RepoRoot = "C:\Users\Joshua\Documents\Claude\Projects\Football-Live",
  [string] $Python = "C:\Users\Joshua\Documents\Claude\Projects\Football\.venv\Scripts\python.exe",
  [int]    $TimeoutSeconds = 90,
  [switch] $IfDue
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Python))   { Write-Error "interpreter not found: $Python"; exit 2 }
if (-not (Test-Path $RepoRoot)) { Write-Error "checkout not found: $RepoRoot"; exit 2 }

$logDir = Join-Path $RepoRoot ".cache\sync-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "sleeper_sync.log"

# Keep the log to the last ~2000 lines. A five-minute job writes ~300 lines a
# day; unbounded, that is the kind of file someone finds two seasons later.
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 512KB)) {
  Get-Content $log -Tail 2000 | Set-Content "$log.tmp"
  Move-Item "$log.tmp" $log -Force
}

$script = Join-Path $RepoRoot "scripts\sync\sleeper_sync.py"
# Quoted: the default checkout path contains "Documents\Claude\Projects" today
# and could contain a space tomorrow. An unquoted path with a space is silently
# split into two arguments and python reports a file it cannot find.
$args = @("`"$script`"", 'run', '--quiet')
if ($IfDue) { $args += '--if-due' }

$env:PYTHONPATH = Join-Path $RepoRoot "src"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

# Unique per run. Two overlapping wrappers redirecting to one file is a
# sharing violation on Windows, and the run that loses it dies for a reason
# that has nothing to do with the league.
$runId = "{0}-{1}" -f (Get-Date -Format "yyyyMMddTHHmmssZ"), $PID
$out = Join-Path $logDir "run_$runId.out"
$err = Join-Path $logDir "run_$runId.err"

# -WindowStyle Hidden, not -NoNewWindow: the two are mutually exclusive, and
# -NoNewWindow inherits the scheduler's console, which is the configuration
# that flashes a window at a signed-in user.
$p = Start-Process -FilePath $Python -ArgumentList $args -WorkingDirectory $RepoRoot `
                   -WindowStyle Hidden -PassThru `
                   -RedirectStandardOutput $out -RedirectStandardError $err

# Touch the handle BEFORE waiting. Without this, .NET never caches the process
# handle and ExitCode reads as $null after WaitForExit on this PowerShell path
# — which logged "exit=" and let a failed sync pass as nothing in particular.
$null = $p.Handle

if (-not $p.WaitForExit($TimeoutSeconds * 1000)) {
  # A hung sync is killed, not waited on. The previous snapshot is untouched
  # either way, and the next run is only five minutes away.
  try { $p.Kill() } catch { }
  Add-Content $log "$stamp TIMEOUT after ${TimeoutSeconds}s; killed"
  Remove-Item $out, $err -ErrorAction SilentlyContinue
  exit 1
}

$p.Refresh()
$code = $p.ExitCode
$tail = @()
foreach ($f in @($out, $err)) {
  if (Test-Path $f) { $tail += (Get-Content $f -Tail 3 | Where-Object { $_ -ne '' }) }
}
if ($null -eq $code) {
  # Unknown is not success. A wrapper that cannot say how its child exited
  # must not report a clean run to the scheduler.
  Add-Content $log "$stamp exit=UNKNOWN (ExitCode null after WaitForExit) $($tail -join ' | ')"
  Remove-Item $out, $err -ErrorAction SilentlyContinue
  exit 1
}
Add-Content $log "$stamp exit=$code $($tail -join ' | ')"
Remove-Item $out, $err -ErrorAction SilentlyContinue
Get-ChildItem $logDir -Filter "run_*" -ErrorAction SilentlyContinue |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddHours(-6) } |
  Remove-Item -ErrorAction SilentlyContinue
exit $code
