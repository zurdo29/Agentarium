[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

function Require-Command([string]$Name, [string]$InstallHint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Falta '$Name'. $InstallHint"
    }
}

Require-Command "python" "Instala Python 3.11+ desde python.org."
Require-Command "node" "Instala Node.js 22+ desde nodejs.org."
Require-Command "npm.cmd" "Repara la instalación de Node.js/npm."
Require-Command "git" "Instala Git for Windows."

$VersionText = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$Parts = $VersionText.Split(".")
if ([int]$Parts[0] -lt 3 -or ([int]$Parts[0] -eq 3 -and [int]$Parts[1] -lt 11)) {
    throw "Agentarium requiere Python 3.11 o posterior; se detectó $VersionText."
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& npm.cmd ci --no-audit --no-fund

if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "Ollama detectado. No se descargó ningún modelo."
} else {
    Write-Warning "Ollama no está instalado; el proveedor mock funciona sin él."
}

Write-Host "Agentarium está listo. Ejecuta .\dev.ps1"
