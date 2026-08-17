@echo off
rem Launch Dictate with no console window -- just the app, same as the
rem installed .exe. Errors still show a readable window with a pause; a
rem normal successful launch hands off to pythonw.exe and closes this
rem window immediately. Use run-dictate-debug.bat instead when you want to
rem see [dictate] status lines and errors live in a console.
cd /d "%~dp0"

set "STEP=the .venv folder"
if not exist ".venv\" goto :noenv
set "STEP=.venv\Scripts\pythonw.exe"
if not exist ".venv\Scripts\pythonw.exe" goto :noenv
set "STEP=.venv\Lib\site-packages"
if not exist ".venv\Lib\site-packages\" goto :noenv
set "STEP=main.py"
if not exist "main.py" goto :noenv
set "STEP=.venv\pyvenv.cfg"
if not exist ".venv\pyvenv.cfg" goto :noenv

rem Prefer the venv's own launcher when it works.
".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
if errorlevel 1 goto :fallback
start "" /B ".venv\Scripts\pythonw.exe" main.py
exit /b 0

:fallback
rem uv's launcher shim sometimes fails naming an interpreter that is present and
rem working. Skip it: run that interpreter's windowless twin directly with the
rem venv's packages on PYTHONPATH. Same result, nothing in between to break.
set "PYHOME="
for /f "tokens=1,* delims== " %%a in ('findstr /b "home" ".venv\pyvenv.cfg" 2^>nul') do set "PYHOME=%%b"
set "STEP=the 'home' line in .venv\pyvenv.cfg"
if not defined PYHOME goto :noenv
set "STEP=%PYHOME%\pythonw.exe"
if not exist "%PYHOME%\pythonw.exe" goto :noenv
set "PYTHONPATH=%~dp0.venv\Lib\site-packages"
start "" /B "%PYHOME%\pythonw.exe" main.py
exit /b 0

:noenv
echo.
echo   Cannot start. Missing: %STEP%
echo.
echo   Looked in this folder:
echo     %~dp0
echo.
echo   Set up the environment there:
echo     uv venv --python 3.12 .venv
echo     uv pip install --python .venv\Scripts\python.exe -r pyproject.toml
echo.
pause
exit /b 1
