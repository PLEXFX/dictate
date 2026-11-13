using System.Diagnostics;
using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using Microsoft.UI.Dispatching;

namespace DictateUI;

// The real connection to the Python engine: one JSON object per line over a per-user named pipe (ui_bridge.py is the
// other end). The engine starts this program with --pipe <name>, so there is nothing to find or configure. Everything
// a page sees comes from the engine's own messages; if the pipe closes the engine is gone and so is this window.
public sealed class PipeLink : IEngineLink
{
    private readonly NamedPipeClientStream _pipe;
    private readonly StreamReader _reader;
    private readonly StreamWriter _writer;
    private readonly DispatcherQueue _ui;
    private readonly object _sendLock = new();
    private TaskCompletionSource<IReadOnlyList<InputDevice>> _devices;
    private string _modelDir = "";

    public Settings Settings { get; private set; }
    public EngineStatus Status { get; } = new();
    public string Version { get; private set; } = "";
    public string WhatsNewNotes { get; private set; } = "";

    public event Action StatusChanged;
    public event Action SettingsChanged;
    public event Action FocusRequested;

    public ShellEvents Shell { get; } = new();
    public TrayInfo Tray { get; private set; } = TrayInfo.Default;
    public bool BarDark { get; private set; } = true;

    private PipeLink(NamedPipeClientStream pipe, StreamReader reader)
    {
        _pipe = pipe;
        _reader = reader;
        _writer = new StreamWriter(pipe, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };
        _ui = DispatcherQueue.GetForCurrentThread();
    }

    // Connects and waits for the engine's hello, so Settings, version and status are known before the window is built.
    // Call it on the UI thread.
    public static PipeLink Connect(string pipeName, int timeoutMs = 5000)
    {
        var pipe = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
        try
        {
            pipe.Connect(timeoutMs);
            var reader = new StreamReader(pipe, new UTF8Encoding(false));
            var read = Task.Run(() => reader.ReadLine());
            if (!read.Wait(timeoutMs) || read.Result == null) throw new IOException("The engine did not say hello.");
            var link = new PipeLink(pipe, reader);
            using var doc = JsonDocument.Parse(read.Result);
            if (doc.RootElement.GetProperty("type").GetString() != "hello") throw new IOException("Unexpected first message.");
            link.ApplyHello(doc.RootElement);
            link.StartReading();
            return link;
        }
        catch
        {
            pipe.Dispose();
            throw;
        }
    }

    // --- what comes from the engine ---

    private void StartReading()
    {
        _ = Task.Run(async () =>
        {
            try
            {
                string line;
                while ((line = await _reader.ReadLineAsync()) != null)
                {
                    var l = line;
                    _ui.TryEnqueue(() => Handle(l));
                }
            }
            catch { /* a broken pipe is the same as a closed one */ }
            _ui.TryEnqueue(() => Environment.Exit(0)); // no engine, nothing to configure
        });
    }

