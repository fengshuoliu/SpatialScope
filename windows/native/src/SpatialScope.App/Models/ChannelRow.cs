using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Media;

namespace SpatialScope.Windows.Models;

public sealed class ChannelRow : INotifyPropertyChanged
{
    private bool _includeInOverlay = true;
    private string _marker = string.Empty;
    private string _colorHex = "#FFFFFF";

    public string FileName { get; init; } = string.Empty;

    public bool IncludeInOverlay
    {
        get => _includeInOverlay;
        set { _includeInOverlay = value; OnPropertyChanged(); }
    }

    public string Marker
    {
        get => _marker;
        set { _marker = value; OnPropertyChanged(); }
    }

    public string ColorHex
    {
        get => _colorHex;
        set
        {
            _colorHex = value;
            OnPropertyChanged();
            OnPropertyChanged(nameof(ColorBrush));
        }
    }

    public Brush ColorBrush => ParseColor(_colorHex);

    public event PropertyChangedEventHandler? PropertyChanged;
    private void OnPropertyChanged([CallerMemberName] string? propertyName = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));

    internal static Brush ParseColor(string value)
    {
        try
        {
            var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(value));
            brush.Freeze();
            return brush;
        }
        catch
        {
            return Brushes.Transparent;
        }
    }
}

public sealed class CellTypeRow : INotifyPropertyChanged
{
    private string _name = string.Empty;
    private string _colorHex = "#FFFFFF";
    private string _allPositive = string.Empty;
    private string _allNegative = string.Empty;
    private string _anyPositiveGroups = string.Empty;

    public string Name { get => _name; set { _name = value; OnPropertyChanged(); } }
    public string ColorHex
    {
        get => _colorHex;
        set
        {
            _colorHex = value;
            OnPropertyChanged();
            OnPropertyChanged(nameof(ColorBrush));
        }
    }
    public Brush ColorBrush => ChannelRow.ParseColor(_colorHex);
    public string AllPositive { get => _allPositive; set { _allPositive = value; OnPropertyChanged(); } }
    public string AllNegative { get => _allNegative; set { _allNegative = value; OnPropertyChanged(); } }
    public string AnyPositiveGroups { get => _anyPositiveGroups; set { _anyPositiveGroups = value; OnPropertyChanged(); } }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void OnPropertyChanged([CallerMemberName] string? propertyName = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
}

public sealed class OutputFileRow
{
    public string Name { get; init; } = string.Empty;
    public string RelativePath { get; init; } = string.Empty;
    public long SizeBytes { get; init; }
    public string SizeText => SizeBytes switch
    {
        >= 1_073_741_824 => $"{SizeBytes / 1_073_741_824d:0.0} GB",
        >= 1_048_576 => $"{SizeBytes / 1_048_576d:0.0} MB",
        >= 1024 => $"{SizeBytes / 1024d:0.0} KB",
        _ => $"{SizeBytes} B",
    };
}
