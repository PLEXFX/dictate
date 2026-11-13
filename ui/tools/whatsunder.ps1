# Prints which window Windows would give a click at the bar's centre, repeatedly, so it can be compared with the bar's state.
# Usage: whatsunder.ps1 -From 20 -To 32      (seconds after this script starts)
param([double]$From = 0, [double]$To = 5)
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public static class U {
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);
}
"@
[void][U]::SetProcessDPIAware()
$area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$p = New-Object U+POINT; $p.X = [int]($area.Left + $area.Width / 2); $p.Y = [int]($area.Bottom - 8 - 18)
$clock = [Diagnostics.Stopwatch]::StartNew()
while ($clock.Elapsed.TotalSeconds -lt $From) { Start-Sleep -Milliseconds 50 }
$last = ""
while ($clock.Elapsed.TotalSeconds -lt $To) {
    $h = [U]::WindowFromPoint($p)
    $cls = New-Object System.Text.StringBuilder 256; [void][U]::GetClassNameW($h, $cls, 256)
    $top = [U]::GetAncestor($h, 2)
    $title = New-Object System.Text.StringBuilder 256; [void][U]::GetWindowTextW($top, $title, 256)
    [uint32]$pid2 = 0; [void][U]::GetWindowThreadProcessId($h, [ref]$pid2)
    $line = "{0} (title '{1}', pid {2})" -f $cls.ToString(), $title.ToString(), $pid2
    if ($line -ne $last) { "{0,5:N1}s  under the cursor point ({1},{2}): {3}" -f $clock.Elapsed.TotalSeconds, $p.X, $p.Y, $line; $last = $line }
    Start-Sleep -Milliseconds 100
}
