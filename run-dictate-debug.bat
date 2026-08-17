@echo off
rem Same app, but keeps a console window so errors and status are visible, and
rem reports exactly which step failed if it cannot start.
cd /d "%~dp0"

echo === Dictate ===
echo folder : %~dp0
echo.

set "STEP=the .venv folder"
if not exist ".venv\" goto :noenv
set "STEP=.venv\Scripts\python.exe"
if not exist ".venv\Scripts\python.exe" goto :noenv
set "STEP=.venv\Lib\site-packages"
if not exist ".venv\Lib\site-packages\" goto :noenv
set "STEP=main.py"
if not exist "main.py" goto :noenv
set "STEP=.venv\pyvenv.cfg"
if not exist ".venv\pyvenv.cfg" goto :noenv

echo [1/2] project files present
".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
if errorlevel 1 goto :fallback

echo [2/2] venv launcher works - starting
echo.
".venv\Scripts\python.exe" main.py
goto :done

:fallback
rem uv's launcher shim sometimes fails naming an interpreter that is present and
rem working. Skip it: run that interpreter directly with the venv's packages on
rem PYTHONPATH.
echo [2/2] venv launcher FAILED - falling back to the base interpreter
set "PYHOME="
for /f "tokens=1,* delims== " %%a in ('findstr /b "home" ".venv\pyvenv.cfg" 2^>nul') do set "PYHOME=%%b"
set "STEP=the 'home' line in .venv\pyvenv.cfg"
if not defined PYHOME goto :noenv
echo       base interpreter: %PYHOME%
set "STEP=%PYHOME%\python.exe"
if not exist "%PYHOME%\python.exe" goto :noenv
set "PYTHONPATH=%~dp0.venv\Lib\site-packages"
echo.
"%PYHOME%\python.exe" main.py

:done
echo.
pause
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
