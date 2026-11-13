@echo off
rem Builds the Dictate UI (needs the .NET 10 SDK). Output: bin\x64\Release\net10.0-windows10.0.19041.0\win-x64\DictateUI.exe
cd /d "%~dp0"
dotnet build DictateUI.csproj -c Release -p:Platform=x64 -nologo -v minimal
if errorlevel 1 (
  echo.
  echo Build failed. Scroll up for the first error.
  pause
)
