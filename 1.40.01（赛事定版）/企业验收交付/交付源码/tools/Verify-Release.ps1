[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$ReleaseRoot
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path $ReleaseRoot).Path
$manifestPath = Join-Path $root 'release-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
  throw "release-manifest.json is missing: $root"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
$failures = @()
foreach ($entry in $manifest.files) {
  $target = Join-Path $root ([string]$entry.path.Replace('/','\'))
  if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    $failures += "missing: $($entry.path)"
    continue
  }
  $actual = Get-FileHash -Algorithm SHA256 -LiteralPath $target
  if ($actual.Hash -ne [string]$entry.sha256) {
    $failures += "hash mismatch: $($entry.path)"
  }
}
if ($failures.Count) {
  $failures | ForEach-Object { Write-Error $_ }
  throw "Release integrity check failed: $($failures.Count) file(s)"
}
Write-Host "Release verified: $($manifest.productVersion)" -ForegroundColor Green
