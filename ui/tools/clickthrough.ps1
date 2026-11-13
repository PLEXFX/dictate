# Checks the activity bar's click-through with a real mouse click: a probe window sits where the bar appears, the bar is
# shown over it by the demo tour, and a click at the bar's centre must reach the probe while the bar is not armed.
# Run with the demo running in tour mode:  DICTATEUI_DEMO=1 DICTATEUI_BARTOUR=1 DictateUI.exe   (the bar shows at 0.5 s)
param([double]$ClickAt = 3.0)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class C {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
  [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr h, System.Text.StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, System.Text.StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, int dx, int dy, uint data, UIntPtr extra);
}
"@
[void][C]::SetProcessDPIAware()
$area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$cx = [int]($area.Left + $area.Width / 2); $cy = [int]($area.Bottom - 8 - 18)   # the capsule's centre at the default 8 px gap
$script:hits = 0
$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = "None"; $form.StartPosition = "Manual"; $form.ShowInTaskbar = $false; $form.TopMost = $true
$form.Bounds = New-Object System.Drawing.Rectangle(($cx - 150), ($cy - 40), 300, 80)
$form.BackColor = [System.Drawing.Color]::DarkSlateGray
$form.Add_MouseDown({ $script:hits++ })
$form.Show()
$clock = [Diagnostics.Stopwatch]::StartNew()
while ($clock.Elapsed.TotalSeconds -lt $ClickAt) { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 20 }
# what is on screen right before the click: the probe, with the bar on top of it
$bmp = New-Object System.Drawing.Bitmap(300, 80)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen(($cx - 150), ($cy - 40), 0, 0, (New-Object System.Drawing.Size(300, 80)))
$g.Dispose()
$shotPath = Join-Path $env:TEMP "clickthrough-before.png"
$bmp.Save($shotPath, [System.Drawing.Imaging.ImageFormat]::Png)
# the bar's pixels differ from the probe's flat colour; count how many do, so the check cannot pass on an empty screen
$probe = [System.Drawing.Color]::DarkSlateGray
$other = 0
for ($y = 0; $y -lt 80; $y += 2) { for ($x = 0; $x -lt 300; $x += 2) { $c = $bmp.GetPixel($x, $y); if ([Math]::Abs($c.R - $probe.R) + [Math]::Abs($c.G - $probe.G) + [Math]::Abs($c.B - $probe.B) -gt 30) { $other++ } } }
$bmp.Dispose()
"pixels over the probe that are not the probe's colour (the bar): $other   (saved $shotPath)"
$sw = [System.Windows.Forms.SystemInformation]::VirtualScreen
$ax = [int](($cx - $sw.X) * 65535 / $sw.Width); $ay = [int](($cy - $sw.Y) * 65535 / $sw.Height)
[void][C]::SetCursorPos($cx, $cy)
$pt = New-Object C+POINT; $pt.X = $cx; $pt.Y = $cy
$hit = [C]::WindowFromPoint($pt); $cn = New-Object System.Text.StringBuilder 256; [void][C]::GetClassNameW($hit, $cn, 256)
$tt = New-Object System.Text.StringBuilder 256; [void][C]::GetWindowTextW([C]::GetAncestor($hit, 2), $tt, 256)
"Windows says the click goes to: $($cn.ToString()) (window titled $($tt.ToString()))"
[C]::mouse_event(0xC001, $ax, $ay, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 150
[C]::mouse_event(0xC002, $ax, $ay, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 60
[C]::mouse_event(0xC004, $ax, $ay, 0, [UIntPtr]::Zero)
$end = [Diagnostics.Stopwatch]::StartNew()
while ($end.Elapsed.TotalMilliseconds -lt 500) { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 20 }
$form.Close()
"click at $cx,$cy reached the window underneath: $($script:hits -ge 1)  (hits=$($script:hits))"
