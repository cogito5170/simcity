@echo off
rem Double-click to inspect the SimCity 3000 install (read-only). No "cd" needed.
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
%PY% -m mayor install %*
echo.
pause
