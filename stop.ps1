[CmdletBinding()]
param()

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

function Stop-ProcessTree([int]$RootPid) {
    & "$env:SystemRoot\System32\taskkill.exe" /PID $RootPid /T /F 2>$null
}

foreach ($Name in @("api", "web")) {
    $PidFile = Join-Path $Root "runtime\$Name.pid"
    if (Test-Path -LiteralPath $PidFile) {
        $SavedPid = [int](Get-Content -LiteralPath $PidFile -Raw)
        $Process = Get-Process -Id $SavedPid -ErrorAction SilentlyContinue
        if ($Process) {
            Stop-ProcessTree $SavedPid
            Write-Host "Proceso $Name detenido (PID $SavedPid)."
        }
        Remove-Item -LiteralPath $PidFile -Force
    }
}
