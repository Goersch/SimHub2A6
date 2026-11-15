
# Start-SimRig.ps1

$ErrorActionPreference = "Stop"

$hmiPath = "H:\SimHub2SimRig\CSharp\bin\SimHub2SimRigHMI.exe"
$rootPath = "H:\SimHub2SimRig"

# 1. Warten, bis SimHub läuft
Write-Host "Warte auf SimHub ..."

do {
    $simHub = Get-Process -Name "SimHubWPF" `
        -ErrorAction SilentlyContinue

    if (-not $simHub) {
        Start-Sleep -Seconds 1
    }
} until ($simHub)

# 2. SimHub-Priorität auf AboveNormal setzen
foreach ($process in @($simHub)) {
    $process.PriorityClass = "AboveNormal"
}

Write-Host "SimHub läuft mit Priorität AboveNormal."

# 3. HMI mit Priorität High starten
Write-Host "Starte SimHub2SimRigHMI ..."

$hmi = Start-Process `
    -FilePath $hmiPath `
    -ArgumentList @("--root", $rootPath) `
    -WorkingDirectory $rootPath `
    -PassThru

$hmi.PriorityClass = "High"

Write-Host "HMI läuft mit Priorität High."

# 4. Kontrolle
Get-Process -Id $simHub[0].Id, $hmi.Id |
    Select-Object ProcessName, Id, PriorityClass
