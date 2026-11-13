# Runs the real engine (an isolated copy, see e2e_engine.py) together with the WinUI program, and checks the whole chain:
# Settings through the pipe, the activity bar driven by the engine, the click on the bar, the tray, a crash and restart of
# the program, and the hand-over to the built-in window when it keeps crashing. The installed Dictate is never touched.
# Usage:  .\e2e.ps1               the full run
#         .\e2e.ps1 -Case crash   kills the program until the engine gives up and falls back
param([string]$Case = "main")
$tools = Split-Path -Parent $MyInvocation.MyCommand.Path
$proj = Split-Path -Parent (Split-Path -Parent $tools)
$py = "$proj\.venv\Scripts\python.exe"
$base = "$tools\e2e-appdata"
$outFile = "$tools\e2e-out.txt"; $errFile = "$tools\e2e-err.txt"; $cmdFile = "$base\e2e-cmd.txt"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$runBefore = (Get-ItemProperty $runKey -Name Dictate -ErrorAction SilentlyContinue).Dictate
$realBefore = (Get-Process dictate -ErrorAction SilentlyContinue | Select-Object -First 1).Id
"real Dictate pid before: $realBefore"

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class E {
  [DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr h, uint msg, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, int dx, int dy, uint data, UIntPtr extra);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
}
"@
[void][E]::SetProcessDPIAware()

Remove-Item $base, $outFile, $errFile -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $base | Out-Null
$env:PYTHONUNBUFFERED = "1"
$engine = Start-Process $py -ArgumentList "`"$tools\e2e_engine.py`"", "`"$base`"" -WorkingDirectory $proj -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $outFile -RedirectStandardError $errFile
"engine started: pid $($engine.Id)"

