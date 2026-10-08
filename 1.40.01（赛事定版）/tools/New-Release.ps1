[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^\d+\.\d+\.\d+$')]
  [string]$Version,
  [Parameter(Mandatory = $true)]
  [string]$RuntimePackage,
  [Parameter(Mandatory = $true)]
  [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = (Resolve-Path $RuntimePackage).Path
$outputRootPath = [System.IO.Path]::GetFullPath($OutputRoot)
$releaseRoot = Join-Path $outputRootPath $Version

if (Test-Path -LiteralPath $releaseRoot) {
  throw "Refusing to overwrite existing release: $releaseRoot"
}
if (-not (Test-Path -LiteralPath (Join-Path $runtimeRoot 'runtime\python\python.exe'))) {
  throw "Runtime package is missing runtime\python\python.exe: $runtimeRoot"
}

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $runtimeRoot 'runtime') -Destination $releaseRoot -Recurse
New-Item -ItemType Directory -Force -Path (Join-Path $releaseRoot 'backend'),(Join-Path $releaseRoot 'dist'),(Join-Path $releaseRoot 'artifacts'),(Join-Path $releaseRoot 'data') | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot 'backend\src') -Destination (Join-Path $releaseRoot 'backend\src') -Recurse
if (Test-Path -LiteralPath (Join-Path $repoRoot 'backend\benchmarks')) {
  Copy-Item -LiteralPath (Join-Path $repoRoot 'backend\benchmarks') -Destination (Join-Path $releaseRoot 'backend\benchmarks') -Recurse
}
Copy-Item -Path (Join-Path $repoRoot 'frontend-dist\*') -Destination (Join-Path $releaseRoot 'dist') -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot 'packaging\start.ps1'),(Join-Path $repoRoot 'packaging\stop.ps1'),(Join-Path $repoRoot 'packaging\README-Windows.txt') -Destination $releaseRoot
Copy-Item -Path (Join-Path $repoRoot 'packaging\*.bat') -Destination $releaseRoot

$versionFile = Join-Path $releaseRoot 'backend\src\gui_agent\version.py'
$versionText = Get-Content -LiteralPath $versionFile -Raw -Encoding utf8
$versionText = [regex]::Replace($versionText, 'APP_VERSION\s*=\s*"[^"]+"', "APP_VERSION = `"$Version`"")
Set-Content -LiteralPath $versionFile -Value $versionText -Encoding utf8

$manifestEntries = @()
$manifestFiles = @()
$manifestFiles += Get-ChildItem -LiteralPath (Join-Path $releaseRoot 'backend') -Recurse -File
$manifestFiles += Get-ChildItem -LiteralPath (Join-Path $releaseRoot 'dist') -Recurse -File
$manifestFiles += Get-ChildItem -LiteralPath $releaseRoot -File | Where-Object {
  $_.Name -in @('start.ps1','stop.ps1','README-Windows.txt') -or $_.Extension -eq '.bat'
}
foreach ($file in $manifestFiles) {
  $relative = $file.FullName.Substring($releaseRoot.Length + 1).Replace('\','/')
  $manifestEntries += [ordered]@{ path = $relative; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash; bytes = $file.Length }
}
$manifest = [ordered]@{
  schemaVersion = 1
  productVersion = $Version
  sourceVersion = (Get-Content -LiteralPath (Join-Path $repoRoot 'VERSION') -Raw).Trim()
  baselineRuntimePackage = $runtimeRoot
  generatedAt = (Get-Date).ToUniversalTime().ToString('o')
  files = $manifestEntries
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $releaseRoot 'release-manifest.json') -Encoding utf8
Write-Host "Release created: $releaseRoot" -ForegroundColor Green
