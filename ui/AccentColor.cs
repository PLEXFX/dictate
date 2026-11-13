using Microsoft.Win32;
using Windows.UI;

namespace DictateUI;

// The user's Windows accent, adjusted for the surface it sits on: port of bar.system_accent plus the "louder is a little
// brighter" nudge. The raw accent is often too dark for a dark pill, so lightness is floored (dark) or capped (light).
internal static class AccentColor
{
    public static readonly Color Fallback = Color.FromArgb(255, 76, 194, 255); // #4CC2FF, the Windows 11 dark accent

    public static Color Get(bool dark)
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\DWM");
            if (key?.GetValue("AccentColor") is int raw)
            {
                uint v = unchecked((uint)raw);
                int r = (int)(v & 0xFF), g = (int)((v >> 8) & 0xFF), b = (int)((v >> 16) & 0xFF);
                if (r != 0 || g != 0 || b != 0)
                {
                    var (h, s, l) = ToHsl(r, g, b);
                    if (h < 0) { h = 0.55; s = 0.75; } // a grey accent has no hue to keep
                    l = dark ? Math.Max(l, 0.66) : Math.Min(l, 0.48);
                    return FromHsl(h, Math.Min(1.0, Math.Max(s, 0.55)), l, 255);
                }
            }
        }
        catch { /* any failure keeps the Fluent default */ }
        return Fallback;
    }

    // As the listening level rises the accent gets a touch brighter and more saturated, never a different colour.
    public static Color Louder(Color accent, double level)
    {
        var (h, s, l) = ToHsl(accent.R, accent.G, accent.B);
        double amount = Math.Min(1.0, Math.Max(0.0, level));
        if (h < 0) h = 0;
        s = Math.Min(1.0, s + 0.18 * amount);
        l = Math.Min(0.85, l + 0.10 * amount);
        return FromHsl(h, s, l, accent.A);
    }

    public static (double H, double S, double L) ToHsl(int r8, int g8, int b8)
    {
        double r = r8 / 255.0, g = g8 / 255.0, b = b8 / 255.0;
        double max = Math.Max(r, Math.Max(g, b)), min = Math.Min(r, Math.Min(g, b));
        double l = (max + min) / 2;
        if (max == min) return (-1, 0, l);
        double d = max - min;
        double s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
        double h = max == r ? (g - b) / d + (g < b ? 6 : 0) : max == g ? (b - r) / d + 2 : (r - g) / d + 4;
        return (h / 6, s, l);
    }

    public static Color FromHsl(double h, double s, double l, byte alpha)
    {
        double q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
        static double Hue(double p, double q, double t)
        {
            if (t < 0) t += 1;
            if (t > 1) t -= 1;
            if (t < 1.0 / 6) return p + (q - p) * 6 * t;
            if (t < 0.5) return q;
            if (t < 2.0 / 3) return p + (q - p) * (2.0 / 3 - t) * 6;
            return p;
        }
        return Color.FromArgb(alpha,
            (byte)Math.Round(Hue(p, q, h + 1.0 / 3) * 255),
            (byte)Math.Round(Hue(p, q, h) * 255),
            (byte)Math.Round(Hue(p, q, h - 1.0 / 3) * 255));
    }
}
