$ErrorActionPreference = 'Stop'

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeRoot = Join-Path $packageRoot 'runtime'
$pythonRoot = Join-Path $runtimeRoot 'python'
$python = Join-Path $pythonRoot 'python.exe'
$browserRoot = Join-Path $runtimeRoot 'ms-playwright'
$imageRoot = Join-Path $runtimeRoot 'images'
$backendRoot = Join-Path $packageRoot 'backend'
$distRoot = Join-Path $packageRoot 'dist'
$stdoutLog = Join-Path $packageRoot 'server-stdout.log'
$stderrLog = Join-Path $packageRoot 'server-stderr.log'
$serverPidFile = Join-Path $packageRoot 'server.pid'
$runnerImage = 'ai-gui-runner:1.30.10'
$runnerImageArchives = @(
  (Join-Path $imageRoot 'ai-gui-runner-1.30.10.tar'),
  (Join-Path $imageRoot 'ai-gui-runner-1.30.10.tar.gz')
)

if (-not ('AiGuiPortableJob' -as [type])) {
  Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class AiGuiPortableJob
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
    private static extern bool SetInformationJobObject(
        IntPtr job,
        Int32 informationClass,
        IntPtr information,
        UInt32 informationLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    public static IntPtr CreateKillOnClose()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not create the service Job Object.");

        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        Int32 size = Marshal.SizeOf(limits);
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(limits, buffer, false);
            if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, buffer, (UInt32)size))
            {
                Int32 error = Marshal.GetLastWin32Error();
                CloseHandle(job);
                throw new Win32Exception(error, "Could not configure the service Job Object.");
            }
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
        return job;
    }

    public static void Assign(IntPtr job, IntPtr process)
    {
        if (!AssignProcessToJobObject(job, process))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not bind the service to the launcher Job Object.");
    }

    public static void Close(IntPtr job)
    {
        if (job != IntPtr.Zero)
            CloseHandle(job);
    }
}
'@
}

