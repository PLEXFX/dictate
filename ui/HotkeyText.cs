using System.Globalization;
using Windows.System;

namespace DictateUI;

// Port of hotkeys.parse_combo / canonical_combo / format_combo: the stored form is "ctrl+alt+d", the label is "Ctrl + Alt + D".
public static class HotkeyText
{
    private static readonly Dictionary<string, int> Order = new() { ["ctrl"] = 0, ["alt"] = 1, ["shift"] = 2, ["win"] = 3 };

    private static readonly Dictionary<string, string> Display = new()
    {
        ["ctrl"] = "Ctrl", ["alt"] = "Alt", ["shift"] = "Shift", ["win"] = "Windows", ["esc"] = "Esc", ["space"] = "Space",
        ["enter"] = "Enter", ["tab"] = "Tab", ["backspace"] = "Backspace", ["delete"] = "Delete", ["insert"] = "Insert",
        ["home"] = "Home", ["end"] = "End", ["page_up"] = "Page Up", ["page_down"] = "Page Down", ["up"] = "Up",
        ["down"] = "Down", ["left"] = "Left", ["right"] = "Right", ["mouse1"] = "Mouse left", ["mouse2"] = "Mouse right",
        ["mouse3"] = "Mouse middle", ["mouse4"] = "Mouse 4", ["mouse5"] = "Mouse 5",
    };

    public static string Canonical(IEnumerable<string> parts) =>
        string.Join("+", parts.Select(p => p.Trim().ToLowerInvariant()).Where(p => p.Length > 0).Distinct()
            .OrderBy(p => Order.TryGetValue(p, out var o) ? o : 10).ThenBy(p => p, StringComparer.Ordinal));

    public static string Format(string text)
    {
        var parts = Canonical((text ?? "").Split('+')).Split('+', StringSplitOptions.RemoveEmptyEntries);
        var labels = new List<string>();
        foreach (var part in parts)
        {
            if (Display.TryGetValue(part, out var label)) labels.Add(label);
            else if (part.Length > 1 && part[0] == 'f' && part[1..].All(char.IsDigit)) labels.Add(part.ToUpperInvariant());
            else labels.Add(CultureInfo.InvariantCulture.TextInfo.ToTitleCase(part.Replace('_', ' ')));
        }
        return string.Join(" + ", labels);
    }

    // The stored name for a pressed key, or "" for one that cannot be part of a binding.
    public static string FromKey(VirtualKey key)
    {
        switch (key)
        {
            case VirtualKey.Control: case VirtualKey.LeftControl: case VirtualKey.RightControl: return "ctrl";
            case VirtualKey.Menu: case VirtualKey.LeftMenu: case VirtualKey.RightMenu: return "alt";
            case VirtualKey.Shift: case VirtualKey.LeftShift: case VirtualKey.RightShift: return "shift";
            case VirtualKey.LeftWindows: case VirtualKey.RightWindows: return "win";
            case VirtualKey.Space: return "space";
            case VirtualKey.Enter: return "enter";
            case VirtualKey.Tab: return "tab";
            case VirtualKey.Back: return "backspace";
            case VirtualKey.Delete: return "delete";
            case VirtualKey.Insert: return "insert";
            case VirtualKey.Home: return "home";
            case VirtualKey.End: return "end";
            case VirtualKey.PageUp: return "page_up";
            case VirtualKey.PageDown: return "page_down";
            case VirtualKey.Up: return "up";
            case VirtualKey.Down: return "down";
            case VirtualKey.Left: return "left";
            case VirtualKey.Right: return "right";
        }
        int v = (int)key;
        if (v >= (int)VirtualKey.A && v <= (int)VirtualKey.Z) return ((char)('a' + (v - (int)VirtualKey.A))).ToString();
        if (v >= (int)VirtualKey.Number0 && v <= (int)VirtualKey.Number9) return ((char)('0' + (v - (int)VirtualKey.Number0))).ToString();
        if (v >= (int)VirtualKey.F1 && v <= (int)VirtualKey.F24) return "f" + (v - (int)VirtualKey.F1 + 1);
        return "";
    }
}
