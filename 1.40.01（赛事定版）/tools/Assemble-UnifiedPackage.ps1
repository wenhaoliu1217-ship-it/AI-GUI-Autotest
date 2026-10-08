[CmdletBinding()]
param(
  [string]$SourcePackage = (Join-Path $env:USERPROFILE 'Desktop\1.32.01'),
  [switch]$SkipRuntime,
  [switch]$SkipData,
  [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourceRoot = (Resolve-Path -LiteralPath $SourcePackage).Path

function Copy-Tree([string]$Source, [string]$Destination) {
  if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    throw "Required source directory is missing: $Source"
  }
  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
  & robocopy $Source $Destination /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /XJ /NFL /NDL /NP | Out-Null
  if ($LASTEXITCODE -gt 7) { throw "Failed to copy $Source to $Destination (robocopy=$LASTEXITCODE)" }
}

if (-not $SkipRuntime) {
  Copy-Tree (Join-Path $sourceRoot 'runtime') (Join-Path $repoRoot 'runtime')
}
if (-not $SkipFrontend) {
  Copy-Tree (Join-Path $sourceRoot 'dist') (Join-Path $repoRoot 'frontend-dist')
}
if (-not $SkipData) {
  Copy-Tree (Join-Path $sourceRoot 'data') (Join-Path $repoRoot '.local\data')
}

$manifest = [ordered]@{
  schemaVersion = 1
  assembledAt = (Get-Date).ToUniversalTime().ToString('o')
  sourcePackage = $sourceRoot
  repositoryRoot = $repoRoot
  appVersion = (Get-Content (Join-Path $repoRoot 'VERSION') -Raw).Trim()
  frontendBundle = 'frontend-dist'
  backendSource = 'backend/src'
  runtimeRoot = 'runtime'
  projectData = '.local/data'
  externalRuntimeFallback = $false
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $repoRoot 'unified-package.json') -Encoding UTF8
Write-Host "Unified package assembled at $repoRoot" -ForegroundColor Green
Write-Host "Runtime: $(Join-Path $repoRoot 'runtime')"
Write-Host "Frontend: $(Join-Path $repoRoot 'frontend-dist')"
Write-Host "Data: $(Join-Path $repoRoot '.local\data')"
