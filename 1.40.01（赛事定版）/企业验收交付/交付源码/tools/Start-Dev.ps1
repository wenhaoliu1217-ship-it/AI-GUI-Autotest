[CmdletBinding()]
param(
  [string]$RuntimePackage,
  [int]$Port = 8080,
  [int]$MaxPort = 8090,
  [switch]$Detached,
  [switch]$PreserveBrowser,
  [switch]$SkipBrowser,
  [switch]$SkipDockerCheck,
  [switch]$RequireDocker
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$localRoot = Join-Path $repoRoot '.local'
$runtimeRecord = Join-Path $localRoot 'runtime-package.txt'
$pidFile = Join-Path $localRoot 'server.pid'
$stdoutLog = Join-Path $localRoot 'server-stdout.log'
$stderrLog = Join-Path $localRoot 'server-stderr.log'
$script:preservedBrowserRecord = $null

function Test-SamePath {
  param([string]$Left, [string]$Right)
  if (-not $Left -or -not $Right) { return $false }
  try {
    $leftFull = [System.IO.Path]::GetFullPath($Left).TrimEnd('\')
    $rightFull = [System.IO.Path]::GetFullPath($Right).TrimEnd('\')
    return [string]::Equals($leftFull, $rightFull, [System.StringComparison]::OrdinalIgnoreCase)
  }
  catch { return $false }
}

function Test-SameInstant {
  param($Left, $Right)
  if ($null -eq $Left -or $null -eq $Right) { return $false }
  try {
    $leftInstant = [DateTimeOffset]$Left
    $rightInstant = [DateTimeOffset]$Right
    return $leftInstant.UtcDateTime.Ticks -eq $rightInstant.UtcDateTime.Ticks
  }
  catch { return $false }
}

function Assert-CanonicalInPlacePackage {
  $manifestPath = Join-Path $repoRoot 'unified-package.json'
  if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "The canonical package manifest is missing: $manifestPath"
  }
  try { $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json }
  catch { throw "The canonical package manifest is invalid: $manifestPath" }
  if (-not $manifest.canonicalInPlace -or [string]$manifest.updatePolicy -ne 'in_place_only') {
    throw 'This package is not marked as the canonical in-place project.'
  }
  if (-not (Test-SamePath -Left ([string]$manifest.repositoryRoot) -Right $repoRoot)) {
    throw "This is not the fixed canonical project path. Start only: $($manifest.repositoryRoot)"
  }
  if ($manifest.externalRuntimeFallback -ne $false) {
    throw 'The canonical project must not use an external runtime fallback.'
  }
}

function Resolve-RuntimePackage {
  param([string]$RequestedPath)
  $canonical = $repoRoot
  if ($RequestedPath) {
    try { $requested = (Resolve-Path -LiteralPath $RequestedPath -ErrorAction Stop).Path }
    catch { throw "The requested runtime path does not exist: $RequestedPath" }
    if (-not (Test-SamePath -Left $requested -Right $canonical)) {
      throw "External runtime packages are disabled. Use this project root: $canonical."
    }
  }
  if (-not (Test-Path -LiteralPath (Join-Path $canonical 'runtime\python\python.exe') -PathType Leaf) -or
      -not (Test-Path -LiteralPath (Join-Path $canonical 'runtime\ms-playwright') -PathType Container)) {
    throw "The canonical runtime is incomplete: $(Join-Path $canonical 'runtime'). Run tools\\Assemble-UnifiedPackage.ps1 once."
  }
  return $canonical
}

function Test-LocalPortAvailable {
  param([int]$LocalPort)
  $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $LocalPort)
  try {
    $listener.Start()
    return $true
  }
  catch { return $false }
  finally { try { $listener.Stop() } catch { } }
}

function Get-ManagedBrowserCdpState {
  param([string]$CdpUrl, [string]$ExpectedBrowserPath, [string]$ExpectedProfilePath)
  if (-not $CdpUrl -or -not $ExpectedBrowserPath -or
      -not (Test-Path -LiteralPath $ExpectedBrowserPath -PathType Leaf)) { return $null }
  try { $uri = [uri]$CdpUrl } catch { return $null }
  if ($uri.Scheme -ne 'http' -or $uri.Host -notin @('127.0.0.1', 'localhost') -or $uri.Port -lt 1) { return $null }
  try {
    $version = Invoke-RestMethod -Uri ($CdpUrl.TrimEnd('/') + '/json/version') -TimeoutSec 2
    if (-not $version.Browser -or -not $version.webSocketDebuggerUrl) { return $null }
    $owner = Get-NetTCPConnection -State Listen -LocalPort $uri.Port -ErrorAction SilentlyContinue |
      Where-Object { $_.LocalAddress -in @('127.0.0.1', '0.0.0.0', '::', '::1') } |
      Select-Object -First 1
    $identity = if ($owner) { Get-ServerProcessIdentity -ProcessId ([int]$owner.OwningProcess) } else { $null }
    if ($identity -and -not (Test-SamePath -Left $identity.ExecutablePath -Right $ExpectedBrowserPath)) { return $null }
    if ($ExpectedProfilePath -and $identity -and $identity.CommandLine -and
        $identity.CommandLine.IndexOf($ExpectedProfilePath, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null }
    return [pscustomobject]@{
      Uri = $uri
      ProcessId = if ($owner) { [int]$owner.OwningProcess } else { $null }
      Identity = $identity
    }
  }
  catch { return $null }
}

function Test-FrontendBundle {
  param([string]$StaticRoot)
  $manifestPath = Join-Path $StaticRoot 'bundle-manifest.json'
  if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Frontend bundle manifest is missing: $manifestPath"
  }
  try { $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json }
  catch { throw "Frontend bundle manifest is invalid: $manifestPath" }
  if ([int]$manifest.schemaVersion -ne 1 -or -not $manifest.assets) {
    throw "Frontend bundle manifest schema is unsupported: $manifestPath"
  }
  if (-not [string]$manifest.apiContractVersion) {
    throw "Frontend bundle API contract is missing: $manifestPath"
  }
  $indexText = Get-Content -Raw -LiteralPath (Join-Path $StaticRoot 'index.html')
  foreach ($property in $manifest.assets.PSObject.Properties) {
    $relative = [string]$property.Name
    $expected = $property.Value
    $target = Join-Path $StaticRoot ($relative -replace '/', '\')
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
      throw "Frontend bundle asset is missing: $relative"
    }
    $actual = Get-Item -LiteralPath $target
    if ([long]$actual.Length -ne [long]$expected.bytes) {
      throw "Frontend bundle asset size mismatch: $relative"
    }
    $stream = [System.IO.File]::OpenRead($target)
    try {
      $sha256 = [System.Security.Cryptography.SHA256]::Create()
      try {
        $hashBytes = $sha256.ComputeHash($stream)
        $hash = [System.BitConverter]::ToString($hashBytes).Replace('-', '').ToLowerInvariant()
      }
      finally { $sha256.Dispose() }
    }
    finally { $stream.Dispose() }
    if ($hash -ne ([string]$expected.sha256).ToLowerInvariant()) {
      throw "Frontend bundle asset hash mismatch: $relative"
    }
    if ($indexText -notmatch [regex]::Escape($relative)) {
      throw "Frontend bundle entry does not reference manifest asset: $relative"
    }
  }
}

function Get-ServerProcessIdentity {
  param([int]$ProcessId)
  $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if (-not $process) { return $null }
  $cimProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  $executablePath = if ($cimProcess) { [string]$cimProcess.ExecutablePath } else { $null }
  if (-not $executablePath) {
    try { $executablePath = [string]$process.Path } catch { }
  }
  return [pscustomobject]@{
    Process = $process
    ExecutablePath = $executablePath
    CommandLine = if ($cimProcess) { [string]$cimProcess.CommandLine } else { $null }
    StartedAtUtc = $process.StartTime.ToUniversalTime().ToString('o')
  }
}

function Test-RecordedServerIdentity {
  param($Record, $Identity, [string]$PythonPath)
  if (-not $Record -or -not $Identity) { return $false }
  if (-not (Test-SamePath -Left ([string]$Record.repoRoot) -Right $repoRoot)) { return $false }
  if (-not (Test-SamePath -Left ([string]$Record.pythonPath) -Right $PythonPath)) { return $false }
  if (-not (Test-SamePath -Left $Identity.ExecutablePath -Right $PythonPath)) { return $false }
  if (-not (Test-SameInstant -Left $Record.startedAtUtc -Right $Identity.StartedAtUtc)) { return $false }
  if ($Identity.CommandLine) {
    return $Identity.CommandLine -match '(?i)run_dev_server\.py'
  }
  return $true
}

function Stop-RecordedManagedBrowser {
  param($Record)
  if (-not $Record.browserPid -or -not $Record.browserPath -or -not $Record.browserStartedAtUtc) { return }
  $identity = Get-ServerProcessIdentity -ProcessId ([int]$Record.browserPid)
  if (-not $identity) { return }
  $matches = (Test-SamePath -Left $identity.ExecutablePath -Right ([string]$Record.browserPath)) -and
    (Test-SameInstant -Left $Record.browserStartedAtUtc -Right $identity.StartedAtUtc)
  if (-not $matches) {
    Write-Warning 'The recorded managed Edge PID now belongs to a different process; it was not stopped.'
    return
  }
  Write-Host "Stopping the previous managed Edge login window (PID $($Record.browserPid))..."
  Stop-Process -Id ([int]$Record.browserPid) -ErrorAction Stop
  $null = $identity.Process.WaitForExit(5000)
  if (-not $identity.Process.HasExited) {
    throw 'The previous managed Edge login window did not stop within 5 seconds.'
  }
}

function Stop-RecordedServer {
  param([string]$PythonPath, [switch]$PreserveManagedBrowser)
  if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { return }
  try { $record = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json }
  catch { throw 'The development server PID record is invalid. No process was stopped.' }

  $identity = Get-ServerProcessIdentity -ProcessId ([int]$record.pid)
  if (-not $identity) {
    if ($PreserveManagedBrowser) { $script:preservedBrowserRecord = $record }
    else { Stop-RecordedManagedBrowser -Record $record }
    Remove-Item -LiteralPath $pidFile -Force
    return
  }
  if (-not (Test-RecordedServerIdentity -Record $record -Identity $identity -PythonPath $PythonPath)) {
    throw 'The recorded PID does not match this development service. No process was stopped.'
  }

  Write-Host "Stopping the previous development service (PID $($record.pid))..."
  Stop-Process -Id ([int]$record.pid) -ErrorAction Stop
  $null = $identity.Process.WaitForExit(5000)
  if (-not $identity.Process.HasExited) {
    throw 'The previous development service did not stop within 5 seconds.'
  }
  if ($PreserveManagedBrowser) { $script:preservedBrowserRecord = $record }
  else { Stop-RecordedManagedBrowser -Record $record }
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

function Test-DockerReady {
  param([string]$DockerPath)
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'SilentlyContinue'
    & $DockerPath info *> $null
    return $LASTEXITCODE -eq 0
  }
  catch { return $false }
  finally { $ErrorActionPreference = $previousPreference }
}

function Test-DockerImageExists {
  param([string]$DockerPath, [string]$Image)
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'SilentlyContinue'
    & $DockerPath image inspect $Image *> $null
    return $LASTEXITCODE -eq 0
  }
  catch { return $false }
  finally { $ErrorActionPreference = $previousPreference }
}