    private void Handle(string line)
    {
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;
            switch (root.GetProperty("type").GetString())
            {
                case "status": ApplyStatus(root); StatusChanged?.Invoke(); break;
                case "settings":
                    ApplySettings(root.GetProperty("settings"));
                    Shell.RaiseSettingsApplied(Settings);
                    SettingsChanged?.Invoke();
                    break;
                case "focus": FocusRequested?.Invoke(); break;
                case "devices": ApplyDevices(root); break;
                case "bar_state": Shell.RaiseBarState(Str(root, "state"), Str(root, "detail"), Number(root, "progress")); break;
                case "bar_level": Shell.RaiseBarLevel(Number(root, "v") ?? 0); break;
                case "bar_notice":
                    Shell.RaiseBarNotice(Str(root, "text"), Str(root, "tone"), Flag(root, "clickable"),
                        Number(root, "duration_ms") is double ms ? (int)ms : null);
                    break;
                case "bar_clickable": Shell.RaiseBarClickable(Flag(root, "value")); break;
                case "bar_theme": BarDark = Flag(root, "dark"); Shell.RaiseBarTheme(BarDark); break;
                case "bar_show": Shell.RaiseBarShow(); break;
                case "bar_hide": Shell.RaiseBarHide(); break;
                case "tray": Tray = ReadTray(root); Shell.RaiseTray(Tray); break;
                case "balloon": Shell.RaiseBalloon(Str(root, "text"), Str(root, "tone") == "error", (int)(Number(root, "duration_ms") ?? 5000)); break;
                case "show_settings": Shell.RaiseShowSettings(); break;
                case "quit": Shell.RaiseQuit(); break;
            }
        }
        catch (Exception ex) { App.Log("Pipe message", ex); }
    }

    private void ApplyHello(JsonElement hello)
    {
        Version = hello.GetProperty("version").GetString() ?? "";
        WhatsNewNotes = hello.GetProperty("notes").GetString() ?? "";
        _modelDir = hello.GetProperty("model_dir").GetString() ?? "";
        ApplySettings(hello.GetProperty("settings"));
        ApplyStatus(hello.GetProperty("status"));
        if (hello.TryGetProperty("shell", out var shell) && shell.ValueKind == JsonValueKind.Object)
        {
            if (shell.TryGetProperty("tray", out var tray) && tray.ValueKind == JsonValueKind.Object) Tray = ReadTray(tray);
            if (shell.TryGetProperty("bar", out var bar) && bar.ValueKind == JsonValueKind.Object) BarDark = Flag(bar, "dark");
        }
    }

    private static TrayInfo ReadTray(JsonElement t) =>
        new(Str(t, "tooltip"), Str(t, "settings_label"), Flag(t, "copy_last"), Flag(t, "undo"), Flag(t, "check_update"));

    private static string Str(JsonElement parent, string name) =>
        parent.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";

    private static bool Flag(JsonElement parent, string name) =>
        parent.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.True;

    private void ApplySettings(JsonElement s)
    {
        Settings = (s.Deserialize<Settings>() ?? new Settings()).Clamped();
    }

    private void ApplyStatus(JsonElement root)
    {
        var u = root.GetProperty("update");
        Status.Update = u.GetProperty("state").GetString() switch
        {
            "checking" => UpdateState.Checking,
            "up_to_date" => UpdateState.UpToDate,
            "available" => UpdateState.Available,
            "downloading" => UpdateState.Downloading,
            "verifying" => UpdateState.Verifying,
            "ready_to_restart" => UpdateState.ReadyToRestart,
            "installing" => UpdateState.Installing,
            "error" => UpdateState.Error,
            _ => UpdateState.Idle,
        };
        Status.UpdateDetail = u.GetProperty("detail").GetString() ?? "";
        Status.UpdateProgress = Number(u, "progress");
        Status.LastChecked = Number(u, "checked_at") is double t
            ? DateTimeOffset.FromUnixTimeMilliseconds((long)(t * 1000)).LocalDateTime
            : null;

        var m = root.GetProperty("model");
        string state = m.GetProperty("state").GetString();
        Status.ModelLoading = state == "loading";
        Status.ModelProgress = Number(m, "progress");
        Status.ModelBusy = Status.ModelLoading && Status.ModelProgress != null;
        Status.ModelDetail = m.GetProperty("detail").GetString() ?? "";

        var g = root.GetProperty("gpu");
        Status.HasCuda = g.GetProperty("has_cuda").GetBoolean();
        Status.GpuReady = g.GetProperty("ready").GetBoolean();
        Status.GpuDownloading = g.GetProperty("downloading").GetBoolean();
        Status.GpuProgress = Number(g, "progress");
        Status.GpuError = g.GetProperty("error").GetString() ?? "";

        var h = root.GetProperty("hardware");
        Status.CpuName = h.GetProperty("cpu").GetString() ?? "";
        Status.GpuName = h.GetProperty("gpu").GetString() ?? "";
        Status.VramMb = h.GetProperty("vram_mb").GetInt32();
    }

    private static double? Number(JsonElement parent, string name) =>
        parent.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble() : null;

    private void ApplyDevices(JsonElement root)
    {
        var list = new List<InputDevice>();
        foreach (var d in root.GetProperty("devices").EnumerateArray())
            list.Add(new InputDevice(d.GetProperty("key").GetString() ?? "", d.GetProperty("label").GetString() ?? ""));
        _devices?.TrySetResult(list);
    }

    // --- what goes to the engine ---

    private void Send(object message)
    {
        try
        {
            lock (_sendLock) _writer.WriteLine(JsonSerializer.Serialize(message));
        }
        catch (IOException) { /* the read loop notices the closed pipe and ends the program */ }
        catch (ObjectDisposedException) { }
    }

    private void Command(string type) => Send(new { type });

    public void SaveSettings(Settings settings)
    {
        Settings = settings;
        Shell.RaiseSettingsApplied(settings); // the bar follows width, gap and linger at once
        Send(new { type = "save_settings", settings });
    }

    public void TrayAction(string id) => Send(new { type = "tray_action", id });
    public void BarClicked() => Command("bar_clicked");

    public async Task<IReadOnlyList<InputDevice>> GetInputDevicesAsync()
    {
        _devices = new TaskCompletionSource<IReadOnlyList<InputDevice>>();
        Command("get_devices");
        var done = await Task.WhenAny(_devices.Task, Task.Delay(8000));
        return done == _devices.Task ? _devices.Task.Result : new List<InputDevice>();
    }

    public void CheckForUpdates() => Command("check_updates");
    public void StartUpdate() => Command("start_update");
    public void RestartToInstall() => Command("restart_to_install");
    public void StartGpuDownload() => Command("start_gpu_download");
    public void CaptureActive(bool active) => Send(new { type = "capture", active });
    // This program owns the bar, so a slider moves it directly; nothing needs to go to the engine.
    public void PreviewBarWidth(int width) => Shell.RaiseBarPreviewWidth(width);
    public void PreviewBarMargin(int margin) => Shell.RaiseBarPreviewMargin(margin);

    public void OpenModelFolder()
    {
        try
        {
            if (_modelDir.Length == 0) return;
            Directory.CreateDirectory(_modelDir);
            Process.Start(new ProcessStartInfo("explorer.exe", $"\"{_modelDir}\"") { UseShellExecute = true });
        }
        catch (Exception ex) { App.Log("Open model folder", ex); }
    }
}
