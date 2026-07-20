$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvRoot = Join-Path $projectRoot '.venv-real'
$python = Join-Path $venvRoot 'Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
  py -3.12 -m venv $venvRoot
}

& $python -m pip install --disable-pip-version-check -e (Join-Path $projectRoot 'backend')
& $python -m playwright install chromium

$api = Start-Process -FilePath $python `
  -ArgumentList @('-m', 'uvicorn', 'gui_agent.api.server:app', '--host', '127.0.0.1', '--port', '8787') `
  -WorkingDirectory (Join-Path $projectRoot 'backend') `
  -WindowStyle Hidden `
  -PassThru

try {
  Start-Process 'http://127.0.0.1:5173'
  npm run dev -- --host 127.0.0.1
}
finally {
  if ($api -and -not $api.HasExited) {
    Stop-Process -Id $api.Id
  }
}
