using System.Runtime.InteropServices;

namespace DictateUI;

// What the tray menu shows, as the engine last said it (ui_shell.RemoteTray.snapshot).
public sealed record TrayInfo(string Tooltip, string SettingsLabel, bool CopyLast, bool Undo, bool CheckUpdate)
{
    public static readonly TrayInfo Default = new("Dictate", "Settings", false, false, true);
}

// The notification-area icon and its menu. It listens through a message-only window of its own, so it never depends on
// any visible window. Left click opens Settings; right click shows the menu; Windows restarting Explorer puts the icon back.
internal sealed class TrayIcon : IDisposable
{
    private const uint WM_APP_TRAY = 0x8001, WM_LBUTTONUP = 0x0202, WM_RBUTTONUP = 0x0205, WM_CONTEXTMENU = 0x007B, WM_NULL = 0;
    private const uint NIM_ADD = 0, NIM_MODIFY = 1, NIM_DELETE = 2;
    private const uint NIF_MESSAGE = 1, NIF_ICON = 2, NIF_TIP = 4, NIF_INFO = 0x10;
    private const uint NIIF_INFO = 1, NIIF_WARNING = 2;
    private const uint MF_STRING = 0, MF_GRAYED = 1, MF_SEPARATOR = 0x800;
    private const uint TPM_RIGHTBUTTON = 2, TPM_BOTTOMALIGN = 0x20, TPM_NONOTIFY = 0x80, TPM_RETURNCMD = 0x100;
    private static readonly IntPtr HWND_MESSAGE = new(-3);

