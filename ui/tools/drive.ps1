# Drives the running DictateUI through UI Automation and takes screenshots.
# Usage: drive.ps1 -Steps "click:Updates","shot:updates","key:..."
param([string[]]$Steps, [string]$Dir)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms
$shot = Join-Path $PSScriptRoot "shot.ps1"
# The Settings window by its title: the bar is a second top-level window of the same program, and may be on screen.
$settingsCond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, "Dictate settings")
$root = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $settingsCond)
if (-not $root) { throw "The Dictate settings window is not open" }
$settingsHwnd = [IntPtr]$root.Current.NativeWindowHandle

function Find($name, $type) {
    $c = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $name)
    if ($type) {
        $t = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, $type)
        $c = New-Object System.Windows.Automation.AndCondition($c, $t)
    }
    # several elements can share a name (a row's title text and its switch): prefer one that can be pressed
    foreach ($scope in @($root, [System.Windows.Automation.AutomationElement]::RootElement)) {
        # flyouts live in their own popup window, outside the main window's tree, hence the second scope
        $all = $scope.FindAll([System.Windows.Automation.TreeScope]::Descendants, $c)
        foreach ($e in $all) { if (CanPress $e) { return $e } }
        if ($all.Count -gt 0 -and -not $type) { $first = $all[0] }
    }
    return $first
}
function CanPress($el) {
    foreach ($p in @([System.Windows.Automation.SelectionItemPattern]::Pattern, [System.Windows.Automation.InvokePattern]::Pattern,
                     [System.Windows.Automation.TogglePattern]::Pattern, [System.Windows.Automation.ExpandCollapsePattern]::Pattern)) {
        $o = $null
        if ($el.TryGetCurrentPattern($p, [ref]$o)) { return $true }
    }
    return $false
}
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class WS {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f);
}
"@
function Press($el) {
    $sel = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$sel)) { $sel.Select(); return }
    $inv = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$inv)) { $inv.Invoke(); return }
    $tog = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$tog)) { $tog.Toggle(); return }
    $exp = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$exp)) { $exp.Expand(); return }
    throw "cannot press '$($el.Current.Name)'"
}

foreach ($s in $Steps) {
    $kind, $arg = $s.Split(":", 2)
    switch ($kind) {
        "click" { $e = Find $arg $null; if (-not $e) { throw "not found: $arg" }; Press $e; Start-Sleep -Milliseconds 500 }
        "shot"  { & $shot -Out (Join-Path $Dir "$arg.png") | Out-Null }
        "wait"  { Start-Sleep -Milliseconds ([int]$arg) }
        "keys"  { [System.Windows.Forms.SendKeys]::SendWait($arg); Start-Sleep -Milliseconds 500 }
        "size"  { $w, $h = $arg.Split(","); [void][WS]::SetWindowPos($settingsHwnd, [IntPtr]::Zero, 40, 20, [int]$w, [int]$h, 0x0004); Start-Sleep -Milliseconds 700 }
        "menu"  {
            $btn, $item = $arg.Split(">")
            $ct = $null
            if ($btn.EndsWith("@combo")) { $btn = $btn.Substring(0, $btn.Length - 6); $ct = [System.Windows.Automation.ControlType]::ComboBox }
            $b = Find $btn $ct; if (-not $b) { throw "not found: $btn" }
            Press $b; Start-Sleep -Milliseconds 500
            $i = Find $item $null; if (-not $i) { throw "menu item not found: $item" }
            Press $i; Start-Sleep -Milliseconds 600
        }
        "scroll" {
            # scroll the content ScrollViewer by percent
            $sv = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, "ScrollViewer")))
            foreach ($v in $sv) {
                $p = $null
                if ($v.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$p) -and $p.Current.VerticallyScrollable) {
                    $p.SetScrollPercent(-1, [double]$arg); break
                }
            }
            Start-Sleep -Milliseconds 400
        }
        default { throw "unknown step $s" }
    }
}
