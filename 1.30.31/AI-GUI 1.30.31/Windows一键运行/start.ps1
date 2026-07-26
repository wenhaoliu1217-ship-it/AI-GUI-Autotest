$ErrorActionPreference = 'Stop'

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeRoot = Join-Path $packageRoot 'runtime'
$pythonRoot = Join-Path $runtimeRoot 'python'
$python = Join-Path $pythonRoot 'python.exe'
$browserRoot = Join-Path $runtimeRoot 'ms-playwright'
$dockerArchiveRoot = Join-Path $runtimeRoot 'images'
$backendRoot = Join-Path $packageRoot 'backend'
$distRoot = Join-Path $packageRoot 'dist'
$stdoutLog = Join-Path $packageRoot 'server-stdout.log'
$stderrLog = Join-Path $packageRoot 'server-stderr.log'
$pidFile = Join-Path $packageRoot 'server.pid'
$runnerImage = 'ai-gui-runner:1.30.31'
$runnerArchives = @(
  (Join-Path $dockerArchiveRoot 'ai-gui-runner-1.30.31.tar'),
  (Join-Path $dockerArchiveRoot 'ai-gui-runner-1.30.31.tar.gz'),
  (Join-Path $dockerArchiveRoot 'ai-gui-runner-1.30.31.tgz')
)

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

function Test-SamePath {
  param([string]$Left, [string]$Right)
  if (-not $Left -or -not $Right) { return $false }
  try {
    $leftFull = [System.IO.Path]::GetFullPath($Left).TrimEnd('\')
    $rightFull = [System.IO.Path]::GetFullPath($Right).TrimEnd('\')
    return [string]::Equals($leftFull, $rightFull, [System.StringComparison]::OrdinalIgnoreCase)
  }
  catch {
    return $false
  }
}

function Get-ServerProcessIdentity {
  param([int]$ProcessId)
  $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if (-not $process) { return $null }
  $cimProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  $executablePath = $null
  $commandLine = $null
  if ($cimProcess) {
    $executablePath = [string]$cimProcess.ExecutablePath
    $commandLine = [string]$cimProcess.CommandLine
  }
  if (-not $executablePath) {
    try { $executablePath = [string]$process.Path } catch { }
  }
  return [pscustomobject]@{
    Process = $process
    ExecutablePath = $executablePath
    CommandLine = $commandLine
    StartedAtUtc = $process.StartTime.ToUniversalTime().ToString('o')
  }
}

function Test-RecordedServerIdentity {
  param($Record, $Identity)
  if (-not $Record -or -not $Identity) { return $false }
  if (-not (Test-SamePath -Left ([string]$Record.packageRoot) -Right $packageRoot)) { return $false }
  if (-not (Test-SamePath -Left ([string]$Record.pythonPath) -Right $python)) { return $false }
  if (-not (Test-SamePath -Left $Identity.ExecutablePath -Right $python)) { return $false }
  if ([string]$Record.startedAtUtc -ne [string]$Identity.StartedAtUtc) { return $false }
  if (-not $Identity.CommandLine) { return $false }
  return $Identity.CommandLine -match '(?i)-m\s+uvicorn' -and
    $Identity.CommandLine -match 'gui_agent\.api\.server:app'
}

function Remove-PidRecordForProcess {
  param([int]$ProcessId)
  if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { return }
  try {
    $record = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
    if ([int]$record.pid -eq $ProcessId) {
      Remove-Item -LiteralPath $pidFile -Force
    }
  }
  catch { }
}

function Stop-RecordedServer {
  if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { return }
  try {
    $record = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
  }
  catch {
    throw 'server.pid is invalid. It was not used to stop any process; remove it only after confirming that this package is not running.'
  }
  if (-not (Test-SamePath -Left ([string]$record.packageRoot) -Right $packageRoot) -or
      -not (Test-SamePath -Left ([string]$record.pythonPath) -Right $python)) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host 'Ignored a PID record belonging to a different package.' -ForegroundColor Yellow
    return
  }
  $identity = Get-ServerProcessIdentity -ProcessId ([int]$record.pid)
  if (-not $identity) {
    Remove-Item -LiteralPath $pidFile -Force
    return
  }
  if (-not (Test-RecordedServerIdentity -Record $record -Identity $identity)) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host 'Ignored a stale PID record because the live process does not match this package.' -ForegroundColor Yellow
    return
  }
  Write-Host "Stopping the previous service from this package (PID $($record.pid))..."
  Stop-Process -Id ([int]$record.pid) -ErrorAction Stop
  $null = $identity.Process.WaitForExit(5000)
  if (-not $identity.Process.HasExited) {
    throw 'The previous service from this package did not stop within 5 seconds.'
  }
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

if (-not ('AiGuiPortableJob13000' -as [type])) {
  Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class AiGuiPortableJob13000
{
    private const UInt32 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    private const Int32 JobObjectExtendedLimitInformation = 9;

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public Int64 PerProcessUserTimeLimit;
        public Int64 PerJobUserTimeLimit;
        public UInt32 LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public UInt32 ActiveProcessLimit;
        public UIntPtr Affinity;
        public UInt32 PriorityClass;
        public UInt32 SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr securityAttributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, Int32 infoClass, IntPtr info, UInt32 length);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    public static IntPtr CreateKillOnClose()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        Int32 size = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(limits, buffer, false);
            if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, buffer, (UInt32)size))
            {
                Int32 error = Marshal.GetLastWin32Error();
                CloseHandle(job);
                throw new Win32Exception(error);
            }
        }
        finally { Marshal.FreeHGlobal(buffer); }
        return job;
    }

    public static void Assign(IntPtr job, IntPtr process)
    {
        if (!AssignProcessToJobObject(job, process))
            throw new Win32Exception(Marshal.GetLastWin32Error());
    }

    public static void Close(IntPtr job)
    {
        if (job != IntPtr.Zero) CloseHandle(job);
    }
}
'@
}

