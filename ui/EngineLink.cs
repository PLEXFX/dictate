using System.Diagnostics;
using Microsoft.UI.Dispatching;
using Microsoft.Win32;
using Windows.Devices.Enumeration;

namespace DictateUI;

public enum UpdateState { Idle, Checking, UpToDate, Available, Downloading, Verifying, ReadyToRestart, Installing, Error }

// What the engine reports. The pages only read this and call IEngineLink; none of them knows whether the engine is real.
public sealed class EngineStatus
{
    public UpdateState Update = UpdateState.Idle;
    public string UpdateDetail = "";
    public double? UpdateProgress;
    public DateTime? LastChecked;

    public bool ModelLoading;   // the speech model is loading or downloading
    public bool ModelBusy;      // ...and a real percentage is known
    public double? ModelProgress;
    public string ModelDetail = "";

    public bool HasCuda;
    public bool GpuReady;
    public bool GpuDownloading;
    public double? GpuProgress;
    public string GpuError = "";

    public string CpuName = "";
    public string GpuName = "";
    public int VramMb;
}

public sealed record InputDevice(string Key, string Label);

// What the engine tells the program about the bar and the tray, and what the window itself tells the bar (previews, saved
// settings). Raised on the UI thread.
public sealed class ShellEvents
{
    public event Action<string, string, double?> BarState;            // state, detail, progress
    public event Action<double> BarLevel;                              // microphone loudness 0..1
    public event Action<string, string, bool, int?> BarNotice;         // text, tone, clickable, duration ms
    public event Action<bool> BarClickable;
    public event Action<bool> BarTheme;                                // dark
    public event Action BarShow;
    public event Action BarHide;
    public event Action<int> BarPreviewWidth;
    public event Action<int> BarPreviewMargin;
    public event Action<Settings> SettingsApplied;                     // the bar re-reads width, gap, linger, always-visible
    public event Action<TrayInfo> Tray;
    public event Action<string, bool, int> Balloon;                    // text, is a warning, duration ms
    public event Action ShowSettings;
    public event Action Quit;

    internal void RaiseBarState(string state, string detail, double? progress) => BarState?.Invoke(state, detail, progress);
    internal void RaiseBarLevel(double v) => BarLevel?.Invoke(v);
    internal void RaiseBarNotice(string text, string tone, bool clickable, int? ms) => BarNotice?.Invoke(text, tone, clickable, ms);
    internal void RaiseBarClickable(bool value) => BarClickable?.Invoke(value);
    internal void RaiseBarTheme(bool dark) => BarTheme?.Invoke(dark);
    internal void RaiseBarShow() => BarShow?.Invoke();
    internal void RaiseBarHide() => BarHide?.Invoke();
    internal void RaiseBarPreviewWidth(int w) => BarPreviewWidth?.Invoke(w);
    internal void RaiseBarPreviewMargin(int m) => BarPreviewMargin?.Invoke(m);
    internal void RaiseSettingsApplied(Settings s) => SettingsApplied?.Invoke(s);
    internal void RaiseTray(TrayInfo t) => Tray?.Invoke(t);
    internal void RaiseBalloon(string text, bool warning, int ms) => Balloon?.Invoke(text, warning, ms);
    internal void RaiseShowSettings() => ShowSettings?.Invoke();
    internal void RaiseQuit() => Quit?.Invoke();
}

// The seam between the UI and the Python engine. DemoLink fills it with made-up work now; the pipe to the real engine
// replaces it next, with no change to any page.
public interface IEngineLink
{
    Settings Settings { get; }
    EngineStatus Status { get; }
    event Action StatusChanged;
    event Action SettingsChanged;   // the engine changed or refused part of a save: pages reload their controls
    event Action FocusRequested;    // the engine was asked to open Settings again: come to the front
    string Version { get; }
    string WhatsNewNotes { get; }

    void SaveSettings(Settings settings);
    Task<IReadOnlyList<InputDevice>> GetInputDevicesAsync();
    void CheckForUpdates();
    void StartUpdate();
    void RestartToInstall();
    void StartGpuDownload();
    void OpenModelFolder();
    void CaptureActive(bool active);
    void PreviewBarWidth(int width);
    void PreviewBarMargin(int margin);

    // The tray icon and the activity bar, which this program owns when the engine starts it.
    ShellEvents Shell { get; }
    TrayInfo Tray { get; }
    bool BarDark { get; }
    void TrayAction(string id);
    void BarClicked();
}

public sealed class DemoLink : IEngineLink
{
    private readonly DispatcherQueueTimer _tick;
    private int _checkTicks, _verifyTicks, _installTicks, _settleTicks;

    public Settings Settings { get; private set; }
    public EngineStatus Status { get; } = new();
    public event Action StatusChanged;
#pragma warning disable CS0067 // the demo engine never changes or refuses a save
    public event Action SettingsChanged;
    public event Action FocusRequested;
#pragma warning restore CS0067
    public string Version => "1.4.3-beta1";

