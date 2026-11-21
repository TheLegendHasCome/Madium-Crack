@echo off
cd /d "%~dp0"
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v MadiumCrack /f
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*madium_crack.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Startup entry and watcher removed.
echo To restore the original binary, copy:
echo   C:\Users\temel\AppData\Local\Madium\Bin\Madium.exe.original.bak
echo over Madium.exe in the same folder.
pause
