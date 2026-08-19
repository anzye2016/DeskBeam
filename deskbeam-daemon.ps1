$ErrorActionPreference = 'Stop'

# Resolve python: prefer the project venv (created by start.bat with
# requirements.txt), then PATH python, then common install paths.
$exe = $null
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $exe = $venvPy
} else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $exe = $cmd.Source
    } else {
        $candidates = @(
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "$env:ProgramFiles\Python312\python.exe",
            "$env:ProgramFiles\Python\Python\312\python.exe"
        )
        $exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
}
if (-not $exe) {
    throw "Python not found. Install Python or create .venv first."
}

# Read the listen port from config.json (fallback 8769).
$port = 8769
try {
    $cfg = Get-Content (Join-Path $PSScriptRoot "config.json") -Raw | ConvertFrom-Json
    if ($cfg.port) { $port = [int]$cfg.port }
} catch {}

function Stop-PortOwners {
    # Same as start.bat: free the listen port before each start.  A leftover
    # instance would otherwise make the new server fail to bind (Errno 10048)
    # and wedge the daemon into an endless crash/restart loop.
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Get-NetUDPEndpoint -LocalPort $port -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
}

$exeArgs = @("$PSScriptRoot\server.py")
$projName = [System.IO.Path]::GetFileName($PSScriptRoot.TrimEnd('\'))
$pidFile = "$env:TEMP\deskbeam-pid-$projName.txt"

Set-Location -LiteralPath $PSScriptRoot

while ($true) {
    if (Test-Path $pidFile) {
        $oldPid = Get-Content $pidFile -Raw
        if ($oldPid -match '^\d+$') {
            taskkill /PID $oldPid /F 2>$null
            Start-Sleep -Seconds 2
        }
    }
    Stop-PortOwners

    Write-Output "[deskbeam] Starting DeskBeam..."
    $started = Get-Date
    $p = Start-Process -FilePath $exe -ArgumentList $exeArgs -WindowStyle Hidden -PassThru
    $p.Id | Set-Content $pidFile
    Write-Output "[deskbeam] DeskBeam started (PID $($p.Id))"

    $p.WaitForExit()
    $code = $p.ExitCode
    $lived = (Get-Date) - $started
    Write-Output "[deskbeam] DeskBeam exited (code $code, lived $([int]$lived.TotalSeconds)s)"

    if ($code -eq 0) {
        Write-Output "[deskbeam] Exited normally, restarting in 5s..."
        Start-Sleep -Seconds 5
    } elseif ($lived.TotalSeconds -lt 5) {
        # Immediate crash: likely a port/config problem. Back off so the
        # watchdog does not spin a tight loop and spam the log.
        Write-Output "[deskbeam] Crashed immediately, restarting in 10s..."
        Start-Sleep -Seconds 10
    } else {
        Write-Output "[deskbeam] Crashed, restarting in 2s..."
        Start-Sleep -Seconds 2
    }
}
