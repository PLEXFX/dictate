@echo off
rem Stages the Dictate UI program for the installer: a self-contained copy (the .NET runtime and Windows App SDK ride along,
rem so the PC needs nothing installed) in ui\publish\. installer\dictate.iss picks it up from there.
rem Run it before building the installer, with the same version as version.py (see the release rule in CLAUDE.md).
cd /d "%~dp0"
if exist publish rmdir /s /q publish
dotnet publish DictateUI.csproj -c Release -p:Platform=x64 -o "%~dp0publish" -nologo -v minimal
if errorlevel 1 (
  echo.
  echo Publish failed. Scroll up for the first error.
  pause
  exit /b 1
)
if not exist "%~dp0publish\DictateUI.exe" (
  echo.
  echo Publish finished but publish\DictateUI.exe is missing.
  pause
  exit /b 1
)
echo.
echo Staged in %~dp0publish
