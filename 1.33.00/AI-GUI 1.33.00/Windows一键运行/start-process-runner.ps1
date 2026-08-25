$ErrorActionPreference = 'Stop'

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$startScript = Join-Path $packageRoot 'start.ps1'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

$env:GUI_RUNNER_MODE = 'process'
$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $powershell
$startInfo.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$startScript`""
$startInfo.WorkingDirectory = $packageRoot
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$null = [System.Diagnostics.Process]::Start($startInfo)
