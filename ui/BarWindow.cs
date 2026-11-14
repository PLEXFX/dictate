using System.Diagnostics;
using Microsoft.UI;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Input;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;
using Windows.Graphics;
using Windows.UI;
using WinRT.Interop;
using Shapes = Microsoft.UI.Xaml.Shapes;

namespace DictateUI;

// The activity bar: a 36 px capsule centred above the taskbar. Port of bar.py's behaviour (same states, same timings, same
// signals), drawn with XAML shapes. Differences on purpose: no shadow, no gradient on the loading segments, and the bars
// settle on their value without overshooting (no spring bounce).
//
// One hairline runs the width of the bar. Listening grows capsules out of it from a scrolling history of how loud you have
// been; loading sinks them back and runs two flat segments along it (or fills it, once a download reports bytes);
// transcribing undulates them; done, ready, error and notices show an icon and one line of text.
// The window never takes focus and is click-through, except while a locked recording or an actionable notice is armed.
public sealed class BarWindow : Window
{
    // Logical pixels, as in bar.py.
    private const int N = 21;
    private const double PAD = 18, PILL_H = 36, BAR_W = 4, MAX_BAR_H = PILL_H - 12;
    private const double TRACK_H = 2, SWEEP_H = 3, LIFT_FADE_PX = 4;
    private const double PILL_W_MIN = 145, PILL_W_MAX = 420;
    private const double EDGE = 2, STRIP = 6, RISE_PX = 6; // room round the capsule; strip under it for the entrance rise
    private const string DefaultErrorText = "Something isn't working";

    // Motion, in seconds. One clock for every signal so a dropped frame costs a frame and nothing else.
    private const double MORPH_SECONDS = 0.15, STAGGER_SECONDS = 0.002, SLICE_SECONDS = 0.07, BURST_TAU = 0.24, SWEEP_TAU = 0.07;
    private const double IDLE_SQUISH_SECONDS = 0.16, MAX_DT = 0.025, CONTENT_TAU = 0.045;
    private const double TRANSCRIBE_HZ = 0.75, TRANSCRIBE_HARMONIC = 2.0, TRANSCRIBE_FLOOR = 0.10, TRANSCRIBE_SWING = 0.32;
    private const double SWEEP_HZ = 0.55, ERROR_HZ = 1.15;
    private const double SPRING_OMEGA = 58, WIDTH_OMEGA = 14, MARGIN_OMEGA = 18, LOUD_OMEGA = 14, PRESS_OMEGA = 90;
    private const double PRESS_DIP = 0.97;
    private const double ENTER_SECONDS = 0.16, EXIT_SECONDS = 0.12;
    private static readonly (double Phase, double Length, double Speed)[] SweepSegments = { (0.00, 0.32, 1.00), (0.55, 0.16, 1.18) };
    private static readonly string[] MessageStates = { "done", "loaded", "error", "notice" };

    private static readonly Color Success = Color.FromArgb(255, 108, 203, 95);
    private static readonly Color Error = Color.FromArgb(255, 255, 92, 92);

    private sealed class ClickGrid : Grid
    {
        public void SetCursor(InputCursor cursor) => ProtectedCursor = cursor;
    }

    private sealed class MessageGroup
    {
        public Canvas Host;
        public Grid TextHost;
        public TextBlock Text;
        public double TextWidth;
    }

    private readonly nint _hwnd;
    private readonly ClickGrid _root = new() { Background = null };
    private readonly Grid _host = new() { HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top };
    private readonly Shapes.Rectangle _surface = new() { RadiusX = PILL_H / 2, RadiusY = PILL_H / 2 };
    private readonly Canvas _stage = new() { IsHitTestVisible = false };
    private readonly Canvas _wave = new();
    private readonly CompositeTransform _waveTransform = new();
    private readonly CompositeTransform _hostTransform = new();
    private readonly Shapes.Rectangle _hairline = new() { RadiusX = 1, RadiusY = 1, Height = TRACK_H };
    private readonly Shapes.Rectangle[] _bars = new Shapes.Rectangle[N];
    private readonly Shapes.Rectangle[] _segments = new Shapes.Rectangle[2];
    private readonly Shapes.Rectangle _fill = new() { RadiusX = SWEEP_H / 2, RadiusY = SWEEP_H / 2, Height = SWEEP_H };
    private readonly TextBlock _caption = new() { Text = "Downloading…", FontSize = 11, TextAlignment = Microsoft.UI.Xaml.TextAlignment.Center };
    private readonly Canvas _lock = new() { IsHitTestVisible = false }; // a padlock, shown while a recording is locked on
    private readonly Dictionary<string, MessageGroup> _messages = new();
    private readonly SolidColorBrush _surfaceBrush = new(), _trackBrush = new(), _tintBrush = new(), _textBrush = new(), _accentBrush = new();
    private readonly Dictionary<string, SolidColorBrush> _toneBrush = new();
    private readonly DispatcherQueueTimer _hideTimer;
    private readonly Stopwatch _clockWatch = Stopwatch.StartNew();
    private readonly UISettingsWatcher _ui = new();

