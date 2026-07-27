[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root
New-Item -ItemType Directory -Path "runtime" -Force | Out-Null

function Start-HiddenCommand([string]$CommandLine) {
    $Info = New-Object System.Diagnostics.ProcessStartInfo
    $Info.FileName = "$env:SystemRoot\System32\cmd.exe"
    $Info.Arguments = "/d /s /c `"$CommandLine`""
    $Info.WorkingDirectory = $Root
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    $Process = New-Object System.Diagnostics.Process
    $Process.StartInfo = $Info
    if (-not $Process.Start()) {
        throw "No se pudo iniciar: $CommandLine"
    }
    return $Process
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "Falta .venv. Ejecuta .\setup.ps1 primero."
}

$Api = Start-HiddenCommand (
    ".venv\Scripts\python.exe -m uvicorn agentarium.api.app:app --reload " +
    "--host 127.0.0.1 --port 8000 1>runtime\api.out.log 2>runtime\api.err.log"
)
$Web = Start-HiddenCommand (
    "npm.cmd run dev 1>runtime\web.out.log 2>runtime\web.err.log"
)

Set-Content -LiteralPath "runtime\api.pid" -Value $Api.Id
Set-Content -LiteralPath "runtime\web.pid" -Value $Web.Id
Write-Host "Agentarium iniciado: http://localhost:3000"
Write-Host "API: http://127.0.0.1:8000/docs"
