[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pidFile = Join-Path $repoRoot '.local\server.pid'

function Test-SamePath {
  param([string]$Left, [string]$Right)
  if (-not $Left -or -not $Right) { return $false }
  try {
    $leftFull = [System.IO.Path]::GetFullPath($Left).TrimEnd('\')
    $rightFull = [System.IO.Path]::GetFullPath($Right).TrimEnd('\')
    return [string]::Equals($leftFull, $rightFull, [System.StringComparison]::OrdinalIgnoreCase)
  }
  catch { return $false }
}

function Test-SameInstant {
  param($Left, $Right)
  if ($null -eq $Left -or $null -eq $Right) { return $false }
  try {
    $leftInstant = [DateTimeOffset]$Left
    $rightInstant = [DateTimeOffset]$Right
    return $leftInstant.UtcDateTime.Ticks -eq $rightInstant.UtcDateTime.Ticks
  }
  catch { return $false }
}

function Stop-RecordedBrowser {
  param($Record)
  if (-not $Record.browserPid -or -not $Record.browserPath -or -not $Record.browserStartedAtUtc) { return }
  $browser = Get-Process -Id ([int]$Record.browserPid) -ErrorAction SilentlyContinue
  if (-not $browser) { return }
  $browserCim = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$Record.browserPid)" -ErrorAction SilentlyContinue
  $browserPath = if ($browserCim) { [string]$browserCim.ExecutablePath } else { $null }
  if (-not $browserPath) { try { $browserPath = [string]$browser.Path } catch { } }
  $matches = (Test-SamePath -Left $browserPath -Right ([string]$Record.browserPath)) -and
    (Test-SameInstant -Left $Record.browserStartedAtUtc -Right $browser.StartTime.ToUniversalTime())
  if (-not $matches) {
    Write-Warning 'The recorded managed Edge PID now belongs to a different process; it was not stopped.'
    return
  }
  Stop-Process -Id ([int]$Record.browserPid) -ErrorAction Stop
  $null = $browser.WaitForExit(5000)
  if (-not $browser.HasExited) { throw 'The managed Edge login window did not stop within 5 seconds.' }
  Write-Host "Stopped the managed Edge login window (PID $($Record.browserPid))." -ForegroundColor Green
}

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
  Write-Host 'No recorded development service is running.'
  exit 0
}

try { $record = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json }
catch { throw 'The development server PID record is invalid. No process was stopped.' }

if (-not (Test-SamePath -Left ([string]$record.repoRoot) -Right $repoRoot)) {
  throw 'The PID record belongs to another source tree. No process was stopped.'
}

$process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
if (-not $process) {
  Stop-RecordedBrowser -Record $record
  Remove-Item -LiteralPath $pidFile -Force
  Write-Host 'Removed a stale PID record; the service was already stopped.'
  exit 0
}

$cimProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$record.pid)" -ErrorAction SilentlyContinue
$executablePath = if ($cimProcess) { [string]$cimProcess.ExecutablePath } else { $null }
if (-not $executablePath) {
  try { $executablePath = [string]$process.Path } catch { }
}
$commandLine = if ($cimProcess) { [string]$cimProcess.CommandLine } else { $null }
$matches = (Test-SamePath -Left $executablePath -Right ([string]$record.pythonPath)) -and
  (Test-SameInstant -Left $record.startedAtUtc -Right $process.StartTime.ToUniversalTime())
if ($matches -and $commandLine) {
  $matches = $commandLine -match '(?i)run_dev_server\.py'
}
if (-not $matches) {
  throw 'The recorded PID now belongs to a different process. No process was stopped.'
}

Stop-Process -Id ([int]$record.pid) -ErrorAction Stop
$null = $process.WaitForExit(5000)
if (-not $process.HasExited) { throw 'The development service did not stop within 5 seconds.' }

Stop-RecordedBrowser -Record $record
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "Stopped the development service (PID $($record.pid))." -ForegroundColor Green
