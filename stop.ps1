[CmdletBinding()]
param()

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

foreach ($Name in @("api", "web")) {
    $PidFile = Join-Path $Root "runtime\$Name.pid"
    if (Test-Path -LiteralPath $PidFile) {
        $SavedPid = [int](Get-Content -LiteralPath $PidFile -Raw)
        $Process = Get-Process -Id $SavedPid -ErrorAction SilentlyContinue
        if ($Process) {
            Stop-Process -Id $SavedPid
            Write-Host "Proceso $Name detenido (PID $SavedPid)."
        }
        Remove-Item -LiteralPath $PidFile -Force
    }
}
