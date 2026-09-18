<#
.SYNOPSIS
  Install / inspect / remove the five-minute gridiron sync task. Idempotent.

.DESCRIPTION
  Creates a per-user Scheduled Task that runs gridiron_sync.ps1 every five
  minutes. Deliberately modest about what it needs:

    * NO ADMIN. Registered under the current user with default (Limited)
      privileges. Nothing here asks for elevation.
    * NO STORED PASSWORD. The task runs with the S4U logon type, so Windows
      never keeps a credential for it. The cost of that choice is stated
      plainly below: the task only runs while the desktop is on and this user
      is signed in. It is not a service and does not survive a logoff.
    * NO VISIBLE WINDOW. Hidden, -WindowStyle Hidden, and the task's own
      Hidden setting.
    * NO OVERLAPPING RUNS. MultipleInstances = IgnoreNew, plus an execution
      time limit shorter than the interval, plus the Python-side file lock.
      Three independent guards because the scheduler's own guarantee is the
      weakest of the three.

  Install is safe to re-run: an existing task is replaced, not duplicated.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\windows\gridiron_task.ps1 -Action Install
#>
[CmdletBinding()]
param(
  [ValidateSet('Install','Status','Uninstall')]
  [string] $Action = 'Status',
  [string] $TaskName = 'Gridiron Sleeper Sync',
  [string] $RepoRoot = "C:\Users\Joshua\Documents\Claude\Projects\Football-Live",
  [string] $Python = "C:\Users\Joshua\Documents\Claude\Projects\Football\.venv\Scripts\python.exe",
  [int]    $IntervalMinutes = 5
)

$ErrorActionPreference = 'Stop'
$runner = Join-Path $RepoRoot "scripts\windows\gridiron_sync.ps1"

function Get-Task { Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }

switch ($Action) {

  'Install' {
    if (-not (Test-Path $runner)) { Write-Error "runner not found: $runner"; exit 2 }
    if (-not (Test-Path $Python)) { Write-Error "interpreter not found: $Python"; exit 2 }

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
      -Argument ("-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden " +
                 "-File `"$runner`" -RepoRoot `"$RepoRoot`" -Python `"$Python`"") `
      -WorkingDirectory $RepoRoot

    # Repeat forever from logon. A 10-year duration rather than [TimeSpan]::MaxValue
    # because some Windows builds reject the latter when the task is written.
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition

    $settings = New-ScheduledTaskSettingsSet `
      -MultipleInstances IgnoreNew `
      -ExecutionTimeLimit (New-TimeSpan -Minutes ([Math]::Max(2, $IntervalMinutes - 1))) `
      -StartWhenAvailable `
      -DontStopOnIdleEnd `
      -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1) `
      -Hidden
    $settings.DisallowStartIfOnBatteries = $false
    $settings.StopIfGoingOnBatteries = $false

    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
      -LogonType S4U -RunLevel Limited

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
      -Settings $settings -Principal $principal -Force | Out-Null

    Write-Host "installed '$TaskName' every $IntervalMinutes min"
    Write-Host "  runs as : $env:USERDOMAIN\$env:USERNAME (no stored password, no admin)"
    Write-Host "  REQUIRES: this desktop powered on and this user signed in."
    Write-Host "            It is a scheduled task, not a service - a logoff stops it."
    Write-Host "  check   : .\scripts\windows\gridiron_task.ps1 -Action Status"
  }

  'Status' {
    $t = Get-Task
    if (-not $t) { Write-Host "task '$TaskName' is NOT installed"; exit 1 }
    $i = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "task      $TaskName [$($t.State)]"
    Write-Host "last run  $($i.LastRunTime)  result=$($i.LastTaskResult)"
    Write-Host "next run  $($i.NextRunTime)"
    Write-Host ""
    # The task's own view is only half the story: it says the process ran, not
    # that the sync succeeded. Ask the sync itself.
    $env:PYTHONPATH = Join-Path $RepoRoot "src"
    & $Python (Join-Path $RepoRoot "scripts\sync\sleeper_sync.py") status
  }

  'Uninstall' {
    if (Get-Task) {
      Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
      Write-Host "removed '$TaskName'"
    } else {
      Write-Host "task '$TaskName' was not installed"   # idempotent
    }
  }
}
