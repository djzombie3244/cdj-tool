@echo off
set "DEST=%LOCALAPPDATA%\Programs\CDJ-Tool"
if exist "%DEST%" rmdir /s /q "%DEST%"
if exist "%LOCALAPPDATA%\CDJ-Tool" rmdir /s /q "%LOCALAPPDATA%\CDJ-Tool"
powershell -NoProfile -Command "foreach($d in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){ Remove-Item (Join-Path $d 'CDJ Tool.lnk') -ErrorAction SilentlyContinue }"
echo CDJ Tool removed.
pause
