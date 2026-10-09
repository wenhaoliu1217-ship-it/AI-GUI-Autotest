[CmdletBinding()]
param(
  [switch]$RequireDocker,
  [switch]$Pretty
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  $bundled = Join-Path $repoRoot 'runtime\python\python.exe'
  if (Test-Path -LiteralPath $bundled -PathType Leaf) { $python = [pscustomobject]@{ Source = $bundled } }
}
if (-not $python) { throw 'Python 3 is required for platform preflight.' }

$arguments = @(Join-Path $repoRoot 'tools\platform_preflight.py')
if ($RequireDocker) { $arguments += '--require-docker' }
if ($Pretty) { $arguments += '--pretty' }
Push-Location $repoRoot
try { & $python.Source @arguments; exit $LASTEXITCODE }
finally { Pop-Location }
