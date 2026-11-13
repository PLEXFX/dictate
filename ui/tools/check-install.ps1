# Checks an installed copy of Dictate (or a scratch install of a test installer): the program is in {app}\ui, nothing
# unwanted came with it, the engine's Qt pieces for the pipe are bundled, and the installed program starts on its own.
# It never starts dictate.exe (the real Dictate holds the single-instance lock and a second copy would signal it).
# Usage: .\check-install.ps1 -AppDir "C:\path\to\installed\Dictate"
param([Parameter(Mandatory = $true)][string]$AppDir)
$ok = $true
function Check([string]$what, [bool]$pass, [string]$detail = "") {
    $script:ok = $script:ok -and $pass
    "{0}  {1}{2}" -f $(if ($pass) { "ok  " } else { "FAIL" }), $what, $(if ($detail) { "  ($detail)" } else { "" })
}

Check "dictate.exe is there" (Test-Path "$AppDir\dictate.exe")
Check "the updater splash is there" (Test-Path "$AppDir\updater\dictate-updater.exe")
Check "the WinUI program is there" (Test-Path "$AppDir\ui\DictateUI.exe")
Check "its icon files are beside it (the tray icon and title bar read them)" ((Test-Path "$AppDir\ui\Assets\dictate.ico") -and (Test-Path "$AppDir\ui\Assets\dictate-32.png"))
Check "Windows App SDK and .NET ride along (self-contained)" ((Test-Path "$AppDir\ui\Microsoft.ui.xaml.dll") -and (Test-Path "$AppDir\ui\System.Private.CoreLib.dll"))
$pdb = @(Get-ChildItem "$AppDir\ui" -Recurse -Filter *.pdb -ErrorAction SilentlyContinue)
Check "no debug symbols were installed" ($pdb.Count -eq 0) "$($pdb.Count) .pdb files"
Check "the engine can open its pipe (QtNetwork is bundled)" ((Test-Path "$AppDir\_internal\PySide6\QtNetwork.pyd") -and (Test-Path "$AppDir\_internal\PySide6\Qt6Network.dll"))
$mb = (Get-ChildItem "$AppDir\ui" -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
"info  ui folder: {0:N0} MB, {1} files" -f $mb, @(Get-ChildItem "$AppDir\ui" -Recurse -File).Count

# the installed program on its own, on made-up data, in a scratch data folder
Get-Process DictateUI -ErrorAction SilentlyContinue | Stop-Process -Force
$home2 = Join-Path $env:TEMP "dictate-install-check"
Remove-Item $home2 -Recurse -Force -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force $home2 | Out-Null
$env:DICTATEUI_DEMO = "1"; $env:DICTATEUI_HOME = $home2; $env:DICTATEUI_DEBUG = "1"
$p = Start-Process "$AppDir\ui\DictateUI.exe" -PassThru
Start-Sleep -Seconds 5
Check "the installed program starts and stays up" (-not $p.HasExited)
$log = Get-Content "$home2\debug.log" -ErrorAction SilentlyContinue
Check "its tray icon was added" ([bool]($log | Where-Object { $_ -like "*tray icon added=True*" }))
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$w = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children,
    (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, "Dictate settings")))
Check "its Settings window opens" ([bool]$w)
Check "no crash log" (-not (Test-Path "$home2\crash.log"))
Get-Process DictateUI -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item Env:\DICTATEUI_DEMO, Env:\DICTATEUI_HOME, Env:\DICTATEUI_DEBUG -ErrorAction SilentlyContinue
""
if ($ok) { "ALL CHECKS PASSED" } else { "SOME CHECKS FAILED" }
