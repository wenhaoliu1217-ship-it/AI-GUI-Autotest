[CmdletBinding()]
param(
  [switch]$SkipFrontend,
  [switch]$SkipBackend,
  [switch]$SkipDocker
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$frontendRoot = Join-Path $repoRoot 'frontend'
$sourceRoot = Join-Path $repoRoot 'backend\src'
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'Development Python was not found. Install Python 3.11+ and pytest, or activate the project environment.' }

Write-Host '[1/4] Runtime source import check'
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($previousPythonPath) {
  "$sourceRoot$([System.IO.Path]::PathSeparator)$previousPythonPath"
} else {
  $sourceRoot
}
try {
  & $python.Source -c "import gui_agent; from gui_agent.api.server import health; print(health()['apiContractVersion'])"
}
finally {
  $env:PYTHONPATH = $previousPythonPath
}
if ($LASTEXITCODE -ne 0) { throw 'Backend source import check failed.' }

if (-not $SkipBackend) {
  Write-Host '[2/4] Backend tests'
  Push-Location $repoRoot
  $pytestBase = Join-Path $repoRoot '.local\pytest-suite'
  New-Item -ItemType Directory -Force -Path $pytestBase | Out-Null
  try { & $python.Source -m pytest -q --basetemp $pytestBase }
  finally { Pop-Location }
  if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
}

if (-not $SkipFrontend) {
  Write-Host '[3/4] Frontend typecheck and tests'
  Push-Location $frontendRoot
  try {
    npm run lint
    if ($LASTEXITCODE -ne 0) { throw 'Frontend typecheck failed.' }
    npm test -- --run
    if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
  }
  finally { Pop-Location }
}

if (-not $SkipDocker) {
  Write-Host '[4/4] Isolated Runner preflight'
  & (Join-Path $PSScriptRoot 'Start-Dev.ps1') -Detached -SkipBrowser -RequireDocker
  if ($LASTEXITCODE -ne 0) { throw 'Strict startup and Docker Runner preflight failed.' }
  try {
    $health = Invoke-RestMethod 'http://127.0.0.1:8080/api/health' -TimeoutSec 10
    if (-not $health.runnerAvailable) { throw "Runner unavailable: $($health.runnerReason)" }
    $health | ConvertTo-Json -Depth 6
  }
  finally {
    & (Join-Path $PSScriptRoot 'Stop-Dev.ps1')
  }
}

Write-Host 'All requested checks passed.' -ForegroundColor Green
