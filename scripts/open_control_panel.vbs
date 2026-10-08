Option Explicit
Dim shell, fso, root, py, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
py = shell.ExpandEnvironmentStrings("%LIVE_PYTHON%")
If py = "%LIVE_PYTHON%" Then py = "pythonw.exe"
command = """" & py & """ -B """ & root & "\scripts\control_panel.py"" --open"
shell.CurrentDirectory = root
shell.Run command, 0, False
