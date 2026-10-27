Option Explicit
' Silent Windows Task Scheduler wrapper for KEIBA AI.
' Keeps the same batch runner, exit status, polling and JV-Link behavior.
' Window style 0 hides the transient cmd.exe window (not the task itself).
Dim fso, shell, folder, runner, command, exitCode
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
runner = fso.BuildPath(folder, "run_scheduled_task.bat")
If Not fso.FileExists(runner) Then
    WScript.Quit 1
End If
Set shell = CreateObject("WScript.Shell")
command = "cmd.exe /d /c " & Chr(34) & Chr(34) & runner & Chr(34) & Chr(34)
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
