param(
    [switch]$Test,
    [switch]$UiTest,
    [switch]$HmiOnly,
    [string]$OutputDirectory,
    [ValidateSet('Release', 'Debug')][string]$Configuration = 'Release'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$out = if ($OutputDirectory) { [System.IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $PSScriptRoot 'bin' }
New-Item -ItemType Directory -Force -Path $out | Out-Null
$frameworkDirectory = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319'
$compiler = Join-Path $frameworkDirectory 'csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Install .NET Framework 4.8 (64 bit) to build this solution.' }
$common = @('/nologo', '/warn:4', '/warnaserror+', '/optimize+', '/platform:x64', '/langversion:5', '/codepage:65001', '/r:System.Core.dll', '/r:System.Web.Extensions.dll')
$compilerPrefix = @()
if ($Configuration -eq 'Debug') {
    # The VS Code Desktop CLR debugger requires portable PDBs. The older
    # Framework compiler only emits Windows PDBs, so use the SDK's Roslyn
    # compiler for Debug while still targeting the installed Framework APIs.
    $dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
    $dotnetPath = if ($dotnetCommand) { $dotnetCommand.Source } else { Join-Path $env:ProgramFiles 'dotnet/dotnet.exe' }
    if (-not (Test-Path -LiteralPath $dotnetPath)) { throw 'Debug builds for VS Code require a .NET SDK. Release builds do not require an SDK.' }
    $sdkLines = @(& $dotnetPath --list-sdks)
    if ($LASTEXITCODE -ne 0) { throw 'Could not locate the .NET SDK for the Debug compiler.' }
    $roslynCompilers = @($sdkLines | ForEach-Object {
        if ($_ -match '^([^ ]+) \[(.+)\]$') {
            $candidate = Join-Path (Join-Path $Matches[2] $Matches[1]) 'Roslyn/bincore/csc.dll'
            if (Test-Path -LiteralPath $candidate) { $candidate }
        }
    })
    if ($roslynCompilers.Count -eq 0) { throw 'The .NET SDK Roslyn compiler was not found.' }
    $compiler = $dotnetPath
    $compilerPrefix = @('exec', $roslynCompilers[-1])
    $common = @('/nologo', '/noconfig', '/nostdlib+', "/lib:$frameworkDirectory", '/r:mscorlib.dll', '/r:System.dll', '/r:System.Core.dll', '/r:System.Web.Extensions.dll', '/warn:4', '/warnaserror+', '/optimize-', '/debug:portable', '/define:DEBUG,TRACE', '/platform:x64', '/langversion:5', '/codepage:65001')
}
function Compile($arguments) {
    & $compiler @compilerPrefix @common @arguments
    if ($LASTEXITCODE -ne 0) { throw 'C# compilation failed.' }
}
$shared = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'Shared') -Filter '*.cs' | ForEach-Object FullName)
$com = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'Com') -Filter '*.cs' | ForEach-Object FullName)
$hmi = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'HMI') -Filter '*.cs' | ForEach-Object FullName)
$sharedReference = Join-Path $out 'SimHub2SimRig.Shared.dll'
if ($HmiOnly) {
    # Compile against the exact DLL used by the running Com process. An HMI
    # display update must not replace that DLL or rebuild the live Com EXE.
    $sharedReference = Join-Path $PSScriptRoot 'bin/SimHub2SimRig.Shared.dll'
    if (-not (Test-Path -LiteralPath $sharedReference)) { throw 'Build the complete application once before using -HmiOnly.' }
    if ([System.IO.Path]::GetFullPath($sharedReference) -ne [System.IO.Path]::GetFullPath((Join-Path $out 'SimHub2SimRig.Shared.dll'))) {
        Copy-Item -LiteralPath $sharedReference -Destination (Join-Path $out 'SimHub2SimRig.Shared.dll') -Force
    }
} else {
    Compile (@('/target:library', "/out:$out/SimHub2SimRig.Shared.dll") + $shared)
    Compile (@('/target:exe', "/out:$out/SimHub2SimRigCom.exe", "/r:$out/SimHub2SimRig.Shared.dll") + $com)
}
if ($hmi.Count -gt 0) {
    Compile (@('/target:winexe', "/out:$out/SimHub2SimRigHMI.exe", "/r:$sharedReference", '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', '/r:System.Windows.Forms.DataVisualization.dll') + $hmi)
}
$runtimeNames = if ($HmiOnly) { @('SimHub2SimRigHMI') } else { @('SimHub2SimRigCom', 'SimHub2SimRigHMI') }
foreach ($name in $runtimeNames) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'app.config') -Destination (Join-Path $out "$name.exe.config") -Force
}
if ($Test -and $HmiOnly) { throw 'Use -UiTest with -HmiOnly; full integration tests require both freshly built executables.' }
if ($Test -or $UiTest) {
    if ($Test) {
        & python (Join-Path $PSScriptRoot 'Tests/generate_reference.py')
        if ($LASTEXITCODE -ne 0) { throw 'Python reference generation failed.' }
    }
    $testOut = Join-Path $PSScriptRoot 'Tests/bin'
    New-Item -ItemType Directory -Force -Path $testOut | Out-Null
    Copy-Item -LiteralPath "$out/SimHub2SimRig.Shared.dll" -Destination $testOut -Force
    Copy-Item -LiteralPath "$out/SimHub2SimRigHMI.exe" -Destination $testOut -Force
    $tests = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'Tests') -Filter '*.cs' | ForEach-Object FullName)
    Compile (@('/target:exe', '/main:SimHub2SimRig.Tests.TestProgram', "/out:$testOut/SimHub2SimRig.Tests.exe", "/r:$out/SimHub2SimRig.Shared.dll", "/r:$out/SimHub2SimRigHMI.exe", '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', '/r:System.Windows.Forms.DataVisualization.dll') + $com + $tests)
    $testArgs = @($root, '--bin', $out)
    if ($UiTest -and -not $Test) { $testArgs += '--ui-only' }
    & (Join-Path $testOut 'SimHub2SimRig.Tests.exe') @testArgs
    if ($LASTEXITCODE -ne 0) { throw 'C# tests failed.' }
}
Write-Host "Build successful ($Configuration): $out"
