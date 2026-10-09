[CmdletBinding()]
param([switch]$Quick)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$manifest = Get-Content -LiteralPath (Join-Path $root 'release-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$checked = 0
foreach ($item in $manifest.files) {
  if ($Quick -and ([string]$item.path).StartsWith('runtime/')) { continue }
  $target = [IO.Path]::GetFullPath((Join-Path $root ([string]$item.path)))
  if (-not $target.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe manifest path.' }
  if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Package file missing: $($item.path)" }
  if ((Get-Item -LiteralPath $target).Length -ne $item.bytes -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $item.sha256) {
    throw "Package file changed: $($item.path)"
  }
  $checked++
}
Write-Host "Package verified: $checked files; product $($manifest.productVersion)"
