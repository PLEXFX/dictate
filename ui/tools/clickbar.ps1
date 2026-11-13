# One real mouse click at the activity bar's centre, at a given time. For checking the armed bar with nothing else on screen.
param([double]$At = 25.0, [switch]$Right)
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class K {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, int dx, int dy, uint data, UIntPtr extra);
}
"@
[void][K]::SetProcessDPIAware()
$area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$cx = [int]($area.Left + $area.Width / 2); $cy = [int]($area.Bottom - 8 - 18)
$clock = [Diagnostics.Stopwatch]::StartNew()
while ($clock.Elapsed.TotalSeconds -lt $At) { Start-Sleep -Milliseconds 20 }
$sw = [System.Windows.Forms.SystemInformation]::VirtualScreen
$ax = [int](($cx - $sw.X) * 65535 / $sw.Width); $ay = [int](($cy - $sw.Y) * 65535 / $sw.Height)
[void][K]::SetCursorPos($cx, $cy)
[K]::mouse_event(0xC001, $ax, $ay, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 200
[K]::mouse_event(0xC001, $ax + 1, $ay, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 100   # a small move so XAML sees the pointer arrive
[K]::mouse_event(0xC002, $ax, $ay, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 80
[K]::mouse_event(0xC004, $ax, $ay, 0, [UIntPtr]::Zero)
"clicked at $cx,$cy at +$([int]$clock.Elapsed.TotalSeconds) s"
