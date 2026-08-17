Option Explicit

Dim shell, files, folder, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
command = Chr(34) & folder & "\run-dictate-startup.bat" & Chr(34)
shell.Run command, 0, False