function Get-NormalizedPath {
  param([string]$Path)
  return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Test-SamePath {
  param([string]$Left, [string]$Right)
  if (-not $Left -or -not $Right) { return $false }
  return [string]::Equals(
    (Get-NormalizedPath -Path $Left),
    (Get-NormalizedPath -Path $Right),
    [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-ServerProcessRecord {
  param([int]$ProcessId)
  $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if (-not $process) { return $null }
  $executablePath = $null
  try { $executablePath = $process.Path } catch { }
  $commandLine = $null
  try {
    $cimRecord = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
    if ($cimRecord) {
      if ($cimRecord.ExecutablePath) { $executablePath = [string]$cimRecord.ExecutablePath }
      $commandLine = [string]$cimRecord.CommandLine
    }
  }
  catch { }
  return [pscustomobject]@{
    ExecutablePath = $executablePath
    CommandLine = $commandLine
  }
}

function Stop-OwnedServerFromPidFile {
  param(
    [string]$PidFile,
    [string]$ExpectedPackageRoot,
    [string]$ExpectedPython
  )
  if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) { return }

  try {
    $metadata = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json
  }
  catch {
    throw 'The existing server.pid is invalid. It was not trusted and no process was stopped.'
  }
  if (-not $metadata.processId -or
      -not (Test-SamePath -Left ([string]$metadata.packageRoot) -Right $ExpectedPackageRoot) -or
      -not (Test-SamePath -Left ([string]$metadata.pythonPath) -Right $ExpectedPython)) {
    throw 'The existing server.pid does not belong to this package. No process was stopped.'
  }

  $record = Get-ServerProcessRecord -ProcessId ([int]$metadata.processId)
  if (-not $record) {
    Remove-Item -LiteralPath $PidFile -Force
    return
  }
  $isSamePython = Test-SamePath -Left ([string]$record.ExecutablePath) -Right $ExpectedPython
  $isExpectedCommand = ([string]$record.CommandLine) -match '(?i)(?:^|\s)-m\s+uvicorn\s+gui_agent\.api\.server:app(?:\s|$)'
  if ($isSamePython -and -not $record.CommandLine) {
    throw 'The previous process command line could not be verified. No process was stopped.'
  }
  if (-not ($isSamePython -and $isExpectedCommand)) {
    Write-Warning 'server.pid refers to a different process. The process was not stopped; the stale PID file was removed.'
    Remove-Item -LiteralPath $PidFile -Force
    return
  }

  Write-Host "Stopping the previous service from this package (PID $($metadata.processId))..."
  Stop-Process -Id ([int]$metadata.processId) -ErrorAction Stop
  $process = Get-Process -Id ([int]$metadata.processId) -ErrorAction SilentlyContinue
  if ($process) { $null = $process.WaitForExit(5000) }
  Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Remove-CurrentServerPidFile {
  param([string]$PidFile, [int]$ProcessId)
  if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) { return }
  try {
    $metadata = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json
    if ([int]$metadata.processId -eq $ProcessId) {
      Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
  }
  catch { }
}

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
    throw 'The portable runtime manifest is missing. Extract the complete package again.'
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

Write-Host '[1/5] Checking the portable runtime...'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw 'The bundled Python runtime is missing. Extract the complete package again; system Python is not used.'
}
if (-not (Test-Path -LiteralPath $browserRoot -PathType Container)) {
  throw 'The bundled Chromium runtime is missing. Extract the complete package again; online installation is disabled.'
}
$runnerImageArchive = $runnerImageArchives | Where-Object {
  Test-Path -LiteralPath $_ -PathType Leaf
} | Select-Object -First 1
if (-not $runnerImageArchive) {
  throw 'The offline Runner image archive is missing. Extract the complete package again; online image builds are disabled.'
}
$runtimeManifest = Test-RuntimeManifest -Root $runtimeRoot

Write-Host '[2/5] Checking application dependencies...'
$env:PYTHONPATH = Join-Path $backendRoot 'src'
$env:PLAYWRIGHT_BROWSERS_PATH = $browserRoot
$previousErrorActionPreference = $ErrorActionPreference
try {
  $ErrorActionPreference = 'SilentlyContinue'
  & $python -c 'import fastapi, httpx, playwright, yaml, pydantic, uvicorn, gui_agent; from gui_agent.artifacts import ArtifactManager; from gui_agent.api.server import app' 2>$null
  $dependenciesReady = $LASTEXITCODE -eq 0
}
finally {
  $ErrorActionPreference = $previousErrorActionPreference
}
if (-not $dependenciesReady) {
  throw 'The bundled Python dependencies are incomplete or incompatible. Extract the complete package again; online pip installation is disabled.'
}

Write-Host '[3/5] Checking the bundled Chromium...'
$browserMetadataPath = Join-Path $pythonRoot 'Lib\site-packages\playwright\driver\package\browsers.json'
if (-not (Test-Path -LiteralPath $browserMetadataPath -PathType Leaf)) {
  throw 'The bundled Playwright browser metadata is missing. Extract the complete package again.'
}
$browserMetadata = Get-Content -Raw -LiteralPath $browserMetadataPath | ConvertFrom-Json
$chromiumRevision = ($browserMetadata.browsers | Where-Object name -eq 'chromium').revision
$ffmpegRevision = ($browserMetadata.browsers | Where-Object name -eq 'ffmpeg').revision
if (-not $chromiumRevision -or -not $ffmpegRevision) {
  throw 'The bundled Playwright browser metadata is invalid.'
}
$chromiumReady = (Test-Path (Join-Path $browserRoot "chromium-$chromiumRevision\chrome-win\chrome.exe")) -or
  (Test-Path (Join-Path $browserRoot "chromium-$chromiumRevision\chrome-win64\chrome.exe"))
$headlessReady = (Test-Path (Join-Path $browserRoot "chromium_headless_shell-$chromiumRevision\chrome-win\headless_shell.exe")) -or
  (Test-Path (Join-Path $browserRoot "chromium_headless_shell-$chromiumRevision\chrome-headless-shell-win64\headless_shell.exe"))
$ffmpegReady = Test-Path (Join-Path $browserRoot "ffmpeg-$ffmpegRevision\ffmpeg-win64.exe")
if (-not ($chromiumReady -and $headlessReady -and $ffmpegReady)) {
  throw 'The bundled Chromium files are incomplete or do not match Playwright. Extract the complete package again; online installation is disabled.'
}

$resolvedPackageRoot = Get-NormalizedPath -Path $packageRoot
$resolvedPython = Get-NormalizedPath -Path $python
Stop-OwnedServerFromPidFile `
  -PidFile $serverPidFile `
  -ExpectedPackageRoot $resolvedPackageRoot `
  -ExpectedPython $resolvedPython

Write-Host '[4/5] Checking the isolated container Runner...'
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
$runnerImageReady = Test-DockerImageExists -DockerPath $docker -Image $runnerImage
if (-not $runnerImageReady) {
  Write-Host 'Loading the isolated Runner from the offline package...'
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    & $docker load --input $runnerImageArchive
    $dockerLoadExitCode = $LASTEXITCODE
  }
  finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  if ($dockerLoadExitCode -ne 0) {
    throw 'The isolated Runner image could not be loaded from the offline package.'
  }
  if (-not (Test-DockerImageExists -DockerPath $docker -Image $runnerImage)) {
    throw 'The offline archive loaded, but the required ai-gui-runner:1.30.10 image tag was not found.'
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
$server = Start-Process -FilePath $python `
  -ArgumentList @('-m', 'uvicorn', 'gui_agent.api.server:app', '--host', '127.0.0.1', '--port', [string]$port) `
  -WorkingDirectory $packageRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog `
  -PassThru

try {
  $jobHandle = [AiGuiPortableJob]::CreateKillOnClose()
  [AiGuiPortableJob]::Assign($jobHandle, $server.Handle)
  [ordered]@{
    schemaVersion = 1
    processId = $server.Id
    packageRoot = $resolvedPackageRoot
    pythonPath = $resolvedPython
    command = 'python -m uvicorn gui_agent.api.server:app'
    port = $port
    startedAt = (Get-Date).ToUniversalTime().ToString('o')
  } | ConvertTo-Json | Set-Content -LiteralPath $serverPidFile -Encoding UTF8

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
  if ($server -and -not $server.HasExited) {
    Stop-Process -Id $server.Id -ErrorAction SilentlyContinue
    $null = $server.WaitForExit(5000)
  }
  if ($server) {
    Remove-CurrentServerPidFile -PidFile $serverPidFile -ProcessId $server.Id
  }
  [AiGuiPortableJob]::Close($jobHandle)
}
