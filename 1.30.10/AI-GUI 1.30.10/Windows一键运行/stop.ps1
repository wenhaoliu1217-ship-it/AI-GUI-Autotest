$ErrorActionPreference = 'Stop'

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $packageRoot 'runtime\python\python.exe'
$serverPidFile = Join-Path $packageRoot 'server.pid'

function Get-NormalizedPath {
  param([string]$Path)
  return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Test-SamePath {
  param([string]$Left, [string]$Right)
  if (-not $Left -or -not $Right) { return $false }
  return [string]::Equals(
    (Get-NormalizedPath -Path $Left),
    (Get-NormalizedPath -Path $Right),
    [System.StringComparison]::OrdinalIgnoreCase)
}

if (-not (Test-Path -LiteralPath $serverPidFile -PathType Leaf)) {
  Write-Host 'No service from this package is recorded as running.'
  exit 0
}

try {
  $metadata = Get-Content -Raw -LiteralPath $serverPidFile | ConvertFrom-Json
}
catch {
  throw 'server.pid is invalid. It was not trusted and no process was stopped.'
}

$expectedPackageRoot = Get-NormalizedPath -Path $packageRoot
$expectedPython = Get-NormalizedPath -Path $python
if (-not $metadata.processId -or
    -not (Test-SamePath -Left ([string]$metadata.packageRoot) -Right $expectedPackageRoot) -or
    -not (Test-SamePath -Left ([string]$metadata.pythonPath) -Right $expectedPython)) {
  throw 'server.pid does not belong to this package. No process was stopped.'
}

$processId = [int]$metadata.processId
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if (-not $process) {
  Remove-Item -LiteralPath $serverPidFile -Force
  Write-Host 'The recorded service is no longer running. Its stale PID file was removed.'
  exit 0
}

$executablePath = $null
try { $executablePath = $process.Path } catch { }
$commandLine = $null
try {
  $record = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $processId" -ErrorAction Stop
  if ($record) {
    if ($record.ExecutablePath) { $executablePath = [string]$record.ExecutablePath }
    $commandLine = [string]$record.CommandLine
  }
}
catch { }

$isSamePython = Test-SamePath -Left $executablePath -Right $expectedPython
$isExpectedCommand = $commandLine -match '(?i)(?:^|\s)-m\s+uvicorn\s+gui_agent\.api\.server:app(?:\s|$)'
if ($isSamePython -and -not $commandLine) {
  throw 'The recorded process command line could not be verified. No process was stopped.'
}
if (-not ($isSamePython -and $isExpectedCommand)) {
  throw 'The recorded PID now belongs to a different process. No process was stopped.'
}

Stop-Process -Id $processId -ErrorAction Stop
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if ($process) { $null = $process.WaitForExit(5000) }
Remove-Item -LiteralPath $serverPidFile -Force -ErrorAction SilentlyContinue
Write-Host "Stopped the service from this package (PID $processId)." -ForegroundColor Green
