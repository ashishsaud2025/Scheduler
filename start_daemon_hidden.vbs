' Starts the Command Scheduler background service with no console window.
' Put a shortcut to this file in shell:startup, or just double-click it.
' VBScript comments start with an apostrophe. The previous version used "#",
' which is a syntax error, so this file never ran at all.
Option Explicit

Dim ws, fso, appDir, pythonw, candidates, i
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)

candidates = Array( _
  ws.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python313\pythonw.exe"), _
  ws.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python312\pythonw.exe"), _
  ws.ExpandEnvironmentStrings("%LocalAppData%\Programs\Python\Python311\pythonw.exe"), _
  "C:\Python313\pythonw.exe", _
  "C:\Python312\pythonw.exe", _
  "C:\Python311\pythonw.exe")

pythonw = ""
For i = 0 To UBound(candidates)
  If pythonw = "" Then
    If fso.FileExists(candidates(i)) Then pythonw = candidates(i)
  End If
Next

If pythonw = "" Then pythonw = "pythonw.exe"   ' fall back to PATH

ws.Run """" & pythonw & """ """ & appDir & "\daemon.py""", 0, False