    // state
    private Settings _settings = new();
    private bool _dark = true;
    private bool _reduced;
    private string _state = "idle", _detail = "", _noticeTone = "info";
    private double? _progress;
    private int? _noticeDurationMs;
    private bool _noticeClickable, _clickable, _visible, _running;
    private Color _accent, _tint, _morphTint, _idleColor, _textColor, _surfaceColor, _trackColor, _successIcon, _errorIcon;

    private double _pillW, _widthTarget, _widthVel;
    private double _margin, _marginTarget, _marginVel;
    private double _reveal, _revealFrom, _revealTo, _revealT = 1, _revealDur = ENTER_SECONDS;
    private double _clock, _last, _sweep, _lockMix, _idleMix = 1, _loudGlow, _loudVel, _micLevel, _sliceAccum;
    private double _pressScale = 1, _pressVel, _pressTarget = 1;
    private readonly double[] _drawn = new double[N], _vel = new double[N], _target = new double[N], _burst = new double[N],
        _morphFrom = new double[N], _history = new double[N + 1];
    private double? _morphElapsed;
    private readonly Dictionary<string, double> _content = new() { ["wave"] = 1, ["done"] = 0, ["loaded"] = 0, ["error"] = 0, ["notice"] = 0 };
    private double _lastWinW, _lastWinH, _lastX, _lastY;

    public event Action Clicked;

    public BarWindow()
    {
        _hwnd = WindowNative.GetWindowHandle(this);
        SystemBackdrop = new TransparentBackdrop();
        Title = "Dictate activity bar";
        if (AppWindow.Presenter is OverlappedPresenter p)
        {
            p.SetBorderAndTitleBar(false, false);
            p.IsResizable = false;
            p.IsMaximizable = false;
            p.IsMinimizable = false;
            p.IsAlwaysOnTop = true;
        }
        Native.MakeFramelessClear(_hwnd);
        Native.NeverTakeFocus(_hwnd);
        Native.SetClickThrough(_hwnd, true);

        BuildVisuals();
        Content = _root;
        _hideTimer = DispatcherQueue.GetForCurrentThread().CreateTimer();
        _hideTimer.IsRepeating = false;
        _hideTimer.Tick += (_, _) => AutoHide();

        // A transparent (not absent) background: XAML only hit-tests what has a brush, and the window must take the click.
        _root.Background = new SolidColorBrush(Colors.Transparent);
        _root.PointerPressed += OnPressed;
        _root.PointerReleased += OnReleased;
        _root.PointerCanceled += (_, _) => _pressTarget = 1;

        UpdateSettings(new Settings());
        _pillW = _widthTarget;
        _margin = _marginTarget;
        ApplyPalette();
        ApplyGeometry(force: true);
        RenderFrame();
        // Created hidden. SW_HIDE first so showing it later never activates it.
        Native.ShowWindow(_hwnd, Native.SW_HIDE);
    }

    // --- building the visuals ---

    private void BuildVisuals()
    {
        _host.RenderTransform = _hostTransform;
        _host.RenderTransformOrigin = new Point(0.5, 0.5);
        _host.Margin = new Thickness(EDGE, EDGE, 0, 0);
        _host.Height = PILL_H;
        _surface.Fill = _surfaceBrush;
        _surface.Height = PILL_H;
        _stage.Height = PILL_H;
        _host.Children.Add(_surface);
        _host.Children.Add(_stage);

        _hairline.Fill = _trackBrush;
        _wave.RenderTransform = _waveTransform;
        _wave.Children.Add(_hairline);
        for (int i = 0; i < N; i++)
        {
            _bars[i] = new Shapes.Rectangle { Width = BAR_W, RadiusX = BAR_W / 2, RadiusY = BAR_W / 2, Fill = _tintBrush, Opacity = 0 };
            _wave.Children.Add(_bars[i]);
        }
        for (int i = 0; i < _segments.Length; i++)
        {
            _segments[i] = new Shapes.Rectangle { Height = SWEEP_H, RadiusX = SWEEP_H / 2, RadiusY = SWEEP_H / 2, Fill = _tintBrush, Visibility = Visibility.Collapsed };
            _wave.Children.Add(_segments[i]);
        }
        _fill.Fill = _tintBrush;
        _fill.Visibility = Visibility.Collapsed;
        _wave.Children.Add(_fill);
        _stage.Children.Add(_wave);

        _caption.Foreground = _textBrush;
        _caption.Visibility = Visibility.Collapsed;
        _stage.Children.Add(_caption);

        _lock.Opacity = 0;
        BuildLock();
        _stage.Children.Add(_lock);

        foreach (var name in MessageStates) _stage.Children.Add(MakeMessage(name).Host);
        _root.Children.Add(_host);
        _surface.Margin = new Thickness(0);
    }