function Initialize-DockerRunner {
  param([string]$RuntimeRoot)
  $runnerImage = 'ai-gui-runner:1.32.00'
  $docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
  if (-not $docker) {
    $dockerCandidates = @(
      (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'),
      (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe')
    )
    $docker = $dockerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
  }
  if (-not $docker) {
    throw 'Docker CLI is unavailable. Install Docker Desktop before running real isolated tests.'
  }

  if (-not (Test-DockerReady -DockerPath $docker)) {
    $desktopCandidates = @(
      (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
      (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe')
    )
    $dockerDesktop = $desktopCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if ($dockerDesktop -and -not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
      Write-Host 'Docker Engine is not ready. Starting Docker Desktop...'
      Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
    }
    $deadline = (Get-Date).AddMinutes(3)
    do {
      Start-Sleep -Seconds 2
      $ready = Test-DockerReady -DockerPath $docker
    } while (-not $ready -and (Get-Date) -lt $deadline)
    if (-not $ready) { throw 'Docker Engine did not become ready within 3 minutes.' }
  }

  if (-not (Test-DockerImageExists -DockerPath $docker -Image $runnerImage)) {
    $archiveRoot = Join-Path $RuntimeRoot 'runtime\images'
    $archives = @(
      (Join-Path $archiveRoot 'ai-gui-runner-1.32.00.tar'),
      (Join-Path $archiveRoot 'ai-gui-runner-1.32.00.tar.gz'),
      (Join-Path $archiveRoot 'ai-gui-runner-1.32.00.tgz')
    )
    $archive = $archives | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $archive) {
      throw "Runner image $runnerImage is not installed and no offline archive was found."
    }
    Write-Host "Loading $runnerImage from the offline archive..."
    & $docker load --input $archive
    if ($LASTEXITCODE -ne 0 -or -not (Test-DockerImageExists -DockerPath $docker -Image $runnerImage)) {
      throw "The offline archive did not load $runnerImage."
    }
  }

  $env:GUI_DOCKER_CLI = $docker
  $env:GUI_RUNNER_IMAGE = $runnerImage
  $env:GUI_RUNNER_MODE = 'container'
}

if (-not ('AiGuiDevelopmentJob' -as [type])) {
  Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class AiGuiDevelopmentJob
{
    private const UInt32 KillOnClose = 0x00002000;
    private const Int32 ExtendedInfo = 9;

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimits
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
    private struct IoCounters
    {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits
    {
        public BasicLimits BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
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
        ExtendedLimits limits = new ExtendedLimits();
        limits.BasicLimitInformation.LimitFlags = KillOnClose;
        Int32 size = Marshal.SizeOf(typeof(ExtendedLimits));
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(limits, buffer, false);
            if (!SetInformationJobObject(job, ExtendedInfo, buffer, (UInt32)size))
                throw new Win32Exception(Marshal.GetLastWin32Error());
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

New-Item -ItemType Directory -Force -Path `
  $localRoot,
  (Join-Path $localRoot 'artifacts'),
  (Join-Path $localRoot 'data') | Out-Null

Assert-CanonicalInPlacePackage
$runtimeRoot = Resolve-RuntimePackage -RequestedPath $RuntimePackage
$pythonRoot = Join-Path $runtimeRoot 'runtime\python'
$python = Join-Path $pythonRoot 'python.exe'
$browserRoot = Join-Path $runtimeRoot 'runtime\ms-playwright'
$sourceRoot = (Resolve-Path (Join-Path $repoRoot 'backend\src')).Path
$staticRoot = (Resolve-Path (Join-Path $repoRoot 'frontend-dist')).Path
$serverLauncher = (Resolve-Path (Join-Path $repoRoot 'tools\run_dev_server.py')).Path
Test-FrontendBundle -StaticRoot $staticRoot

Set-Content -LiteralPath $runtimeRecord -Value $runtimeRoot -Encoding UTF8
Stop-RecordedServer -PythonPath $python -PreserveManagedBrowser:$PreserveBrowser

$env:PYTHONHOME = $pythonRoot
$env:PYTHONPATH = $sourceRoot
$env:PLAYWRIGHT_BROWSERS_PATH = $browserRoot
$env:GUI_STATIC_DIR = $staticRoot
$env:GUI_AGENT_ARTIFACTS = Join-Path $localRoot 'artifacts'
$env:GUI_AGENT_DATA = Join-Path $localRoot 'data'
$env:GUI_API_HOST = '127.0.0.1'

$managedBrowser = $null
$managedBrowserPort = $null
$managedBrowserReused = $false
$managedBrowserProfile = Join-Path $localRoot 'managed-edge-profile'
if (-not $SkipBrowser -and $env:GUI_SKIP_BROWSER -ne '1' -and $env:OS -eq 'Windows_NT') {
  if ($PreserveBrowser -and $script:preservedBrowserRecord) {
    $saved = $script:preservedBrowserRecord
    $savedCdp = Get-ManagedBrowserCdpState -CdpUrl ([string]$saved.browserCdpUrl) -ExpectedBrowserPath ([string]$saved.browserPath) -ExpectedProfilePath $managedBrowserProfile
    if (-not $savedCdp) {
      foreach ($candidatePort in 9222..9232) {
        $candidateUrl = "http://127.0.0.1:$candidatePort"
        $candidateCdp = Get-ManagedBrowserCdpState -CdpUrl $candidateUrl -ExpectedBrowserPath ([string]$saved.browserPath) -ExpectedProfilePath $managedBrowserProfile
        if ($candidateCdp) {
          $savedCdp = $candidateCdp
          $saved.browserCdpUrl = $candidateUrl
          break
        }
      }
    }
    if ($savedCdp) {
      $managedBrowser = [string]$saved.browserPath
      $managedBrowserPort = [int]$savedCdp.Uri.Port
      $managedBrowserReused = $true
      $env:GUI_BROWSER_CDP_URL = [string]$saved.browserCdpUrl
      $env:GUI_BROWSER_NAME = 'Microsoft Edge'
      if ($savedCdp.ProcessId -and $savedCdp.Identity) {
        $saved.browserPid = [int]$savedCdp.ProcessId
        $saved.browserStartedAtUtc = [string]$savedCdp.Identity.StartedAtUtc
      }
    }
  }
  if (-not $managedBrowser) {
    $edgeCandidates = @()
    foreach ($programRoot in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA)) {
      if ($programRoot) { $edgeCandidates += Join-Path $programRoot 'Microsoft\Edge\Application\msedge.exe' }
    }
    $managedBrowser = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
  }
  if ($managedBrowser) {
    if (-not $managedBrowserPort) {
      foreach ($candidatePort in 9222..9232) {
        if (Test-LocalPortAvailable -LocalPort $candidatePort) {
          $managedBrowserPort = $candidatePort
          break
        }
      }
    }
    if ($managedBrowserPort) {
      New-Item -ItemType Directory -Force -Path $managedBrowserProfile | Out-Null
      $env:GUI_BROWSER_CDP_URL = "http://127.0.0.1:$managedBrowserPort"
      $env:GUI_BROWSER_NAME = 'Microsoft Edge'
    }
    else { Write-Warning 'No available managed Edge debugging port was found; the built-in login window will be used.' }
  }
  else { Write-Warning 'Microsoft Edge was not found; the built-in login window will be used.' }
}

Write-Host '[1/4] Checking the bundled Python runtime...'
& $python $serverLauncher --check --frontend-manifest (Join-Path $staticRoot 'bundle-manifest.json')
$runtimeCheckExit = $LASTEXITCODE
if ($runtimeCheckExit -eq 4) {
  throw 'The frontend bundle API contract is incompatible with the current backend source.'
}
if ($runtimeCheckExit -ne 0) { throw 'The bundled Python runtime cannot load the current source tree.' }

Write-Host '[2/4] Checking the bundled Chromium runtime...'
$chromium = Get-ChildItem -LiteralPath $browserRoot -Directory -Filter 'chromium-*' -ErrorAction SilentlyContinue |
  ForEach-Object { Join-Path $_.FullName 'chrome-win\chrome.exe' } |
  Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
  Select-Object -First 1
if (-not $chromium) { throw 'The bundled Playwright Chromium runtime is incomplete.' }

Write-Host '[3/4] Checking the isolated Runner...'
if ($SkipDockerCheck) {
  $env:GUI_RUNNER_MODE = 'container'
  Write-Host 'Docker validation was skipped for this diagnostic launch.' -ForegroundColor Yellow
}
else {
  try {
    Initialize-DockerRunner -RuntimeRoot $runtimeRoot
  }
  catch {
    if ($RequireDocker) { throw }
    $env:GUI_RUNNER_MODE = 'container'
    Write-Warning "The web UI will start, but real isolated tests require Docker: $($_.Exception.Message)"
  }
}

if ($managedBrowser -and $managedBrowserPort) {
  $env:GUI_RUNNER_MODE = 'process'
  Write-Host "Managed Edge login is enabled on debugging port $managedBrowserPort." -ForegroundColor Cyan
}

$selectedPort = $Port
while ($selectedPort -le $MaxPort -and -not (Test-LocalPortAvailable -LocalPort $selectedPort)) {
  $selectedPort++
}
if ($selectedPort -gt $MaxPort) {
  throw "No available local port was found between $Port and $MaxPort."
}

$env:GUI_API_PORT = [string]$selectedPort
$url = "http://127.0.0.1:$selectedPort/"
Write-Host "[4/4] Starting the AI-GUI service on $url"

$server = $null
$browserProcess = $null
$jobHandle = [IntPtr]::Zero
$startupCompleted = $false
try {
  if (-not $Detached) { $jobHandle = [AiGuiDevelopmentJob]::CreateKillOnClose() }
  $server = Start-Process -FilePath $python `
    -ArgumentList @(('"{0}"' -f $serverLauncher)) `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru
  if (-not $Detached) { [AiGuiDevelopmentJob]::Assign($jobHandle, $server.Handle) }

  $pidRecord = [ordered]@{
    pid = $server.Id
    port = $selectedPort
    url = $url
    pythonPath = [System.IO.Path]::GetFullPath($python)
    repoRoot = [System.IO.Path]::GetFullPath($repoRoot)
    runtimeRoot = [System.IO.Path]::GetFullPath($runtimeRoot)
    startedAtUtc = $server.StartTime.ToUniversalTime().ToString('o')
    command = 'python tools/run_dev_server.py'
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
    Write-Host 'The service did not start. Recent error log:' -ForegroundColor Red
    if (Test-Path -LiteralPath $stderrLog) { Get-Content -LiteralPath $stderrLog -Tail 40 }
    throw 'AI-GUI service startup failed.'
  }

  if ($managedBrowser -and $managedBrowserPort) {
    try {
      if ($managedBrowserReused) {
        $pidRecord['browserPid'] = [int]$script:preservedBrowserRecord.browserPid
        $pidRecord['browserPath'] = [string]$script:preservedBrowserRecord.browserPath
        $pidRecord['browserStartedAtUtc'] = [string]$script:preservedBrowserRecord.browserStartedAtUtc
        $pidRecord['browserCdpUrl'] = [string]$script:preservedBrowserRecord.browserCdpUrl
        $pidRecord | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8
        Write-Host 'The existing controlled Microsoft Edge window was preserved.' -ForegroundColor Cyan
      }
      else {
        $browserArguments = @(
        "--remote-debugging-port=$managedBrowserPort",
        "--user-data-dir=$managedBrowserProfile",
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-blink-features=AutomationControlled',
        '--new-window',
        $url
      )
        $browserProcess = Start-Process -FilePath $managedBrowser -ArgumentList $browserArguments -PassThru
        $browserProcess.Refresh()
        $pidRecord['browserPid'] = $browserProcess.Id
        $pidRecord['browserPath'] = [System.IO.Path]::GetFullPath($managedBrowser)
        $pidRecord['browserStartedAtUtc'] = $browserProcess.StartTime.ToUniversalTime().ToString('o')
        $pidRecord['browserCdpUrl'] = $env:GUI_BROWSER_CDP_URL
        $pidRecord | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8
        Write-Host 'The controlled Microsoft Edge window is open. Login pages will appear there.' -ForegroundColor Cyan
      }
    }
    catch { Write-Warning "The service is ready, but managed Edge could not be opened; use the built-in login window: $($_.Exception.Message)" }
  }
  elseif (-not $SkipBrowser -and $env:GUI_SKIP_BROWSER -ne '1') {
    try { Start-Process $url }
    catch { Write-Warning "The service is ready, but the browser could not be opened: $($_.Exception.Message)" }
  }

  Write-Host ''
  Write-Host "Ready: $url" -ForegroundColor Green
  Write-Host "Backend log: $stderrLog"
  Write-Host "Artifacts: $(Join-Path $localRoot 'artifacts')"
  $startupCompleted = $true

  if (-not $Detached -and $env:GUI_AUTO_STOP -ne '1') {
    Read-Host 'Press Enter to stop the service'
  }
}
finally {
  if (-not $Detached -or -not $startupCompleted) {
    if ($browserProcess -and -not $browserProcess.HasExited) {
      Stop-Process -Id $browserProcess.Id -ErrorAction SilentlyContinue
      $null = $browserProcess.WaitForExit(5000)
    }
    if ($server) {
      if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
        try {
          $record = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
          if ([int]$record.pid -eq $server.Id) { Remove-Item -LiteralPath $pidFile -Force }
        }
        catch { }
      }
    }
    if ($jobHandle -ne [IntPtr]::Zero) {
      [AiGuiDevelopmentJob]::Close($jobHandle)
      $jobHandle = [IntPtr]::Zero
    }
    if ($server -and -not $server.HasExited) { $null = $server.WaitForExit(5000) }
  }
}
