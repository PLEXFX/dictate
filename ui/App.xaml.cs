using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;

namespace DictateUI;

public partial class App : Application
{
    private ShellHost _shell;
    private static Mutex _single;
    private static DispatcherQueueTimer _saveTimer;

    // The one connection to the engine: PipeLink when the engine started this program (--pipe <name>), DemoLink on made-up data otherwise.
    public static IEngineLink Link { get; private set; }
    public static bool Demo => Link is DemoLink && Environment.GetEnvironmentVariable("DICTATEUI_DEMO") == "1";
    public static event Action ThemeModeChanged;

    public App()
    {
        InitializeComponent();
        UnhandledException += (s, e) => Log("UI thread", e.Exception);
        AppDomain.CurrentDomain.UnhandledException += (s, e) => Log("Background", e.ExceptionObject as Exception);
    }

    // A crash leaves one readable file instead of vanishing: %LOCALAPPDATA%\Dictate UI\crash.log
    internal static void Log(string where, Exception ex)
    {
        try
        {
            Directory.CreateDirectory(SettingsStore.DataDir);
            File.AppendAllText(Path.Combine(SettingsStore.DataDir, "crash.log"),
                $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} [{where}] {ex}\r\n\r\n");
        }
        catch { }
    }

    // Trace lines for tests and debugging: only written when DICTATEUI_DEBUG=1, to debug.log beside crash.log.
    internal static void Note(string text)
    {
        if (Environment.GetEnvironmentVariable("DICTATEUI_DEBUG") != "1") return;
        try
        {
            Directory.CreateDirectory(SettingsStore.DataDir);
            File.AppendAllText(Path.Combine(SettingsStore.DataDir, "debug.log"), $"{DateTime.Now:HH:mm:ss.fff} {text}\r\n");
        }
        catch { }
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        string pipe = PipeNameFromArgs();
        bool test = Environment.GetEnvironmentVariable("DICTATEUI_HOME") != null;
        string kind = pipe != null ? "Settings" : "Demo";
        _single = new Mutex(true, $"DictateUI.{kind}{(test ? ".Test" : "")}", out bool first);
        if (!first)
        {
            Environment.Exit(0);
            return;
        }
        try
        {
            // With no engine to talk to the window cannot show anything true, so a failed connection ends the program;
            // the engine then falls back to its built-in window.
            Link = pipe != null ? PipeLink.Connect(pipe) : new DemoLink();
            _saveTimer = DispatcherQueue.GetForCurrentThread().CreateTimer();
            _saveTimer.Interval = TimeSpan.FromMilliseconds(250);
            _saveTimer.IsRepeating = false;
            _saveTimer.Tick += (_, _) => Link.SaveSettings(Link.Settings.Clamped());
            // The tray icon and the activity bar, for as long as this runs. Settings opens from the tray (or the engine's
            // hotkey); in the demo, with no engine, it opens at once.
            _shell = new ShellHost(Link, demo: pipe == null);
            if (pipe == null)
            {
                if (Environment.GetEnvironmentVariable("DICTATEUI_BARTOUR") == "1") ((DemoLink)Link).RunBarTour();
                else _shell.ShowSettings();
            }
        }
        catch (Exception ex)
        {
            Log("Startup", ex);
            if (pipe != null) Environment.Exit(1);
            throw;
        }
    }

    private static string PipeNameFromArgs()
    {
        var args = Environment.GetCommandLineArgs();
        for (int i = 1; i < args.Length - 1; i++)
            if (args[i] == "--pipe") return args[i + 1];
        return null;
    }

    // Changes save by themselves a moment after the last one, so there is no Apply button.
    public static void QueueSave()
    {
        _saveTimer.Stop();
        _saveTimer.Start();
    }

    public static void RaiseThemeModeChanged() => ThemeModeChanged?.Invoke();
}
