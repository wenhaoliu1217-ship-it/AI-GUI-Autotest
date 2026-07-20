$ErrorActionPreference = 'Stop'

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvRoot = Join-Path $packageRoot '.venv'
$python = Join-Path $venvRoot 'Scripts\python.exe'
$backendRoot = Join-Path $packageRoot 'backend'
$distRoot = Join-Path $packageRoot 'dist'
$stdoutLog = Join-Path $packageRoot 'server-stdout.log'
$stderrLog = Join-Path $packageRoot 'server-stderr.log'
$firstRun = -not (Test-Path -LiteralPath $python)

function Test-LocalPortAvailable {
  param([int]$Port)
  $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
  try {
    $listener.Start()
    return $true
  }
  catch {
    return $false
  }
  finally {
    try { $listener.Stop() } catch { }
  }
}

if (-not (Test-Path -LiteralPath $distRoot)) {
  throw 'The dist folder is missing. Please extract the complete package again.'
}

if ($firstRun) {
  $launcher = Get-Command py -ErrorAction SilentlyContinue
  if (-not $launcher) {
    throw 'Python Launcher was not found. Install Python 3.12 and enable Add Python to PATH.'
  }
  Write-Host '[1/4] Creating the private Python environment...'
  & py -3.12 -m venv $venvRoot
  if ($LASTEXITCODE -ne 0) {
    throw 'Could not create a Python 3.12 environment.'
  }
}

Write-Host '[2/4] Checking application dependencies...'
$needsInstall = $firstRun
if (-not $needsInstall) {
  & $python -c 'import fastapi, httpx, playwright, gui_agent; from gui_agent.artifacts import ArtifactManager; from gui_agent.api.server import app' 2>$null
  $needsInstall = $LASTEXITCODE -ne 0
}
if ($needsInstall) {
  & $python -m pip install --disable-pip-version-check -e $backendRoot
  if ($LASTEXITCODE -ne 0) {
    throw 'Dependency installation failed. Check the network and try again.'
  }
}

Write-Host '[3/4] Checking Chromium...'
& $python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
  throw 'Chromium installation failed. Check the network and try again.'
}

$port = 8080
while ($port -le 8090 -and -not (Test-LocalPortAvailable -Port $port)) {
  $port++
}
if ($port -gt 8090) {
  throw 'No available local port was found between 8080 and 8090.'
}

$env:GUI_STATIC_DIR = $distRoot
$env:GUI_AGENT_ARTIFACTS = Join-Path $packageRoot 'artifacts'
$env:GUI_API_HOST = '127.0.0.1'
$env:GUI_API_PORT = [string]$port
$url = "http://127.0.0.1:$port/"

Write-Host "[4/4] Starting the real GUI test service on $url"
$server = Start-Process -FilePath $python `
  -ArgumentList @('-m', 'uvicorn', 'gui_agent.api.server:app', '--host', '127.0.0.1', '--port', [string]$port) `
  -WorkingDirectory $packageRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog `
  -PassThru

try {
  $ready = $false
  for ($attempt = 0; $attempt -lt 120; $attempt++) {
    if ($server.HasExited) { break }
    try {
      $response = Invoke-WebRequest -Uri ($url + 'api/health') -UseBasicParsing -TimeoutSec 2
      if ($response.StatusCode -eq 200) {
        $ready = $true
        break
      }
    }
    catch { }
    Start-Sleep -Milliseconds 250
  }

  if (-not $ready) {
    Write-Host ''
    Write-Host 'The service did not start. Error log:' -ForegroundColor Red
    if (Test-Path -LiteralPath $stderrLog) {
      Get-Content -LiteralPath $stderrLog -Tail 30
    }
    throw 'Real GUI test service startup failed.'
  }

  Start-Process $url
  Write-Host ''
  Write-Host "Ready: $url" -ForegroundColor Green
  Write-Host 'Screenshots and reports are stored in the artifacts folder.'
  Read-Host 'Press Enter to stop the service'
}
finally {
  if ($server -and -not $server.HasExited) {
    Stop-Process -Id $server.Id
    $null = $server.WaitForExit(5000)
  }
}