    private void BuildLock()
    {
        // Body 9 x 7 with rounded corners, shackle an arc above it, both in the accent.
        var body = new Shapes.Rectangle { Width = 9, Height = 7, RadiusX = 2, RadiusY = 2, Stroke = _accentBrush, StrokeThickness = 1.5 };
        Canvas.SetTop(body, 6);
        var fig = new PathFigure { StartPoint = new Point(2, 6), IsClosed = false };
        fig.Segments.Add(new ArcSegment { Point = new Point(7, 6), Size = new Size(2.5, 4.5), SweepDirection = SweepDirection.Clockwise });
        var geometry = new PathGeometry();
        geometry.Figures.Add(fig);
        var shackle = new Shapes.Path
        {
            Data = geometry, Stroke = _accentBrush, StrokeThickness = 1.5,
            StrokeStartLineCap = Microsoft.UI.Xaml.Media.PenLineCap.Round, StrokeEndLineCap = Microsoft.UI.Xaml.Media.PenLineCap.Round,
        };
        _lock.Children.Add(body);
        _lock.Children.Add(shackle);
    }

    private MessageGroup MakeMessage(string name)
    {
        var host = new Canvas { Opacity = 0, Visibility = Visibility.Collapsed, IsHitTestVisible = false };
        var text = new TextBlock
        {
            FontSize = 13, TextTrimming = TextTrimming.CharacterEllipsis, TextWrapping = TextWrapping.NoWrap,
            VerticalAlignment = VerticalAlignment.Center, Foreground = _textBrush,
        };
        var textHost = new Grid { Height = 24 };
        textHost.Children.Add(text);
        Canvas.SetLeft(textHost, 25);
        Canvas.SetTop(textHost, -12);

        var tone = new SolidColorBrush();
        _toneBrush[name] = tone;
        switch (name)
        {
            case "done":
            case "loaded":
                host.Children.Add(new Shapes.Polyline
                {
                    Points = new PointCollection { new Point(0, 0), new Point(4, 4), new Point(12, -5) },
                    Stroke = tone, StrokeThickness = 1.8, StrokeLineJoin = PenLineJoin.Round,
                    StrokeStartLineCap = Microsoft.UI.Xaml.Media.PenLineCap.Round, StrokeEndLineCap = Microsoft.UI.Xaml.Media.PenLineCap.Round,
                });
                break;
            case "error":
                var ring = new Shapes.Ellipse { Width = 12, Height = 12, Stroke = tone, StrokeThickness = 1.8 };
                Canvas.SetLeft(ring, 0);
                Canvas.SetTop(ring, -6);
                host.Children.Add(ring);
                host.Children.Add(new Shapes.Line
                {
                    X1 = 6, Y1 = -3, X2 = 6, Y2 = 0, Stroke = tone, StrokeThickness = 1.8,
                    StrokeStartLineCap = Microsoft.UI.Xaml.Media.PenLineCap.Round, StrokeEndLineCap = Microsoft.UI.Xaml.Media.PenLineCap.Round,
                });
                var dot = new Shapes.Ellipse { Width = 1.8, Height = 1.8, Fill = tone };
                Canvas.SetLeft(dot, 5.1);
                Canvas.SetTop(dot, 2.1);
                host.Children.Add(dot);
                break;
            default: // notice
                var disc = new Shapes.Ellipse { Width = 8, Height = 8, Fill = tone };
                Canvas.SetLeft(disc, 2);
                Canvas.SetTop(disc, -4);
                host.Children.Add(disc);
                break;
        }
        host.Children.Add(textHost);
        var group = new MessageGroup { Host = host, TextHost = textHost, Text = text };
        _messages[name] = group;
        return group;
    }

    // --- what the engine asks for ---

    public void SetTheme(bool dark)
    {
        if (dark == _dark) return;
        _dark = dark;
        ApplyPalette();
        if (_visible) Start();
    }

    public void UpdateSettings(Settings settings)
    {
        _settings = settings;
        _accent = AccentColor.Get(_dark); // the Windows accent may have changed while this ran
        ApplyPalette();
        if (Math.Abs(_widthTarget - StateWidth()) >= 0.5 || _widthTarget == 0) AnimateWidth(StateWidth());
        if (Math.Abs(_marginTarget - settings.BarMargin) >= 0.5 || _marginTarget == 0) AnimateMargin(settings.BarMargin);
        if (_state == "idle")
        {
            if (settings.AlwaysVisible) { _hideTimer.Stop(); ShowBar(); }
            else if (_visible) { _hideTimer.Stop(); AutoHide(); }
        }
    }

    public void PreviewWidth(int width)
    {
        AnimateWidth(width);
        if (_state == "idle") ShowBar();
        RestartHide(3000);
    }

    public void PreviewMargin(int margin)
    {
        AnimateMargin(margin);
        if (_state == "idle") ShowBar();
        RestartHide(3000);
    }

