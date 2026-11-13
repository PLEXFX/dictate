using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace DictateUI;

public sealed partial class UpdatesPage : Page
{
    private bool _loading = true;

    public UpdatesPage()
    {
        InitializeComponent();
        AutoToggle.IsOn = App.Link.Settings.AutoUpdateEnabled;
        NotesRow.Description = $"Read the changes included with Dictate {App.Link.Version}.";
        _loading = false;
        Loaded += (_, _) =>
        {
            App.Link.StatusChanged -= ApplyStatus; App.Link.StatusChanged += ApplyStatus;
            App.Link.SettingsChanged -= OnSettingsChanged; App.Link.SettingsChanged += OnSettingsChanged;
            ApplyStatus();
        };
        Unloaded += (_, _) => { App.Link.StatusChanged -= ApplyStatus; App.Link.SettingsChanged -= OnSettingsChanged; };
        ActualThemeChanged += (_, _) => ApplyStatus();
        ApplyStatus();
    }

    private void OnSettingsChanged()
    {
        _loading = true;
        AutoToggle.IsOn = App.Link.Settings.AutoUpdateEnabled;
        _loading = false;
        ApplyStatus();
    }

    private static Brush Res(string key) => (Brush)Application.Current.Resources[key];

    private void ApplyStatus()
    {
        var st = App.Link.Status;
        bool autoOn = AutoToggle.IsOn;
        bool busy = st.Update is UpdateState.Checking or UpdateState.Downloading or UpdateState.Verifying or UpdateState.Installing;
        bool actionable = st.Update is UpdateState.Available or UpdateState.ReadyToRestart;
        UpdateButton.IsEnabled = actionable || (autoOn && !busy);

        StatusRow.Title = st.Update switch
        {
            UpdateState.Checking => "Checking for updates",
            UpdateState.UpToDate => "You're up to date",
            UpdateState.Available => "Update available",
            UpdateState.Downloading => "Downloading update",
            UpdateState.ReadyToRestart => "Restart to finish",
            UpdateState.Verifying => "Verifying update",
            UpdateState.Installing => "Restarting Dictate",
            UpdateState.Error => "Update needs attention",
            _ => autoOn ? "You're up to date" : "Updates paused",
        };

        UpdateButton.Content = st.Update switch
        {
            UpdateState.Available => "Download update",
            UpdateState.ReadyToRestart => "Restart Dictate",
            UpdateState.Verifying => "Verifying…",
            UpdateState.Installing => "Restarting…",
            UpdateState.Checking => "Checking…",
            UpdateState.Downloading => "Downloading…",
            _ => "Check for updates",
        };

        StatusRow.Description = st.Update switch
        {
            UpdateState.Checking => "Looking for a newer official release.",
            UpdateState.UpToDate => "Dictate is running the latest available version.",
            UpdateState.Available => st.UpdateDetail.Length > 0 ? $"{st.UpdateDetail}. Nothing downloads until you choose it." : "A newer version is ready to download when you choose.",
            UpdateState.Downloading => st.UpdateDetail.Length > 0 ? st.UpdateDetail : "Downloading the installer.",
            UpdateState.ReadyToRestart => "The update is downloaded and verified. Restart when you're ready.",
            UpdateState.Verifying => "Checking the installer before restart.",
            UpdateState.Installing => "Dictate is closing to install the update.",
            UpdateState.Error => st.UpdateDetail.Length > 0 ? st.UpdateDetail : "The update could not be completed.",
            _ => autoOn ? "Dictate is running the latest version found in this session." : "Turned off. Dictate won't check GitHub for new releases.",
        };

        UpdateBar.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        if (busy)
        {
            bool known = st.Update == UpdateState.Downloading && st.UpdateProgress != null;
            UpdateBar.IsIndeterminate = !known;
            if (known) UpdateBar.Value = st.UpdateProgress.Value * 100;
        }

        MetaText.Text = $"Version {App.Link.Version} · {LastChecked(st)}";
        ApplyIcon(st, autoOn);
    }

    private static string LastChecked(EngineStatus st)
    {
        if (st.LastChecked is not DateTime t) return "Not checked yet this session";
        string clock = t.ToString("h:mm tt");
        return t.Date == DateTime.Today ? $"Last checked today at {clock}" : $"Last checked {t:MMM dd} at {clock}";
    }

    // The glyph and its colour say the state; the words beside it say it too, so colour is never the only cue.
    private void ApplyIcon(EngineStatus st, bool autoOn)
    {
        string glyph = "";
        string brush = "AccentTextFillColorPrimaryBrush";
        switch (st.Update)
        {
            case UpdateState.UpToDate: glyph = ""; brush = "SystemFillColorSuccessBrush"; break;
            case UpdateState.Idle:
                if (autoOn) { glyph = ""; brush = "SystemFillColorSuccessBrush"; }
                else brush = "TextFillColorSecondaryBrush";
                break;
            case UpdateState.Available: glyph = ""; break;
            case UpdateState.ReadyToRestart: glyph = ""; break;
            case UpdateState.Error: glyph = ""; brush = "SystemFillColorCautionBrush"; break;
        }
        StatusIcon.Glyph = glyph;
        StatusIcon.Foreground = Res(brush);
    }

    private void OnUpdateClick(object sender, RoutedEventArgs e)
    {
        switch (App.Link.Status.Update)
        {
            case UpdateState.Available: App.Link.StartUpdate(); break;
            case UpdateState.ReadyToRestart: App.Link.RestartToInstall(); break;
            default: App.Link.CheckForUpdates(); break;
        }
    }

    private void OnAutoToggled(object sender, RoutedEventArgs e)
    {
        if (_loading) return;
        App.Link.Settings.AutoUpdateEnabled = AutoToggle.IsOn;
        App.QueueSave();
        ApplyStatus();
    }

    private async void OnNotes(object sender, RoutedEventArgs e) => await WhatsNewDialog.ShowAsync(this);
}