function Test-DockerReady {
  param([string]$DockerPath)
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'SilentlyContinue'
    & $DockerPath info *> $null
    return $LASTEXITCODE -eq 0
  }
  catch {
    return $false
  }
  finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
}

function Test-DockerImageExists {
  param([string]$DockerPath, [string]$Image)
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'SilentlyContinue'
    & $DockerPath image inspect $Image *> $null
    return $LASTEXITCODE -eq 0
  }
  catch {
    return $false
  }
  finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
}

function Get-DockerImageId {
  param([string]$DockerPath, [string]$Image)
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'SilentlyContinue'
    $imageId = & $DockerPath image inspect --format '{{.Id}}' $Image 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return ([string]$imageId).Trim()
  }
  finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
}

function Test-RuntimeManifest {
  param([string]$Root)
  $manifestPath = Join-Path $Root 'runtime-manifest.json'
  if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw 'The portable runtime manifest is missing. Please extract the complete package again.'
  }
  $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
  foreach ($entry in $manifest.files) {
    $filePath = Join-Path $Root ([string]$entry.path)
    if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
      throw "The portable runtime is incomplete: $($entry.path)"
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $filePath).Hash
    if ($actualHash -ne [string]$entry.sha256) {
      throw "The portable runtime failed its integrity check: $($entry.path)"
    }
  }
  return $manifest
}

if (-not (Test-Path -LiteralPath $distRoot)) {
  throw 'The dist folder is missing. Please extract the complete package again.'
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw 'The bundled Python runtime is missing: runtime\python\python.exe. Please extract the complete delivery package again.'
}
$runtimeManifest = Test-RuntimeManifest -Root $runtimeRoot
Stop-RecordedServer

$env:PYTHONHOME = $pythonRoot
$env:PYTHONPATH = Join-Path $backendRoot 'src'
$pythonScripts = Join-Path $pythonRoot 'Scripts'
if (($env:PATH -split ';') -notcontains $pythonRoot) {
  $env:PATH = "$pythonRoot;$pythonScripts;$env:PATH"
}

Write-Host '[1/5] Checking the bundled Python runtime...'
& $python -c 'import pathlib, sys; import fastapi, httpx, playwright, gui_agent, uvicorn; from gui_agent.artifacts import ArtifactManager; from gui_agent.api.server import app; module = pathlib.Path(gui_agent.__file__).resolve(); expected = pathlib.Path(sys.argv[1]).resolve(); raise SystemExit(0 if sys.version_info[:2] == (3, 12) and module.is_relative_to(expected) else 2)' (Join-Path $backendRoot 'src')
if ($LASTEXITCODE -ne 0) {
  throw 'The bundled Python runtime is incomplete or does not match this package. No online repair was attempted.'
}

Write-Host '[2/5] Checking the bundled Chromium runtime...'
if (-not (Test-Path -LiteralPath $browserRoot -PathType Container)) {
  throw 'The bundled Playwright browser runtime is missing: runtime\ms-playwright.'
}
$headedChromium = Get-ChildItem -LiteralPath $browserRoot -Directory -Filter 'chromium-*' -ErrorAction SilentlyContinue |
  ForEach-Object { Join-Path $_.FullName 'chrome-win\chrome.exe' } |
  Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
  Select-Object -First 1
$headlessChromium = Get-ChildItem -LiteralPath $browserRoot -Directory -Filter 'chromium_headless_shell-*' -ErrorAction SilentlyContinue |
  ForEach-Object { Join-Path $_.FullName 'chrome-win\headless_shell.exe' } |
  Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
  Select-Object -First 1
$ffmpeg = Get-ChildItem -LiteralPath $browserRoot -Directory -Filter 'ffmpeg-*' -ErrorAction SilentlyContinue |
  ForEach-Object { Join-Path $_.FullName 'ffmpeg-win64.exe' } |
  Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
  Select-Object -First 1
$winldd = Get-ChildItem -LiteralPath $browserRoot -Directory -Filter 'winldd-*' -ErrorAction SilentlyContinue |
  ForEach-Object { Join-Path $_.FullName 'PrintDeps.exe' } |
  Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
  Select-Object -First 1