    public void Notify(string text, string tone, bool clickable, int? durationMs)
    {
        _noticeTone = tone;
        _noticeDurationMs = durationMs;
        _noticeClickable = clickable;
        RefreshMouse();
        SetState("notice", text, null);
    }

    public void SetClickable(bool clickable)
    {
        _clickable = clickable;
        RefreshMouse();
    }

    public void SetLevel(double level) => _micLevel = Math.Min(1, Math.Max(0, level));

    public void SetState(string state, string detail, double? progress)
    {
        if (state == _state)
        {
            _detail = detail ?? "";
            _progress = progress;
            RefreshMessageText();
            AnimateWidth(StateWidth());
            if (_visible) Start();
            return;
        }
        ApplyState(state, detail ?? "", progress);
    }

    private void ApplyState(string state, string detail, double? progress)
    {
        if (state != "notice" && _noticeClickable)
        {
            _noticeClickable = false; // whatever the last notice offered is no longer on screen
            RefreshMouse();
        }
        if (state != _state)
        {
            Array.Copy(_drawn, _morphFrom, N);
            _morphTint = _tint;
            _morphElapsed = 0;
            if (Array.IndexOf(MessageStates, state) >= 0 && Array.IndexOf(MessageStates, _state) >= 0)
                foreach (var key in _content.Keys.ToList()) _content[key] = key == state ? 1 : 0; // a clean swap, not a cross-dissolve
        }
        _state = state;
        _detail = detail;
        _progress = progress;
        RefreshMessageText();
        AnimateWidth(StateWidth());

        switch (state)
        {
            case "listening":
                Array.Clear(_history);
                _micLevel = 0;
                _sliceAccum = 0;
                _hideTimer.Stop();
                ShowBar();
                break;
            case "transcribing":
            case "loading":
                _hideTimer.Stop();
                ShowBar();
                break;
            case "done":
            case "empty":
            case "error":
            case "loaded":
            case "notice":
                for (int i = 0; i < N; i++) _burst[i] = 1 - Math.Pow(CentreDistance(i), 1.5);
                ShowBar();
                int hideAfter = state == "error" ? 2600 : state == "notice" ? (_noticeDurationMs ?? 3600) : _settings.BarLingerMs;
                RestartHide(hideAfter);
                break;
            case "idle":
                if (_settings.AlwaysVisible) ShowBar();
                else RestartHide(600);
                break;
        }
        if (_visible) Start();
    }

    private static double CentreDistance(int i) => Math.Abs(i - (N - 1) / 2.0) / ((N - 1) / 2.0);

    // --- sizes ---

    private double StateWidth()
    {
        switch (_state)
        {
            case "done": return 145;
            case "error": return LabelWidth(string.IsNullOrEmpty(_detail) ? DefaultErrorText : _detail, true);
            case "notice": return LabelWidth(_detail, true);
            case "loaded": return LabelWidth(string.IsNullOrEmpty(_detail) ? "Ready" : _detail, true);
            case "loading" when _progress != null && !string.IsNullOrEmpty(_detail): return LabelWidth(_detail, false);
        }
        return _settings.BarWidth;
    }

    // A width that fits the text on one line with room to breathe, so a real message is never cut to a canned label.
    private double LabelWidth(string text, bool hasIcon)
    {
        double textW = MeasureText(text, hasIcon ? 13 : 12);
        double width = textW + (hasIcon ? 25 : 0) + 2 * PAD + 16;
        return Math.Max(PILL_W_MIN, Math.Min(PILL_W_MAX, width));
    }

    private static double MeasureText(string text, double size)
    {
        var tb = new TextBlock { Text = text ?? "", FontSize = size, TextWrapping = TextWrapping.NoWrap };
        tb.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
        return tb.DesiredSize.Width;
    }

    private void RefreshMessageText()
    {
        string Label(string name) => name switch
        {
            "done" => "Inserted",
            "loaded" => string.IsNullOrEmpty(_detail) ? "Ready" : _detail,
            "error" => string.IsNullOrEmpty(_detail) ? DefaultErrorText : _detail,
            _ => _detail ?? "",
        };
        foreach (var name in MessageStates)
        {
            var g = _messages[name];
            string text = Label(name);
            if (g.Text.Text == text && g.TextWidth > 0) continue;
            g.Text.Text = text;
            g.TextWidth = MeasureText(text, 13);
        }
    }

    private double WaveWidth => Math.Max(1, _pillW - 2 * PAD);

    // --- width and margin ---

    private void AnimateWidth(double width)
    {
        _widthTarget = Math.Min(PILL_W_MAX, Math.Max(PILL_W_MIN, width));
        if (_reduced) { _pillW = _widthTarget; _widthVel = 0; ApplyGeometry(); return; }
        if (!_visible) { _pillW = _widthTarget; _widthVel = 0; return; }
        Start();
    }

    private void AnimateMargin(double margin)
    {
        _marginTarget = Math.Max(0, margin);
        if (_reduced || !_visible) { _margin = _marginTarget; _marginVel = 0; ApplyGeometry(); return; }
        Start();
    }

