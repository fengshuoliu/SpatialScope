using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Media;

namespace SpatialScope.Windows.Models;

public enum WorkflowStatus
{
    NotStarted,
    Ready,
    Running,
    Complete,
    Error,
}

public sealed class WorkflowSection : INotifyPropertyChanged
{
    private WorkflowStatus _status;
    private bool _isSelected;
    private string _statusDisplayText = string.Empty;

    public required string Key { get; init; }
    public required int Number { get; init; }
    public required string IconGlyph { get; init; }
    public string Title { get; set; } = string.Empty;
    public string Subtitle { get; set; } = string.Empty;

    public string StatusDisplayText
    {
        get => _statusDisplayText;
        set
        {
            if (_statusDisplayText == value) return;
            _statusDisplayText = value;
            OnPropertyChanged();
        }
    }

    public WorkflowStatus Status
    {
        get => _status;
        set
        {
            if (_status == value) return;
            _status = value;
            OnPropertyChanged();
            OnPropertyChanged(nameof(StatusBackground));
            OnPropertyChanged(nameof(StatusForeground));
            OnPropertyChanged(nameof(StatusText));
            OnPropertyChanged(nameof(StatusGlyph));
        }
    }

    public bool IsSelected
    {
        get => _isSelected;
        set
        {
            if (_isSelected == value) return;
            _isSelected = value;
            OnPropertyChanged();
            OnPropertyChanged(nameof(BorderBrush));
            OnPropertyChanged(nameof(BorderThickness));
        }
    }

    public string NumberText => Number.ToString("00");

    public Brush StatusBackground => Status switch
    {
        WorkflowStatus.Ready => BrushFrom("#E1F0F9"),
        WorkflowStatus.Running => BrushFrom("#FFF0D8"),
        WorkflowStatus.Complete => BrushFrom("#DFF2E7"),
        WorkflowStatus.Error => BrushFrom("#F9DDDD"),
        _ => BrushFrom("#EEF1F2"),
    };

    public Brush StatusForeground => Status switch
    {
        WorkflowStatus.Ready => BrushFrom("#0B6F9F"),
        WorkflowStatus.Running => BrushFrom("#9A5A00"),
        WorkflowStatus.Complete => BrushFrom("#17653A"),
        WorkflowStatus.Error => BrushFrom("#9A2828"),
        _ => BrushFrom("#526168"),
    };

    public Brush BorderBrush => IsSelected ? BrushFrom("#087E8B") : BrushFrom("#00000000");
    public double BorderThickness => IsSelected ? 1.5 : 0;

    public string StatusText => Status switch
    {
        WorkflowStatus.Ready => "Ready",
        WorkflowStatus.Running => "Running",
        WorkflowStatus.Complete => "Complete",
        WorkflowStatus.Error => "NeedsAttention",
        _ => "NotStarted",
    };

    public string StatusGlyph => Status switch
    {
        WorkflowStatus.Ready => "→",
        WorkflowStatus.Running => "●",
        WorkflowStatus.Complete => "✓",
        WorkflowStatus.Error => "!",
        _ => "○",
    };

    public event PropertyChangedEventHandler? PropertyChanged;

    public void RefreshText() => OnPropertyChanged(string.Empty);

    private static SolidColorBrush BrushFrom(string value)
    {
        var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(value));
        brush.Freeze();
        return brush;
    }

    private void OnPropertyChanged([CallerMemberName] string? propertyName = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
}
