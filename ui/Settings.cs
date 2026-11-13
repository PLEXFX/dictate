using System.Text.Json;
using System.Text.Json.Serialization;

namespace DictateUI;

// C# twin of config.Settings in the Python engine: same field names in settings.json, same defaults, same clamping.
// The engine stays the owner of the real file; this copy is what the pages edit.
public sealed class Settings
{
    public const string DefaultPttKey = "f9";
    public const string DefaultSettingsHotkey = "ctrl+alt+d";
    public const int MaxVocabularyWords = 100;
    public const int MaxVocabularyWordLength = 80;

    [JsonPropertyName("device")] public string Device { get; set; } = "cpu";
    [JsonPropertyName("model_size")] public string ModelSize { get; set; } = "small.en";
    [JsonPropertyName("sleep_enabled")] public bool SleepEnabled { get; set; } = true;
    [JsonPropertyName("sleep_after_minutes")] public double SleepAfterMinutes { get; set; } = 10.0;
    [JsonPropertyName("start_with_windows")] public bool StartWithWindows { get; set; }
    [JsonPropertyName("input_device")] public string InputDevice { get; set; } = "";
    [JsonPropertyName("ptt_key")] public string PttKey { get; set; } = DefaultPttKey;
    [JsonPropertyName("tap_to_lock")] public bool TapToLock { get; set; } = true;
    [JsonPropertyName("settings_hotkey")] public string SettingsHotkey { get; set; } = DefaultSettingsHotkey;
    [JsonPropertyName("vocabulary")] public List<string> Vocabulary { get; set; } = new();
    [JsonPropertyName("sound_cues")] public bool SoundCues { get; set; } = true;
    [JsonPropertyName("duck_other_audio")] public bool DuckOtherAudio { get; set; }
    [JsonPropertyName("auto_update_enabled")] public bool AutoUpdateEnabled { get; set; } = true;
    [JsonPropertyName("system_notifications_enabled")] public bool SystemNotificationsEnabled { get; set; }
    [JsonPropertyName("theme_mode")] public string ThemeMode { get; set; } = "system";
    [JsonPropertyName("always_visible")] public bool AlwaysVisible { get; set; }
    [JsonPropertyName("bar_width")] public int BarWidth { get; set; } = 200;
    [JsonPropertyName("bar_margin")] public int BarMargin { get; set; } = 8;
    [JsonPropertyName("bar_linger_ms")] public int BarLingerMs { get; set; } = 750;
    [JsonPropertyName("onboarding_complete")] public bool OnboardingComplete { get; set; }

    // The engine's model list, smallest first: id, display name, what choosing it costs.
    public static readonly (string Id, string Name, string Summary)[] Models =
    {
        ("tiny.en", "Tiny", "~150 MB, fastest, least accurate"),
        ("base.en", "Base", "~250 MB, fast"),
        ("small.en", "Small", "~600 MB, best balance for dictation"),
        ("medium.en", "Medium", "~1.8 GB, slower, better on hard audio"),
        ("large-v3-turbo", "Large v3 Turbo", "~1.9 GB, most accurate, GPU recommended"),
    };

    public Settings Copy() => JsonSerializer.Deserialize<Settings>(JsonSerializer.Serialize(this)) ?? new Settings();

    // Pulls every value back into something the UI can show, whatever a hand-edited file held.
    public Settings Clamped()
    {
        var o = Copy();
        var d = new Settings();
        if (o.Device != "cpu" && o.Device != "cuda") o.Device = d.Device;
        if (!Models.Any(m => m.Id == o.ModelSize)) o.ModelSize = d.ModelSize;
        o.InputDevice ??= "";
        if (!ValidCombo(o.PttKey)) o.PttKey = DefaultPttKey;
        if (!ValidCombo(o.SettingsHotkey)) o.SettingsHotkey = DefaultSettingsHotkey;
        o.Vocabulary = CleanVocabulary(o.Vocabulary);
        if (o.ThemeMode != "system" && o.ThemeMode != "light" && o.ThemeMode != "dark") o.ThemeMode = d.ThemeMode;
        o.SleepAfterMinutes = double.IsNaN(o.SleepAfterMinutes) ? d.SleepAfterMinutes : Math.Min(Math.Max(o.SleepAfterMinutes, 0.5), 240.0);
        o.BarWidth = Math.Min(Math.Max(o.BarWidth, 180), 280);
        o.BarMargin = Math.Min(Math.Max(o.BarMargin, 0), 400);
        o.BarLingerMs = Math.Min(Math.Max(o.BarLingerMs, 400), 4000);
        return o;
    }

    public static bool ValidCombo(string text) =>
        !string.IsNullOrEmpty(text) && text.Split('+').Any(p => p.Trim().Length > 0);

    public static List<string> CleanVocabulary(IEnumerable<string> words)
    {
        var cleaned = new List<string>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var word in words ?? Enumerable.Empty<string>())
        {
            if (word == null) continue;
            var text = string.Join(' ', word.Split((char[])null, StringSplitOptions.RemoveEmptyEntries));
            if (text.Length > MaxVocabularyWordLength) text = text[..MaxVocabularyWordLength];
            if (text.Length > 0 && seen.Add(text)) cleaned.Add(text);
            if (cleaned.Count == MaxVocabularyWords) break;
        }
        return cleaned;
    }
}

// Where the demo reads and writes. The real %APPDATA%\dictate\settings.json is only ever read, never written, until the
// engine is connected (the engine will own the file then).
public static class SettingsStore
{
    private static readonly JsonSerializerOptions Pretty = new()
    {
        WriteIndented = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping, // "ctrl+alt+d", not "ctrl+alt+d"
    };

    public static string RealPath => Path.Combine(
        Environment.GetEnvironmentVariable("APPDATA") ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
        "dictate", "settings.json");

    public static string DataDir => Environment.GetEnvironmentVariable("DICTATEUI_HOME")
        ?? Path.Combine(Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? Path.GetTempPath(), "Dictate UI");

    public static string DemoPath => Path.Combine(DataDir, "demo-settings.json");

    public static Settings Load()
    {
        foreach (var path in new[] { DemoPath, RealPath })
        {
            try
            {
                if (!File.Exists(path)) continue;
                var s = JsonSerializer.Deserialize<Settings>(File.ReadAllText(path));
                if (s != null) return s.Clamped();
            }
            catch { /* a bad file falls through to the next, then to defaults */ }
        }
        return new Settings();
    }

    public static void Save(Settings s)
    {
        try
        {
            Directory.CreateDirectory(DataDir);
            File.WriteAllText(DemoPath, JsonSerializer.Serialize(s, Pretty));
        }
        catch { /* the demo keeps running on what it has in memory */ }
    }
}
