' CDJ Tool launcher (no console window). Lives next to the python\, site-packages\, ffmpeg\ and src\ folders.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
Set env = sh.Environment("Process")
env("CDJ_BUNDLED") = "1"
env("CDJ_PYTHON") = base & "\python\python.exe"
env("CDJ_SITE_PACKAGES") = base & "\site-packages"
env("CDJ_FFMPEG") = base & "\ffmpeg\ffmpeg.exe"
env("PYTHONUTF8") = "1"
sh.CurrentDirectory = base & "\src"
sh.Run """" & base & "\python\pythonw.exe"" """ & base & "\src\app.py""", 0, False
