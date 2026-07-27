[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root
New-Item -ItemType Directory -Path "runtime" -Force | Out-Null

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "Falta .venv. Ejecuta .\setup.ps1 primero."
}

$Api = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "agentarium.api.app:app", "--reload", `
    "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $Root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput "runtime\api.out.log" `
    -RedirectStandardError "runtime\api.err.log"
$Web = Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" `
    -WorkingDirectory $Root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput "runtime\web.out.log" `
    -RedirectStandardError "runtime\web.err.log"

Set-Content -LiteralPath "runtime\api.pid" -Value $Api.Id
Set-Content -LiteralPath "runtime\web.pid" -Value $Web.Id
Write-Host "Agentarium iniciado: http://127.0.0.1:3000"
Write-Host "API: http://127.0.0.1:8000/docs"
