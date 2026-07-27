[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "Falta .venv. Ejecuta .\setup.ps1 primero."
}

function Invoke-Checked([scriptblock]$Command, [string]$Description) {
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description falló con código $LASTEXITCODE."
    }
}

Invoke-Checked { & .\.venv\Scripts\python.exe -m ruff check backend } "Ruff"
Invoke-Checked { & .\.venv\Scripts\python.exe -m mypy backend\agentarium } "MyPy"
Invoke-Checked { & .\.venv\Scripts\python.exe -m pytest } "Pytest"
Invoke-Checked { & npm.cmd run lint } "ESLint"
Invoke-Checked { & npm.cmd test } "Pruebas web"
