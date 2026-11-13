# Captures the strip of screen where the activity bar sits (bottom centre of the work area), at the given times.
# Usage: shotbar.ps1 -Dir <folder> -At 1.5,3,5 -Prefix bar      (seconds after this script starts)
param([string]$Dir, [double[]]$At, [string]$Prefix = "bar", [int]$Width = 520, [int]$Height = 90)
Add-Type -AssemblyName System.Drawing, System.Windows.Forms
Add-Type @"
using System.Runtime.InteropServices;
public static class D { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); [DllImport("user32.dll")] public static extern System.IntPtr GetForegroundWindow(); }
"@
[void][D]::SetProcessDPIAware()
$area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$x = [int]($area.Left + ($area.Width - $Width) / 2)
$y = [int]($area.Bottom - $Height)
$clock = [Diagnostics.Stopwatch]::StartNew()
foreach ($t in $At) {
    while ($clock.Elapsed.TotalSeconds -lt $t) { Start-Sleep -Milliseconds 20 }
    $bmp = New-Object System.Drawing.Bitmap($Width, $Height)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($Width, $Height)))
    $g.Dispose()
    $name = "{0}-{1:00.0}.png" -f $Prefix, $t
    $bmp.Save((Join-Path $Dir $name), [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    "{0}: foreground window {1}" -f $name, [D]::GetForegroundWindow()
}