function Ui { Get-Process DictateUI -ErrorAction SilentlyContinue }
function Drive([string[]]$steps) { try { & "$tools\drive.ps1" -Dir $tools -Steps $steps } catch { "  (drive step failed: $($_.Exception.Message))" } }
function Settings { Get-Content "$base\dictate\settings.json" -Raw | ConvertFrom-Json }
function Cmd([string]$c, [int]$wait = 800) { Add-Content -Path $cmdFile -Value $c; Start-Sleep -Milliseconds $wait }
function Log { (Get-Content $outFile -ErrorAction SilentlyContinue) + (Get-Content $errFile -ErrorAction SilentlyContinue) }
function Log-Has([string]$text) { [bool](Log | Where-Object { $_ -like "*$text*" }) }
function Show-Log { "--- engine log ---"; Log | ForEach-Object { "  " + $_ } }
function Root { [System.Windows.Automation.AutomationElement]::RootElement }
function By-Name([string]$name, $scope) { (Root).FindFirst($scope, (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $name))) }
function Settings-Open { [bool](By-Name "Dictate settings" ([System.Windows.Automation.TreeScope]::Children)) }
function Tray-Hwnd {
    $log = "$base\ui\debug.log"
    if (-not (Test-Path $log)) { return [IntPtr]::Zero }
    $line = Get-Content $log | Where-Object { $_ -like "*tray window*" } | Select-Object -Last 1
    if (-not $line) { return [IntPtr]::Zero }
    [IntPtr][int64]($line -replace '.*tray window ', '')
}
function Tray-Message([uint32]$mouse) {
    $w = Tray-Hwnd
    if ($w -eq [IntPtr]::Zero -or -not [E]::IsWindow($w)) { return $false }
    # the message Windows sends when the tray icon is clicked: WM_APP+1 with the mouse message in lParam
    [E]::PostMessageW($w, 0x8001, [IntPtr]1, [IntPtr][int]$mouse)
}
function Shot-Bar([string]$name) { & "$tools\shotbar.ps1" -Dir $tools -At 0 -Prefix $name | Out-Null }
# Only inside the popup menu itself (window class #32768), never anywhere else on the desktop: a "Quit" in another app's
# menu must not be mistaken for ours.
function Menu-Item([string]$name) {
    $menu = (Root).FindFirst([System.Windows.Automation.TreeScope]::Children,
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, "#32768")))
    if (-not $menu) { return $null }
    $menu.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $name)))
}
function Press-Menu([string]$name) {
    $e = Menu-Item $name
    if (-not $e) { return "menu item '$name' not found" }
    $ip = $null
    $owner = (Get-Process -Id $e.Current.ProcessId -ErrorAction SilentlyContinue).ProcessName   # whose menu this really is
    if ($owner -ne "DictateUI") { return "menu item '$name' belongs to $owner, not DictateUI: left alone" }
    if ($e.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$ip)) { $ip.Invoke(); return "chose '$name' (menu owned by $owner)" }
    "menu item '$name' cannot be invoked"
}
function Click-Bar {
    $bar = By-Name "Dictate activity bar" ([System.Windows.Automation.TreeScope]::Children)
    if (-not $bar) { return "bar window not found" }
    $r = $bar.Current.BoundingRectangle
    $x = [int]($r.X + $r.Width / 2); $y = [int]($r.Y + $r.Height / 2)
    $sw = [System.Windows.Forms.SystemInformation]::VirtualScreen
    [void][E]::SetCursorPos($x, $y)
    $ax = [int](($x - $sw.X) * 65535 / $sw.Width); $ay = [int](($y - $sw.Y) * 65535 / $sw.Height)
    [E]::mouse_event(0xC001, $ax, $ay, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 150
    [E]::mouse_event(0xC002, $ax, $ay, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 60
    [E]::mouse_event(0xC004, $ax, $ay, 0, [UIntPtr]::Zero)
    "clicked at $x,$y"
}

try {
    # wait for the program to come up and connect
    for ($i = 0; $i -lt 40 -and -not (Log-Has "E2E READY"); $i++) { Start-Sleep -Milliseconds 500 }
    Start-Sleep -Seconds 3

    if ($Case -eq "quit" -or $Case -eq "restartquit") {
        # Quit from the tray menu, with or without the program having crashed and been restarted first
        if ($Case -eq "restartquit") {
            Get-Process DictateUI -ErrorAction SilentlyContinue | Stop-Process -Force
            Start-Sleep -Seconds 9
            "restarted: $(@(Ui).Count -eq 1)   tray window: $(Tray-Hwnd)"
        }
        [void][E]::SetCursorPos(1200, 600)
        "tray right click sent: " + (Tray-Message 0x0205)
        Start-Sleep -Milliseconds 1200
        Press-Menu "Quit"
        for ($i = 0; $i -lt 12 -and -not $engine.HasExited; $i++) { Start-Sleep -Milliseconds 500 }
        "after Quit from the tray: engine exited=$($engine.HasExited)  program running=$(@(Ui).Count -gt 0)"
        return
    }

    if ($Case -eq "crash") {
        "program up: " + [bool](Ui)
        for ($i = 1; $i -le 4; $i++) {
            Get-Process DictateUI -ErrorAction SilentlyContinue | Stop-Process -Force
            "killed the program ($i); waiting"
            Start-Sleep -Seconds 7
            "  running again: " + [bool](Ui)
        }
        Cmd "ping" 1000
        "engine alive: $(-not $engine.HasExited)   answered ping: $(Log-Has 'PONG')"
        "log says it gave up: $(Log-Has 'using the built-in window')"
        Cmd "settings" 3500
        $w = By-Name "Dictate settings" ([System.Windows.Automation.TreeScope]::Children)
        if ($w) { [uint32]$owner = 0; [void][E]::GetWindowThreadProcessId([IntPtr]$w.Current.NativeWindowHandle, [ref]$owner); "Settings window belongs to pid $owner (the engine is $($engine.Id))" } else { "no Settings window" }
        "DictateUI processes now: " + @(Ui).Count
        return
    }

    "DictateUI processes: " + @(Ui).Count + "   Settings open: " + (Settings-Open) + "   tray window: " + (Tray-Hwnd)
    if (-not (Ui)) { throw "the program did not start" }
    Drive @("shot:e1-settings")
    $s = Settings; "initial ptt_key=$($s.ptt_key) sound_cues=$($s.sound_cues) theme=$($s.theme_mode)"

    # 1. Settings through the pipe
    Drive @("click:Recording sounds")
    Start-Sleep -Milliseconds 1200
    $s = Settings; "toggle reached the engine: sound_cues=$($s.sound_cues)  (expected False)"
    Drive @("click:Appearance","menu:Color mode@combo>Light","shot:e2-light")
    Start-Sleep -Milliseconds 1200
    $s = Settings; "theme reached the engine: theme_mode=$($s.theme_mode)  (expected light)"

    # 2. closing Settings keeps the program (tray and bar) alive; the tray brings Settings back
    Drive @("click:Close")
    Start-Sleep -Seconds 2
    "after closing Settings: program running=$(@(Ui).Count -eq 1) Settings open=$(Settings-Open) engine alive=$(-not $engine.HasExited)"
    "tray left click sent: " + (Tray-Message 0x0202)
    Start-Sleep -Seconds 3
    "after tray click: Settings open=$(Settings-Open)"
    Drive @("click:Close")
    Start-Sleep -Seconds 1

    # 3. the activity bar, driven by the engine through the real RemoteBar and the pipe
    foreach ($state in @("listening", "transcribing", "error", "progress")) {
        Cmd "bar $state" 1300
        Shot-Bar "e3-bar-$state"
    }
    Cmd "bar done" 300
    Shot-Bar "e3-bar-done"
    Cmd "bar lock" 1300
    Shot-Bar "e3-bar-lock"
    "click on the locked bar: " + (Click-Bar)
    Start-Sleep -Milliseconds 800
    "engine saw the bar click: $(Log-Has 'BAR CLICKED')"
    Cmd "bar idle" 1500
    Cmd "notify" 1300
    Shot-Bar "e3-bar-notice"
    "click on the notice: " + (Click-Bar)
    Start-Sleep -Milliseconds 800
    "engine ran the notice's action: $(Log-Has 'NOTICE CLICKED')"
    Cmd "bar idle" 800

    # 4. the tray menu: its state, then one real entry
    [void][E]::SetCursorPos(1200, 600)
    "tray right click sent: " + (Tray-Message 0x0205)
    Start-Sleep -Milliseconds 1200
    foreach ($item in @("Settings", "Load model now", "Unload model", "Copy last dictation", "Undo last dictation", "Check for updates", "Quit")) {
        $e = Menu-Item $item
        if ($e) { "  menu item '{0}' enabled={1}" -f $item, $e.Current.IsEnabled } else { "  menu item '$item' not found" }
    }
    # the open menu, as it looks on screen (it appears at the cursor, bottom-aligned)
    $menuShot = New-Object System.Drawing.Bitmap(700, 330)
    $g = [System.Drawing.Graphics]::FromImage($menuShot)
    $g.CopyFromScreen(900, 290, 0, 0, (New-Object System.Drawing.Size(700, 330)))
    $g.Dispose()
    $menuShot.Save("$tools\e4-tray-menu.png")
    $menuShot.Dispose()
    Press-Menu "Check for updates"
    Start-Sleep -Seconds 2
    Shot-Bar "e4-after-check"; Start-Sleep -Seconds 4
    "the engine ran the update check the tray asked for: $(Log-Has 'update')"
    Cmd "balloon" 1500
    "program still running after a balloon: $(@(Ui).Count -eq 1)"

    # 5. the program crashing: the engine starts it again
    Get-Process DictateUI -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 8
    "after killing the program: running again=$(@(Ui).Count -eq 1)  engine logged the restart: $(Log-Has 'starting it again')"
    Cmd "bar error" 1500
    Shot-Bar "e5-bar-after-restart"

    # 6. the engine quitting takes the program, its tray icon and its bar down with it.
    #    (Quit from the tray menu itself is checked by -Case quit and -Case restartquit. This run posts a made-up tray
    #    message, so a program restarted a moment ago may lack the foreground right a real tray click gives it, and its
    #    menu then ignores the test's automation click.)
    Cmd "quit" 500
    $t = [Diagnostics.Stopwatch]::StartNew()
    while ($t.Elapsed.TotalSeconds -lt 15 -and -not $engine.HasExited) { Start-Sleep -Milliseconds 250 }
    "quit took $([int]$t.Elapsed.TotalSeconds) s"
    Start-Sleep -Seconds 1
    "after the engine's quit: engine exited=$($engine.HasExited)  program running=$(@(Ui).Count -gt 0)"
}
finally {
    Show-Log
    if (-not $engine.HasExited) { $engine.Kill() }
    Get-Process DictateUI -ErrorAction SilentlyContinue | Stop-Process -Force
    $realAfter = (Get-Process dictate -ErrorAction SilentlyContinue | Select-Object -First 1).Id
    $runAfter = (Get-ItemProperty $runKey -Name Dictate -ErrorAction SilentlyContinue).Dictate
    "real Dictate pid after: $realAfter  (same: $($realAfter -eq $realBefore))"
    "autostart entry unchanged: $($runAfter -eq $runBefore)"
    foreach ($f in @("$base\ui\crash.log")) { if (Test-Path $f) { "--- UI crash.log ---"; Get-Content $f -Tail 20 } }
}
