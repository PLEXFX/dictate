@echo off
rem Runs the built Dictate UI on made-up engine data (the engine is not connected yet).
rem Settings are read from the real settings.json but saved only to %LOCALAPPDATA%\Dictate UI\demo-settings.json.
set DICTATEUI_DEMO=1
start "" "%~dp0bin\x64\Release\net10.0-windows10.0.19041.0\win-x64\DictateUI.exe"