    // The window is the capsule plus a little room: centred above the taskbar, the configured gap below the capsule.
    private void ApplyGeometry(bool force = false)
    {
        double scale = Native.GetDpiForWindow(_hwnd) / 96.0;
        if (scale < 0.5) scale = 1;
        var area = DisplayArea.Primary.WorkArea;
        double winWLogical = _pillW + 2 * EDGE;
        double winHLogical = PILL_H + 2 * EDGE + STRIP;
        int w = (int)Math.Round(winWLogical * scale), h = (int)Math.Round(winHLogical * scale);
        int x = area.X + (area.Width - w) / 2;
        double pillBottom = area.Y + area.Height - _margin * scale;
        int y = (int)Math.Round(pillBottom - (EDGE + PILL_H) * scale);
        if (!force && w == _lastWinW && h == _lastWinH && x == _lastX && y == _lastY) return;
        _lastWinW = w; _lastWinH = h; _lastX = x; _lastY = y;
        AppWindow.MoveAndResize(new RectInt32(x, y, w, h));
    }

    // --- visibility ---

    public void ShowBar()
    {
        _reduced = !_ui.AnimationsEnabled;
        if (!_visible)
        {
            _visible = true;
            _pillW = _widthTarget;
            _margin = _marginTarget;
            ApplyGeometry(force: true);
            Native.ShowWindow(_hwnd, Native.SW_SHOWNOACTIVATE);
            Native.SetWindowPos(_hwnd, new IntPtr(-1), 0, 0, 0, 0, Native.SWP_NOMOVE | Native.SWP_NOSIZE | Native.SWP_NOACTIVATE);
        }
        if (_revealTo != 1)
            StartReveal(1, _reduced ? 0 : ENTER_SECONDS);
        Start();
    }

    private void StartReveal(double to, double seconds)
    {
        _revealFrom = _reveal;
        _revealTo = to;
        _revealT = 0;
        _revealDur = Math.Max(0.0001, seconds);
        if (seconds <= 0) { _reveal = to; _revealT = 1; }
    }

    public void HideBar()
    {
        _hideTimer.Stop();
        AutoHide(force: true);
    }

    private void RestartHide(int ms)
    {
        _hideTimer.Stop();
        _hideTimer.Interval = TimeSpan.FromMilliseconds(Math.Max(1, ms));
        _hideTimer.Start();
    }

    private void AutoHide(bool force = false)
    {
        if (_settings.AlwaysVisible && !force)
        {
            ApplyState("idle", "", null);
            return;
        }
        StartReveal(0, _reduced ? 0 : EXIT_SECONDS);
        Start();
    }

    private void FinishHide()
    {
        _visible = false;
        _running = false;
        CompositionTarget.Rendering -= OnFrame;
        Native.ShowWindow(_hwnd, Native.SW_HIDE);
        // Reset while nothing is on screen, so the next appearance starts from a clean idle.
        _state = "idle";
        foreach (var key in _content.Keys.ToList()) _content[key] = key == "wave" ? 1 : 0;
        _lockMix = 0; _idleMix = 1; _sweep = 0; _widthVel = 0; _marginVel = 0; _morphElapsed = null;
        _widthTarget = _settings.BarWidth;
        _pillW = _widthTarget;
        _margin = _marginTarget;
        Array.Clear(_drawn); Array.Clear(_vel); Array.Clear(_target); Array.Clear(_burst); Array.Clear(_history); Array.Clear(_morphFrom);
        _morphTint = _idleColor;
        _tint = _idleColor;
        _noticeClickable = false;
        RefreshMouse();
    }

    // --- the click gesture ---

    private bool Armed => _clickable || _noticeClickable;

    private void RefreshMouse()
    {
        Native.SetClickThrough(_hwnd, !Armed);
        _root.SetCursor(Armed ? InputSystemCursor.Create(InputSystemCursorShape.Hand) : InputSystemCursor.Create(InputSystemCursorShape.Arrow));
    }

    private void OnPressed(object sender, PointerRoutedEventArgs e)
    {
        if (!Armed) return;
        _pressTarget = PRESS_DIP;
        _root.CapturePointer(e.Pointer);
        Start();
    }

    private void OnReleased(object sender, PointerRoutedEventArgs e)
    {
        if (!Armed) return;
        _pressTarget = 1;
        _root.ReleasePointerCapture(e.Pointer);
        var pos = e.GetCurrentPoint(_root).Position;
        if (pos.X < 0 || pos.Y < 0 || pos.X > _root.ActualWidth || pos.Y > _root.ActualHeight) return;
        if (_noticeClickable) { _noticeClickable = false; RefreshMouse(); }
        Clicked?.Invoke();
    }

    // --- the frame loop ---

    private void Start()
    {
        if (_running) return;
        _running = true;
        _last = _clockWatch.Elapsed.TotalSeconds;
        CompositionTarget.Rendering += OnFrame;
    }

