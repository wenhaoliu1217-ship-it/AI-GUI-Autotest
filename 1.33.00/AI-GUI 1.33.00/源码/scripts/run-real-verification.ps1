param([string]$RunName = 'real-gui-live')

$ErrorActionPreference = 'Stop'

$sourceRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$backendRoot = Join-Path $sourceRoot 'backend'
$verificationRoot = Join-Path $sourceRoot ('.verification\' + $RunName)
$python = Join-Path $sourceRoot '.venv-real\Scripts\python.exe'

if (Test-Path -LiteralPath $verificationRoot) {
  throw "Verification path already exists: $verificationRoot"
}
New-Item -ItemType Directory -Path $verificationRoot | Out-Null
$env:GUI_AGENT_ARTIFACTS = Join-Path $verificationRoot 'artifacts'
$env:GUI_STATIC_DIR = Join-Path $sourceRoot 'dist'

$demo = Start-Process -FilePath $python `
  -ArgumentList @('-m', 'gui_agent', 'serve-demo', '--port', '8765') `
  -WorkingDirectory $backendRoot -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $verificationRoot 'demo.out.log') `
  -RedirectStandardError (Join-Path $verificationRoot 'demo.err.log') -PassThru
$api = Start-Process -FilePath $python `
  -ArgumentList @('-m', 'uvicorn', 'gui_agent.api.server:app', '--host', '127.0.0.1', '--port', '4190') `
  -WorkingDirectory $backendRoot -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $verificationRoot 'api.out.log') `
  -RedirectStandardError (Join-Path $verificationRoot 'api.err.log') -PassThru
try {
  foreach ($url in @('http://127.0.0.1:8765', 'http://127.0.0.1:4190/api/health', 'http://127.0.0.1:4190')) {
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
      try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $ready = $true; break }
      }
      catch { }
      Start-Sleep -Milliseconds 250
    }
    if (-not $ready) { throw "Service not ready: $url" }
  }

  $env:AIGU_WEB_URL = 'http://127.0.0.1:4190'
  $env:AIGU_VERIFY_OUT = Join-Path $verificationRoot 'screenshots'
  & node (Join-Path $sourceRoot 'scripts\verify-playwright.mjs')
  if ($LASTEXITCODE -ne 0) { throw "GUI verification failed with exit code $LASTEXITCODE" }
}
finally {
  foreach ($process in @($api, $demo)) {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force }
  }
}
