using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace DictateUI;

public sealed partial class DictationPage : Page
{
    private static readonly int[] SleepSteps = { 1, 2, 3, 5, 7, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240 };

    private bool _loading = true;
    private bool _advanced;
    private bool _micsLoaded;

    public DictationPage()
    {
        InitializeComponent();
        FillChoices();
        WireBindings();
        Load();
        // Listen only while on screen, so a closed Settings window leaves nothing running.
        Loaded += (_, _) =>
        {
            App.Link.StatusChanged -= ApplyStatus; App.Link.StatusChanged += ApplyStatus;
            App.Link.SettingsChanged -= Load; App.Link.SettingsChanged += Load; // the engine refused or changed part of a save
            ApplyStatus();
        };
        Unloaded += (_, _) => { App.Link.StatusChanged -= ApplyStatus; App.Link.SettingsChanged -= Load; };
        ApplyStatus();
        _ = LoadMicrophonesAsync();
    }

    private static Settings S => App.Link.Settings;

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        _loading = true;
        WordsButton.Content = S.Vocabulary.Count == 0 ? "Edit words" : $"Edit words ({S.Vocabulary.Count})";
        _loading = false;
        ApplyStatus();
    }

    private void FillChoices()
    {
        DeviceBox.Items.Add(new ComboBoxItem { Content = "CPU", Tag = "cpu" });
        DeviceBox.Items.Add(new ComboBoxItem { Content = "GPU (NVIDIA)", Tag = "cuda" });
        foreach (var (id, name, summary) in Settings.Models)
            ModelBox.Items.Add(new ComboBoxItem { Content = name, Tag = id, DataContext = summary });
        SleepSlider.Configure(SleepSteps, (int)S.SleepAfterMinutes, 10, " min");
    }

    private void WireBindings()
    {
        PttBox.BindingChanged += b => { S.PttKey = b; UpdateTapText(); App.QueueSave(); };
        PttBox.CaptureActive += App.Link.CaptureActive;
        SettingsKeyBox.BindingChanged += b => { S.SettingsHotkey = b; App.QueueSave(); };
        SettingsKeyBox.CaptureActive += App.Link.CaptureActive;
        SleepSlider.ValueChanged += v => { if (_loading) return; S.SleepAfterMinutes = v; App.QueueSave(); };
    }

    private void Load()
    {
        _loading = true;
        PttBox.Binding = S.PttKey;
        SettingsKeyBox.Binding = S.SettingsHotkey;
        TapToggle.IsOn = S.TapToLock;
        DuckToggle.IsOn = S.DuckOtherAudio;
        SoundToggle.IsOn = S.SoundCues;
        StartupToggle.IsOn = S.StartWithWindows;
        SleepToggle.IsOn = S.SleepEnabled;
        SleepSlider.SetValue((int)S.SleepAfterMinutes);
        DeviceBox.SelectedIndex = S.Device == "cuda" ? 1 : 0;
        ModelBox.SelectedIndex = Math.Max(0, Array.FindIndex(Settings.Models, m => m.Id == S.ModelSize));
        WordsButton.Content = S.Vocabulary.Count == 0 ? "Edit words" : $"Edit words ({S.Vocabulary.Count})";
        UpdateTapText();
        UpdateModelText();
        _loading = false;
    }

    private async Task LoadMicrophonesAsync()
    {
        var devices = await App.Link.GetInputDevicesAsync();
        var items = new List<InputDevice> { new("", "Windows default") };
        items.AddRange(devices);
        if (!string.IsNullOrEmpty(S.InputDevice) && items.All(d => d.Key != S.InputDevice))
            items.Add(new InputDevice(S.InputDevice, "Unavailable (previous selection)"));
        _loading = true;
        MicBox.ItemsSource = items;
        MicBox.SelectedValue = S.InputDevice ?? "";
        if (MicBox.SelectedIndex < 0) MicBox.SelectedIndex = 0;
        _loading = false;
        _micsLoaded = true;
    }

    // --- text that follows the choices ---

    private void UpdateTapText() =>
        TapRow.Description = $"Tap {HotkeyText.Format(S.PttKey)} again to finish. Esc cancels.";

    private void UpdateModelText()
    {
        var summary = Settings.Models.FirstOrDefault(m => m.Id == S.ModelSize).Summary;
        ModelRow.Description = summary ?? "Which speech model to use.";
    }

    private void UpdateDeviceText()
    {
        var st = App.Link.Status;
        if (st.HasCuda && !st.GpuReady)
            DeviceRow.Description = "Set up GPU acceleration in Advanced settings to use your graphics card.";
        else if (S.Device == "cuda" && st.GpuName.Length > 0 && st.VramMb > 0)
            DeviceRow.Description = st.GpuName;
        else
            DeviceRow.Description = st.CpuName.Length > 0 ? st.CpuName : "Where Dictate runs the speech model.";
    }

    // --- what the engine is doing ---

    private void ApplyStatus()
    {
        var st = App.Link.Status;

        // GPU stays unselectable until its runtime is on disk.
        bool gpuSelectable = st.HasCuda && st.GpuReady;
        var gpuItem = (ComboBoxItem)DeviceBox.Items[1];
        gpuItem.IsEnabled = gpuSelectable;
        ToolTipService.SetToolTip(gpuItem, gpuSelectable ? null
            : st.HasCuda ? "Set up GPU acceleration first to use this option." : "No supported NVIDIA GPU was found on this PC.");
        UpdateDeviceText();

        bool busy = st.ModelBusy && st.ModelProgress != null;
        ModelDownloadRow.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        if (busy)
        {
            ModelDownloadBar.Value = st.ModelProgress.Value * 100;
            ModelDownloadRow.Description = st.ModelDetail;
        }
        TranscriptionGroup.Refresh();

        bool needs = st.HasCuda && !st.GpuReady;
        bool show = needs || st.GpuDownloading;
        GpuRow.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
        GpuBar.Visibility = st.GpuDownloading ? Visibility.Visible : Visibility.Collapsed;
        if (st.GpuDownloading)
        {
            GpuButton.IsEnabled = false;
            GpuButton.Content = "Downloading…";
            GpuBar.IsIndeterminate = st.GpuProgress == null;
            if (st.GpuProgress is double g)
            {
                GpuBar.Value = g * 100;
                GpuRow.Description = $"Downloading {(int)(g * 100)}%. Dictate keeps using CPU until this finishes.";
            }
        }
        else if (needs)
        {
            GpuButton.IsEnabled = true;
            if (st.GpuError.Length > 0) { GpuButton.Content = "Try again"; GpuRow.Description = st.GpuError; }
            else
            {
                GpuButton.Content = "Download now";
                GpuRow.Description = "Not downloaded yet. Dictate keeps using CPU until this finishes. About 1.3 GB, downloaded once in the background.";
            }
        }
        GpuGroup.Refresh();
    }

    // --- changes the person makes ---

    private void OnMicChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading || !_micsLoaded || MicBox.SelectedValue is not string key) return;
        S.InputDevice = key;
        App.QueueSave();
    }

    private void OnTapToggled(object sender, RoutedEventArgs e) { if (!_loading) { S.TapToLock = TapToggle.IsOn; App.QueueSave(); } }
    private void OnDuckToggled(object sender, RoutedEventArgs e) { if (!_loading) { S.DuckOtherAudio = DuckToggle.IsOn; App.QueueSave(); } }
    private void OnSoundToggled(object sender, RoutedEventArgs e) { if (!_loading) { S.SoundCues = SoundToggle.IsOn; App.QueueSave(); } }
    private void OnStartupToggled(object sender, RoutedEventArgs e) { if (!_loading) { S.StartWithWindows = StartupToggle.IsOn; App.QueueSave(); } }
    private void OnSleepToggled(object sender, RoutedEventArgs e) { if (!_loading) { S.SleepEnabled = SleepToggle.IsOn; App.QueueSave(); } }

    private void OnDeviceChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading || DeviceBox.SelectedItem is not ComboBoxItem item) return;
        S.Device = (string)item.Tag;
        UpdateDeviceText();
        App.QueueSave();
    }

    private void OnModelChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading || ModelBox.SelectedItem is not ComboBoxItem item) return;
        S.ModelSize = (string)item.Tag;
        UpdateModelText();
        App.QueueSave();
    }

    private void OnEditWords(object sender, RoutedEventArgs e) => Frame.Navigate(typeof(VocabularyPage), null, new Microsoft.UI.Xaml.Media.Animation.SuppressNavigationTransitionInfo());

    private void OnAdvancedClick(object sender, RoutedEventArgs e)
    {
        _advanced = !_advanced;
        AdvancedPanel.Visibility = _advanced ? Visibility.Visible : Visibility.Collapsed;
        AdvancedChevron.Glyph = _advanced ? "" : "";
    }

    private void OnGpuClick(object sender, RoutedEventArgs e) => App.Link.StartGpuDownload();
    private void OnOpenModels(object sender, RoutedEventArgs e) => App.Link.OpenModelFolder();
}
