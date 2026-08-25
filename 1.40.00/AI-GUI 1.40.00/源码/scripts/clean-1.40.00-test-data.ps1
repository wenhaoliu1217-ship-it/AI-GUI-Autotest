$ErrorActionPreference = 'Stop'

$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$productRoot = (Resolve-Path (Join-Path $sourceRoot '..')).Path.TrimEnd('\')
$runtimeRoot = (Resolve-Path (Join-Path $productRoot 'Windows一键运行')).Path

function Assert-InProduct {
  param([string]$Path)
  $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
  if ($full -eq $productRoot -or -not $full.StartsWith($productRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean a path outside the 1.40.00 product: $full"
  }
  return $full
}

function Remove-DirectoryTree {
  param([string]$Path)
  $safePath = Assert-InProduct -Path $Path
  Remove-Item -LiteralPath $safePath -Recurse -Force -ErrorAction SilentlyContinue
  if (-not (Test-Path -LiteralPath $safePath)) { return }

  $temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
  $emptyRoot = Join-Path $temporaryRoot ('ai-gui-empty-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $emptyRoot -Force | Out-Null
  try {
    & robocopy $emptyRoot $safePath /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Robocopy cleanup failed for: $safePath" }
    Remove-Item -LiteralPath $safePath -Recurse -Force -ErrorAction SilentlyContinue
  }
  finally {
    Remove-Item -LiteralPath $emptyRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
  if (Test-Path -LiteralPath $safePath) { throw "Could not remove generated test directory: $safePath" }
}

$removedFiles = 0
$removedBytes = [int64]0

$stateRoots = @(
  (Join-Path $productRoot 'data'),
  (Join-Path $productRoot 'artifacts'),
  (Join-Path $sourceRoot 'data'),
  (Join-Path $sourceRoot 'artifacts'),
  (Join-Path $runtimeRoot 'data'),
  (Join-Path $runtimeRoot 'artifacts')
)

foreach ($stateRoot in $stateRoots) {
  $safeRoot = Assert-InProduct -Path $stateRoot
  if (Test-Path -LiteralPath $safeRoot -PathType Container) {
    $files = @(Get-ChildItem -LiteralPath $safeRoot -Recurse -File -Force -ErrorAction SilentlyContinue)
    $removedFiles += $files.Count
    $removedBytes += [int64](($files | Measure-Object Length -Sum).Sum)
    foreach ($child in @(Get-ChildItem -LiteralPath $safeRoot -Force)) {
      if ($child.PSIsContainer) {
        Remove-DirectoryTree -Path $child.FullName
      }
      else {
        Remove-Item -LiteralPath $child.FullName -Force
      }
    }
  }
  else {
    New-Item -ItemType Directory -Path $safeRoot -Force | Out-Null
  }
}

$generatedRoots = @(
  $productRoot,
  $sourceRoot,
  (Join-Path $sourceRoot 'backend'),
  (Join-Path $runtimeRoot 'backend')
)
foreach ($generatedRoot in $generatedRoots) {
  $resolvedGeneratedRoot = [System.IO.Path]::GetFullPath($generatedRoot).TrimEnd('\')
  if ($resolvedGeneratedRoot -eq $productRoot) {
    $safeGeneratedRoot = $productRoot
  }
  else {
    $safeGeneratedRoot = Assert-InProduct -Path $generatedRoot
  }
  $generated = @(Get-ChildItem -LiteralPath $safeGeneratedRoot -Directory -Force -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -like '.pytest*' -or $_.Name -like '.test-temp*'
  })
  foreach ($directory in $generated) {
    $safeDirectory = Assert-InProduct -Path $directory.FullName
    $files = @(Get-ChildItem -LiteralPath $safeDirectory -Recurse -File -Force -ErrorAction SilentlyContinue)
    $removedFiles += $files.Count
    $removedBytes += [int64](($files | Measure-Object Length -Sum).Sum)
    Remove-DirectoryTree -Path $safeDirectory
  }
}

$cacheRoots = @(
  (Join-Path $sourceRoot 'backend'),
  (Join-Path $runtimeRoot 'backend')
)
foreach ($cacheRoot in $cacheRoots) {
  foreach ($directory in @(Get-ChildItem -LiteralPath $cacheRoot -Directory -Recurse -Force -Filter '__pycache__' -ErrorAction SilentlyContinue)) {
    $safeDirectory = Assert-InProduct -Path $directory.FullName
    $files = @(Get-ChildItem -LiteralPath $safeDirectory -Recurse -File -Force -ErrorAction SilentlyContinue)
    $removedFiles += $files.Count
    $removedBytes += [int64](($files | Measure-Object Length -Sum).Sum)
    Remove-DirectoryTree -Path $safeDirectory
  }
}

$residueFiles = @(
  (Get-ChildItem -LiteralPath $runtimeRoot -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in @('.log', '.pid') }),
  (Get-ChildItem -LiteralPath $productRoot -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in @('.log', '.pid') }),
  (Get-ChildItem -LiteralPath (Join-Path $sourceRoot 'backend') -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'pytest-output-*' })
) | ForEach-Object { $_ }
foreach ($file in $residueFiles) {
  $safeFile = Assert-InProduct -Path $file.FullName
  $removedFiles += 1
  $removedBytes += [int64]$file.Length
  Remove-Item -LiteralPath $safeFile -Force
}

Write-Output "REMOVED_FILES=$removedFiles"
Write-Output "REMOVED_BYTES=$removedBytes"
Write-Output 'PRESERVED=backend benchmarks, source code, portable runtime, delivery ZIP'



