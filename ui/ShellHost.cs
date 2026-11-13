namespace DictateUI;

// What this program is when the engine starts it: the tray icon, the activity bar, and Settings when asked for. Settings is
// a window that comes and goes; the tray and the bar stay for as long as the engine runs.
internal sealed class ShellHost
{
    private readonly IEngineLink _link;
    private readonly bool _demo;
    private readonly TrayIcon _tray;
    private readonly BarWindow _bar;
    private SettingsWindow _settings;

    public ShellHost(IEngineLink link, bool demo)
    {
        _link = link;
        _demo = demo;

        _bar = new BarWindow();
        _bar.SetTheme(link.BarDark);
        _bar.UpdateSettings(link.Settings);
        _bar.Clicked += link.BarClicked;

        var s = link.Shell;
        s.BarState += _bar.SetState;
        s.BarLevel += _bar.SetLevel;
        s.BarNotice += _bar.Notify;
        s.BarClickable += _bar.SetClickable;
        s.BarTheme += _bar.SetTheme;
        s.BarShow += _bar.ShowBar;
        s.BarHide += _bar.HideBar;
        s.BarPreviewWidth += _bar.PreviewWidth;
        s.BarPreviewMargin += _bar.PreviewMargin;
        s.SettingsApplied += _bar.UpdateSettings;
        s.ShowSettings += ShowSettings;
        s.Quit += Exit;

        _tray = new TrayIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "dictate.ico"));
        _tray.Activated += ShowSettings;
        _tray.ActionChosen += OnTrayAction;
        s.Tray += _tray.Update;
        s.Balloon += (text, warning, _) => _tray.Balloon(text, warning);
        _tray.Show(link.Tray);
    }

    public void ShowSettings()
    {
        App.Note($"show settings (window already open: {_settings != null})");
        if (_settings != null)
        {
            _settings.BringToFront();
            return;
        }
        _settings = new SettingsWindow(exitOnClose: _demo);
        _settings.Closed += (_, _) => _settings = null;
        _settings.Activate();
    }

    private void OnTrayAction(string id)
    {
        App.Note($"tray menu chose {id}");
        if (id == "settings") ShowSettings(); // no need to ask the engine to ask us
        else _link.TrayAction(id);
    }

    // The engine is quitting (or, in the demo, the tray's Quit): take the tray icon with us.
    public void Exit()
    {
        _tray.Dispose();
        Environment.Exit(0);
    }
}