    public string WhatsNewNotes =>
        "### Changed\n" +
        "- Settings now opens with compact page headers instead of a repeated Dictate banner, uses one responsive grouped-row layout throughout, and adds bundled Microsoft Fluent icons to the navigation rail.\n" +
        "- **Words I use** now opens as an in-place Settings page and saves changes automatically.\n" +
        "- The Settings rail now hides its passive **Up to date** row and keeps only the app version in the footer.\n" +
        "### Fixed\n" +
        "- An update that was already found remains downloadable from Settings after automatic future checks are turned off.";

    public DemoLink()
    {
        Settings = SettingsStore.Load();
        Status.HasCuda = true;
        Status.GpuReady = Settings.Device == "cuda";
        Status.CpuName = CpuName();
        Status.GpuName = "NVIDIA GeForce RTX 4070 SUPER";
        Status.VramMb = 12282;
        _tick = DispatcherQueue.GetForCurrentThread().CreateTimer();
        _tick.Interval = TimeSpan.FromMilliseconds(100);
        _tick.Tick += (_, _) => OnTick();
        _tick.Start();
    }

    private static string CpuName()
    {
        try
        {
            using var key = Registry.LocalMachine.OpenSubKey(@"HARDWARE\DESCRIPTION\System\CentralProcessor\0");
            var name = key?.GetValue("ProcessorNameString") as string;
            if (!string.IsNullOrWhiteSpace(name)) return string.Join(' ', name.Split((char[])null, StringSplitOptions.RemoveEmptyEntries));
        }
        catch { }
        return "This PC's processor";
    }

    private void Raise() => StatusChanged?.Invoke();

    public void SaveSettings(Settings settings)
    {
        bool modelChanged = settings.ModelSize != Settings.ModelSize || settings.Device != Settings.Device;
        Settings = settings;
        Shell.RaiseSettingsApplied(settings);
        SettingsStore.Save(settings);
        if (modelChanged && settings.ModelSize is "medium.en" or "large-v3-turbo") ShowModelDownload();
    }

    public async Task<IReadOnlyList<InputDevice>> GetInputDevicesAsync()
    {
        var list = new List<InputDevice>();
        try
        {
            var found = await DeviceInformation.FindAllAsync(DeviceClass.AudioCapture);
            foreach (var d in found.OrderBy(d => d.Name)) list.Add(new InputDevice(d.Name, d.Name));
        }
        catch { }
        return list;
    }

    // --- made-up work, so every state of the pages can be seen ---

    public void CheckForUpdates()
    {
        Status.Update = UpdateState.Checking;
        Status.UpdateDetail = "";
        Status.UpdateProgress = null;
        _checkTicks = 12;
        Raise();
    }

    public void StartUpdate()
    {
        Status.Update = UpdateState.Downloading;
        Status.UpdateDetail = "Downloading Dictate-Setup-1.4.4-beta1.exe";
        Status.UpdateProgress = 0;
        Raise();
    }

    public void RestartToInstall()
    {
        Status.Update = UpdateState.Verifying;
        _verifyTicks = 8;
        _installTicks = 0;
        Raise();
    }

    public void StartGpuDownload()
    {
        Status.GpuDownloading = true;
        Status.GpuProgress = 0;
        Status.GpuError = "";
        Raise();
    }

    public void OpenModelFolder() { /* demo: nothing to open */ }
    public void CaptureActive(bool active) { /* demo: no global listener to stand down */ }
    public void PreviewBarWidth(int width) => Shell.RaiseBarPreviewWidth(width);
    public void PreviewBarMargin(int margin) => Shell.RaiseBarPreviewMargin(margin);

    public ShellEvents Shell { get; } = new();
    public TrayInfo Tray { get; } = new("Dictate (demo)", "Settings\tCtrl + Alt + D", true, true, true);
    public bool BarDark => true;
    public void BarClicked() { StopFakeVoice(); }

    public void TrayAction(string id)
    {
        if (id == "settings") Shell.RaiseShowSettings();
        else if (id == "quit") Shell.RaiseQuit();
        // the other entries act on the engine, which the demo does not have
    }

    // --- the activity bar on made-up input, for the demo menu ---

    private DispatcherQueueTimer _voice;
    private double _voiceT;

    public void BarListening()
    {
        StopFakeVoice();
        Shell.RaiseBarState("listening", "", null);
        _voiceT = 0;
        _voice = DispatcherQueue.GetForCurrentThread().CreateTimer();
        _voice.Interval = TimeSpan.FromMilliseconds(16);
        var rng = new Random(7);
        _voice.Tick += (_, _) =>
        {
            _voiceT += 0.016;
            // speech comes in phrases: a slow envelope with quick wobble on top
            double phrase = Math.Max(0, Math.Sin(_voiceT * 1.7)) * (0.55 + 0.3 * Math.Sin(_voiceT * 0.6));
            Shell.RaiseBarLevel(Math.Min(1, phrase * (0.7 + 0.3 * rng.NextDouble())));
        };
        _voice.Start();
    }

    private void StopFakeVoice()
    {
        _voice?.Stop();
        _voice = null;
    }

