' Login autostart: run the course assistant silently.
' Local service only (HOST in .env is 127.0.0.1), hidden window, no browser, no tunnel.
Option Explicit

Dim fso, sh, base, py, logFile, inner
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base

py = "C:\Users\fanxi\AppData\Local\Programs\Python\Python311\python.exe"
If Not fso.FileExists(py) Then
    py = "python.exe"
End If

If Not fso.FolderExists(base & "\.tools") Then
    fso.CreateFolder base & "\.tools"
End If
logFile = base & "\.tools\server-start.log"

' cmd /c ""python.exe" run.py ... > "log" 2>&1"  -- hidden window, output to file
inner = """" & py & """ -u run.py --no-browser > """ & logFile & """ 2>&1"
sh.Run "cmd /c """ & inner & """", 0, False
