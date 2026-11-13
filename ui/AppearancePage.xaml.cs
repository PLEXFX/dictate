using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DictateUI;

public sealed partial class AppearancePage : Page
{
    private bool _loading = true;

    public AppearancePage()
    {
        InitializeComponent();
        Reload();
        Loaded += (_, _) => { App.Link.SettingsChanged -= Reload; App.Link.SettingsChanged += Reload; };
        Unloaded += (_, _) => App.Link.SettingsChanged -= Reload;
    }

    private void Reload()
    {
        _loading = true;
        var s = App.Link.Settings;
        ThemeBox.SelectedIndex = s.ThemeMode switch { "light" => 1, "dark" => 2, _ => 0 };
        NoticeToggle.IsOn = s.SystemNotificationsEnabled;
        _loading = false;
    }

    // The window re-themes at once; the choice saves a moment later.
    private void OnThemeChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading || ThemeBox.SelectedItem is not ComboBoxItem item) return;
        App.Link.Settings.ThemeMode = (string)item.Tag;
        App.RaiseThemeModeChanged();
        App.QueueSave();
    }

    private void OnNoticeToggled(object sender, RoutedEventArgs e)
    {
        if (_loading) return;
        App.Link.Settings.SystemNotificationsEnabled = NoticeToggle.IsOn;
        App.QueueSave();
    }
}
