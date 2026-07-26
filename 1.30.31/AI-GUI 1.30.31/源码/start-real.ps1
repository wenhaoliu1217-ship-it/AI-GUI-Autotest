$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvRoot = Join-Path $projectRoot '.venv-real'
$python = Join-Path $venvRoot 'Scripts\python.exe'
$backendRoot = Join-Path $projectRoot 'backend'
$runnerImage = 'ai-gui-runner:1.30.31'

if (-not (Test-Path -LiteralPath $python)) {
  py -3.12 -m venv $venvRoot
}

& $python -m pip install --disable-pip-version-check -e $backendRoot
& $python -m playwright install chromium

$docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $docker) {
  $candidate = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
  if (Test-Path -LiteralPath $candidate) { $docker = $candidate }
}
if (-not $docker) {
  throw 'Docker CLI 不可用。请安装并启动 Docker Desktop 后重试；测试 Runner 不会降级到宿主进程。'
}
$dockerBin = Split-Path -Parent $docker
if (($env:PATH -split ';') -notcontains $dockerBin) { $env:PATH = "$dockerBin;$env:PATH" }
& $docker info *> $null
if ($LASTEXITCODE -ne 0) {
  throw 'Docker Engine 未就绪。请启动 Docker Desktop 并等待 Linux Engine 可用。'
}
& $docker build -q -t $runnerImage -f (Join-Path $backendRoot 'Dockerfile.runner') $backendRoot
if ($LASTEXITCODE -ne 0) {
  throw '容器 Runner 镜像构建失败。'
}
$env:GUI_DOCKER_CLI = $docker
$env:GUI_RUNNER_IMAGE = $runnerImage
$env:GUI_RUNNER_MODE = 'container'

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
