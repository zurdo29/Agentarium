[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "Falta .venv. Ejecuta .\setup.ps1 primero."
}

& .\.venv\Scripts\python.exe -m ruff check backend
& .\.venv\Scripts\python.exe -m mypy backend\agentarium
& .\.venv\Scripts\python.exe -m pytest
& npm.cmd test
