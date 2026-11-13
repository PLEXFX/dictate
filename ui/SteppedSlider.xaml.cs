using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Data;

namespace DictateUI;

// A slider that moves through a fixed list of values and always shows the default, as the Python ValueSlider did.
public sealed partial class SteppedSlider : UserControl
{
    private int[] _values = { 0 };
    private int _default;
    private string _suffix = "";
    private bool _quiet;

    public event Action<int> ValueChanged;

    public SteppedSlider()
    {
        InitializeComponent();
        Track.ThumbToolTipValueConverter = new StepLabel(this);
    }

    public void Configure(int[] values, int value, int def, string suffix)
    {
        _values = values;
        _default = def;
        _suffix = suffix;
        Track.Maximum = values.Length - 1;
        SetValue(value);
    }

    public int Value => _values[(int)Math.Round(Track.Value)];

    public void SetValue(int value)
    {
        int nearest = 0;
        for (int i = 1; i < _values.Length; i++)
            if (Math.Abs(_values[i] - value) < Math.Abs(_values[nearest] - value)) nearest = i;
        _quiet = true;
        Track.Value = nearest;
        _quiet = false;
        UpdateLabel();
    }

    internal string Format(int index) => $"{_values[Math.Clamp(index, 0, _values.Length - 1)]}{_suffix}";

    private void UpdateLabel()
    {
        string now = Format((int)Math.Round(Track.Value));
        ValueText.Text = Value == _default ? $"{now} · Default" : $"{now} · Default: {_default}{_suffix}";
    }

    private void OnTrackChanged(object sender, RangeBaseValueChangedEventArgs e)
    {
        UpdateLabel();
        if (!_quiet) ValueChanged?.Invoke(Value);
    }

    private sealed class StepLabel : IValueConverter
    {
        private readonly SteppedSlider _owner;
        public StepLabel(SteppedSlider owner) => _owner = owner;
        public object Convert(object value, Type targetType, object parameter, string language) =>
            _owner.Format((int)Math.Round((double)value));
        public object ConvertBack(object value, Type targetType, object parameter, string language) => throw new NotSupportedException();
    }
}