    private void OnFrame(object sender, object e)
    {
        double now = _clockWatch.Elapsed.TotalSeconds;
        double dt = now - _last;
        _last = now;
        if (dt <= 0) return;
        dt = Math.Min(dt, MAX_DT);
        Tick(dt);
        RenderFrame();
        if (_visible && _revealTo == 0 && _revealT >= 1 && _reveal < 0.02) { FinishHide(); return; }
        if (Settled()) { _running = false; CompositionTarget.Rendering -= OnFrame; }
    }

    private bool Settled()
    {
        if (_state != "idle" || _morphElapsed != null || _revealT < 1) return false;
        if (_idleMix < 0.999 || _content["wave"] < 0.999 || _sweep > 0.01) return false;
        if (Math.Abs(_pillW - _widthTarget) > 0.05 || Math.Abs(_margin - _marginTarget) > 0.05) return false;
        if (Math.Abs(_pressScale - _pressTarget) > 0.002 || Math.Abs(_loudGlow) > 0.002) return false;
        for (int i = 0; i < N; i++) if (Math.Abs(_drawn[i] - _target[i]) > 0.002 || Math.Abs(_vel[i]) > 0.01) return false;
        return _revealTo == 1 || _settings.AlwaysVisible;
    }

    private void Tick(double dt)
    {
        if (!_reduced) _clock = (_clock + dt) % 86400.0;
        if (_morphElapsed == null)
            for (int i = 0; i < N; i++) _burst[i] *= Math.Exp(-dt / BURST_TAU);

        if (_state == "listening")
        {
            _sliceAccum += dt;
            while (_sliceAccum >= SLICE_SECONDS)
            {
                _sliceAccum -= SLICE_SECONDS;
                Array.Copy(_history, 1, _history, 0, N);
            }
            _history[N] = _micLevel; // the newest column keeps taking the live level, so the right end answers your voice
        }

        double wantSweep = _state == "loading" ? 1 : 0;
        _sweep += (wantSweep - _sweep) * (1 - Math.Exp(-dt / SWEEP_TAU));
        Spring(ref _pressScale, ref _pressVel, _pressTarget, PRESS_OMEGA, dt);
        Spring(ref _loudGlow, ref _loudVel, _state == "listening" ? _micLevel : 0, LOUD_OMEGA, dt);
        if (_state != "listening" && Math.Abs(_loudGlow) < 0.002) _loudGlow = 0;

        Spring(ref _pillW, ref _widthVel, _widthTarget, WIDTH_OMEGA, dt);
        if (Math.Abs(_pillW - _widthTarget) < 0.05 && Math.Abs(_widthVel) < 0.5) { _pillW = _widthTarget; _widthVel = 0; }
        Spring(ref _margin, ref _marginVel, _marginTarget, MARGIN_OMEGA, dt);
        if (Math.Abs(_margin - _marginTarget) < 0.05 && Math.Abs(_marginVel) < 0.5) { _margin = _marginTarget; _marginVel = 0; }

        var live = Signal(_state);
        if (_morphElapsed is double me)
        {
            me += dt;
            _morphElapsed = me;
            double blendSum = 0;
            for (int i = 0; i < N; i++)
            {
                double ramp = Math.Min(1, Math.Max(0, (me - i * STAGGER_SECONDS) / MORPH_SECONDS));
                double blend = ramp * ramp * (3 - 2 * ramp);
                _target[i] = _morphFrom[i] * (1 - blend) + live[i] * blend;
                blendSum += blend;
            }
            _tint = Lerp(_morphTint, TintFor(_state), blendSum / N);
            if (me >= MORPH_SECONDS + (N - 1) * STAGGER_SECONDS) _morphElapsed = null;
        }
        else
        {
            Array.Copy(live, _target, N);
            _tint = TintFor(_state);
        }

        for (int i = 0; i < N; i++) Spring(ref _drawn[i], ref _vel[i], _target[i], SPRING_OMEGA, dt);

        string contentTarget = Array.IndexOf(MessageStates, _state) >= 0 ? _state : "wave";
        double cb = _reduced ? 1 : 1 - Math.Exp(-dt / CONTENT_TAU);
        foreach (var name in _content.Keys.ToList())
            _content[name] += ((name == contentTarget ? 1 : 0) - _content[name]) * cb;
        _lockMix += ((_clickable && _state == "listening" ? 1 : 0) - _lockMix) * cb;
        double idleTarget = _state == "idle" && _morphElapsed == null ? 1 : 0;
        double ib = _reduced ? 1 : 1 - Math.Exp(-dt / IDLE_SQUISH_SECONDS);
        _idleMix += (idleTarget - _idleMix) * ib;

        if (_reduced)
        {
            Array.Copy(live, _drawn, N);
            Array.Clear(_vel);
            _pressScale = 1;
            _morphElapsed = null;
        }

        if (_revealT < 1)
        {
            _revealT = Math.Min(1, _revealT + dt / _revealDur);
            double t = _revealT;
            // entrances decelerate, exits accelerate
            double eased = _revealTo >= _revealFrom ? 1 - Math.Pow(1 - t, 3) : t * t * t;
            _reveal = _revealFrom + (_revealTo - _revealFrom) * eased;
        }
    }

