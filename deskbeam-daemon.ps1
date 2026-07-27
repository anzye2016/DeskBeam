$ErrorActionPreference = 'Stop'
$exe = "$PSScriptRoot\DeskBeam.exe"
$pidFile = "$env:TEMP\deskbeam-pid.txt"

Set-Location -LiteralPath $PSScriptRoot

while ($true) {
    if (Test-Path $pidFile) {
        $oldPid = Get-Content $pidFile -Raw
        if ($oldPid -match '^\d+$') {
            taskkill /PID $oldPid /F 2>$null
            Start-Sleep -Seconds 2
        }
    }

    Write-Output "[deskbeam] Starting DeskBeam..."
    $p = Start-Process -FilePath $exe -WindowStyle Hidden -PassThru
    $p.Id | Set-Content $pidFile
    Write-Output "[deskbeam] DeskBeam started (PID $($p.Id))"

    $p.WaitForExit()
    $code = $p.ExitCode
    Write-Output "[deskbeam] DeskBeam exited (code $code)"

    if ($code -eq 0) {
        Write-Output "[deskbeam] Exited normally, restarting in 5s..."
        Start-Sleep -Seconds 5
    } else {
        Write-Output "[deskbeam] Crashed, restarting in 2s..."
        Start-Sleep -Seconds 2
    }
}
