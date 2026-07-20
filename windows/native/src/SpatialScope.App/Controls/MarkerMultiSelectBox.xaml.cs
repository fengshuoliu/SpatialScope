using System.Collections;
using System.Collections.ObjectModel;
using System.Collections.Specialized;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace SpatialScope.Windows.Controls;

public partial class MarkerMultiSelectBox : UserControl
{
    private sealed class MarkerOption(string name) : INotifyPropertyChanged
    {
        private bool _isSelected;

        public string Name { get; } = name;

        public bool IsSelected
        {
            get => _isSelected;
            set
            {
                if (_isSelected == value) return;
                _isSelected = value;
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsSelected)));
            }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
    }

    public static readonly DependencyProperty AvailableMarkersProperty = DependencyProperty.Register(
        nameof(AvailableMarkers),
        typeof(IEnumerable),
        typeof(MarkerMultiSelectBox),
        new PropertyMetadata(null, AvailableMarkersChanged));

    public static readonly DependencyProperty SelectedMarkersProperty = DependencyProperty.Register(
        nameof(SelectedMarkers),
        typeof(string),
        typeof(MarkerMultiSelectBox),
        new FrameworkPropertyMetadata(
            string.Empty,
            FrameworkPropertyMetadataOptions.BindsTwoWayByDefault,
            SelectedMarkersChanged));

    public static readonly DependencyProperty PickerLabelProperty = DependencyProperty.Register(
        nameof(PickerLabel),
        typeof(string),
        typeof(MarkerMultiSelectBox),
        new PropertyMetadata("Select markers", DisplayPropertyChanged));

    public static readonly DependencyProperty PlaceholderProperty = DependencyProperty.Register(
        nameof(Placeholder),
        typeof(string),
        typeof(MarkerMultiSelectBox),
        new PropertyMetadata("Select markers", DisplayPropertyChanged));

    public static readonly DependencyProperty ClearTextProperty = DependencyProperty.Register(
        nameof(ClearText),
        typeof(string),
        typeof(MarkerMultiSelectBox),
        new PropertyMetadata("Clear"));

    public static readonly DependencyProperty PickerHelpTextProperty = DependencyProperty.Register(
        nameof(PickerHelpText),
        typeof(string),
        typeof(MarkerMultiSelectBox),
        new PropertyMetadata("Open the marker list and select one or more markers."));

    public static readonly DependencyProperty SelectionCountFormatProperty = DependencyProperty.Register(
        nameof(SelectionCountFormat),
        typeof(string),
        typeof(MarkerMultiSelectBox),
        new PropertyMetadata("{0} selected", DisplayPropertyChanged));

    private static readonly DependencyPropertyKey DisplayTextPropertyKey = DependencyProperty.RegisterReadOnly(
        nameof(DisplayText),
        typeof(string),
        typeof(MarkerMultiSelectBox),
        new PropertyMetadata(string.Empty));

    public static readonly DependencyProperty DisplayTextProperty = DisplayTextPropertyKey.DependencyProperty;

    private static readonly DependencyPropertyKey DisplayForegroundPropertyKey = DependencyProperty.RegisterReadOnly(
        nameof(DisplayForeground),
        typeof(Brush),
        typeof(MarkerMultiSelectBox),
        new PropertyMetadata(Brushes.Gray));

    public static readonly DependencyProperty DisplayForegroundProperty = DisplayForegroundPropertyKey.DependencyProperty;

    private static readonly DependencyPropertyKey SelectionCountTextPropertyKey = DependencyProperty.RegisterReadOnly(
        nameof(SelectionCountText),
        typeof(string),
        typeof(MarkerMultiSelectBox),
        new PropertyMetadata(string.Empty));

    public static readonly DependencyProperty SelectionCountTextProperty = SelectionCountTextPropertyKey.DependencyProperty;

    public static readonly RoutedEvent SelectionChangedEvent = EventManager.RegisterRoutedEvent(
        nameof(SelectionChanged),
        RoutingStrategy.Bubble,
        typeof(RoutedEventHandler),
        typeof(MarkerMultiSelectBox));

    private readonly ObservableCollection<MarkerOption> _options = [];
    private INotifyCollectionChanged? _observableMarkers;
    private bool _synchronizingSelection;

    public MarkerMultiSelectBox()
    {
        InitializeComponent();
        OptionsItemsControl.ItemsSource = _options;
        UpdateDisplay();
    }

    public IEnumerable? AvailableMarkers
    {
        get => (IEnumerable?)GetValue(AvailableMarkersProperty);
        set => SetValue(AvailableMarkersProperty, value);
    }

    public string SelectedMarkers
    {
        get => (string)GetValue(SelectedMarkersProperty);
        set => SetValue(SelectedMarkersProperty, value);
    }

    public string PickerLabel
    {
        get => (string)GetValue(PickerLabelProperty);
        set => SetValue(PickerLabelProperty, value);
    }

    public string Placeholder
    {
        get => (string)GetValue(PlaceholderProperty);
        set => SetValue(PlaceholderProperty, value);
    }

    public string ClearText
    {
        get => (string)GetValue(ClearTextProperty);
        set => SetValue(ClearTextProperty, value);
    }

    public string PickerHelpText
    {
        get => (string)GetValue(PickerHelpTextProperty);
        set => SetValue(PickerHelpTextProperty, value);
    }

    public string SelectionCountFormat
    {
        get => (string)GetValue(SelectionCountFormatProperty);
        set => SetValue(SelectionCountFormatProperty, value);
    }

    public string DisplayText => (string)GetValue(DisplayTextProperty);

    public Brush DisplayForeground => (Brush)GetValue(DisplayForegroundProperty);

    public string SelectionCountText => (string)GetValue(SelectionCountTextProperty);

    public event RoutedEventHandler SelectionChanged
    {
        add => AddHandler(SelectionChangedEvent, value);
        remove => RemoveHandler(SelectionChangedEvent, value);
    }

    private static void AvailableMarkersChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        var picker = (MarkerMultiSelectBox)sender;
        picker.ObserveAvailableMarkers(args.OldValue, args.NewValue);
        picker.RebuildOptions();
    }

    private static void SelectedMarkersChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        var picker = (MarkerMultiSelectBox)sender;
        picker.SynchronizeSelectionFromText();
        picker.UpdateDisplay();
    }

    private static void DisplayPropertyChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args) =>
        ((MarkerMultiSelectBox)sender).UpdateDisplay();

    private void ObserveAvailableMarkers(object? oldValue, object? newValue)
    {
        if (_observableMarkers is not null)
            _observableMarkers.CollectionChanged -= AvailableMarkers_CollectionChanged;
        _observableMarkers = newValue as INotifyCollectionChanged;
        if (_observableMarkers is not null)
            _observableMarkers.CollectionChanged += AvailableMarkers_CollectionChanged;
    }

    private void AvailableMarkers_CollectionChanged(object? sender, NotifyCollectionChangedEventArgs e) => RebuildOptions();

    private void RebuildOptions()
    {
        var selected = ParseMarkers(SelectedMarkers);
        var markerNames = new List<string>();
        var keys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        if (AvailableMarkers is not null)
        {
            foreach (var value in AvailableMarkers)
            {
                var marker = value?.ToString()?.Trim() ?? string.Empty;
                if (marker.Length > 0 && keys.Add(marker)) markerNames.Add(marker);
            }
        }

        // Preserve markers from older configurations even if their source channel
        // is no longer present, so opening the picker never destroys user data.
        foreach (var marker in selected)
            if (keys.Add(marker)) markerNames.Add(marker);

        _synchronizingSelection = true;
        try
        {
            _options.Clear();
            foreach (var marker in markerNames)
            {
                _options.Add(new MarkerOption(marker)
                {
                    IsSelected = selected.Contains(marker, StringComparer.OrdinalIgnoreCase),
                });
            }
        }
        finally
        {
            _synchronizingSelection = false;
        }
        UpdateDisplay();
    }

    private void SynchronizeSelectionFromText()
    {
        if (_synchronizingSelection) return;
        var selected = ParseMarkers(SelectedMarkers);
        _synchronizingSelection = true;
        try
        {
            foreach (var option in _options)
                option.IsSelected = selected.Contains(option.Name, StringComparer.OrdinalIgnoreCase);
        }
        finally
        {
            _synchronizingSelection = false;
        }
    }

    private static List<string> ParseMarkers(string? value) => (value ?? string.Empty)
        .Split([',', ';', '|', '\r', '\n'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
        .Where(marker => marker.Length > 0)
        .Distinct(StringComparer.OrdinalIgnoreCase)
        .ToList();

    private void CommitSelection()
    {
        if (_synchronizingSelection) return;
        var value = string.Join(", ", _options.Where(option => option.IsSelected).Select(option => option.Name));
        if (string.Equals(value, SelectedMarkers, StringComparison.Ordinal))
        {
            UpdateDisplay();
            return;
        }

        SetCurrentValue(SelectedMarkersProperty, value);
        RaiseEvent(new RoutedEventArgs(SelectionChangedEvent, this));
    }

    private void UpdateDisplay()
    {
        var selected = ParseMarkers(SelectedMarkers);
        var hasSelection = selected.Count > 0;
        SetValue(DisplayTextPropertyKey, hasSelection ? string.Join(", ", selected) : Placeholder);
        SetValue(DisplayForegroundPropertyKey, hasSelection ? TryFindResource("TextBrush") as Brush ?? Brushes.Black : TryFindResource("SecondaryTextBrush") as Brush ?? Brushes.Gray);
        string countText;
        try
        {
            countText = string.Format(SelectionCountFormat, selected.Count);
        }
        catch (FormatException)
        {
            countText = $"{selected.Count} selected";
        }
        SetValue(SelectionCountTextPropertyKey, countText);
    }

    private void MarkerCheckBox_Click(object sender, RoutedEventArgs e) => CommitSelection();

    private void ClearButton_Click(object sender, RoutedEventArgs e)
    {
        _synchronizingSelection = true;
        try
        {
            foreach (var option in _options) option.IsSelected = false;
        }
        finally
        {
            _synchronizingSelection = false;
        }
        CommitSelection();
    }

    private void PickerButton_Checked(object sender, RoutedEventArgs e) => PickerPopup.IsOpen = true;

    private void PickerButton_Unchecked(object sender, RoutedEventArgs e) => PickerPopup.IsOpen = false;

    private void PickerPopup_Opened(object? sender, EventArgs e)
    {
        PickerButton.IsChecked = true;
        Dispatcher.BeginInvoke(DispatcherPriority.Input, () =>
        {
            if (OptionsItemsControl.ItemContainerGenerator.ContainerFromIndex(0) is ContentPresenter presenter)
                FindVisualChild<CheckBox>(presenter)?.Focus();
        });
    }

    private void PickerPopup_Closed(object? sender, EventArgs e)
    {
        PickerButton.IsChecked = false;
    }

    private void PickerButton_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Down && Keyboard.Modifiers.HasFlag(ModifierKeys.Alt))
        {
            PickerPopup.IsOpen = true;
            e.Handled = true;
        }
    }

    private void PickerPopup_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != Key.Escape) return;
        PickerPopup.IsOpen = false;
        PickerButton.Focus();
        e.Handled = true;
    }

    private static T? FindVisualChild<T>(DependencyObject parent) where T : DependencyObject
    {
        for (var index = 0; index < VisualTreeHelper.GetChildrenCount(parent); index++)
        {
            var child = VisualTreeHelper.GetChild(parent, index);
            if (child is T match) return match;
            var descendant = FindVisualChild<T>(child);
            if (descendant is not null) return descendant;
        }
        return null;
    }
}
