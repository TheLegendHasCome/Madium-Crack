@echo off
cd /d "%~dp0"
python "%~dp0madium_crack.py" install
echo.
echo Done. Madium is patched, the auth server is running, and it restarts at logon.
pause
