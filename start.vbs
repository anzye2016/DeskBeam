' DeskBeam launcher (UAC elevation + hidden window)
' Kills old instance before starting
Dim fso, scriptDir, shell
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' Kill old instance by port (same as stop.bat)
shell.Run "powershell -NoProfile -Command ""$ErrorActionPreference='SilentlyContinue'; Get-NetTCPConnection -LocalPort 8769 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }""", 0, False

Dim pythonw : pythonw = scriptDir & "\.venv\Scripts\pythonw.exe"
Dim serverpy : serverpy = scriptDir & "\server.py"

If Not fso.FileExists(pythonw) Then
    MsgBox "pythonw.exe not found:" & vbCrLf & pythonw, 16, "DeskBeam Error"
    WScript.Quit 1
End If
If Not fso.FileExists(serverpy) Then
    MsgBox "server.py not found:" & vbCrLf & serverpy, 16, "DeskBeam Error"
    WScript.Quit 1
End If

Dim app : Set app = CreateObject("Shell.Application")
app.ShellExecute pythonw, Chr(34) & serverpy & Chr(34), scriptDir, "runas", 0