    public void BarState(string state, string detail = "", double? progress = null)
    {
        StopFakeVoice();
        Shell.RaiseBarState(state, detail, progress);
    }

    // A scripted run through every state, for checking the bar from outside (DICTATEUI_BARTOUR=1): no Settings window, so
    // nothing takes focus. Seconds from start, and what happens then.
    public void RunBarTour()
    {
        var steps = new (double At, Action Do)[]
        {
            (0.5, BarListening),
            (4.0, () => BarState("transcribing")),
            (5.5, () => BarState("done")),
            (8.0, () => BarState("loading")),
            (10.0, () => BarState("loading", "Downloading Small", 0.4)),
            (12.0, () => BarState("loading", "Downloading Small", 0.8)),
            (14.0, () => BarState("error", "No microphone detected.")),
            (18.0, () => BarNotice("Ready. Hold F9 to talk.", "success", false)),
            (22.0, () => BarState("idle")),
            (24.0, BarLocked),
            (29.0, () => BarState("idle")),
            (31.0, () => BarNotice("Update 1.4.4 available. Click to open.", "info", true)),
        };
        var clock = Stopwatch.StartNew();
        int next = 0;
        var timer = DispatcherQueue.GetForCurrentThread().CreateTimer();
        timer.Interval = TimeSpan.FromMilliseconds(50);
        timer.Tick += (_, _) =>
        {
            while (next < steps.Length && clock.Elapsed.TotalSeconds >= steps[next].At) steps[next++].Do();
            if (next >= steps.Length) timer.Stop();
        };
        timer.Start();
    }

    public void BarNotice(string text, string tone, bool clickable) => Shell.RaiseBarNotice(text, tone, clickable, 6000);
    public void BarLocked() { BarListening(); Shell.RaiseBarClickable(true); }

    public void ShowModelDownload()
    {
        Status.ModelLoading = true;
        Status.ModelBusy = true;
        Status.ModelProgress = 0;
        Status.ModelDetail = "Downloading Large v3 Turbo · 0%";
        Raise();
    }

    // Used by the demo menu to jump straight to a state.
    public void SetUpdateState(UpdateState state)
    {
        Status.Update = state;
        Status.UpdateProgress = state == UpdateState.Downloading ? 0.42 : null;
        Status.UpdateDetail = state switch
        {
            UpdateState.Available => "Version 1.4.4-beta1 is available",
            UpdateState.Downloading => "Downloading Dictate-Setup-1.4.4-beta1.exe",
            UpdateState.Error => "Could not reach GitHub. Check your connection and try again.",
            _ => "",
        };
        if (state is UpdateState.UpToDate or UpdateState.Available or UpdateState.Error) Status.LastChecked = DateTime.Now;
        _settleTicks = 0;
        Raise();
    }

    public void SetGpu(bool hasCuda, bool ready, bool downloading = false)
    {
        Status.HasCuda = hasCuda;
        Status.GpuReady = ready;
        Status.GpuDownloading = downloading;
        Status.GpuProgress = downloading ? 0.35 : null;
        Raise();
    }

    private void OnTick()
    {
        bool changed = false;

        if (Status.Update == UpdateState.Checking && --_checkTicks <= 0)
        {
            Status.Update = UpdateState.UpToDate;
            Status.LastChecked = DateTime.Now;
            _settleTicks = 40;
            changed = true;
        }
        else if (Status.Update == UpdateState.UpToDate && _settleTicks > 0 && --_settleTicks == 0)
        {
            Status.Update = UpdateState.Idle;
            changed = true;
        }
        else if (Status.Update == UpdateState.Downloading && Status.UpdateProgress is double p)
        {
            p += 0.025;
            if (p >= 1)
            {
                Status.Update = UpdateState.Verifying;
                Status.UpdateProgress = null;
                _verifyTicks = 8;
                _installTicks = -1;
            }
            else Status.UpdateProgress = p;
            changed = true;
        }
        else if (Status.Update == UpdateState.Verifying && --_verifyTicks <= 0)
        {
            if (_installTicks == 0) { Status.Update = UpdateState.Installing; _installTicks = 20; }
            else Status.Update = UpdateState.ReadyToRestart;
            changed = true;
        }
        else if (Status.Update == UpdateState.Installing && --_installTicks <= 0)
        {
            Status.Update = UpdateState.ReadyToRestart; // the demo cannot really restart
            changed = true;
        }

        if (Status.GpuDownloading && Status.GpuProgress is double g)
        {
            g += 0.02;
            if (g >= 1) { Status.GpuDownloading = false; Status.GpuProgress = null; Status.GpuReady = true; }
            else Status.GpuProgress = g;
            changed = true;
        }

        if (Status.ModelBusy && Status.ModelProgress is double m)
        {
            m += 0.03;
            if (m >= 1) { Status.ModelLoading = false; Status.ModelBusy = false; Status.ModelProgress = null; }
            else
            {
                Status.ModelProgress = m;
                Status.ModelDetail = $"Downloading Large v3 Turbo · {(int)(m * 100)}%";
            }
            changed = true;
        }

        if (changed) Raise();
    }
}
