param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
if (-not $SkipBuild) { & (Join-Path $PSScriptRoot 'build.ps1') }
$repository = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $PSScriptRoot ('dist/SimHub2SimRig-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
if (Test-Path -LiteralPath $destination) { throw "Package directory already exists: $destination" }
New-Item -ItemType Directory -Path (Join-Path $destination 'INI') -Force | Out-Null
foreach ($name in @('SimHub2SimRigCom.exe', 'SimHub2SimRigHMI.exe', 'SimHub2SimRig.Shared.dll', 'SimHub2SimRigCom.exe.config', 'SimHub2SimRigHMI.exe.config')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "bin/$name") -Destination $destination
}
foreach ($name in @('SimHub2SimRig.ini', 'Language.ini')) {
    Copy-Item -LiteralPath (Join-Path $repository "INI/$name") -Destination (Join-Path $destination 'INI')
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination $destination
Copy-Item -LiteralPath (Join-Path $repository 'LICENSE') -Destination $destination
Write-Host "Package with exactly two application executables: $destination"
