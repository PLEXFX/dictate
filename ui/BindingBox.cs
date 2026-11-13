using Microsoft.UI.Input;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.System;

namespace DictateUI;

// Shows a key binding ("F9", "Ctrl + Alt + D"). Click it, hold the keys or mouse buttons you want together, release:
// the combination is stored. Esc or clicking away cancels. While it listens the engine's own global listener must stand
// down (CaptureActive), or pressing the talk key would start recording.
public sealed class BindingBox : Button
{
    private readonly HashSet<string> _down = new();
    private readonly HashSet<string> _taken = new();
    private bool _capturing;
    private string _binding = "";

    public event Action<string> BindingChanged;
    public event Action<bool> CaptureActive;

    public BindingBox()
    {
        MinWidth = 140;
        HorizontalContentAlignment = HorizontalAlignment.Center;
        Click += (_, _) => Begin();
        PreviewKeyDown += OnKeyDown;
        PreviewKeyUp += OnKeyUp;
        PointerPressed += OnPointerPressed;
        PointerReleased += OnPointerReleased;
        LostFocus += (_, _) => Cancel();
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetHelpText(this, "Click to change. Press a key or mouse button, or hold inputs together for a combination.");
    }

    public string Binding
    {
        get => _binding;
        set { _binding = value ?? ""; if (!_capturing) Content = HotkeyText.Format(_binding); }
    }

    private void Begin()
    {
        if (_capturing) return;
        _capturing = true;
        _down.Clear();
        _taken.Clear();
        Content = "Press a key or mouse button";
        CaptureActive?.Invoke(true);
    }

    private void End(bool commit)
    {
        if (!_capturing) return;
        _capturing = false;
        if (commit && _taken.Count > 0)
        {
            string combo = HotkeyText.Canonical(_taken);
            if (Settings.ValidCombo(combo) && combo != _binding)
            {
                _binding = combo;
                BindingChanged?.Invoke(combo);
            }
        }
        Content = HotkeyText.Format(_binding);
        CaptureActive?.Invoke(false);
    }

    private void Cancel() => End(false);

    private void Add(string name)
    {
        if (name.Length == 0) return;
        _down.Add(name);
        _taken.Add(name);
    }

    private void Remove(string name)
    {
        _down.Remove(name);
        if (_capturing && _down.Count == 0) End(true);
    }

    private void OnKeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (!_capturing) return;
        e.Handled = true;
        if (e.Key == VirtualKey.Escape) { Cancel(); return; }
        Add(HotkeyText.FromKey(e.Key));
    }

    private void OnKeyUp(object sender, KeyRoutedEventArgs e)
    {
        if (!_capturing) return;
        e.Handled = true;
        Remove(HotkeyText.FromKey(e.Key));
    }

    private void OnPointerPressed(object sender, PointerRoutedEventArgs e)
    {
        if (!_capturing) return;
        var p = e.GetCurrentPoint(this).Properties;
        if (p.IsXButton1Pressed) Add("mouse4");
        else if (p.IsXButton2Pressed) Add("mouse5");
        else if (p.IsMiddleButtonPressed) Add("mouse3");
        else if (p.IsRightButtonPressed) Add("mouse2");
        else return; // a left click on the box itself only starts the capture
        e.Handled = true;
    }

    private void OnPointerReleased(object sender, PointerRoutedEventArgs e)
    {
        if (!_capturing) return;
        foreach (var name in new[] { "mouse2", "mouse3", "mouse4", "mouse5" })
            if (_down.Contains(name)) { Remove(name); e.Handled = true; return; }
    }
}
