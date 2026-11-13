@echo off
rem Same as run-dictate-debug.bat, but Settings opens in the new WinUI window (ui\DictateUI.exe).
rem Build the window first with ui\Build.bat. Quit the installed Dictate from its tray icon before
rem running this, or this copy exits at once because one is already running.
set DICTATE_WINUI=1
call "%~dp0run-dictate-debug.bat"
