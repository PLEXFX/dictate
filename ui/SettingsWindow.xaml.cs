using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Xaml.Media.Imaging;
using Microsoft.UI.Xaml.Navigation;
using Windows.Graphics;
using WinRT.Interop;

namespace DictateUI;

public sealed partial class SettingsWindow : Window
{
    private static readonly Dictionary<string, Type> Pages = new()
    {
        ["dictation"] = typeof(DictationPage),
        ["activity"] = typeof(ActivityPage),
        ["appearance"] = typeof(AppearancePage),
        ["updates"] = typeof(UpdatesPage),
        ["privacy"] = typeof(PrivacyPage),
    };

    private string _chipTarget = "updates";
    private string _chipAction = "";

    private readonly bool _exitOnClose;

    // exitOnClose is true only in the demo, where this is the one window. Under the engine, closing Settings just closes
    // the window; the tray icon and the bar stay.
    public SettingsWindow(bool exitOnClose = false)
    {
        _exitOnClose = exitOnClose;
        InitializeComponent();
        Title = "Dictate settings";
        SystemBackdrop = new MicaBackdrop();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(TitleBar);

        TitleIcon.Source = new BitmapImage(new Uri(Path.Combine(AppContext.BaseDirectory, "Assets", "dictate-32.png")));
        try { AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "dictate.ico")); } catch { }

        PlaceWindow();
        VersionText.Text = $"Version {App.Link.Version}";
        ApplyTheme();
        App.ThemeModeChanged += ApplyTheme;
        App.Link.StatusChanged += RefreshChip;
        App.Link.FocusRequested += BringToFront;
        App.Link.SettingsChanged += OnSettingsChanged;
        RefreshChip();

        if (App.Demo) BuildDemoMenu();

        ContentFrame.NavigationFailed += (_, e) => { App.Log("Navigation", e.Exception); e.Handled = true; };
        Nav.SelectedItem = Nav.MenuItems[0];
        Closed += OnClosed;
    }

    private void OnSettingsChanged() { ApplyTheme(); RefreshChip(); }

    private void OnClosed(object sender, WindowEventArgs args)
    {
        // This window can come and go while the program keeps running, so it must not leave handlers behind.
        App.ThemeModeChanged -= ApplyTheme;
        App.Link.StatusChanged -= RefreshChip;
        App.Link.FocusRequested -= BringToFront;
        App.Link.SettingsChanged -= OnSettingsChanged;
        if (_exitOnClose) Environment.Exit(0);
    }

    // 860 x 740 like the Python window, kept on the screen, never smaller than 760 x 540.
    private void PlaceWindow()
    {
        double scale = Native.GetDpiForWindow(WindowNative.GetWindowHandle(this)) / 96.0;
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        int w = Math.Min((int)(860 * scale), area.Width - 40);
        int h = Math.Min((int)(740 * scale), area.Height - 40);
        AppWindow.MoveAndResize(new RectInt32(area.X + (area.Width - w) / 2, area.Y + (area.Height - h) / 2, w, h));
        if (AppWindow.Presenter is OverlappedPresenter p)
        {
            p.PreferredMinimumWidth = (int)(760 * scale);
            p.PreferredMinimumHeight = (int)(540 * scale);
        }
    }

    private void ApplyTheme()
    {
        var mode = App.Link.Settings.ThemeMode;
        Root.RequestedTheme = mode switch { "light" => ElementTheme.Light, "dark" => ElementTheme.Dark, _ => ElementTheme.Default };
        AppWindow.TitleBar.PreferredTheme = mode switch { "light" => TitleBarTheme.Light, "dark" => TitleBarTheme.Dark, _ => TitleBarTheme.UseDefaultAppMode };
    }

    private void OnNavSelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.SelectedItem is NavigationViewItem item) Show((string)item.Tag);
    }

    // Clicking the rail item you are already on still leaves a sub-page (Words I use) and returns to the main page.
    private void OnNavItemInvoked(NavigationView sender, NavigationViewItemInvokedEventArgs args)
    {
        if (args.InvokedItemContainer?.Tag is string tag) Show(tag);
    }

    private void Show(string section)
    {
        if (Pages.TryGetValue(section, out var page) && ContentFrame.CurrentSourcePageType != page)
            ContentFrame.Navigate(page, null, new SuppressNavigationTransitionInfo());
    }

    // Settings was asked for while this window is already open.
    public void BringToFront()
    {
        if (AppWindow.Presenter is OverlappedPresenter p && p.State == OverlappedPresenterState.Minimized) p.Restore();
        Activate();
    }

    public void GoTo(string section)
    {
        foreach (NavigationViewItem item in Nav.MenuItems)
            if ((string)item.Tag == section) { Nav.SelectedItem = item; return; }
    }

    // --- the rail footer ---

    private void RefreshChip()
    {
        var s = App.Link.Status;
        string title = "", detail = "", glyph = "", tone = "accent", target = "updates", action = "";
        double? progress = null;
        bool active = false, show = true;

        var busy = new List<(int Order, string Title, string Detail, double? Progress, string Target)>();
        if (s.ModelLoading)
            busy.Add((1, "Downloading model", s.ModelProgress is double mp ? $"{ModelName()} · {(int)(mp * 100)}%" : $"Preparing {ModelName()}", s.ModelProgress, "dictation"));
        if (s.GpuDownloading)
            busy.Add((2, "Installing GPU support", s.GpuProgress is double gp ? $"GPU acceleration · {(int)(gp * 100)}%" : "GPU acceleration · Working", s.GpuProgress, "dictation"));
        switch (s.Update)
        {
            case UpdateState.Checking: busy.Add((0, "Checking for updates", "Connecting to GitHub", null, "updates")); break;
            case UpdateState.Downloading: busy.Add((0, "Downloading update", s.UpdateProgress is double up ? $"Dictate · {(int)(up * 100)}%" : "Preparing download", s.UpdateProgress, "updates")); break;
            case UpdateState.Verifying: busy.Add((0, "Finishing update", "Verifying before restart", null, "updates")); break;
            case UpdateState.Installing: busy.Add((0, "Restarting Dictate", "Installing verified update", null, "updates")); break;
        }

        if (busy.Count > 0)
        {
            var first = busy.OrderBy(b => b.Order).First();
            title = first.Title;
            detail = first.Detail + (busy.Count > 1 ? $" · +{busy.Count - 1}" : "");
            progress = first.Progress;
            target = first.Target;
            active = true;
        }
        else if (s.Update == UpdateState.ReadyToRestart)
        {
            title = "Restart Dictate"; detail = "Update verified and ready"; glyph = ""; action = "restart";
        }
        else if (s.Update == UpdateState.Available)
        {
            title = "Update available"; detail = "Click to download"; glyph = ""; action = "download";
        }
        else if (s.Update == UpdateState.Error)
        {
            title = "Needs attention"; detail = string.IsNullOrEmpty(s.UpdateDetail) ? "Open Updates for details" : s.UpdateDetail;
            glyph = ""; tone = "caution";
        }
        else if (s.HasCuda && !s.GpuReady)
        {
            title = "GPU available"; detail = "Set up acceleration"; glyph = ""; target = "dictation";
        }
        else show = false;

        Chip.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
        if (!show) return;
        _chipTarget = target;
        _chipAction = action;
        ChipTitle.Text = title;
        ChipDetail.Text = detail;
        ChipDetail.Visibility = string.IsNullOrEmpty(detail) ? Visibility.Collapsed : Visibility.Visible;
        ChipIcon.Glyph = glyph;
        ChipIcon.Foreground = tone == "caution"
            ? (Brush)Application.Current.Resources["SystemFillColorCautionBrush"]
            : (Brush)Application.Current.Resources["AccentTextFillColorPrimaryBrush"];
        ChipBar.Visibility = active ? Visibility.Visible : Visibility.Collapsed;
        ChipBar.IsIndeterminate = active && progress == null;
        if (progress is double v) ChipBar.Value = v * 100;
        AutomationProperties_SetName(Chip, detail.Length > 0 ? $"{title}. {detail}" : title);
    }

    private static void AutomationProperties_SetName(DependencyObject o, string name) =>
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(o, name);

    private static string ModelName() =>
        Settings.Models.FirstOrDefault(m => m.Id == App.Link.Settings.ModelSize).Name ?? App.Link.Settings.ModelSize;

    private void OnChipClick(object sender, RoutedEventArgs e)
    {
        if (_chipAction == "download") { App.Link.StartUpdate(); return; }
        if (_chipAction == "restart") { App.Link.RestartToInstall(); return; }
        GoTo(_chipTarget);
    }

    private async void OnWhatsNew(object sender, RoutedEventArgs e) => await WhatsNewDialog.ShowAsync(Root);

    private async void OnGitHub(object sender, RoutedEventArgs e) =>
        await Windows.System.Launcher.LaunchUriAsync(new Uri("https://github.com/PLEXFX/dictate"));

    // --- made-up states, shown only in the demo run, so each look can be checked ---

    private void BuildDemoMenu()
    {
        var demo = (DemoLink)App.Link; // App.Demo is true only for the demo link
        DemoMenu.Visibility = Visibility.Visible;
        var flyout = new MenuFlyout();
        void Add(string text, Action act)
        {
            var item = new MenuFlyoutItem { Text = text };
            item.Click += (_, _) => act();
            flyout.Items.Add(item);
        }
        Add("Update: up to date", () => demo.SetUpdateState(UpdateState.UpToDate));
        Add("Update: available", () => demo.SetUpdateState(UpdateState.Available));
        Add("Update: downloading", () => demo.SetUpdateState(UpdateState.Downloading));
        Add("Update: ready to restart", () => demo.SetUpdateState(UpdateState.ReadyToRestart));
        Add("Update: error", () => demo.SetUpdateState(UpdateState.Error));
        Add("Update: idle", () => demo.SetUpdateState(UpdateState.Idle));
        flyout.Items.Add(new MenuFlyoutSeparator());
        Add("GPU: needs download", () => demo.SetGpu(true, false));
        Add("GPU: downloading", () => demo.SetGpu(true, false, true));
        Add("GPU: ready", () => demo.SetGpu(true, true));
        Add("GPU: none on this PC", () => demo.SetGpu(false, false));
        flyout.Items.Add(new MenuFlyoutSeparator());
        Add("Model download", () => demo.ShowModelDownload());
        flyout.Items.Add(new MenuFlyoutSeparator());
        Add("Bar: listening (made-up voice)", () => demo.BarListening());
        Add("Bar: locked recording (click to finish)", () => demo.BarLocked());
        Add("Bar: transcribing", () => demo.BarState("transcribing"));
        Add("Bar: loading", () => demo.BarState("loading"));
        Add("Bar: downloading 40%", () => demo.BarState("loading", "Downloading Small", 0.4));
        Add("Bar: inserted", () => demo.BarState("done"));
        Add("Bar: ready", () => demo.BarState("loaded", "Ready"));
        Add("Bar: error", () => demo.BarState("error", "No microphone detected."));
        Add("Bar: notice", () => demo.BarNotice("Ready. Hold F9 to talk.", "success", false));
        Add("Bar: update notice (click)", () => demo.BarNotice("Update 1.4.4 available. Click to open.", "info", true));
        Add("Bar: idle", () => demo.BarState("idle"));
        DemoMenu.Flyout = flyout;
    }
}