    private double[] Signal(string state)
    {
        var s = new double[N];
        switch (state)
        {
            case "listening":
            {
                double frac = _sliceAccum / SLICE_SECONDS;
                for (int i = 0; i < N; i++)
                    s[i] = Math.Min(1, Math.Max(0, _history[i] * (1 - frac) + _history[i + 1] * frac));
                break;
            }
            case "transcribing":
            {
                // Two sine components crossing in opposite directions at different speeds: where they reinforce a crest
                // rises, where they cancel the line dips, so it never settles into a loop you can predict.
                double phase = _clock * TRANSCRIBE_HZ;
                for (int i = 0; i < N; i++)
                {
                    double u = i / (double)(N - 1);
                    double forward = 0.5 + 0.5 * Math.Sin(2 * Math.PI * (u - phase));
                    double back = 0.5 + 0.5 * Math.Sin(2 * Math.PI * (u * TRANSCRIBE_HARMONIC + phase * 0.55 + 0.37));
                    double mixed = 0.62 * forward + 0.38 * back;
                    double envelope = 0.58 + 0.42 * Math.Pow(Math.Max(0, Math.Sin(Math.PI * u)), 0.7);
                    s[i] = TRANSCRIBE_FLOOR + TRANSCRIBE_SWING * mixed * envelope;
                }
                break;
            }
            case "error":
            {
                double pulse = 0.5 - 0.5 * Math.Cos(2 * Math.PI * _clock * ERROR_HZ);
                Array.Fill(s, 0.22 + 0.40 * pulse);
                break;
            }
            case "done":
            case "empty":
            case "loaded":
            case "notice":
                Array.Copy(_burst, s, N);
                break;
        }
        return s; // idle and loading: collapsed to the hairline
    }

    private Color TintFor(string state)
    {
        switch (state)
        {
            case "error": return Error;
            case "done":
            case "loaded": return Success;
            case "notice": return _noticeTone == "success" ? Success : _accent;
            case "idle": return _idleColor;
            case "listening" when _loudGlow > 0.01: return AccentColor.Louder(_accent, _loudGlow);
        }
        return _accent;
    }

    // --- drawing ---

    private void ApplyPalette()
    {
        if (_accent.A == 0) _accent = AccentColor.Get(_dark);
        if (_dark)
        {
            _surfaceColor = Color.FromArgb(255, 44, 44, 44);
            _trackColor = Color.FromArgb(38, 255, 255, 255);
            _idleColor = Color.FromArgb(78, 255, 255, 255);
            _textColor = Color.FromArgb(235, 255, 255, 255);
            _successIcon = Success;
            _errorIcon = Error;
        }
        else
        {
            _surfaceColor = Color.FromArgb(255, 249, 249, 249);
            _trackColor = Color.FromArgb(38, 0, 0, 0);
            _idleColor = Color.FromArgb(92, 0, 0, 0);
            _textColor = Color.FromArgb(225, 0, 0, 0);
            _successIcon = Color.FromArgb(255, 37, 117, 44);
            _errorIcon = Color.FromArgb(255, 196, 43, 28);
        }
        _surfaceBrush.Color = _surfaceColor;
        _trackBrush.Color = _trackColor;
        _textBrush.Color = _textColor;
        _accentBrush.Color = _accent;
        if (_state == "idle") { _tint = _idleColor; _morphTint = _idleColor; }
    }

