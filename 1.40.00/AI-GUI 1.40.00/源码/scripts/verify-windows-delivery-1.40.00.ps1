$ErrorActionPreference = 'Stop'

$version = '1.40.00'
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$productRoot = (Resolve-Path (Join-Path $sourceRoot '..')).Path
$zipPath = Join-Path $productRoot "构建成品\AI-GUI-$version-Windows-组员交付版.zip"
$hashPath = Join-Path $productRoot "构建成品\AI-GUI-$version-Windows-组员交付版.sha256"
$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
$verifyRoot = Join-Path $temporaryRoot ("ai-gui-$version-package-verify-" + [guid]::NewGuid().ToString('N'))

$verifyFull = [System.IO.Path]::GetFullPath($verifyRoot)
if (-not $verifyFull.StartsWith($temporaryRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to verify outside the system temp directory: $verifyFull"
}

try {
  if (-not (Test-Path -LiteralPath $zipPath -PathType Leaf)) { throw "ZIP is missing: $zipPath" }
  if (-not (Test-Path -LiteralPath $hashPath -PathType Leaf)) { throw "SHA-256 file is missing: $hashPath" }
  $expectedHash = ((Get-Content -Raw -LiteralPath $hashPath).Trim() -split '\s+')[0]
  $actualHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
  if ($expectedHash -ne $actualHash) { throw 'ZIP SHA-256 does not match its checksum file.' }

  Expand-Archive -LiteralPath $zipPath -DestinationPath $verifyRoot
  $packageRoot = Join-Path $verifyRoot "AI-GUI-$version"
  if (Test-Path -LiteralPath (Join-Path $packageRoot 'data')) { throw 'The initial package contains data.' }
  if (Test-Path -LiteralPath (Join-Path $packageRoot 'artifacts')) { throw 'The initial package contains artifacts.' }
  if (Get-ChildItem -LiteralPath $packageRoot -Recurse -File -Filter '*.dpapi') { throw 'The package contains a DPAPI secret file.' }
  if (Get-ChildItem -LiteralPath $packageRoot -Recurse -File -Filter 'profiles.json') { throw 'The package contains saved model profiles.' }
  $runtimeResidue = Get-ChildItem -LiteralPath $packageRoot -Recurse -File | Where-Object { $_.Extension -in @('.log', '.pid') }
  if ($runtimeResidue) { throw "The package contains runtime logs or PID files: $($runtimeResidue[0].FullName)" }

  $runtimeRoot = Join-Path $packageRoot 'runtime'
  $runtimeManifest = Get-Content -Raw -LiteralPath (Join-Path $runtimeRoot 'runtime-manifest.json') | ConvertFrom-Json
  if ([string]$runtimeManifest.productVersion -ne $version) { throw 'Runtime manifest version mismatch.' }
  foreach ($entry in $runtimeManifest.files) {
    $file = Join-Path $runtimeRoot ([string]$entry.path)
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Runtime file is missing: $($entry.path)" }
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne [string]$entry.sha256) {
      throw "Runtime file hash mismatch: $($entry.path)"
    }
  }

  foreach ($scriptName in @('start.ps1', 'start-process-runner.ps1', 'stop.ps1')) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $packageRoot $scriptName), [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) { throw "Packaged script parse failure: $scriptName :: $($errors[0].Message)" }
  }

  $previousRunnerMode = $env:GUI_RUNNER_MODE
  $previousSkipBrowser = $env:GUI_SKIP_BROWSER
  $previousAutoStop = $env:GUI_AUTO_STOP
  try {
    $env:GUI_RUNNER_MODE = 'process'
    $env:GUI_SKIP_BROWSER = '1'
    $env:GUI_AUTO_STOP = '1'
    & (Join-Path $packageRoot 'start.ps1')
    if ($LASTEXITCODE -ne 0) { throw "Packaged start.ps1 failed with exit code $LASTEXITCODE" }
  }
  finally {
    $env:GUI_RUNNER_MODE = $previousRunnerMode
    $env:GUI_SKIP_BROWSER = $previousSkipBrowser
    $env:GUI_AUTO_STOP = $previousAutoStop
  }

  $healthVersion = & (Join-Path $packageRoot 'runtime\python\python.exe') -c "import sys; sys.path.insert(0, r'$($packageRoot.Replace("'", "''"))\backend\src'); from gui_agent.version import APP_VERSION; print(APP_VERSION)"
  if (($healthVersion | Select-Object -Last 1).Trim() -ne $version) { throw 'Packaged backend version import failed.' }

  Write-Output "PACKAGE_SMOKE=PASS"
  Write-Output "ZIP_SHA256=$actualHash"
  Write-Output "RUNTIME_FILES_VERIFIED=$($runtimeManifest.files.Count)"
  Write-Output "PACKAGED_APP_VERSION=$version"
}
finally {
  if (Test-Path -LiteralPath $verifyRoot) {
    $resolved = [System.IO.Path]::GetFullPath($verifyRoot)
    if ($resolved.StartsWith($temporaryRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
      Remove-Item -LiteralPath $verifyRoot -Recurse -Force
    }
  }
}


