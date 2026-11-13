using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Foundation;

namespace DictateUI;

// One row of a settings group, the way Windows Settings lays it out: title and an optional line of explanation on the
// left, the control (the row's Content) on the right. When the row gets too narrow the control drops below the text so
// nothing clips. 16 px across, 12 px down, as DESIGN.md asks.
public sealed class SettingsRow : ContentControl
{
    public static readonly DependencyProperty TitleProperty = Prop(nameof(Title), typeof(string), "");
    public static readonly DependencyProperty DescriptionProperty = Prop(nameof(Description), typeof(string), "");
    public static readonly DependencyProperty IconProperty = Prop(nameof(Icon), typeof(UIElement), null);
    public static readonly DependencyProperty DetailProperty = Prop(nameof(Detail), typeof(UIElement), null);
    public static readonly DependencyProperty DividerProperty = Prop(nameof(Divider), typeof(bool), false);

    private static DependencyProperty Prop(string name, Type type, object def) =>
        DependencyProperty.Register(name, type, typeof(SettingsRow), new PropertyMetadata(def, (d, _) => ((SettingsRow)d).Refresh()));

    public string Title { get => (string)GetValue(TitleProperty); set => SetValue(TitleProperty, value); }
    public string Description { get => (string)GetValue(DescriptionProperty); set => SetValue(DescriptionProperty, value); }
    public UIElement Icon { get => (UIElement)GetValue(IconProperty); set => SetValue(IconProperty, value); }
    public UIElement Detail { get => (UIElement)GetValue(DetailProperty); set => SetValue(DetailProperty, value); }
    public bool Divider { get => (bool)GetValue(DividerProperty); set => SetValue(DividerProperty, value); }

    private Grid _root;
    private ContentPresenter _icon, _detail, _slot;
    private UIElement _divider, _desc;
    private bool _narrow;

    public SettingsRow()
    {
        DefaultStyleKey = typeof(SettingsRow);
        IsTabStop = false;
        SizeChanged += (_, _) => UpdateMode();
    }

    protected override void OnApplyTemplate()
    {
        base.OnApplyTemplate();
        _root = GetTemplateChild("RootGrid") as Grid;
        _icon = GetTemplateChild("IconHost") as ContentPresenter;
        _detail = GetTemplateChild("DetailHost") as ContentPresenter;
        _slot = GetTemplateChild("SlotHost") as ContentPresenter;
        _divider = GetTemplateChild("DividerLine") as UIElement;
        _desc = GetTemplateChild("DescText") as UIElement;
        Refresh();
    }

    private void Refresh()
    {
        if (_root == null) return;
        _desc.Visibility = string.IsNullOrEmpty(Description) ? Visibility.Collapsed : Visibility.Visible;
        _icon.Visibility = Icon == null ? Visibility.Collapsed : Visibility.Visible;
        _detail.Visibility = Detail == null ? Visibility.Collapsed : Visibility.Visible;
        _divider.Visibility = Divider ? Visibility.Visible : Visibility.Collapsed;
        UpdateMode();
    }

    private void UpdateMode()
    {
        if (_slot == null || Content == null) return;
        _slot.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
        double needed = _slot.DesiredSize.Width + 16 + 16 + 240 + (Icon == null ? 0 : 40);
        bool narrow = ActualWidth > 0 && ActualWidth < needed;
        if (narrow == _narrow) return;
        _narrow = narrow;
        if (narrow)
        {
            Grid.SetRow(_slot, 2);
            Grid.SetColumn(_slot, 1);
            Grid.SetColumnSpan(_slot, 2);
            _slot.HorizontalAlignment = HorizontalAlignment.Left;
            _slot.Margin = new Thickness(16, 0, 16, 12);
        }
        else
        {
            Grid.SetRow(_slot, 0);
            Grid.SetColumn(_slot, 2);
            Grid.SetColumnSpan(_slot, 1);
            _slot.HorizontalAlignment = HorizontalAlignment.Right;
            _slot.Margin = new Thickness(0, 8, 16, 8);
        }
    }
}

// The rounded frame around a run of rows. It draws the thin line between the rows that are showing.
public sealed class SettingsGroup : StackPanel
{
    private readonly HashSet<SettingsRow> _watched = new();

    public SettingsGroup() => Loaded += (_, _) => Refresh();

    public void Refresh()
    {
        bool first = true;
        foreach (var child in Children)
        {
            if (child is not SettingsRow row) continue;
            if (_watched.Add(row)) row.RegisterPropertyChangedCallback(VisibilityProperty, (_, _) => Refresh());
            if (row.Visibility == Visibility.Collapsed) continue;
            row.Divider = !first;
            first = false;
        }
    }
}
