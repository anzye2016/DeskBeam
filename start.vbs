' DeskBeam launcher (UAC elevation + hidden window)
' Kills old instance before starting
Dim fso, scriptDir, shell
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' Kill old DeskBeam processes in this directory (pythonw.exe or python.exe)
Dim pattern : pattern = Replace(scriptDir, "\", "\\")
shell.Run "powershell -NoProfile -Command ""Get-WmiObject Win32_Process -Filter """"name='pythonw.exe' and commandline like '%" & pattern & "%'"""" | ForEach-Object { $_.Terminate() | Out-Null }""", 0, False
shell.Run "powershell -NoProfile -Command ""Get-WmiObject Win32_Process -Filter """"name='python.exe' and commandline like '%" & pattern & "%'"""" | ForEach-Object { $_.Terminate() | Out-Null }""", 0, False

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
