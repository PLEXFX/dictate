@echo off
setlocal
set "DICTATE_ROOT=%~dp0"
"%DICTATE_ROOT%.venv\Scripts\python.exe" "%DICTATE_ROOT%release_notes.py" --copy > "%DICTATE_ROOT%release-notes.md"
if errorlevel 1 (
    del "%DICTATE_ROOT%release-notes.md" 2>nul
    echo.
    echo Release notes were not prepared. Read the message above, then try again.
    pause
    exit /b 1
)
echo Release notes for this version are copied to your clipboard.
echo A readable copy is opening now. Paste the copied text into the matching GitHub release.
start "" notepad.exe "%DICTATE_ROOT%release-notes.md"
pause
