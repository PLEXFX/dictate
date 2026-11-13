using System.Runtime.InteropServices;

namespace DictateUI;

// The few Windows calls WinUI does not cover: a clear, frameless, topmost window that never takes focus (the activity bar),
// and click-through on and off.
internal static class Native
{
    public const int GWL_STYLE = -16, GWL_EXSTYLE = -20;
    public const long WS_EX_TOPMOST = 0x00000008, WS_EX_TRANSPARENT = 0x00000020, WS_EX_TOOLWINDOW = 0x00000080,
        WS_EX_LAYERED = 0x00080000, WS_EX_NOACTIVATE = 0x08000000;
    public const uint SWP_NOSIZE = 0x1, SWP_NOMOVE = 0x2, SWP_NOZORDER = 0x4, SWP_NOACTIVATE = 0x10, SWP_FRAMECHANGED = 0x20;
    public const int SW_HIDE = 0, SW_SHOWNOACTIVATE = 4;

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    private struct MARGINS { public int Left, Right, Top, Bottom; }

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] private static extern IntPtr GetWindowLongPtr(IntPtr h, int i);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] private static extern IntPtr SetWindowLongPtr(IntPtr h, int i, IntPtr v);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
    [DllImport("user32.dll")] private static extern bool SetLayeredWindowAttributes(IntPtr h, uint key, byte alpha, uint flags);
    [DllImport("dwmapi.dll")] private static extern int DwmExtendFrameIntoClientArea(IntPtr h, ref MARGINS m);

    private static void SetExStyle(IntPtr hwnd, long flags, bool on)
    {
        long ex = GetWindowLongPtr(hwnd, GWL_EXSTYLE).ToInt64();
        ex = on ? ex | flags : ex & ~flags;
        SetWindowLongPtr(hwnd, GWL_EXSTYLE, new IntPtr(ex));
    }

    // Off Alt+Tab, and never the window a click or a show makes active: dictation pastes into whatever was being typed in,
    // and a bar that took focus would paste into itself.
    public static void NeverTakeFocus(IntPtr hwnd) => SetExStyle(hwnd, WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST, true);

    // True: the mouse passes straight through (the bar at rest). False: the bar can be clicked (a locked recording).
    // A transparent window only passes clicks on if it is also layered, and a layered WinUI window cannot be clicked at all,
    // so the two flags come on and off together. The layer is fully opaque, so nothing changes on screen.
    // Both halves are checked with real clicks: tools\clickthrough.ps1.
    public static void SetClickThrough(IntPtr hwnd, bool through)
    {
        if (through)
        {
            SetExStyle(hwnd, WS_EX_LAYERED | WS_EX_TRANSPARENT, true);
            SetLayeredWindowAttributes(hwnd, 0, 255, 2 /*LWA_ALPHA*/);
        }
        else
        {
            SetExStyle(hwnd, WS_EX_LAYERED | WS_EX_TRANSPARENT, false);
        }
    }

    // Strip every frame style so the window is a plain popup, then let Windows blend its clear pixels with what is behind.
    public static void MakeFramelessClear(IntPtr hwnd)
    {
        long style = GetWindowLongPtr(hwnd, GWL_STYLE).ToInt64();
        style &= ~(0x00C00000L /*caption*/ | 0x00040000L /*thick frame*/ | 0x00080000L /*system menu*/ | 0x00800000L /*border*/ | 0x00400000L /*dialog frame*/);
        SetWindowLongPtr(hwnd, GWL_STYLE, new IntPtr(style));
        SetWindowPos(hwnd, IntPtr.Zero, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED);
        var m = new MARGINS { Left = -1, Right = -1, Top = -1, Bottom = -1 };
        DwmExtendFrameIntoClientArea(hwnd, ref m);
    }
}
