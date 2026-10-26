@echo off
setlocal
title CDJ Tool - install
set "DEST=%LOCALAPPDATA%\Programs\CDJ-Tool"
set "SRC=%~dp0app"
if not exist "%SRC%\CDJ-Tool.vbs" (
  echo Unzip the whole archive first, then run Install.bat from the unzipped folder.
  pause & exit /b 1
)
echo Installing CDJ Tool to %DEST% ...
if exist "%DEST%" rmdir /s /q "%DEST%"
xcopy "%SRC%" "%DEST%\" /e /i /q /y >nul
if errorlevel 1 ( echo Copy failed. & pause & exit /b 1 )
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$s=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){ $l=$s.CreateShortcut((Join-Path $d 'CDJ Tool.lnk')); $l.TargetPath='%DEST%\CDJ-Tool.vbs'; $l.WorkingDirectory='%DEST%'; $l.IconLocation='%DEST%\CDJ-Tool.ico'; $l.Save() }"
echo.
echo Done. Start CDJ Tool from the Desktop shortcut or the Start menu.
pause
