$ErrorActionPreference = 'Stop'

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $packageRoot 'runtime\python\python.exe'
$pidFile = Join-Path $packageRoot 'server.pid'

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

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
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
$matches = (Test-SamePath -Left $executablePath -Right $python) -and
  ([string]$record.startedAtUtc -eq $startedAtUtc) -and
  $commandLine -and
  ($commandLine -match '(?i)-m\s+uvicorn') -and
  ($commandLine -match 'gui_agent\.api\.server:app')
if (-not $matches) {
  throw 'The recorded PID now belongs to a different process. No process was stopped.'
}

Stop-Process -Id ([int]$record.pid) -ErrorAction Stop
$null = $process.WaitForExit(5000)
if (-not $process.HasExited) {
  throw 'The service did not stop within 5 seconds.'
}
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "Stopped this package service (PID $($record.pid))." -ForegroundColor Green

