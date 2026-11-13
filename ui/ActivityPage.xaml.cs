using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DictateUI;

public sealed partial class ActivityPage : Page
{
    private bool _loading = true;

    public ActivityPage()
    {
        InitializeComponent();
        var s = App.Link.Settings;
        WidthSlider.Configure(new[] { 180, 190, 200, 210, 220, 240, 260, 280 }, s.BarWidth, 200, " px");
        MarginSlider.Configure(new[] { 0, 2, 4, 6, 8, 10, 12, 16, 20, 24, 28, 32, 40, 48, 56, 64, 80, 96 }, s.BarMargin, 8, " px");
        LingerSlider.Configure(new[] { 500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000 }, s.BarLingerMs, 750, " ms");
        VisibleToggle.IsOn = s.AlwaysVisible;

        // While a slider moves, the real bar follows it live; the value saves a moment after it stops.
        WidthSlider.ValueChanged += v => { if (_loading) return; App.Link.Settings.BarWidth = v; App.Link.PreviewBarWidth(v); App.QueueSave(); };
        MarginSlider.ValueChanged += v => { if (_loading) return; App.Link.Settings.BarMargin = v; App.Link.PreviewBarMargin(v); App.QueueSave(); };
        LingerSlider.ValueChanged += v => { if (_loading) return; App.Link.Settings.BarLingerMs = v; App.QueueSave(); };
        // Listen only while on screen, so a closed Settings window leaves nothing running.
        Loaded += (_, _) => { App.Link.SettingsChanged -= Reload; App.Link.SettingsChanged += Reload; };
        Unloaded += (_, _) => App.Link.SettingsChanged -= Reload;
        _loading = false;
    }

    private void Reload()
    {
        _loading = true;
        var s = App.Link.Settings;
        WidthSlider.SetValue(s.BarWidth);
        MarginSlider.SetValue(s.BarMargin);
        LingerSlider.SetValue(s.BarLingerMs);
        VisibleToggle.IsOn = s.AlwaysVisible;
        _loading = false;
    }

    private void OnVisibleToggled(object sender, RoutedEventArgs e)
    {
        if (_loading) return;
        App.Link.Settings.AlwaysVisible = VisibleToggle.IsOn;
        App.QueueSave();
    }
}