    private delegate IntPtr WndProc(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WNDCLASS
    {
        public uint style;
        public IntPtr lpfnWndProc;
        public int cbClsExtra, cbWndExtra;
        public IntPtr hInstance, hIcon, hCursor, hbrBackground;
        public string lpszMenuName, lpszClassName;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NOTIFYICONDATA
    {
        public int cbSize;
        public IntPtr hWnd;
        public uint uID, uFlags, uCallbackMessage;
        public IntPtr hIcon;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string szTip;
        public uint dwState, dwStateMask;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string szInfo;
        public uint uTimeoutOrVersion;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string szInfoTitle;
        public uint dwInfoFlags;
        public Guid guidItem;
        public IntPtr hBalloonIcon;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern ushort RegisterClassW(ref WNDCLASS c);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CreateWindowExW(uint ex, string cls, string name, uint style, int x, int y, int w, int h, IntPtr parent, IntPtr menu, IntPtr inst, IntPtr param);
    [DllImport("user32.dll")] private static extern bool DestroyWindow(IntPtr h);
    [DllImport("user32.dll")] private static extern IntPtr DefWindowProcW(IntPtr h, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern uint RegisterWindowMessageW(string name);
    [DllImport("user32.dll")] private static extern IntPtr CreatePopupMenu();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool AppendMenuW(IntPtr menu, uint flags, UIntPtr id, string text);
    [DllImport("user32.dll")] private static extern uint TrackPopupMenuEx(IntPtr menu, uint flags, int x, int y, IntPtr hwnd, IntPtr tpm);
    [DllImport("user32.dll")] private static extern bool DestroyMenu(IntPtr menu);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] private static extern bool PostMessageW(IntPtr h, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr LoadImageW(IntPtr inst, string name, uint type, int cx, int cy, uint load);
    [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr icon);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool Shell_NotifyIcon(uint msg, ref NOTIFYICONDATA data);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr GetModuleHandleW(string name);
    // Lets popup menus follow Windows dark mode (undocumented uxtheme ordinals 135 and 136, used by Explorer's own apps).
    [DllImport("uxtheme.dll", EntryPoint = "#135")] private static extern int SetPreferredAppMode(int mode);
    [DllImport("uxtheme.dll", EntryPoint = "#136")] private static extern void FlushMenuThemes();

    private static readonly (string Id, uint Command)[] Commands =
    {
        ("settings", 1), ("load", 2), ("unload", 3), ("copy_last", 4), ("undo", 5), ("check_update", 6), ("quit", 7),
    };

    private readonly string _iconPath;
    private readonly WndProc _proc;   // kept alive: the window calls back into it
    private IntPtr _hwnd, _icon;
    private NOTIFYICONDATA _nid;
    private uint _taskbarCreated;
    private bool _added;
    private TrayInfo _info = TrayInfo.Default;

    public event Action Activated;
    public event Action<string> ActionChosen;

    public TrayIcon(string iconPath)
    {
        _iconPath = iconPath;
        _proc = Callback;
        try { SetPreferredAppMode(1); FlushMenuThemes(); } catch { /* an older Windows: classic light menus */ }

        var cls = new WNDCLASS { lpfnWndProc = Marshal.GetFunctionPointerForDelegate(_proc), hInstance = GetModuleHandleW(null), lpszClassName = "DictateUiTrayWindow" };
        if (RegisterClassW(ref cls) == 0) Fail("RegisterClass");
        _hwnd = CreateWindowExW(0, cls.lpszClassName, "Dictate tray", 0, 0, 0, 0, 0, HWND_MESSAGE, IntPtr.Zero, cls.hInstance, IntPtr.Zero);
        if (_hwnd == IntPtr.Zero) Fail("CreateWindowEx");
        App.Note($"tray window {_hwnd}");
        _taskbarCreated = RegisterWindowMessageW("TaskbarCreated");
    }

    // A tray icon that cannot be made leaves no sign on screen, so say why in the crash log.
    private static void Fail(string call) =>
        App.Log("Tray", new InvalidOperationException($"{call} failed, Windows error {Marshal.GetLastWin32Error()}"));

    public void Show(TrayInfo info)
    {
        _info = info;
        if (_added) { Update(); return; }
        Add();
    }

    public void Update(TrayInfo info)
    {
        _info = info;
        if (_added) Update();
    }

    private void Add()
    {
        int size = GetSystemMetrics(49 /*SM_CXSMICON*/);
        if (_icon != IntPtr.Zero) DestroyIcon(_icon);
        _icon = LoadImageW(IntPtr.Zero, _iconPath, 1 /*IMAGE_ICON*/, size, size, 0x10 /*LR_LOADFROMFILE*/);
        _nid = new NOTIFYICONDATA
        {
            cbSize = Marshal.SizeOf<NOTIFYICONDATA>(),
            hWnd = _hwnd,
            uID = 1,
            uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP,
            uCallbackMessage = WM_APP_TRAY,
            hIcon = _icon,
            szTip = Tip(_info.Tooltip),
            szInfo = "",
            szInfoTitle = "",
        };
        _added = Shell_NotifyIcon(NIM_ADD, ref _nid);
        if (!_added) Fail("Shell_NotifyIcon");
        App.Note($"tray icon added={_added} icon={_icon} size={size}");
    }

    private void Update()
    {
        _nid.uFlags = NIF_TIP;
        _nid.szTip = Tip(_info.Tooltip);
        Shell_NotifyIcon(NIM_MODIFY, ref _nid);
    }

    private static string Tip(string text) => text.Length > 127 ? text[..127] : text;

    // A Windows notification, shown only when the person turned them on in Settings.
    public void Balloon(string text, bool warning, string title = "Dictate")
    {
        if (!_added) return;
        _nid.uFlags = NIF_INFO;
        _nid.szInfo = text.Length > 255 ? text[..255] : text;
        _nid.szInfoTitle = title;
        _nid.dwInfoFlags = warning ? NIIF_WARNING : NIIF_INFO;
        Shell_NotifyIcon(NIM_MODIFY, ref _nid);
    }

    private IntPtr Callback(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam)
    {
        if (msg == WM_APP_TRAY)
        {
            uint ev = (uint)(lParam.ToInt64() & 0xFFFF);
            App.Note($"tray event 0x{ev:X}");
            if (ev == WM_LBUTTONUP) Activated?.Invoke();
            else if (ev == WM_RBUTTONUP || ev == WM_CONTEXTMENU) ShowMenu();
            return IntPtr.Zero;
        }
        if (msg == _taskbarCreated && _taskbarCreated != 0)
        {
            _added = false; // Explorer restarted: the icon is gone, put it back
            Add();
            return IntPtr.Zero;
        }
        return DefWindowProcW(hwnd, msg, wParam, lParam);
    }

    private void ShowMenu()
    {
        var menu = CreatePopupMenu();
        void Item(uint command, string text, bool enabled = true) =>
            AppendMenuW(menu, MF_STRING | (enabled ? 0u : MF_GRAYED), new UIntPtr(command), text);
        void Separator() => AppendMenuW(menu, MF_SEPARATOR, UIntPtr.Zero, null);

        Item(1, _info.SettingsLabel);
        Separator();
        Item(2, "Load model now");
        Item(3, "Unload model");
        Item(4, "Copy last dictation", _info.CopyLast);
        Item(5, "Undo last dictation", _info.Undo);
        Separator();
        Item(6, "Check for updates", _info.CheckUpdate);
        Separator();
        Item(7, "Quit");

        Native.GetCursorPos(out var p);
        SetForegroundWindow(_hwnd); // lets the menu close when you click away
        uint chosen = TrackPopupMenuEx(menu, TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON | TPM_BOTTOMALIGN, p.X, p.Y, _hwnd, IntPtr.Zero);
        PostMessageW(_hwnd, WM_NULL, IntPtr.Zero, IntPtr.Zero);
        DestroyMenu(menu);
        App.Note($"tray menu closed with command {chosen}");
        foreach (var (id, command) in Commands)
            if (command == chosen) { ActionChosen?.Invoke(id); break; }
    }

    public void Dispose()
    {
        if (_added) Shell_NotifyIcon(NIM_DELETE, ref _nid);
        _added = false;
        if (_icon != IntPtr.Zero) { DestroyIcon(_icon); _icon = IntPtr.Zero; }
        if (_hwnd != IntPtr.Zero) { DestroyWindow(_hwnd); _hwnd = IntPtr.Zero; }
    }
}
