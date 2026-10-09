$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$sha = [Security.Cryptography.SHA256]::Create()
try { $identity = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant())))).Replace('-','').ToLowerInvariant() }
finally { $sha.Dispose() }
$docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $docker) {
  $candidate = Join-Path $env:ProgramFiles 'Docker/Docker/resources/bin/docker.exe'
  if (Test-Path -LiteralPath $candidate) { $docker = $candidate }
}
if (-not $docker) { return }
$previousPreference = $ErrorActionPreference
try {
  $ErrorActionPreference = 'Continue'
  $ids = @(& $docker ps -q --filter "label=ai-gui.delivery=$identity" 2>$null)
  $dockerQueryExit = $LASTEXITCODE
} finally { $ErrorActionPreference = $previousPreference }
if ($dockerQueryExit -ne 0) { Write-Warning 'Docker cleanup could not query the Engine; start Docker and run the close script again.'; return }
foreach ($id in $ids) {
  if ($id -match '^[0-9a-f]{12,64}$') {
    & $docker stop --time 5 $id | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not stop this delivery container: $id" }
  }
}
