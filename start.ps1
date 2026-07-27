[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

function Invoke-Checked([scriptblock]$Command, [string]$Description) {
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description falló con código $LASTEXITCODE."
    }
}

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

Invoke-Checked { & npm.cmd run build } "La compilación web"
$Api = Start-HiddenCommand (
    ".venv\Scripts\python.exe -m uvicorn agentarium.api.app:app " +
    "--host 127.0.0.1 --port 8000 1>runtime\api.out.log 2>runtime\api.err.log"
)
$Web = Start-HiddenCommand (
    "npm.cmd run start 1>runtime\web.out.log 2>runtime\web.err.log"
)

Set-Content -LiteralPath "runtime\api.pid" -Value $Api.Id
Set-Content -LiteralPath "runtime\web.pid" -Value $Web.Id
Write-Host "Agentarium iniciado: http://localhost:3000"
