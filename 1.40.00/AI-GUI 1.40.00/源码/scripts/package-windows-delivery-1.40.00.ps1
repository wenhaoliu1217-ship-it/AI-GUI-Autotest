$ErrorActionPreference = 'Stop'

$version = '1.40.00'
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$productRoot = (Resolve-Path (Join-Path $sourceRoot '..')).Path
$versionRoot = (Resolve-Path (Join-Path $productRoot '..')).Path
$runtimeSource = (Resolve-Path (Join-Path $productRoot 'Windows一键运行')).Path
$outputRoot = (Resolve-Path (Join-Path $productRoot '构建成品')).Path
$packageName = "AI-GUI-$version"
$zipName = "$packageName-Windows-组员交付版.zip"
$hashName = "$packageName-Windows-组员交付版.sha256"
$zipPath = Join-Path $outputRoot $zipName
$hashPath = Join-Path $outputRoot $hashName
$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
$workRoot = Join-Path $temporaryRoot ("ai-gui-$version-delivery-" + [guid]::NewGuid().ToString('N'))
$stageRoot = Join-Path $workRoot $packageName
$verifyRoot = Join-Path $workRoot 'verify'

function Assert-InTemporaryRoot {
  param([string]$Path)
  $full = [System.IO.Path]::GetFullPath($Path)
  if (-not $full.StartsWith($temporaryRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a delivery temporary path outside the system temp directory: $full"
  }
}

function Invoke-CleanRobocopy {
  param(
    [string]$Source,
    [string]$Destination,
    [string[]]$ExcludeDirectories = @(),
    [string[]]$ExcludeFiles = @()
  )
  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  $arguments = @($Source, $Destination, '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
  if ($ExcludeDirectories.Count -gt 0) { $arguments += '/XD'; $arguments += $ExcludeDirectories }
  if ($ExcludeFiles.Count -gt 0) { $arguments += '/XF'; $arguments += $ExcludeFiles }
  & robocopy @arguments | Out-Null
  if ($LASTEXITCODE -gt 7) { throw "Robocopy failed with exit code ${LASTEXITCODE}: $Source" }
}

Assert-InTemporaryRoot -Path $workRoot
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

try {
  $launchFiles = @(
    'README-Windows.txt',
    'Start-AI-GUI.bat',
    'start-process-runner.ps1',
    'start.ps1',
    'Stop-AI-GUI.bat',
    'stop.ps1',
    '双击启动AI测试.bat',
    '双击关闭AI测试.bat'
  )
  foreach ($name in $launchFiles) {
    $source = Join-Path $runtimeSource $name
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Required launcher file is missing: $name" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $stageRoot $name)
  }

  Invoke-CleanRobocopy `
    -Source (Join-Path $runtimeSource 'runtime') `
    -Destination (Join-Path $stageRoot 'runtime') `
    -ExcludeDirectories @('images') `
    -ExcludeFiles @('debug.log')

  Invoke-CleanRobocopy `
    -Source (Join-Path $sourceRoot 'dist') `
    -Destination (Join-Path $stageRoot 'dist')

  $backendTarget = Join-Path $stageRoot 'backend'
  New-Item -ItemType Directory -Path $backendTarget -Force | Out-Null
  Invoke-CleanRobocopy `
    -Source (Join-Path $sourceRoot 'backend\src') `
    -Destination (Join-Path $backendTarget 'src') `
    -ExcludeDirectories @('__pycache__', '.pytest_cache') `
    -ExcludeFiles @('*.pyc', '*.pyo')
  Invoke-CleanRobocopy `
    -Source (Join-Path $sourceRoot 'backend\benchmarks') `
    -Destination (Join-Path $backendTarget 'benchmarks') `
    -ExcludeDirectories @('__pycache__', '.pytest_cache') `
    -ExcludeFiles @('*.pyc', '*.pyo')
  foreach ($name in @('.env.example', 'container-entrypoint.sh', 'Dockerfile.runner', 'Dockerfile.runner.offline', 'pyproject.toml', 'requirements.txt')) {
    $source = Join-Path (Join-Path $sourceRoot 'backend') $name
    if (Test-Path -LiteralPath $source -PathType Leaf) {
      Copy-Item -LiteralPath $source -Destination (Join-Path $backendTarget $name)
    }
  }

  $documentMap = @{
    (Join-Path $productRoot '交付说明_1.40.00.md') = '交付说明_1.40.00.md'
    (Join-Path $productRoot '使用与验证说明_1.40.00.md') = '使用与验证说明_1.40.00.md'
    (Join-Path $productRoot '封装清单_1.40.00.md') = '封装清单_1.40.00.md'
    (Join-Path $versionRoot '1.40.00版本更新与需求状态.md') = '1.40.00版本更新与需求状态.md'
  }
  foreach ($entry in $documentMap.GetEnumerator()) {
    Copy-Item -LiteralPath $entry.Key -Destination (Join-Path $stageRoot $entry.Value)
  }

  $forbidden = Get-ChildItem -LiteralPath $stageRoot -Recurse -Force | Where-Object {
    $relative = $_.FullName.Substring($stageRoot.Length).TrimStart('\')
    $outsideBundledRuntime = -not $relative.StartsWith('runtime\', [System.StringComparison]::OrdinalIgnoreCase)
    ($outsideBundledRuntime -and (
      $_.Name -match '^\.pytest' -or
      $_.Name -eq '__pycache__' -or
      $_.Extension -in @('.pyc', '.pyo')
    )) -or
    $_.Extension -eq '.dpapi' -or
    $_.Name -in @('server.pid', 'browser.pid', 'profiles.json', 'storage-state.json', 'cookies.json')
  }
  if ($forbidden) {
    throw "Forbidden test or secret material entered the package: $($forbidden[0].FullName)"
  }
  foreach ($name in @('data', 'artifacts', 'node_modules', 'tests', '.venv')) {
    if (Test-Path -LiteralPath (Join-Path $stageRoot $name)) {
      throw "Forbidden top-level delivery directory exists: $name"
    }
  }

  $manifestPath = Join-Path $stageRoot 'PACKAGE-MANIFEST.json'
  $payloadFiles = Get-ChildItem -LiteralPath $stageRoot -Recurse -File
  $manifest = [ordered]@{
    schemaVersion = 1
    productVersion = $version
    packageType = 'windows-process-runner-team-delivery'
    builtAt = (Get-Date).ToUniversalTime().ToString('o')
    initialState = [ordered]@{
      artifacts = 'empty-on-first-run'
      projects = 'empty-on-first-run'
      modelProfiles = 'empty-on-first-run'
      apiKeys = 'not-included'
      loginState = 'not-included'
    }
    fileCount = $payloadFiles.Count
    uncompressedBytes = ($payloadFiles | Measure-Object Length -Sum).Sum
  }
  $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

  if (Test-Path -LiteralPath $zipPath -PathType Leaf) { Remove-Item -LiteralPath $zipPath -Force }
  if (Test-Path -LiteralPath $hashPath -PathType Leaf) { Remove-Item -LiteralPath $hashPath -Force }
  Compress-Archive -LiteralPath $stageRoot -DestinationPath $zipPath -CompressionLevel Optimal

  Expand-Archive -LiteralPath $zipPath -DestinationPath $verifyRoot
  $verifiedRoot = Join-Path $verifyRoot $packageName
  foreach ($required in @(
    'Start-AI-GUI.bat',
    'start-process-runner.ps1',
    'start.ps1',
    'runtime\python\python.exe',
    'runtime\runtime-manifest.json',
    'dist\index.html',
    'backend\src\gui_agent\api\server.py',
    'backend\src\gui_agent\planning\model_profiles.py'
  )) {
    if (-not (Test-Path -LiteralPath (Join-Path $verifiedRoot $required) -PathType Leaf)) {
      throw "The ZIP verification copy is missing: $required"
    }
  }
  $verifiedManifest = Get-Content -Raw -LiteralPath (Join-Path $verifiedRoot 'runtime\runtime-manifest.json') | ConvertFrom-Json
  if ([string]$verifiedManifest.productVersion -ne $version) {
    throw "The bundled runtime manifest reports the wrong product version: $($verifiedManifest.productVersion)"
  }

  $hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
  $hash | Set-Content -LiteralPath $hashPath -Encoding ASCII
  $zipInfo = Get-Item -LiteralPath $zipPath
  $finalFiles = Get-ChildItem -LiteralPath $verifiedRoot -Recurse -File
  Write-Output "ZIP_PATH=$zipPath"
  Write-Output "SHA256_PATH=$hashPath"
  Write-Output "SHA256=$hash"
  Write-Output "ZIP_BYTES=$($zipInfo.Length)"
  Write-Output "PACKAGE_FILES=$($finalFiles.Count)"
}
finally {
  if (Test-Path -LiteralPath $workRoot) {
    Assert-InTemporaryRoot -Path $workRoot
    Remove-Item -LiteralPath $workRoot -Recurse -Force
  }
}