    private void RenderFrame()
    {
        ApplyGeometry();
        double reveal = _reveal;
        double pillW = _pillW;
        _host.Width = pillW;
        _surface.Width = pillW;
        _stage.Width = pillW;
        _host.Opacity = reveal;
        _hostTransform.TranslateY = RISE_PX * (1 - reveal);
        _hostTransform.ScaleX = _hostTransform.ScaleY = _pressScale;
        if (reveal <= 0.001) return;

        bool caption = _state == "loading" && _progress != null && !string.IsNullOrEmpty(_detail);
        double trackCy = PILL_H / 2 + (caption ? 7 : 0);
        double cx = pillW / 2;
        double waveAlpha = _content["wave"];
        double waveW = WaveWidth;
        double barGap = Math.Max(0, (waveW - N * BAR_W) / (N - 1));

        _wave.Opacity = waveAlpha;
        _waveTransform.CenterX = cx;
        _waveTransform.TranslateX = 9 * _lockMix;
        _waveTransform.ScaleX = (1 - 0.64 * _idleMix) * (1 - 0.16 * _lockMix);
        Canvas.SetLeft(_hairline, PAD);
        Canvas.SetTop(_hairline, trackCy - TRACK_H / 2);
        _hairline.Width = waveW;

        // Capsules growing out of the hairline; each fades in over its first few pixels of lift.
        double baseAlpha = _tint.A / 255.0;
        _tintBrush.Color = Color.FromArgb(255, _tint.R, _tint.G, _tint.B);
        for (int i = 0; i < N; i++)
        {
            double h = Math.Max(TRACK_H, _drawn[i] * MAX_BAR_H);
            double lift = Math.Min(1, (h - TRACK_H) / LIFT_FADE_PX);
            var bar = _bars[i];
            if (lift <= 0.004 || _sweep >= 0.995) { bar.Opacity = 0; continue; }
            bar.Height = h;
            Canvas.SetLeft(bar, PAD + i * (BAR_W + barGap));
            Canvas.SetTop(bar, trackCy - h / 2);
            bar.Opacity = baseAlpha * lift * (1 - _sweep);
        }

        bool determinate = _state == "loading" && _progress != null;
        for (int s = 0; s < _segments.Length; s++)
        {
            var seg = _segments[s];
            if (_sweep <= 0.005 || determinate) { seg.Visibility = Visibility.Collapsed; continue; }
            var (offset, frac, speed) = SweepSegments[s];
            double phase = (_clock * SWEEP_HZ * speed + offset) % 1.0;
            double eased = phase * phase * (3 - 2 * phase);
            double len = waveW * frac;
            double x = PAD + eased * (waveW + len) - len;
            double left = Math.Max(PAD, x), right = Math.Min(PAD + waveW, x + len);
            if (right <= left) { seg.Visibility = Visibility.Collapsed; continue; }
            seg.Visibility = Visibility.Visible;
            seg.Width = right - left;
            Canvas.SetLeft(seg, left);
            Canvas.SetTop(seg, trackCy - SWEEP_H / 2);
            seg.Opacity = baseAlpha * _sweep;
        }
        if (determinate && _sweep > 0.005)
        {
            double filled = waveW * Math.Min(1, Math.Max(0, _progress.Value));
            _fill.Visibility = filled > 0.5 ? Visibility.Visible : Visibility.Collapsed;
            _fill.Width = Math.Max(0, filled);
            Canvas.SetLeft(_fill, PAD);
            Canvas.SetTop(_fill, trackCy - SWEEP_H / 2);
            _fill.Opacity = baseAlpha * _sweep;
        }
        else _fill.Visibility = Visibility.Collapsed;

        _caption.Visibility = caption ? Visibility.Visible : Visibility.Collapsed;
        if (caption)
        {
            _caption.Width = waveW;
            Canvas.SetLeft(_caption, PAD);
            Canvas.SetTop(_caption, 4);
            _caption.Opacity = waveAlpha;
        }

        _lock.Opacity = _lockMix;
        _accentBrush.Color = _accent;
        Canvas.SetLeft(_lock, 18);
        Canvas.SetTop(_lock, PILL_H / 2 - 8);

        // Icon and one line of text, centred on the message's own width.
        double targetWave = Math.Max(1, _widthTarget - 2 * PAD);
        double maxText = Math.Max(40, targetWave - 25);
        foreach (var name in MessageStates)
        {
            var g = _messages[name];
            double weight = _content[name];
            if (weight <= 0.001) { g.Host.Visibility = Visibility.Collapsed; continue; }
            g.Host.Visibility = Visibility.Visible;
            g.Host.Opacity = weight;
            g.Text.MaxWidth = maxText;
            double textWidth = Math.Min(g.TextWidth, maxText);
            double x = cx - (textWidth + 25) / 2;
            Canvas.SetLeft(g.Host, x);
            Canvas.SetTop(g.Host, PILL_H / 2 + 3 * (1 - weight));
            var tone = _toneBrush[name];
            tone.Color = name switch
            {
                "done" or "loaded" => _successIcon,
                "error" => _errorIcon,
                _ => _noticeTone == "success" ? Success : _accent,
            };
        }
    }

    // --- math ---

    // Critically damped: reaches its target without passing it, whatever the frame time.
    private static void Spring(ref double pos, ref double vel, double target, double omega, double dt)
    {
        double offset = pos - target;
        double decay = Math.Exp(-omega * dt);
        double b = vel + omega * offset;
        pos = target + (offset + b * dt) * decay;
        vel = (vel - omega * b * dt) * decay;
    }

    private static Color Lerp(Color a, Color b, double t) => Color.FromArgb(
        (byte)Math.Round(a.A + (b.A - a.A) * t), (byte)Math.Round(a.R + (b.R - a.R) * t),
        (byte)Math.Round(a.G + (b.G - a.G) * t), (byte)Math.Round(a.B + (b.B - a.B) * t));
}

// Windows' "Animation effects" switch (Settings > Accessibility > Visual effects).
internal sealed class UISettingsWatcher
{
    private readonly Windows.UI.ViewManagement.UISettings _settings = new();
    public bool AnimationsEnabled
    {
        get { try { return _settings.AnimationsEnabled; } catch { return true; } }
    }
}
