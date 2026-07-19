using Microsoft.Win32;
using SpatialScope.Windows.Models;
using SpatialScope.Windows.Services;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace SpatialScope.Windows;

public partial class MainWindow : Window
{
    private static readonly string[] ChannelPalette =
    [
        "#FFFFFF", "#EF476F", "#06D6A0", "#118AB2", "#FFD166", "#8E6CEF",
        "#F78C6B", "#4CC9F0", "#B8DE6F", "#FF70A6", "#70D6FF", "#C77DFF",
    ];

    private readonly LocalizationService _localization = new();
    private readonly EngineClient _engine = new();
    private readonly ObservableCollection<WorkflowSection> _sections = [];
    private readonly ObservableCollection<ChannelRow> _channels = [];
    private readonly ObservableCollection<CellTypeRow> _cellTypes = [];
    private readonly ObservableCollection<OutputFileRow> _outputFiles = [];
    private readonly Dictionary<string, double> _nucleiValues = ParameterCatalog.Nuclei.ToDictionary(item => item.Key, item => item.DefaultValue);
    private readonly Dictionary<string, double> _assignmentValues = ParameterCatalog.Assignment.ToDictionary(item => item.Key, item => item.DefaultValue);
    private readonly Dictionary<string, string> _previewPaths = [];
    private readonly Dictionary<string, string> _exportPaths = [];
    private readonly List<string> _resolvedCellTypes = [];
    private readonly List<(string Label, string Path)> _boundaries = [];
    private readonly ProcessTreeCpuMonitor _cpuMonitor;

    private string _selectedSectionKey = "inputs";
    private string _inputFolder = string.Empty;
    private string _outputFolder = string.Empty;
    private string _nucleusChannel = string.Empty;
    private string _thresholdMode = "global_otsu";
    private bool _resolveAmbiguous = true;
    private double _xMicrometers = 1000;
    private double _yMicrometers = 1000;
    private double _xPixels = 1000;
    private double _yPixels = 1000;
    private double _nucleiOptimizerBudget = 64;
    private double _assignmentOptimizerBudget = 64;
    private bool _suppressLanguageSelection;
    private bool _isBusy;
    private string? _activeSectionKey;
    private string? _statusResourceKey;

    public MainWindow()
    {
        InitializeComponent();
        _sections.Add(new WorkflowSection { Key = "inputs", Number = 1, IconGlyph = "\uE8B7", Status = WorkflowStatus.Ready });
        _sections.Add(new WorkflowSection { Key = "overlay", Number = 2, IconGlyph = "\uE81E", Status = WorkflowStatus.NotStarted });
        _sections.Add(new WorkflowSection { Key = "nuclei", Number = 3, IconGlyph = "\uE9D9", Status = WorkflowStatus.NotStarted });
        _sections.Add(new WorkflowSection { Key = "cellTypes", Number = 4, IconGlyph = "\uE8EC", Status = WorkflowStatus.NotStarted });
        _sections.Add(new WorkflowSection { Key = "neighborhood", Number = 5, IconGlyph = "\uE80A", Status = WorkflowStatus.NotStarted });
        _sections.Add(new WorkflowSection { Key = "region", Number = 6, IconGlyph = "\uEF20", Status = WorkflowStatus.NotStarted });
        _sections.Add(new WorkflowSection { Key = "distribution", Number = 7, IconGlyph = "\uE9D2", Status = WorkflowStatus.NotStarted });
        _sections.Add(new WorkflowSection { Key = "distance", Number = 8, IconGlyph = "\uE8EF", Status = WorkflowStatus.NotStarted });
        _sections.Add(new WorkflowSection { Key = "outputs", Number = 9, IconGlyph = "\uE838", Status = WorkflowStatus.NotStarted });
        WorkflowItemsControl.ItemsSource = _sections;

        _localization.LanguageChanged += (_, _) => Dispatcher.Invoke(ApplyLocalization);
        _engine.ProgressChanged += Engine_ProgressChanged;
        _engine.EngineMessage += (_, message) => Debug.WriteLine(message);
        _cpuMonitor = new ProcessTreeCpuMonitor(Environment.ProcessId);
        _cpuMonitor.PropertyChanged += (_, args) =>
        {
            if (args.PropertyName == nameof(ProcessTreeCpuMonitor.CpuPercent)) Dispatcher.Invoke(UpdateCpuText);
        };

        SetLanguageSelection();
        SelectSection("inputs");
        ApplyLocalization();
        _cpuMonitor.Start();

        Loaded += MainWindow_Loaded;
        Closed += MainWindow_Closed;
    }