if (-not $headedChromium -or -not $headlessChromium -or -not $ffmpeg -or -not $winldd) {
  throw 'The bundled Playwright runtime is incomplete. Headed Chromium, the headless shell, FFmpeg and winldd are required.'
}
$env:PLAYWRIGHT_BROWSERS_PATH = $browserRoot

Write-Host '[3/5] Checking Docker Desktop...'
$docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $docker) {
  $candidate = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
  if (Test-Path -LiteralPath $candidate) { $docker = $candidate }
}
if (-not $docker) {
  throw 'Docker CLI is unavailable. Install and start Docker Desktop; the Runner will not fall back to a host process.'
}
$dockerBin = Split-Path -Parent $docker
if (($env:PATH -split ';') -notcontains $dockerBin) { $env:PATH = "$dockerBin;$env:PATH" }
if (-not (Test-DockerReady -DockerPath $docker)) {
  $dockerDesktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
  if (Test-Path -LiteralPath $dockerDesktop) {
    Write-Host 'Docker Engine is not ready. Starting Docker Desktop...'
    if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
      Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
    }
    $dockerDeadline = (Get-Date).AddMinutes(3)
    do {
      Start-Sleep -Seconds 2
      $dockerReady = Test-DockerReady -DockerPath $docker
    } while (-not $dockerReady -and (Get-Date) -lt $dockerDeadline)
  }
  if (-not $dockerReady) {
    throw 'Docker Engine did not become ready within 3 minutes. Open Docker Desktop, resolve its startup error, and run start.bat again.'
  }
}

Write-Host '[4/5] Checking the offline isolated Runner image...'
$runnerReady = Test-DockerImageExists -DockerPath $docker -Image $runnerImage
if (-not $runnerReady) {
  $runnerArchive = $runnerArchives | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
  if (-not $runnerArchive) {
    throw 'The Runner image is not installed and its offline archive is missing from runtime\images. No online build was attempted.'
  }
  Write-Host "Loading $runnerImage from the offline archive..."
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    & $docker load --input $runnerArchive
    $dockerLoadExitCode = $LASTEXITCODE
  }
  finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  if ($dockerLoadExitCode -ne 0) {
    throw 'The offline Runner image archive could not be loaded.'
  }
  if (-not (Test-DockerImageExists -DockerPath $docker -Image $runnerImage)) {
    throw "The offline archive did not provide the required image tag: $runnerImage"
  }
}
$actualRunnerImageId = Get-DockerImageId -DockerPath $docker -Image $runnerImage
if ($actualRunnerImageId -ne [string]$runtimeManifest.runnerImageId) {
  throw "The installed Runner image does not match this release: $runnerImage"
}
$env:GUI_DOCKER_CLI = $docker
$env:GUI_RUNNER_IMAGE = $runnerImage
$env:GUI_RUNNER_MODE = 'container'

$port = 8080
while ($port -le 8090 -and -not (Test-LocalPortAvailable -Port $port)) {
  $port++
}
if ($port -gt 8090) {
  throw 'No available local port was found between 8080 and 8090.'
}

$env:GUI_STATIC_DIR = $distRoot
$env:GUI_AGENT_ARTIFACTS = Join-Path $packageRoot 'artifacts'
$env:GUI_AGENT_DATA = Join-Path $packageRoot 'data'
$env:GUI_API_HOST = '127.0.0.1'
$env:GUI_API_PORT = [string]$port
$url = "http://127.0.0.1:$port/"

Write-Host "[5/5] Starting the real GUI test service on $url"
$server = $null
$jobHandle = [IntPtr]::Zero
try {
  $jobHandle = [AiGuiPortableJob13000]::CreateKillOnClose()
  $server = Start-Process -FilePath $python `
    -ArgumentList @('-m', 'uvicorn', 'gui_agent.api.server:app', '--host', '127.0.0.1', '--port', [string]$port) `
    -WorkingDirectory $packageRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru
  [AiGuiPortableJob13000]::Assign($jobHandle, $server.Handle)
  $pidRecord = [ordered]@{
    pid = $server.Id
    pythonPath = [System.IO.Path]::GetFullPath($python)
    packageRoot = [System.IO.Path]::GetFullPath($packageRoot)
    startedAtUtc = $server.StartTime.ToUniversalTime().ToString('o')
    command = 'python -m uvicorn gui_agent.api.server:app'
  }
  $pidRecord | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8

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

  if ($env:GUI_SKIP_BROWSER -ne '1') {
    Start-Process $url
  }
  Write-Host ''
  Write-Host "Ready: $url" -ForegroundColor Green
  Write-Host 'Screenshots and reports are stored in the artifacts folder.'
  if ($env:GUI_AUTO_STOP -ne '1') {
    Read-Host 'Press Enter to stop the service'
  }
}
finally {
  if ($server) {
    Remove-PidRecordForProcess -ProcessId $server.Id
  }
  if ($jobHandle -ne [IntPtr]::Zero) {
    [AiGuiPortableJob13000]::Close($jobHandle)
    $jobHandle = [IntPtr]::Zero
  }
  if ($server -and -not $server.HasExited) {
    $null = $server.WaitForExit(5000)
  }
}
