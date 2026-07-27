[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

& npm.cmd run build
$Api = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "agentarium.api.app:app", `
    "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $Root -PassThru -WindowStyle Hidden
$Web = Start-Process -FilePath "npm.cmd" -ArgumentList "run", "start" `
    -WorkingDirectory $Root -PassThru -WindowStyle Hidden

Set-Content -LiteralPath "runtime\api.pid" -Value $Api.Id
Set-Content -LiteralPath "runtime\web.pid" -Value $Web.Id
Write-Host "Agentarium iniciado: http://127.0.0.1:3000"