    private async void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        SetLocalizedStatus("EngineStarting");
        try
        {
            await _engine.StartAsync();
            SetLocalizedStatus("EngineReady");
            await CaptureQaScenarioIfRequestedAsync();
        }
        catch (Exception exception)
        {
            SetStatus(exception.Message, isError: true);
            _sections[0].Status = WorkflowStatus.Error;
            UpdateHeader();
        }
    }

    private async void MainWindow_Closed(object? sender, EventArgs e)
    {
        _cpuMonitor.Dispose();
        await _engine.DisposeAsync();
    }

    private async Task CaptureQaScenarioIfRequestedAsync()
    {
        var capturePath = Environment.GetEnvironmentVariable("SPATIALSCOPE_CAPTURE_PATH");
        if (string.IsNullOrWhiteSpace(capturePath)) return;

        var qaInput = Environment.GetEnvironmentVariable("SPATIALSCOPE_QA_INPUT_FOLDER");
        var qaOutput = Environment.GetEnvironmentVariable("SPATIALSCOPE_QA_OUTPUT_FOLDER");
        if (!string.IsNullOrWhiteSpace(qaInput) && Directory.Exists(qaInput)
            && !string.IsNullOrWhiteSpace(qaOutput))
        {
            _inputFolder = Path.GetFullPath(qaInput);
            _outputFolder = Path.GetFullPath(qaOutput);
            Directory.CreateDirectory(_outputFolder);
            SelectSection("inputs");
            await SaveConfigurationAsync();
        }

        await Dispatcher.InvokeAsync(() => UpdateLayout(), DispatcherPriority.ApplicationIdle);
        await Task.Delay(300);
        CaptureWindowPng(Path.GetFullPath(capturePath));
        if (string.Equals(
                Environment.GetEnvironmentVariable("SPATIALSCOPE_CAPTURE_EXIT"),
                "1",
                StringComparison.Ordinal))
        {
            Close();
        }
    }

    private void CaptureWindowPng(string path)
    {
        var directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrWhiteSpace(directory)) Directory.CreateDirectory(directory);
        var width = Math.Max(1, (int)Math.Ceiling(ActualWidth));
        var height = Math.Max(1, (int)Math.Ceiling(ActualHeight));
        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(this);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var stream = File.Create(path);
        encoder.Save(stream);
    }

    private void Engine_ProgressChanged(object? sender, EngineProgress progress)
    {
        Dispatcher.Invoke(() =>
        {
            _statusResourceKey = null;
            OperationProgress.Value = progress.Value;
            var active = _activeSectionKey is null
                ? CurrentSection
                : _sections.FirstOrDefault(section => section.Key == _activeSectionKey) ?? CurrentSection;
            StatusText.Text = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese
                ? $"{_localization["Running"]} · {active.Title} · {progress.Value:P0}"
                : $"{progress.Message} · {progress.Value:P0}";
        });
    }

    private WorkflowSection CurrentSection => _sections.First(section => section.Key == _selectedSectionKey);

    private void ApplyLocalization()
    {
        ApplyLocalizedFontFamily();
        TaglineText.Text = _localization["Tagline"];
        WorkflowProgressLabel.Text = _localization["WorkflowProgress"];
        LanguageLabel.Text = _localization["Language"];
        DatasetLabel.Text = _localization["Dataset"];
        ScaleLabel.Text = _localization["Scale"];
        ComputeLabel.Text = _localization["Compute"];
        OpenOutputButton.Content = _localization["OpenOutput"];

        var titleKeys = new[]
        {
            ("InputsTitle", "InputsSubtitle"), ("OverlayTitle", "OverlaySubtitle"),
            ("NucleiTitle", "NucleiSubtitle"), ("CellTypesTitle", "CellTypesSubtitle"),
            ("NeighborhoodTitle", "NeighborhoodSubtitle"), ("RegionTitle", "RegionSubtitle"),
            ("DistributionTitle", "DistributionSubtitle"), ("DistanceTitle", "DistanceSubtitle"),
            ("OutputsTitle", "OutputsSubtitle"),
        };
        for (var index = 0; index < _sections.Count; index++)
        {
            _sections[index].Title = _localization[titleKeys[index].Item1];
            _sections[index].Subtitle = _localization[titleKeys[index].Item2];
            _sections[index].RefreshText();
        }

        if (LanguageComboBox.Items.Count == 3)
        {
            ((ComboBoxItem)LanguageComboBox.Items[0]).Content = _localization["FollowSystem"];
            ((ComboBoxItem)LanguageComboBox.Items[1]).Content = _localization["English"];
            ((ComboBoxItem)LanguageComboBox.Items[2]).Content = _localization["SimplifiedChinese"];
        }
        UpdateProgressMetadata();
        UpdateHeader();
        UpdateCpuText();
        if (_statusResourceKey is not null) SetLocalizedStatus(_statusResourceKey);
        DetailHost.Content = BuildSectionView(_selectedSectionKey);
    }

    private void ApplyLocalizedFontFamily()
    {
        var useChineseFont = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese;
        var interfaceFont = new FontFamily(useChineseFont
            ? "Microsoft YaHei UI, Microsoft YaHei"
            : "Segoe UI Variable Text, Microsoft YaHei UI, Microsoft YaHei, Segoe UI");
        var displayFont = new FontFamily(useChineseFont
            ? "Microsoft YaHei UI, Microsoft YaHei"
            : "Segoe UI Variable Display, Microsoft YaHei UI, Microsoft YaHei, Segoe UI");

        // Dynamic resources update XAML styles, popups, data grids, and existing
        // controls immediately when the language changes. Icon glyphs and the
        // English-only SpatialScope brand retain their explicit Segoe families.
        Application.Current.Resources["InterfaceFont"] = interfaceFont;
        Application.Current.Resources["DisplayFont"] = displayFont;
        FontFamily = interfaceFont;
    }

    private void SetLanguageSelection()
    {
        _suppressLanguageSelection = true;
        LanguageComboBox.SelectedIndex = _localization.Language switch
        {
            InterfaceLanguage.English => 1,
            InterfaceLanguage.SimplifiedChinese => 2,
            _ => 0,
        };
        _suppressLanguageSelection = false;
    }

    private void LanguageComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_suppressLanguageSelection || LanguageComboBox.SelectedItem is not ComboBoxItem item) return;
        if (Enum.TryParse(item.Tag?.ToString(), out InterfaceLanguage language)) _localization.SetLanguage(language);
    }

    private void WorkflowRow_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (sender is Border { Tag: string key }) SelectSection(key);
    }

    private void SelectSection(string key)
    {
        _selectedSectionKey = key;
        foreach (var section in _sections) section.IsSelected = section.Key == key;
        UpdateHeader();
        DetailHost.Content = BuildSectionView(key);
    }

    private void UpdateHeader()
    {
        if (_sections.Count == 0) return;
        var section = CurrentSection;
        HeaderIcon.Text = section.IconGlyph;
        HeaderStep.Text = $"{_localization["Step"]} {section.Number} {_localization["Of"]} {_sections.Count}";
        HeaderTitle.Text = section.Title;
        HeaderSubtitle.Text = section.Subtitle;
        HeaderStatusText.Text = _localization[section.StatusText];
        HeaderStatusText.Foreground = section.StatusForeground;
        HeaderStatusBadge.Background = section.StatusBackground;
    }

    private void UpdateProgressMetadata()
    {
        var complete = _sections.Count(section => section.Status == WorkflowStatus.Complete);
        WorkflowProgressBar.Value = complete;
        WorkflowProgressText.Text = $"{complete} {_localization["Of"].ToLowerInvariant()} {_sections.Count}";
        DatasetValue.Text = $"{_channels.Count} {_localization["Channels"]}";
        ScaleValue.Text = _xPixels > 0 && _yPixels > 0
            ? $"{_xMicrometers / _xPixels:0.###} × {_yMicrometers / _yPixels:0.###} µm/px"
            : _localization["NotSet"];
    }

    private void UpdateCpuText() => CpuValue.Text = $"{_cpuMonitor.CpuPercent:0.0}% {_localization["AppCpu"]}";

    private UIElement BuildSectionView(string key) => key switch
    {
        "inputs" => BuildInputsView(),
        "overlay" => BuildOverlayView(),
        "nuclei" => BuildNucleiView(),
        "cellTypes" => BuildCellTypesView(),
        "neighborhood" => BuildNeighborhoodView(),
        "region" => BuildRegionView(),
        "distribution" => BuildDistributionView(),
        "distance" => BuildDistanceView(),
        "outputs" => BuildOutputsView(),
        _ => new Grid(),
    };

    private ScrollViewer CreatePage(params UIElement[] children)
    {
        var stack = new StackPanel { Margin = new Thickness(24, 22, 24, 24) };
        foreach (var child in children) stack.Children.Add(child);
        return new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
            Content = stack,
        };
    }

    private Border CreateCard(string title, UIElement content)
    {
        var stack = new StackPanel();
        stack.Children.Add(new TextBlock { Text = title, Style = (Style)FindResource("SectionTitleStyle") });
        stack.Children.Add(content);
        return new Border { Style = (Style)FindResource("CardStyle"), Child = stack };
    }

    private Button CreateButton(string text, RoutedEventHandler onClick, bool primary = false)
    {
        var button = new Button
        {
            Content = text,
            Style = (Style)FindResource(primary ? "PrimaryButtonStyle" : "SecondaryButtonStyle"),
            Margin = new Thickness(0, 0, 10, 0),
        };
        button.Click += onClick;
        return button;
    }

    private UIElement BuildInputsView()
    {
        var locations = new Grid();
        locations.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(155) });
        locations.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        locations.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        locations.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        locations.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        AddFolderRow(locations, 0, _localization["InputFolder"], _inputFolder, ChooseInputFolder);
        AddFolderRow(locations, 1, _localization["OutputFolder"], _outputFolder, ChooseOutputFolder);

        var calibrationGrid = new UniformGrid { Columns = 4 };
        calibrationGrid.Children.Add(CreateNumberField(_localization["MicrometersWide"], _xMicrometers, value => _xMicrometers = value, "µm"));
        calibrationGrid.Children.Add(CreateNumberField(_localization["MicrometersHigh"], _yMicrometers, value => _yMicrometers = value, "µm"));
        calibrationGrid.Children.Add(CreateNumberField(_localization["PixelWidth"], _xPixels, value => _xPixels = value, "px"));
        calibrationGrid.Children.Add(CreateNumberField(_localization["PixelHeight"], _yPixels, value => _yPixels = value, "px"));

        var tools = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        tools.Children.Add(CreateButton(_localization["RescanCsv"], async (_, _) => await SaveConfigurationAsync()));
        tools.Children.Add(CreateButton(_localization["ResetMarkerNames"], (_, _) =>
        {
            foreach (var channel in _channels) channel.Marker = Path.GetFileNameWithoutExtension(channel.FileName);
            InvalidateAfter("inputs");
        }));
        tools.Children.Add(CreateButton(_localization["ReassignColors"], (_, _) =>
        {
            for (var index = 0; index < _channels.Count; index++) _channels[index].ColorHex = ChannelPalette[index % ChannelPalette.Length];
            InvalidateAfter("inputs");
        }));
        tools.Children.Add(CreateButton(_localization["SaveConfiguration"], async (_, _) => await SaveConfigurationAsync(), primary: true));

        var channelGrid = new DataGrid { ItemsSource = _channels, MinHeight = 245 };
        channelGrid.Columns.Add(new DataGridCheckBoxColumn { Header = _localization["Overlay"], Binding = new Binding(nameof(ChannelRow.IncludeInOverlay)), Width = 80 });
        channelGrid.Columns.Add(new DataGridTextColumn { Header = _localization["CsvFile"], Binding = new Binding(nameof(ChannelRow.FileName)), IsReadOnly = true, Width = new DataGridLength(1, DataGridLengthUnitType.Star) });
        channelGrid.Columns.Add(new DataGridTextColumn { Header = _localization["Marker"], Binding = new Binding(nameof(ChannelRow.Marker)) { UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged }, Width = 220 });
        channelGrid.Columns.Add(new DataGridTemplateColumn
        {
            Header = _localization["Color"],
            CellTemplate = (DataTemplate)FindResource("ColorEditorTemplate"),
            Width = 160,
        });

        var registry = new StackPanel();
        registry.Children.Add(tools);
        registry.Children.Add(channelGrid);
        return CreatePage(
            CreateCard(_localization["DataLocations"], locations),
            CreateCard(_localization["SpatialCalibration"], calibrationGrid),
            CreateCard(_localization["ChannelRegistry"], registry));
    }

    private void AddFolderRow(Grid grid, int row, string label, string path, Action choose)
    {
        var labelBlock = new TextBlock { Text = label, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center };
        Grid.SetRow(labelBlock, row);
        Grid.SetColumn(labelBlock, 0);
        grid.Children.Add(labelBlock);

        var pathBox = new TextBox
        {
            Text = path,
            IsReadOnly = true,
            Cursor = Cursors.Hand,
            Margin = new Thickness(0, row == 0 ? 0 : 10, 12, 0),
            ToolTip = _localization["ChooseFolder"],
        };
        pathBox.PreviewMouseLeftButtonDown += (_, _) => choose();
        Grid.SetRow(pathBox, row);
        Grid.SetColumn(pathBox, 1);
        grid.Children.Add(pathBox);

        var chooseButton = CreateButton(_localization["Choose"], (_, _) => choose());
        chooseButton.Margin = new Thickness(0, row == 0 ? 0 : 10, 0, 0);
        Grid.SetRow(chooseButton, row);
        Grid.SetColumn(chooseButton, 2);
        grid.Children.Add(chooseButton);
    }

    private UIElement CreateNumberField(string label, double value, Action<double> setter, string unit)
    {
        var panel = new StackPanel { Margin = new Thickness(0, 0, 14, 0) };
        panel.Children.Add(new TextBlock { Text = label, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 6) });
        var row = new DockPanel();
        var unitText = new TextBlock { Text = unit, Margin = new Thickness(7, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center, Foreground = (Brush)FindResource("SecondaryTextBrush") };
        DockPanel.SetDock(unitText, Dock.Right);
        row.Children.Add(unitText);
        var editor = new TextBox { Text = value.ToString("0.###", CultureInfo.CurrentCulture) };
        editor.LostFocus += (_, _) =>
        {
            if (TryReadDouble(editor.Text, out var parsed) && parsed > 0)
            {
                setter(parsed);
                UpdateProgressMetadata();
                InvalidateAfter("inputs");
            }
            else editor.Text = value.ToString("0.###", CultureInfo.CurrentCulture);
        };
        row.Children.Add(editor);
        panel.Children.Add(row);
        return panel;
    }

    private void ChooseInputFolder()
    {
        var selected = ChooseFolder(_inputFolder);
        if (selected is null) return;
        _inputFolder = selected;
        InvalidateAfter("inputs");
        DetailHost.Content = BuildInputsView();
    }

    private void ChooseOutputFolder()
    {
        var selected = ChooseFolder(_outputFolder);
        if (selected is null) return;
        _outputFolder = selected;
        InvalidateAfter("inputs");
        DetailHost.Content = BuildInputsView();
    }

    private string? ChooseFolder(string initialPath)
    {
        var dialog = new OpenFolderDialog { Title = _localization["ChooseFolder"], Multiselect = false };
        if (Directory.Exists(initialPath)) dialog.InitialDirectory = initialPath;
        return dialog.ShowDialog(this) == true ? dialog.FolderName : null;
    }

    private async Task SaveConfigurationAsync()
    {
        if (string.IsNullOrWhiteSpace(_inputFolder) || string.IsNullOrWhiteSpace(_outputFolder))
        {
            SetLocalizedStatus("SelectFoldersFirst", isError: true);
            return;
        }
        var channels = _channels.Select(channel => new
        {
            file = channel.FileName,
            channel = channel.Marker,
            colorHex = channel.ColorHex,
            includeOverlay = channel.IncludeInOverlay,
        }).ToArray();
        var response = await RunWorkflowAsync("inputs", "configure", new
        {
            inputFolder = _inputFolder,
            outputFolder = _outputFolder,
            pixelSizeUm = new[] { _xMicrometers / _xPixels, _yMicrometers / _yPixels },
            imageId = "FieldA",
            channels,
        });
        if (response is null) return;
        _channels.Clear();
        foreach (var item in response.Value.GetProperty("channels").EnumerateArray())
        {
            _channels.Add(new ChannelRow
            {
                FileName = item.GetProperty("file").GetString() ?? string.Empty,
                Marker = item.GetProperty("channel").GetString() ?? string.Empty,
                ColorHex = item.GetProperty("colorHex").GetString() ?? "#FFFFFF",
                IncludeInOverlay = item.GetProperty("includeOverlay").GetBoolean(),
            });
        }
        _nucleusChannel = _channels.FirstOrDefault(channel => channel.Marker.Contains("DAPI", StringComparison.OrdinalIgnoreCase))?.Marker
            ?? _channels.FirstOrDefault()?.Marker
            ?? string.Empty;
        EnsureDefaultCellTypes();
        SetLocalizedStatus("ConfigurationSaved");
        UpdateProgressMetadata();
        DetailHost.Content = BuildInputsView();
    }

    private UIElement BuildOverlayView()
    {
        var action = new StackPanel();
        action.Children.Add(CreateButton(_localization["GenerateOverlay"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("overlay", "overlay", new { clipHighPercentile = 99.8 });
            if (result is null) return;
            var previews = result.Value.GetProperty("previewPaths");
            _previewPaths["overlay"] = previews.GetProperty("overlay").GetString() ?? string.Empty;
            _previewPaths["split"] = previews.GetProperty("splitChannels").GetString() ?? string.Empty;
            CaptureExportPaths(result.Value);
            DetailHost.Content = BuildOverlayView();
        }, primary: true));

        var previewsPanel = new Grid();
        previewsPanel.ColumnDefinitions.Add(new ColumnDefinition());
        previewsPanel.ColumnDefinitions.Add(new ColumnDefinition());
        var overlayPanel = CreateImagePanel(_localization["OverlayPreview"], _previewPaths.GetValueOrDefault("overlay"), _exportPaths.GetValueOrDefault("overlay.png"));
        overlayPanel.Margin = new Thickness(0, 0, 9, 0);
        previewsPanel.Children.Add(overlayPanel);
        var splitPanel = CreateImagePanel(_localization["SplitChannelsPreview"], _previewPaths.GetValueOrDefault("split"), _exportPaths.GetValueOrDefault("split_channels.png"));
        splitPanel.Margin = new Thickness(9, 0, 0, 0);
        Grid.SetColumn(splitPanel, 1);
        previewsPanel.Children.Add(splitPanel);
        return CreatePage(CreateCard(_localization["CompositePreview"], action), CreateCard(_localization["CompositePreview"], previewsPanel));
    }

    private Border CreateImagePanel(string title, string? previewPath, string? originalPath = null)
    {
        var panel = new StackPanel();
        var heading = new DockPanel { Margin = new Thickness(0, 0, 0, 10) };
        if (!string.IsNullOrWhiteSpace(originalPath) && File.Exists(originalPath))
        {
            var open = CreateButton(_localization["OpenOriginal"], (_, _) => OpenPath(originalPath));
            open.Padding = new Thickness(10, 5, 10, 5);
            DockPanel.SetDock(open, Dock.Right);
            heading.Children.Add(open);
        }
        heading.Children.Add(new TextBlock { Text = title, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center });
        panel.Children.Add(heading);
        if (!string.IsNullOrWhiteSpace(previewPath) && File.Exists(previewPath))
        {
            var image = new Image
            {
                Source = LoadBoundedBitmap(previewPath, 1500),
                Stretch = Stretch.Uniform,
                HorizontalAlignment = HorizontalAlignment.Center,
                VerticalAlignment = VerticalAlignment.Center,
            };
            panel.Children.Add(new Border
            {
                Height = 430,
                Background = Brushes.Black,
                BorderBrush = (Brush)FindResource("PanelBorderBrush"),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(7),
                Padding = new Thickness(6),
                Child = image,
            });
        }
        else
        {
            panel.Children.Add(new Border
            {
                Height = 320,
                Background = new SolidColorBrush(Color.FromRgb(242, 246, 247)),
                CornerRadius = new CornerRadius(7),
                Child = new StackPanel
                {
                    VerticalAlignment = VerticalAlignment.Center,
                    HorizontalAlignment = HorizontalAlignment.Center,
                    Children =
                    {
                        new TextBlock { Text = _localization["NoPreview"], FontSize = 18, FontWeight = FontWeights.SemiBold, HorizontalAlignment = HorizontalAlignment.Center },
                        new TextBlock { Text = _localization["NoPreviewDetail"], Foreground = (Brush)FindResource("SecondaryTextBrush"), Margin = new Thickness(0, 7, 0, 0), TextWrapping = TextWrapping.Wrap, MaxWidth = 360, TextAlignment = TextAlignment.Center },
                    },
                },
            });
        }
        return new Border { Child = panel };
    }

    private UIElement BuildNucleiView()
    {
        var channelRow = new Grid();
        channelRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(200) });
        channelRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(320) });
        channelRow.Children.Add(new TextBlock { Text = _localization["NucleusChannel"], FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center });
        var channelPicker = new ComboBox { ItemsSource = _channels.Select(item => item.Marker).ToArray(), SelectedItem = _nucleusChannel };
        channelPicker.SelectionChanged += (_, _) => _nucleusChannel = channelPicker.SelectedItem?.ToString() ?? string.Empty;
        Grid.SetColumn(channelPicker, 1);
        channelRow.Children.Add(channelPicker);

        var parameterGrid = new UniformGrid { Columns = 2 };
        foreach (var parameter in ParameterCatalog.Nuclei) parameterGrid.Children.Add(CreateParameterEditor(parameter, _nucleiValues));
        var optimizerStack = new StackPanel();
        optimizerStack.Children.Add(new TextBlock
        {
            Text = _localization["NucleiOptimizerHelp"],
            TextWrapping = TextWrapping.Wrap,
            Foreground = (Brush)FindResource("SecondaryTextBrush"),
            Margin = new Thickness(0, 0, 0, 10),
        });
        optimizerStack.Children.Add(CreateNumberField(_localization["OptimizerBudget"], _nucleiOptimizerBudget, value => _nucleiOptimizerBudget = Math.Clamp(Math.Round(value), 1, 4096), ""));
        var optimizerButton = CreateButton(_localization["RunOptimizer"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("nuclei", "nuclei_optimizer", new
            {
                parameters = BuildNucleiPayload(),
                maxEvaluations = (int)_nucleiOptimizerBudget,
                parallelWorkers = Math.Max(1, Math.Min(4, Environment.ProcessorCount / 2)),
                parallelBackend = "threading",
                useFixedRoiSubset = true,
            }, completesSection: false);
            if (result is null) return;
            ApplyNucleiRecommendation(result.Value);
            _previewPaths["nucleiOptimizer"] = result.Value.TryGetProperty("previewPath", out var preview) && preview.ValueKind == JsonValueKind.String ? preview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            DetailHost.Content = BuildNucleiView();
        }, primary: true);
        optimizerButton.Margin = new Thickness(0, 12, 0, 0);
        optimizerStack.Children.Add(optimizerButton);
        if (_previewPaths.TryGetValue("nucleiOptimizer", out var optimizerPreview))
        {
            var previewPanel = CreateImagePanel(_localization["AdvancedScreening"], optimizerPreview);
            previewPanel.Margin = new Thickness(0, 16, 0, 0);
            optimizerStack.Children.Add(previewPanel);
        }
        var actions = new WrapPanel { Margin = new Thickness(0, 14, 0, 0) };
        actions.Children.Add(CreateButton(_localization["RunNuclei"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("nuclei", "nuclei", new
            {
                parameters = BuildNucleiPayload(),
                nativeThreads = Math.Max(1, (int)Math.Round(Environment.ProcessorCount * 0.75)),
            });
            if (result is null) return;
            _previewPaths["nuclei"] = result.Value.TryGetProperty("previewPath", out var preview) ? preview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            DetailHost.Content = BuildNucleiView();
        }, primary: true));
        var parameterStack = new StackPanel();
        parameterStack.Children.Add(parameterGrid);
        var finalStack = new StackPanel();
        finalStack.Children.Add(actions);
        var finalPreview = CreateImagePanel(_localization["FinalSegmentation"], _previewPaths.GetValueOrDefault("nuclei"));
        finalPreview.Margin = new Thickness(0, 16, 0, 0);
        finalStack.Children.Add(finalPreview);

        return CreatePage(
            CreateCard(_localization["NucleusChannel"], channelRow),
            CreateCard(_localization["ManualParameters"], parameterStack),
            CreateCard(_localization["AdvancedScreening"], optimizerStack),
            CreateCard(_localization["FinalSegmentation"], finalStack));
    }

    private UIElement CreateParameterEditor(ParameterDefinition parameter, Dictionary<string, double> values)
    {
        var panel = new Border
        {
            BorderBrush = new SolidColorBrush(Color.FromRgb(226, 232, 234)),
            BorderThickness = new Thickness(0, 0, 0, 1),
            Padding = new Thickness(0, 10, 18, 12),
            Margin = new Thickness(0, 0, 12, 0),
        };
        var stack = new StackPanel();
        var row = new Grid();
        row.ColumnDefinitions.Add(new ColumnDefinition());
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(128) });
        var name = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? parameter.ChineseName : parameter.EnglishName;
        row.Children.Add(new TextBlock { Text = name, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center });
        var editorRow = new DockPanel();
        if (!string.IsNullOrWhiteSpace(parameter.Unit))
        {
            var unit = new TextBlock { Text = parameter.Unit, Margin = new Thickness(6, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center, Foreground = (Brush)FindResource("SecondaryTextBrush") };
            DockPanel.SetDock(unit, Dock.Right);
            editorRow.Children.Add(unit);
        }
        var editor = new TextBox { Text = values[parameter.Key].ToString("0.###", CultureInfo.CurrentCulture), FontFamily = new FontFamily("Cascadia Mono, Consolas") };
        editor.LostFocus += (_, _) =>
        {
            if (TryReadDouble(editor.Text, out var parsed))
            {
                values[parameter.Key] = Math.Clamp(parsed, parameter.Minimum, parameter.Maximum);
                editor.Text = values[parameter.Key].ToString("0.###", CultureInfo.CurrentCulture);
                InvalidateAfter(ReferenceEquals(values, _assignmentValues) ? "cellTypes" : "nuclei");
            }
            else editor.Text = values[parameter.Key].ToString("0.###", CultureInfo.CurrentCulture);
        };
        editor.ToolTip = $"{parameter.Minimum:0.###}–{parameter.Maximum:0.###}";
        editorRow.Children.Add(editor);
        Grid.SetColumn(editorRow, 1);
        row.Children.Add(editorRow);
        stack.Children.Add(row);
        stack.Children.Add(new TextBlock
        {
            Text = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? parameter.ChineseExplanation : parameter.EnglishExplanation,
            FontSize = 12.5,
            Foreground = (Brush)FindResource("SecondaryTextBrush"),
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 6, 0, 0),
            LineHeight = 18,
        });
        panel.Child = stack;
        return panel;
    }

    private object BuildNucleiPayload() => new
    {
        nucleus_channel = _nucleusChannel,
        min_diam_um = _nucleiValues["min_diam_um"],
        max_diam_um = _nucleiValues["max_diam_um"],
        tophat_radius_um = _nucleiValues["tophat_radius_um"],
        gauss_sigma_um = _nucleiValues["gauss_sigma_um"],
        local_win_um = _nucleiValues["local_win_um"],
        local_offset = _nucleiValues["local_offset"],
        h_maxima_um = _nucleiValues["h_maxima_um"],
        seed_min_dist_um = _nucleiValues["seed_min_dist_um"],
        watershed_compactness = _nucleiValues["watershed_compactness"],
        post_resplit_mult = _nucleiValues["post_resplit_mult"],
    };

    private void ApplyNucleiRecommendation(JsonElement result)
    {
        if (!result.TryGetProperty("recommendedParameters", out var recommended) || recommended.ValueKind != JsonValueKind.Object) return;
        foreach (var property in recommended.EnumerateObject())
        {
            if (_nucleiValues.ContainsKey(property.Name) && property.Value.ValueKind == JsonValueKind.Number)
                _nucleiValues[property.Name] = property.Value.GetDouble();
        }
        InvalidateAfter("nuclei");
    }

    private UIElement BuildCellTypesView()
    {
        EnsureDefaultCellTypes();
        var tabs = new TabControl { Margin = new Thickness(24, 22, 24, 24) };
        tabs.Items.Add(new TabItem { Header = _localization["CellTypeDefinitions"], Content = BuildCellTypeRulesPanel() });
        tabs.Items.Add(new TabItem { Header = _localization["AssignmentParameters"], Content = BuildAssignmentPanel() });
        return tabs;
    }

    private UIElement BuildCellTypeRulesPanel()
    {
        var stack = new StackPanel { Margin = new Thickness(0, 18, 0, 0) };
        var actions = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        actions.Children.Add(CreateButton(_localization["AddCellType"], (_, _) => _cellTypes.Add(new CellTypeRow { Name = $"Cell type {_cellTypes.Count + 1}", ColorHex = ChannelPalette[(_cellTypes.Count + 2) % ChannelPalette.Length] })));
        actions.Children.Add(CreateButton(_localization["Remove"], (_, _) =>
        {
            if (_cellTypes.Count > 0) _cellTypes.RemoveAt(_cellTypes.Count - 1);
        }));
        stack.Children.Add(actions);
        var grid = new DataGrid { ItemsSource = _cellTypes, MinHeight = 420 };
        grid.Columns.Add(new DataGridTextColumn { Header = _localization["CellTypeName"], Binding = new Binding(nameof(CellTypeRow.Name)) { UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged }, Width = 170 });
        grid.Columns.Add(new DataGridTemplateColumn
        {
            Header = _localization["Color"],
            CellTemplate = (DataTemplate)FindResource("ColorEditorTemplate"),
            Width = 145,
        });
        grid.Columns.Add(new DataGridTextColumn { Header = _localization["AllPositive"], Binding = new Binding(nameof(CellTypeRow.AllPositive)) { UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged }, Width = new DataGridLength(1, DataGridLengthUnitType.Star) });
        grid.Columns.Add(new DataGridTextColumn { Header = _localization["AllNegative"], Binding = new Binding(nameof(CellTypeRow.AllNegative)) { UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged }, Width = new DataGridLength(1, DataGridLengthUnitType.Star) });
        grid.Columns.Add(new DataGridTextColumn { Header = _localization["AnyPositiveGroups"], Binding = new Binding(nameof(CellTypeRow.AnyPositiveGroups)) { UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged }, Width = new DataGridLength(1, DataGridLengthUnitType.Star) });
        stack.Children.Add(grid);
        stack.Children.Add(new TextBlock
        {
            Text = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese
                ? "用逗号分隔同组标记；任一阳性组之间用竖线“|”分隔。细胞类型和标记名称保持用户输入的语言。"
                : "Separate markers with commas; separate any-positive groups with a vertical bar (|). User-entered cell type and marker names are never translated.",
            FontSize = 12.5,
            Foreground = (Brush)FindResource("SecondaryTextBrush"),
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 10, 0, 0),
        });
        return new ScrollViewer { Content = CreateCard(_localization["CellTypeDefinitions"], stack), VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    }

    private UIElement BuildAssignmentPanel()
    {
        var parameters = new UniformGrid { Columns = 2 };
        foreach (var parameter in ParameterCatalog.Assignment) parameters.Children.Add(CreateParameterEditor(parameter, _assignmentValues));

        var thresholdPanel = new StackPanel { Margin = new Thickness(0, 14, 0, 0) };
        thresholdPanel.Children.Add(new TextBlock { Text = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? "阈值模式" : "Threshold mode", FontWeight = FontWeights.SemiBold });
        var threshold = new ComboBox { Width = 280, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 6, 0, 0) };
        threshold.Items.Add(new ComboBoxItem { Content = "Global Otsu", Tag = "global_otsu" });
        threshold.Items.Add(new ComboBoxItem { Content = "Yen", Tag = "yen" });
        threshold.Items.Add(new ComboBoxItem { Content = "Triangle", Tag = "triangle" });
        threshold.SelectedIndex = _thresholdMode switch { "yen" => 1, "triangle" => 2, _ => 0 };
        threshold.SelectionChanged += (_, _) => _thresholdMode = ((ComboBoxItem)threshold.SelectedItem).Tag?.ToString() ?? "global_otsu";
        thresholdPanel.Children.Add(threshold);
        thresholdPanel.Children.Add(new TextBlock
        {
            Text = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese
                ? "Global Otsu 是通用默认值；Yen 对稀疏亮信号通常更严格；Triangle 适合强烈偏斜的直方图。"
                : "Global Otsu is the general default, Yen is often stricter for sparse bright signal, and Triangle suits strongly skewed histograms.",
            FontSize = 12.5, Foreground = (Brush)FindResource("SecondaryTextBrush"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 0),
        });
        var resolve = new CheckBox
        {
            IsChecked = _resolveAmbiguous,
            Content = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? "使用概率证据解析模糊细胞" : "Resolve ambiguous cells using probability evidence",
            FontWeight = FontWeights.SemiBold,
            Margin = new Thickness(0, 16, 0, 0),
        };
        resolve.Checked += (_, _) => _resolveAmbiguous = true;
        resolve.Unchecked += (_, _) => _resolveAmbiguous = false;
        thresholdPanel.Children.Add(resolve);
        thresholdPanel.Children.Add(new TextBlock
        {
            Text = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese
                ? "启用后，符合条件的多重匹配细胞可按概率重新分配；禁用后，所有多重匹配细胞都保留为 Ambiguous。"
                : "When enabled, eligible multi-match cells may be reassigned by probability; when disabled, every multi-match cell remains Ambiguous.",
            FontSize = 12.5, Foreground = (Brush)FindResource("SecondaryTextBrush"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 0),
        });

        var optimizerPanel = new Border
        {
            Background = new SolidColorBrush(Color.FromRgb(244, 248, 249)),
            BorderBrush = new SolidColorBrush(Color.FromRgb(214, 225, 228)),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(7),
            Padding = new Thickness(14),
            Margin = new Thickness(0, 18, 0, 0),
        };
        var optimizerStack = new StackPanel();
        optimizerStack.Children.Add(new TextBlock { Text = _localization["AdvancedScreening"], FontSize = 16, FontWeight = FontWeights.SemiBold });
        optimizerStack.Children.Add(new TextBlock
        {
            Text = _localization["AssignmentOptimizerHelp"],
            TextWrapping = TextWrapping.Wrap,
            Foreground = (Brush)FindResource("SecondaryTextBrush"),
            Margin = new Thickness(0, 6, 0, 10),
        });
        optimizerStack.Children.Add(CreateNumberField(_localization["OptimizerBudget"], _assignmentOptimizerBudget, value => _assignmentOptimizerBudget = Math.Clamp(Math.Round(value), 1, 4096), ""));
        var optimize = CreateButton(_localization["RunAssignmentOptimizer"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("cellTypes", "celltype_optimizer", new
            {
                cellTypes = BuildCellTypePayload(),
                parameters = BuildAssignmentPayload(),
                maxEvaluations = (int)_assignmentOptimizerBudget,
                parallelWorkers = Math.Max(1, Math.Min(4, Environment.ProcessorCount / 2)),
                parallelBackend = "threading",
                useFixedRoiSubset = true,
            }, completesSection: false);
            if (result is null) return;
            ApplyAssignmentRecommendation(result.Value);
            _previewPaths["assignmentOptimizer"] = result.Value.TryGetProperty("previewPath", out var preview) && preview.ValueKind == JsonValueKind.String ? preview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            DetailHost.Content = BuildCellTypesView();
        });
        optimize.Margin = new Thickness(0, 10, 0, 0);
        optimizerStack.Children.Add(optimize);
        if (_previewPaths.TryGetValue("assignmentOptimizer", out var assignmentOptimizerPreview))
        {
            var previewPanel = CreateImagePanel(_localization["AdvancedScreening"], assignmentOptimizerPreview);
            previewPanel.Margin = new Thickness(0, 14, 0, 0);
            optimizerStack.Children.Add(previewPanel);
        }
        optimizerPanel.Child = optimizerStack;

        var run = CreateButton(_localization["RunAssignment"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("cellTypes", "celltype_assignment", new
            {
                nucleusChannel = _nucleusChannel,
                cellTypes = BuildCellTypePayload(),
                parameters = BuildAssignmentPayload(),
                nativeThreads = Math.Max(1, Environment.ProcessorCount / 2),
                supportWorkers = Math.Max(1, Environment.ProcessorCount / 2),
            });
            if (result is null) return;
            _previewPaths["cellTypes"] = result.Value.TryGetProperty("previewPath", out var preview) && preview.ValueKind == JsonValueKind.String ? preview.GetString() ?? string.Empty : string.Empty;
            _resolvedCellTypes.Clear();
            if (result.Value.TryGetProperty("summary", out var summary) && summary.TryGetProperty("cellCounts", out var counts))
            {
                foreach (var property in counts.EnumerateObject())
                    if (property.Name is not "Unassigned" and not "Ambiguous") _resolvedCellTypes.Add(property.Name);
            }
            CaptureExportPaths(result.Value);
            DetailHost.Content = BuildCellTypesView();
        }, primary: true);
        run.Margin = new Thickness(0, 18, 0, 0);

        var stack = new StackPanel();
        stack.Children.Add(parameters);
        stack.Children.Add(thresholdPanel);
        stack.Children.Add(optimizerPanel);
        stack.Children.Add(new TextBlock { Text = _localization["ResultsStayEnglish"], FontSize = 12.5, Foreground = (Brush)FindResource("SecondaryTextBrush"), Margin = new Thickness(0, 14, 0, 0) });
        stack.Children.Add(run);
        if (_previewPaths.TryGetValue("cellTypes", out var previewPath) && !string.IsNullOrWhiteSpace(previewPath))
        {
            var preview = CreateImagePanel(_localization["CellTypesTitle"], previewPath);
            preview.Margin = new Thickness(0, 18, 0, 0);
            stack.Children.Add(preview);
        }
        return new ScrollViewer { Content = CreateCard(_localization["AssignmentParameters"], stack), VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    }

    private object[] BuildCellTypePayload() => _cellTypes.Select(item => new
    {
        name = item.Name,
        color_hex = item.ColorHex,
        mode = "simple",
        all_pos = SplitMarkers(item.AllPositive),
        all_neg = SplitMarkers(item.AllNegative),
        any_pos_groups = item.AnyPositiveGroups.Split('|', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).Select(SplitMarkers).Where(group => group.Length > 0).ToArray(),
    }).Cast<object>().ToArray();

    private object BuildAssignmentPayload() => new
    {
        r_voronoi_um = _assignmentValues["r_voronoi_um"],
        r_buffer_um = _assignmentValues["r_buffer_um"],
        r_vote_um = _assignmentValues["r_vote_um"],
        tophat_r_um = _assignmentValues["tophat_r_um"],
        gauss_sigma_um = _assignmentValues["gauss_sigma_um"],
        thresh_mode = _thresholdMode,
        min_pos_object_size_px = (int)Math.Round(_assignmentValues["min_pos_object_size_px"]),
        min_pos_pix = (int)Math.Round(_assignmentValues["min_pos_pix"]),
        resolve_ambiguous = _resolveAmbiguous,
        ambiguous_min_probability = _assignmentValues["ambiguous_min_probability"],
        ambiguous_min_gap = _assignmentValues["ambiguous_min_gap"],
    };

    private void ApplyAssignmentRecommendation(JsonElement result)
    {
        if (!result.TryGetProperty("recommendedParameters", out var recommended) || recommended.ValueKind != JsonValueKind.Object) return;
        foreach (var property in recommended.EnumerateObject())
        {
            if (_assignmentValues.ContainsKey(property.Name) && property.Value.ValueKind == JsonValueKind.Number)
                _assignmentValues[property.Name] = property.Value.GetDouble();
            else if (property.Name == "thresh_mode" && property.Value.ValueKind == JsonValueKind.String)
                _thresholdMode = property.Value.GetString() ?? "global_otsu";
            else if (property.Name == "resolve_ambiguous" && property.Value.ValueKind is JsonValueKind.True or JsonValueKind.False)
                _resolveAmbiguous = property.Value.GetBoolean();
        }
        InvalidateAfter("cellTypes");
    }

    private UIElement BuildNeighborhoodView()
    {
        var gridSize = 20.0;
        var field = CreateNumberField(_localization["GridSize"], gridSize, value => gridSize = value, "µm");
        var stack = new StackPanel();
        stack.Children.Add(field);
        var run = CreateButton(_localization["RunNeighborhood"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("neighborhood", "neighborhood", new { gridSizeUm = gridSize });
            if (result is null) return;
            _previewPaths["neighborhood"] = result.Value.TryGetProperty("previewPath", out var preview) && preview.ValueKind == JsonValueKind.String ? preview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            DetailHost.Content = BuildNeighborhoodView();
        }, primary: true);
        run.Margin = new Thickness(0, 14, 0, 0);
        stack.Children.Add(run);
        return CreatePage(CreateCard(_localization["NeighborhoodTitle"], stack), CreateCard(_localization["NeighborhoodTitle"], CreateImagePanel(_localization["NeighborhoodTitle"], _previewPaths.GetValueOrDefault("neighborhood"))));
    }

    private UIElement BuildRegionView()
    {
        var selected = new ObservableCollection<string>(_resolvedCellTypes.Take(1));
        var typePicker = new ListBox { ItemsSource = _resolvedCellTypes, SelectionMode = SelectionMode.Multiple, MinHeight = 90 };
        foreach (var item in selected) typePicker.SelectedItems.Add(item);
        var close = 15.0;
        var dilate = 10.0;
        var minArea = 20000.0;
        var minCells = 5.0;
        var parameters = new UniformGrid { Columns = 2 };
        parameters.Children.Add(CreateNumberField(_localization["ClosingRadius"], close, value => close = value, "µm"));
        parameters.Children.Add(CreateNumberField(_localization["DilationRadius"], dilate, value => dilate = value, "µm"));
        parameters.Children.Add(CreateNumberField(_localization["MinimumArea"], minArea, value => minArea = value, "µm²"));
        parameters.Children.Add(CreateNumberField(_localization["MinimumCells"], minCells, value => minCells = value, ""));
        var stack = new StackPanel();
        stack.Children.Add(new TextBlock { Text = _localization["SelectedCellTypes"], FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 6) });
        stack.Children.Add(typePicker);
        stack.Children.Add(parameters);
        var run = CreateButton(_localization["RunRegion"], async (_, _) =>
        {
            var selectedTypes = typePicker.SelectedItems.Cast<string>().ToArray();
            var result = await RunWorkflowAsync("region", "region", new { selectedTypes, closeUm = close, dilateUm = dilate, minAreaUm2 = minArea, minCells = (int)Math.Round(minCells) });
            if (result is null) return;
            _boundaries.Clear();
            foreach (var item in result.Value.GetProperty("boundaries").EnumerateArray())
                _boundaries.Add((item.GetProperty("label").GetString() ?? string.Empty, item.GetProperty("path").GetString() ?? string.Empty));
            _previewPaths["region"] = result.Value.TryGetProperty("previewPath", out var preview) && preview.ValueKind == JsonValueKind.String ? preview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            DetailHost.Content = BuildRegionView();
        }, primary: true);
        run.Margin = new Thickness(0, 14, 0, 0);
        stack.Children.Add(run);
        return CreatePage(CreateCard(_localization["RegionTitle"], stack), CreateCard(_localization["RegionTitle"], CreateImagePanel(_localization["RegionTitle"], _previewPaths.GetValueOrDefault("region"))));
    }

    private UIElement BuildDistributionView()
    {
        var boundary = new ComboBox { ItemsSource = _boundaries.Select(item => item.Label).ToArray(), SelectedIndex = _boundaries.Count > 0 ? 0 : -1, Width = 360, HorizontalAlignment = HorizontalAlignment.Left };
        var bandWidth = 10.0;
        var stack = new StackPanel();
        stack.Children.Add(new TextBlock { Text = _localization["Boundary"], FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 6) });
        stack.Children.Add(boundary);
        stack.Children.Add(CreateNumberField(_localization["BandWidth"], bandWidth, value => bandWidth = value, "µm"));
        var run = CreateButton(_localization["RunDistribution"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("distribution", "cell_distribution", new
            {
                boundaryLabel = boundary.SelectedItem?.ToString(),
                bandWidthUm = bandWidth,
                selectedCellTypes = _resolvedCellTypes.ToArray(),
            });
            if (result is null) return;
            _previewPaths["distribution"] = result.Value.TryGetProperty("previewPath", out var preview) && preview.ValueKind == JsonValueKind.String ? preview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            DetailHost.Content = BuildDistributionView();
        }, primary: true);
        run.Margin = new Thickness(0, 14, 0, 0);
        stack.Children.Add(run);
        return CreatePage(CreateCard(_localization["DistributionTitle"], stack), CreateCard(_localization["DistributionTitle"], CreateImagePanel(_localization["DistributionTitle"], _previewPaths.GetValueOrDefault("distribution"))));
    }

    private UIElement BuildDistanceView()
    {
        var tabs = new TabControl { Margin = new Thickness(24, 22, 24, 24) };
        tabs.Items.Add(new TabItem { Header = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? "最近邻距离" : "Nearest-neighbor distances", Content = BuildDistancePanel("nearest") });
        tabs.Items.Add(new TabItem { Header = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? "细胞到边界距离" : "Cell-to-boundary distances", Content = BuildDistancePanel("boundary") });
        return tabs;
    }

    private UIElement BuildDistancePanel(string mode)
    {
        var target = new ComboBox { ItemsSource = _resolvedCellTypes, SelectedIndex = _resolvedCellTypes.Count > 0 ? 0 : -1, Width = 320, HorizontalAlignment = HorizontalAlignment.Left };
        var queries = new ListBox { ItemsSource = _resolvedCellTypes, SelectionMode = SelectionMode.Multiple, MinHeight = 110 };
        if (_resolvedCellTypes.Count > 1) queries.SelectedItems.Add(_resolvedCellTypes[1]);
        var boundary = new ComboBox { ItemsSource = _boundaries.Select(item => item.Label).ToArray(), SelectedIndex = _boundaries.Count > 0 ? 0 : -1, Width = 320, HorizontalAlignment = HorizontalAlignment.Left };
        var stack = new StackPanel { Margin = new Thickness(0, 18, 0, 0) };
        stack.Children.Add(new TextBlock { Text = _localization["TargetCellType"], FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 6) });
        stack.Children.Add(target);
        stack.Children.Add(new TextBlock { Text = _localization["QueryCellTypes"], FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 14, 0, 6) });
        stack.Children.Add(queries);
        if (mode == "boundary")
        {
            stack.Children.Add(new TextBlock { Text = _localization["Boundary"], FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 14, 0, 6) });
            stack.Children.Add(boundary);
        }
        var runLabel = mode == "boundary" ? _localization["RunBoundaryDistance"] : _localization["RunNearestDistance"];
        var run = CreateButton(runLabel, async (_, _) =>
        {
            var result = await RunWorkflowAsync("distance", "distance", new
            {
                mode,
                targetType = target.SelectedItem?.ToString(),
                queryTypes = queries.SelectedItems.Cast<string>().ToArray(),
                boundaryLabel = boundary.SelectedItem?.ToString(),
            });
            if (result is null) return;
            _previewPaths[$"distance_{mode}"] = result.Value.TryGetProperty("previewPath", out var preview) && preview.ValueKind == JsonValueKind.String ? preview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            DetailHost.Content = BuildDistanceView();
        }, primary: true);
        run.Margin = new Thickness(0, 16, 0, 0);
        stack.Children.Add(run);
        if (_previewPaths.TryGetValue($"distance_{mode}", out var previewPath))
        {
            var preview = CreateImagePanel(runLabel, previewPath);
            preview.Margin = new Thickness(0, 18, 0, 0);
            stack.Children.Add(preview);
        }
        return new ScrollViewer { Content = CreateCard(runLabel, stack), VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    }

    private UIElement BuildOutputsView()
    {
        var stack = new StackPanel();
        var actions = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        actions.Children.Add(CreateButton(_localization["RefreshOutputs"], async (_, _) => await RefreshOutputsAsync(), primary: true));
        actions.Children.Add(CreateButton(_localization["OpenOutput"], (_, _) => OpenPath(_outputFolder)));
        stack.Children.Add(actions);
        var grid = new DataGrid { ItemsSource = _outputFiles, MinHeight = 520, IsReadOnly = true };
        grid.Columns.Add(new DataGridTextColumn { Header = _localization["Name"], Binding = new Binding(nameof(OutputFileRow.Name)), Width = 240 });
        grid.Columns.Add(new DataGridTextColumn { Header = _localization["GeneratedFiles"], Binding = new Binding(nameof(OutputFileRow.RelativePath)), Width = new DataGridLength(1, DataGridLengthUnitType.Star) });
        grid.Columns.Add(new DataGridTextColumn { Header = _localization["Size"], Binding = new Binding(nameof(OutputFileRow.SizeText)), Width = 110 });
        grid.MouseDoubleClick += (_, _) =>
        {
            if (grid.SelectedItem is OutputFileRow row) OpenPath(Path.Combine(_outputFolder, row.RelativePath));
        };
        stack.Children.Add(grid);
        stack.Children.Add(new TextBlock { Text = _localization["ResultsStayEnglish"], FontSize = 12.5, Foreground = (Brush)FindResource("SecondaryTextBrush"), Margin = new Thickness(0, 10, 0, 0) });
        return CreatePage(CreateCard(_localization["GeneratedFiles"], stack));
    }

    private async Task RefreshOutputsAsync()
    {
        var result = await RunWorkflowAsync("outputs", "outputs", new { });
        if (result is null) return;
        _outputFiles.Clear();
        foreach (var file in result.Value.GetProperty("files").EnumerateArray())
        {
            _outputFiles.Add(new OutputFileRow
            {
                Name = file.GetProperty("name").GetString() ?? string.Empty,
                RelativePath = file.GetProperty("relative_path").GetString() ?? string.Empty,
                SizeBytes = file.GetProperty("size_bytes").GetInt64(),
            });
        }
        DetailHost.Content = BuildOutputsView();
    }

    private async Task<JsonElement?> RunWorkflowAsync(string sectionKey, string command, object payload, bool completesSection = true)
    {
        if (_isBusy)
        {
            SetLocalizedStatus("Running");
            return null;
        }
        var section = _sections.First(item => item.Key == sectionKey);
        if (section.Status == WorkflowStatus.NotStarted)
        {
            SetLocalizedStatus("CompletePreviousSteps", isError: true);
            return null;
        }
        _isBusy = true;
        _activeSectionKey = sectionKey;
        section.Status = WorkflowStatus.Running;
        OperationProgress.Value = 0;
        OperationProgress.Visibility = Visibility.Visible;
        UpdateHeader();
        try
        {
            var result = await _engine.SendAsync(command, payload);
            section.Status = completesSection ? WorkflowStatus.Complete : WorkflowStatus.Ready;
            if (completesSection) MarkNextReady(sectionKey);
            SetLocalizedStatus("AnalysisComplete");
            UpdateProgressMetadata();
            UpdateHeader();
            return result;
        }
        catch (Exception exception)
        {
            section.Status = WorkflowStatus.Error;
            SetStatus(LocalizeEngineError(exception.Message), isError: true);
            UpdateHeader();
            return null;
        }
        finally
        {
            _isBusy = false;
            _activeSectionKey = null;
            OperationProgress.Visibility = Visibility.Collapsed;
        }
    }

    private void MarkNextReady(string sectionKey)
    {
        var index = _sections.ToList().FindIndex(section => section.Key == sectionKey);
        if (index >= 0 && index + 1 < _sections.Count && _sections[index + 1].Status == WorkflowStatus.NotStarted)
            _sections[index + 1].Status = WorkflowStatus.Ready;
    }

    private void InvalidateAfter(string sectionKey)
    {
        var index = _sections.ToList().FindIndex(section => section.Key == sectionKey);
        if (index < 0) return;
        for (var position = index + 1; position < _sections.Count; position++) _sections[position].Status = WorkflowStatus.NotStarted;
        if (_sections[index].Status == WorkflowStatus.Complete) _sections[index].Status = WorkflowStatus.Ready;
        UpdateProgressMetadata();
        UpdateHeader();
    }

    private void EnsureDefaultCellTypes()
    {
        if (_cellTypes.Count > 0 || _channels.Count == 0) return;
        foreach (var channel in _channels.Where(channel => channel.Marker != _nucleusChannel))
        {
            _cellTypes.Add(new CellTypeRow
            {
                Name = channel.Marker,
                ColorHex = channel.ColorHex,
                AllPositive = $"nucleus, {channel.Marker}",
                AllNegative = string.Empty,
                AnyPositiveGroups = string.Empty,
            });
        }
    }

    private void CaptureExportPaths(JsonElement result)
    {
        if (!result.TryGetProperty("artifacts", out var artifacts) || artifacts.ValueKind != JsonValueKind.Array) return;
        foreach (var artifact in artifacts.EnumerateArray())
        {
            var name = artifact.GetProperty("name").GetString();
            var path = artifact.GetProperty("absolutePath").GetString();
            if (!string.IsNullOrWhiteSpace(name) && !string.IsNullOrWhiteSpace(path)) _exportPaths[name] = path;
        }
    }

    private static BitmapImage LoadBoundedBitmap(string path, int decodeWidth)
    {
        var bitmap = new BitmapImage();
        bitmap.BeginInit();
        bitmap.CacheOption = BitmapCacheOption.OnLoad;
        bitmap.DecodePixelWidth = decodeWidth;
        bitmap.UriSource = new Uri(path, UriKind.Absolute);
        bitmap.EndInit();
        bitmap.Freeze();
        return bitmap;
    }

    private void OpenOutputButton_Click(object sender, RoutedEventArgs e) => OpenPath(_outputFolder);

    private static void OpenPath(string? path)
    {
        if (string.IsNullOrWhiteSpace(path) || (!File.Exists(path) && !Directory.Exists(path))) return;
        Process.Start(new ProcessStartInfo { FileName = path, UseShellExecute = true });
    }

    private void SetStatus(string message, bool isError = false)
    {
        _statusResourceKey = null;
        StatusText.Text = message;
        StatusText.Foreground = isError ? (Brush)FindResource("ErrorBrush") : (Brush)FindResource("TextBrush");
    }

    private void SetLocalizedStatus(string resourceKey, bool isError = false)
    {
        _statusResourceKey = resourceKey;
        StatusText.Text = _localization[resourceKey];
        StatusText.Foreground = isError ? (Brush)FindResource("ErrorBrush") : (Brush)FindResource("TextBrush");
    }

    private string LocalizeEngineError(string message)
    {
        if (_localization.EffectiveLanguage != InterfaceLanguage.SimplifiedChinese) return message;
        var lower = message.ToLowerInvariant();
        if (lower.Contains("save the input configuration")) return "请先保存输入配置。";
        if (lower.Contains("input folder was not found")) return "未找到输入文件夹。";
        if (lower.Contains("no csv") || lower.Contains("text image files")) return "输入文件夹中没有找到 CSV 或文本图像文件。";
        if (lower.Contains("nuclei segmentation before cell type")) return "请先运行最终细胞核分割，再进行细胞类型分配。";
        if (lower.Contains("cell type assignment before neighborhood")) return "请先运行细胞类型分配，再进行邻域分析。";
        if (lower.Contains("cell type assignment before region")) return "请先运行细胞类型分配，再进行区域分析。";
        if (lower.Contains("region analysis before cell distribution")) return "请先运行区域分析，再进行细胞分布分析。";
        if (lower.Contains("cell type assignment before distance")) return "请先运行细胞类型分配，再进行距离分析。";
        if (lower.Contains("select at least one")) return "请至少选择一个有效项目。";
        return $"分析失败：{message}";
    }

    private static bool TryReadDouble(string text, out double value) =>
        double.TryParse(text, NumberStyles.Float, CultureInfo.CurrentCulture, out value)
        || double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value);

    private static string[] SplitMarkers(string value) => value
        .Split([',', ';'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
        .Where(item => !string.IsNullOrWhiteSpace(item))
        .ToArray();
}
