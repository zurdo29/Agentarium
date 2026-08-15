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

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description falló con código $LASTEXITCODE."
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

Invoke-Checked { & .\.venv\Scripts\python.exe -m pip install --upgrade pip } `
    "La actualización de pip"
Invoke-Checked { & .\.venv\Scripts\python.exe -m pip install -e ".[dev]" } `
    "La instalación de dependencias Python"
Invoke-Checked { & npm.cmd ci --no-audit --no-fund } `
    "La instalación de dependencias web"

if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "Ollama detectado. No se descargó ningún modelo."
} else {
    Write-Warning "Ollama no está instalado; el proveedor mock funciona sin él."
}

# doctor nunca debe abortar la instalación: pip/npm ya terminaron bien
# acá, y un FAIL de doctor (ej. proveedor mal configurado, Ollama sin
# modelos) es diagnóstico, no un fallo de setup.ps1 en sí. Se captura el
# exit code explícito en vez de confiar en $ErrorActionPreference con un
# ejecutable nativo, que se comporta distinto entre versiones/hosts de
# PowerShell.
$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& .\.venv\Scripts\agentarium.exe doctor
$DoctorExitCode = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorActionPreference
# $LASTEXITCODE now holds doctor's own code (already captured above) --
# reset it explicitly. Nothing after this point runs another native exe,
# but leaving it non-zero would otherwise leak out as this whole script's
# own exit code once it falls off the end, exactly the "doctor FAIL must
# never look like setup.ps1 failed" guarantee this block exists for.
$global:LASTEXITCODE = 0

if ($DoctorExitCode -ne 0) {
    Write-Warning "agentarium doctor encontró problemas (código $DoctorExitCode). Ver docs\guides\windows-setup.md."
}

Write-Host "Agentarium está listo. Ejecuta .\dev.ps1"
exit 0
