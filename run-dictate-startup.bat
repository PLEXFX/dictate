@echo off
rem Hidden, non-interactive launcher used by the Windows startup entry.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" exit /b 1
if not exist ".venv\Lib\site-packages\" exit /b 1
if not exist ".venv\pyvenv.cfg" exit /b 1
if not exist "main.py" exit /b 1

".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
if errorlevel 1 goto :fallback
".venv\Scripts\python.exe" main.py
exit /b %errorlevel%

:fallback
set "DICTATE_PYHOME="
for /f "tokens=1,* delims== " %%a in ('findstr /b "home" ".venv\pyvenv.cfg" 2^>nul') do set "DICTATE_PYHOME=%%b"
if not defined DICTATE_PYHOME exit /b 1
if not exist "%DICTATE_PYHOME%\python.exe" exit /b 1
set "PYTHONPATH=%~dp0.venv\Lib\site-packages"
"%DICTATE_PYHOME%\python.exe" main.py
exit /b %errorlevel%
