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
& (Join-Path $PSScriptRoot 'tools\Start-Dev.ps1') @PSBoundParameters
