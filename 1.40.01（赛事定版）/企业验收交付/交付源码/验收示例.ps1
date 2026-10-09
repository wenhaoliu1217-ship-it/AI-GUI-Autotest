$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONHOME = Join-Path $root 'runtime/python'
$env:PYTHONPATH = Join-Path $root 'backend/src'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root 'runtime/ms-playwright'
& (Join-Path $root 'runtime/python/python.exe') (Join-Path $root 'acceptance/smoke.py')
if ($LASTEXITCODE -ne 0) { throw 'Real container acceptance failed; see .acceptance-evidence.' }
