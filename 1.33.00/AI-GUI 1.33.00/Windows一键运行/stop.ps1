$ErrorActionPreference = 'Stop'

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $packageRoot 'runtime\python\python.exe'
$pidFile = Join-Path $packageRoot 'server.pid'
$browserPidFile = Join-Path $packageRoot 'browser.pid.json'

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

function Stop-RecordedManagedBrowser {
  if (-not (Test-Path -LiteralPath $browserPidFile -PathType Leaf)) { return }
  try {
    $decodedRecords = Get-Content -Raw -LiteralPath $browserPidFile | ConvertFrom-Json
    $records = if ($decodedRecords -is [System.Array]) { $decodedRecords } else { @($decodedRecords) }
  }
  catch {
    Write-Warning 'The recorded AI-GUI browser PID file is invalid; it was not used to stop any process.'
    return
  }
  foreach ($record in $records) {
    $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    $processPath = $null
    $processStartedAt = $null
    try { $processPath = [string]$process.Path } catch { }
    try { $processStartedAt = $process.StartTime } catch { }
    if (-not $processPath -or -not $processStartedAt) { continue }
    if (-not (Test-SamePath -Left $processPath -Right ([string]$record.path))) { continue }
    $recordStartedAt = [datetime]::Parse([string]$record.startedAtUtc).ToUniversalTime()
    if (($processStartedAt.ToUniversalTime() - $recordStartedAt).Duration() -gt [timespan]::FromSeconds(5)) { continue }
    try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch { }
  }
  Remove-Item -LiteralPath $browserPidFile -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
  Stop-RecordedManagedBrowser
  Write-Host 'This package has no recorded running service.'
  exit 0
}

try {
  $record = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
}
catch {
  throw 'server.pid is invalid. No process was stopped.'
}

if (-not (Test-SamePath -Left ([string]$record.packageRoot) -Right $packageRoot) -or
    -not (Test-SamePath -Left ([string]$record.pythonPath) -Right $python)) {
  throw 'server.pid belongs to another package. No process was stopped.'
}

$process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
if (-not $process) {
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
$startedAtUtc = $process.StartTime.ToUniversalTime().ToString('o')
$pathAndTimeMatches = (Test-SamePath -Left $executablePath -Right $python) -and
  ([string]$record.startedAtUtc -eq $startedAtUtc)
$commandLineMatches = (-not $commandLine) -or (
  ($commandLine -match '(?i)-m\s+uvicorn') -and
  ($commandLine -match 'gui_agent\.api\.server:app')
)
$matches = $pathAndTimeMatches -and $commandLineMatches
if (-not $matches) {
  throw 'The recorded PID now belongs to a different process. No process was stopped.'
}

Stop-Process -Id ([int]$record.pid) -ErrorAction Stop
$null = $process.WaitForExit(5000)
if (-not $process.HasExited) {
  throw 'The service did not stop within 5 seconds.'
}
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Stop-RecordedManagedBrowser
Write-Host "Stopped this package service (PID $($record.pid))." -ForegroundColor Green
