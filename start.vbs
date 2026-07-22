' DeskBeam launcher (UAC elevation + hidden window)
' Kills old instance before starting
Dim fso, scriptDir, shell
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' Kill old pythonw.exe running DeskBeam server.py in this directory
Dim pattern : pattern = Replace(scriptDir, "\", "\\")
shell.Run "powershell -NoProfile -Command ""Get-WmiObject Win32_Process -Filter """"name='pythonw.exe' and commandline like '%" & pattern & "%'"""" | ForEach-Object { $_.Terminate() | Out-Null }""", 0, True

Dim pythonw : pythonw = scriptDir & "\.venv\Scripts\pythonw.exe"
Dim app : Set app = CreateObject("Shell.Application")
app.ShellExecute pythonw, Chr(34) & scriptDir & "\server.py" & Chr(34), scriptDir, "runas", 0
