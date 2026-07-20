using Microsoft.Win32;
using SpatialScope.Windows.Controls;
using SpatialScope.Windows.Models;
using SpatialScope.Windows.Services;
using System.ComponentModel;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace SpatialScope.Windows;

public partial class MainWindow : Window
{
    private const string NucleusMarker = "Nucleus";

    private sealed class Win32DialogOwner(nint handle) : System.Windows.Forms.IWin32Window
    {
        public nint Handle { get; } = handle;
    }

    private enum ParameterRunMode
    {
        Manual,
        Advanced,
    }

    private static readonly string[] ChannelPalette =
    [
        "#FFFFFF", "#EF476F", "#06D6A0", "#118AB2", "#FFD166", "#8E6CEF",
        "#F78C6B", "#4CC9F0", "#B8DE6F", "#FF70A6", "#70D6FF", "#C77DFF",
    ];

    private static readonly IReadOnlyDictionary<string, string[]> WorkflowPreviewKeys =
        new Dictionary<string, string[]>(StringComparer.Ordinal)
        {
            ["overlay"] = ["overlay", "split"],
            ["nuclei"] = ["nucleiOptimizer", "nuclei"],
            ["cellTypes"] = ["assignmentOptimizer", "cellTypes"],
            ["neighborhood"] = ["neighborhood", "neighborhoodLegend"],
            ["region"] =
            [
                "region",
                "regionOverlay",
                "regionMask",
                "regionOriginal",
                "regionOriginalOverlay",
                "regionOriginalMask",
                "regionMap",
                "regionMapOverlay",
                "regionMapMask",
                "regionCustomized",
                "regionCustomizedOverlay",
                "regionCustomizedMask",
                "regionManualEditor",
                "regionManualAdjusted",
            ],
            ["distribution"] =
            [
                "distribution",
                "distributionBandMap",
                "distributionDensity",
            ],
            ["distance"] = ["distance_nearest", "distance_boundary"],
        };

    private readonly LocalizationService _localization = new();
    private readonly EngineClient _engine = new();
    private readonly ObservableCollection<WorkflowSection> _sections = [];
    private readonly ObservableCollection<ChannelRow> _channels = [];
    private readonly ObservableCollection<CellTypeRow> _cellTypes = [];
    private readonly ObservableCollection<string> _cellTypeMarkerOptions = [NucleusMarker];
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
    private bool _nucleiFixMinimumDiameter = true;
    private bool _nucleiFixMaximumDiameter = true;
    private bool _assignmentFixVoronoiRadius;
    private bool _assignmentFixBufferRadius;
    private double _neighborhoodGridSize = 20;
    private readonly List<string> _regionSelectedCellTypes = [];
    private bool _regionSelectionInitialized;
    private double _regionClosingRadius = 15;
    private double _regionDilationRadius = 10;
    private double _regionMinimumArea = 20000;
    private double _regionMinimumCells = 5;
    private double _regionContourDownsample = 2;
    private double _regionLineWidth = 2;
    private string _regionLineStyle = "-";
    private string _regionBoundaryColor = "#A1D99B";
    private bool _regionUseTypeColors;
    private int _regionSourceWidth;
    private int _regionSourceHeight;
    private readonly ObservableCollection<RegionSummaryRow> _regionRows = [];
    private readonly ObservableCollection<RegionDominantCountRow> _regionDominantCounts = [];
    private readonly List<string> _regionDisplayedBoundaries = [];
    private readonly List<string> _regionDisplayedCellTypes = [];
    private readonly List<string> _regionCustomizedBoundaries = [];
    private readonly List<string> _regionCustomizedCellTypes = [];
    private readonly List<string> _regionManualVisibleBoundaries = [];
    private readonly List<string> _regionManualSeedCellTypes = [];
    private string _regionManualMode = "create";
    private string? _regionManualTargetBoundary;
    private string _regionManualDisplayName = "manual_drawn_ROI";
    private RegionDrawingMode _regionManualDrawingMode = RegionDrawingMode.Polygon;
    private List<List<Point>> _regionManualPolygons = [];
    private double _regionManualClosingRadius = 2;
    private double _regionManualDilationRadius;
    private double _regionManualMinimumArea;
    private double _regionManualMinimumCells = 1;
    private double _regionManualContourDownsample = 1;
    private readonly Dictionary<string, int> _regionPreviewGenerations = [];
    private int? _regionMapRenderedBoundaryCount;
    private int? _regionMapRenderedCellTypeCount;
    private int? _regionMapRenderedCellCount;
    private int _regionManualPreviewGeneration;
    private string? _distributionBoundaryLabel;
    private readonly List<string> _distributionSelectedCellTypes = [];
    private bool _distributionSelectionInitialized;
    private double _distributionBandWidth = 10;
    private string? _nearestDistanceTarget;
    private readonly List<string> _nearestDistanceQueries = [];
    private bool _nearestDistanceQueriesInitialized;
    private readonly List<string> _boundaryDistanceQueries = [];
    private bool _boundaryDistanceQueriesInitialized;
    private string? _distanceBoundaryLabel;
    private ParameterRunMode _nucleiRunMode = ParameterRunMode.Manual;
    private ParameterRunMode _assignmentRunMode = ParameterRunMode.Manual;
    private JsonElement? _pendingNucleiRecommendation;
    private JsonElement? _pendingAssignmentRecommendation;
    private bool _closeInProgress;
    private bool _engineShutdownComplete;
    private int _outputRestoreGeneration;
    private int _cellTypesTabIndex;
    private int _distanceTabIndex;
    private bool _suppressLanguageSelection;
    private bool _isBusy;
    private string? _activeSectionKey;
    private string? _statusResourceKey;

    public IEnumerable<string> CellTypeMarkerOptions => _cellTypeMarkerOptions;
    public string AllPositivePickerLabel => _localization["AllPositive"];
    public string AllNegativePickerLabel => _localization["AllNegative"];
    public string AnyPositivePickerLabel => _localization["AnyPositiveGroups"];
    public string SelectMarkersText => _localization["SelectMarkers"];
    public string ClearSelectionText => _localization["ClearSelection"];
    public string MarkerPickerHelpText => _localization["MarkerPickerHelp"];
    public string MarkerSelectionCountFormat => _localization["MarkerSelectionCountFormat"];

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
        Closing += MainWindow_Closing;
    }

    private async void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        SetLocalizedStatus("EngineStarting");
        try
        {
            await _engine.StartAsync();
            SetLocalizedStatus("EngineReady");
            UpdateCpuText();
            await RestoreQaSessionIfRequestedAsync();
            await CaptureQaScenarioIfRequestedAsync();
        }
        catch (Exception exception)
        {
            SetStatus(exception.Message, isError: true);
            _sections[0].Status = WorkflowStatus.Error;
            UpdateHeader();
        }
    }

    private async void MainWindow_Closing(object? sender, CancelEventArgs e)
    {
        if (_engineShutdownComplete) return;

        e.Cancel = true;
        if (_closeInProgress) return;
        _closeInProgress = true;

        _cpuMonitor.Dispose();
        try
        {
            await _engine.DisposeAsync();
        }
        finally
        {
            _engineShutdownComplete = true;
            Close();
        }
    }

    private async Task RestoreQaSessionIfRequestedAsync()
    {
        var restoreFolder = Environment.GetEnvironmentVariable("SPATIALSCOPE_QA_RESTORE_OUTPUT_FOLDER");
        if (string.IsNullOrWhiteSpace(restoreFolder) || !Directory.Exists(restoreFolder)) return;

        var restoreGeneration = ++_outputRestoreGeneration;
        _outputFolder = Path.GetFullPath(restoreFolder);
        await TryRestoreExistingResultsAsync(_outputFolder, restoreGeneration);
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

        var qaSection = Environment.GetEnvironmentVariable("SPATIALSCOPE_QA_SECTION")?.Trim();
        if (!string.IsNullOrWhiteSpace(qaSection)
            && _sections.Any(section => string.Equals(section.Key, qaSection, StringComparison.Ordinal)))
        {
            SelectSection(qaSection);
        }

        await Dispatcher.InvokeAsync(() => UpdateLayout(), DispatcherPriority.ApplicationIdle);
        if (DetailHost.Content is ScrollViewer detailScroll
            && double.TryParse(
                Environment.GetEnvironmentVariable("SPATIALSCOPE_QA_SCROLL_OFFSET"),
                NumberStyles.Float,
                CultureInfo.InvariantCulture,
                out var scrollOffset)
            && scrollOffset > 0)
        {
            detailScroll.ScrollToVerticalOffset(scrollOffset);
            await Dispatcher.InvokeAsync(() => UpdateLayout(), DispatcherPriority.ApplicationIdle);
        }
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
            ApplyStatusTone("Running", isError: false);
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
        AutomationProperties.SetName(OperationProgress, _localization["Progress"]);
        AutomationProperties.SetName(StatusSurface, _localization["Status"]);

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
            _sections[index].StatusDisplayText = _localization[_sections[index].StatusText];
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
        if (_isBusy || _suppressLanguageSelection || LanguageComboBox.SelectedItem is not ComboBoxItem item) return;
        if (Enum.TryParse(item.Tag?.ToString(), out InterfaceLanguage language)) _localization.SetLanguage(language);
    }

    private void WorkflowRow_Click(object sender, RoutedEventArgs e)
    {
        if (_isBusy) return;
        if (sender is Button { CommandParameter: string key }) SelectSection(key);
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
        RefreshWorkflowStatusLabels();
        var section = CurrentSection;
        HeaderIcon.Text = section.IconGlyph;
        HeaderStep.Text = $"{_localization["Step"]} {section.Number} {_localization["Of"]} {_sections.Count}";
        HeaderTitle.Text = section.Title;
        HeaderSubtitle.Text = section.Subtitle;
        HeaderStatusText.Text = _localization[section.StatusText];
        HeaderStatusText.Foreground = section.StatusForeground;
        HeaderStatusBadge.Background = section.StatusBackground;
    }

    private void RefreshWorkflowStatusLabels()
    {
        foreach (var section in _sections)
            section.StatusDisplayText = _localization[section.StatusText];
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
        OpenOutputButton.IsEnabled = !_isBusy && !string.IsNullOrWhiteSpace(_outputFolder) && Directory.Exists(_outputFolder);
    }

    private void SetInteractionBusy(bool busy)
    {
        _isBusy = busy;
        if (busy) Keyboard.ClearFocus();
        WorkflowItemsControl.IsEnabled = !busy;
        DetailHost.IsEnabled = !busy;
        LanguageComboBox.IsEnabled = !busy;
        OpenOutputButton.IsEnabled = !busy
            && !string.IsNullOrWhiteSpace(_outputFolder)
            && Directory.Exists(_outputFolder);
    }

    private void RefreshSectionViewIfSelected(string sectionKey)
    {
        if (_selectedSectionKey == sectionKey) DetailHost.Content = BuildSectionView(sectionKey);
    }

    private void UpdateCpuText()
    {
        var cpuText = $"{_engine.DefaultCpuWorkers} {_localization["Cpus"]}";
        var gpuBackend = _engine.AnalysisGpuBackend?.Trim();
        if (string.IsNullOrWhiteSpace(gpuBackend))
        {
            CpuValue.Text = $"{cpuText} · {_localization["CpuAnalysis"]} · {_cpuMonitor.CpuPercent:0.0}%";
            CpuValue.ToolTip = null;
            AutomationProperties.SetHelpText(CpuValue, CpuValue.Text);
            return;
        }

        var gpuText = $"{_engine.DetectedGpus.Count} {_localization["Gpus"]}";
        CpuValue.Text = $"{cpuText} · {gpuBackend} · {gpuText} · {_cpuMonitor.CpuPercent:0.0}%";

        var gpuNames = string.Join(Environment.NewLine, _engine.DetectedGpus);
        CpuValue.ToolTip = string.IsNullOrWhiteSpace(gpuNames)
            ? gpuBackend
            : $"{gpuBackend}{Environment.NewLine}{gpuNames}";
        AutomationProperties.SetHelpText(
            CpuValue,
            string.IsNullOrWhiteSpace(gpuNames) ? CpuValue.Text : $"{CpuValue.Text}. {gpuNames}");
    }

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

    private TextBlock CreateSubsectionTitle(string text, Thickness? margin = null) => new()
    {
        Text = text,
        Style = (Style)FindResource("SubsectionTitleTextStyle"),
        Margin = margin ?? new Thickness(0),
    };

    private TextBlock CreateFieldLabel(string text, Thickness? margin = null) => new()
    {
        Text = text,
        Style = (Style)FindResource("FieldLabelTextStyle"),
        Margin = margin ?? new Thickness(0),
        VerticalAlignment = VerticalAlignment.Center,
    };

    private TextBlock CreateSupportingText(string text, Thickness? margin = null) => new()
    {
        Text = text,
        Style = (Style)FindResource("SupportingTextStyle"),
        Margin = margin ?? new Thickness(0),
        LineHeight = 19,
    };

    private Border CreateInlineNotice(string text, bool warning = false)
    {
        var noticeText = CreateSupportingText(text);
        noticeText.Foreground = warning
            ? (Brush)FindResource("ErrorBrush")
            : (Brush)FindResource("MutedTextBrush");
        var panel = new DockPanel();
        var icon = new TextBlock
        {
            Text = warning ? "\uE7BA" : "\uE946",
            FontFamily = new FontFamily("Segoe Fluent Icons, Segoe MDL2 Assets"),
            FontSize = 15,
            Foreground = noticeText.Foreground,
            Width = 25,
            VerticalAlignment = VerticalAlignment.Top,
            Margin = new Thickness(0, 1, 0, 0),
        };
        DockPanel.SetDock(icon, Dock.Left);
        panel.Children.Add(icon);
        panel.Children.Add(noticeText);
        return new Border
        {
            Background = new SolidColorBrush(warning ? Color.FromRgb(253, 242, 242) : Color.FromRgb(242, 247, 248)),
            BorderBrush = new SolidColorBrush(warning ? Color.FromRgb(235, 199, 203) : Color.FromRgb(207, 224, 227)),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(7),
            Padding = new Thickness(12, 10, 12, 10),
            Margin = new Thickness(0, 0, 0, 14),
            Child = panel,
        };
    }

    private void SetActionAvailability(Button button, bool available, string unavailableHelp)
    {
        // Busy state is enforced by disabling the complete detail surface.
        // Keep each action's local state tied only to its prerequisites so a
        // view created during restoration becomes usable when the surface is
        // re-enabled.
        button.IsEnabled = available;
        if (available)
        {
            button.ClearValue(ToolTipProperty);
            AutomationProperties.SetHelpText(button, string.Empty);
        }
        else
        {
            button.ToolTip = unavailableHelp;
            AutomationProperties.SetHelpText(button, unavailableHelp);
        }
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

    private UIElement CreateModeSelector(
        string groupName,
        string manualAutomationId,
        string advancedAutomationId,
        ParameterRunMode selectedMode,
        string manualHelp,
        string advancedHelp,
        Action<ParameterRunMode> selectionChanged)
    {
        var description = CreateSupportingText(
            selectedMode == ParameterRunMode.Manual ? manualHelp : advancedHelp,
            new Thickness(0, 10, 0, 0));
        AutomationProperties.SetLiveSetting(description, AutomationLiveSetting.Polite);

        var manual = new RadioButton
        {
            Content = _localization["ManualMode"],
            GroupName = groupName,
            IsChecked = selectedMode == ParameterRunMode.Manual,
            Style = (Style)FindResource("SegmentedModeButtonStyle"),
        };
        AutomationProperties.SetAutomationId(manual, manualAutomationId);
        AutomationProperties.SetName(manual, _localization["ManualMode"]);
        AutomationProperties.SetHelpText(manual, manualHelp);

        var advanced = new RadioButton
        {
            Content = _localization["AdvancedScreening"],
            GroupName = groupName,
            IsChecked = selectedMode == ParameterRunMode.Advanced,
            Style = (Style)FindResource("SegmentedModeButtonStyle"),
        };
        AutomationProperties.SetAutomationId(advanced, advancedAutomationId);
        AutomationProperties.SetName(advanced, _localization["AdvancedScreening"]);
        AutomationProperties.SetHelpText(advanced, advancedHelp);

        void ApplyMode(ParameterRunMode mode)
        {
            description.Text = mode == ParameterRunMode.Manual ? manualHelp : advancedHelp;
            selectionChanged(mode);
        }

        manual.Checked += (_, _) => ApplyMode(ParameterRunMode.Manual);
        advanced.Checked += (_, _) => ApplyMode(ParameterRunMode.Advanced);

        var choices = new UniformGrid { Rows = 1, Columns = 2, Width = 420 };
        choices.Children.Add(manual);
        choices.Children.Add(advanced);
        var selectorHost = new Border
        {
            Background = new SolidColorBrush(Color.FromRgb(244, 248, 249)),
            BorderBrush = new SolidColorBrush(Color.FromRgb(205, 219, 222)),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(2),
            HorizontalAlignment = HorizontalAlignment.Left,
            Child = choices,
        };

        var stack = new StackPanel();
        stack.Children.Add(selectorHost);
        stack.Children.Add(description);
        return stack;
    }

    private Border CreateOptimizerLockSwitch(
        string label,
        string helpText,
        bool isChecked,
        string automationId,
        Action<bool> selectionChanged)
    {
        var toggle = new CheckBox
        {
            Content = label,
            IsChecked = isChecked,
            Style = (Style)FindResource("OptimizerLockSwitchStyle"),
        };
        AutomationProperties.SetAutomationId(toggle, automationId);
        AutomationProperties.SetName(toggle, label);
        AutomationProperties.SetHelpText(toggle, helpText);
        toggle.Checked += (_, _) => selectionChanged(true);
        toggle.Unchecked += (_, _) => selectionChanged(false);

        var stack = new StackPanel();
        stack.Children.Add(toggle);
        stack.Children.Add(CreateSupportingText(helpText, new Thickness(0, 7, 0, 0)));
        return new Border
        {
            Background = new SolidColorBrush(Color.FromRgb(244, 248, 249)),
            BorderBrush = new SolidColorBrush(Color.FromRgb(214, 224, 226)),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(7),
            Padding = new Thickness(12),
            Margin = new Thickness(0, 0, 10, 0),
            Child = stack,
        };
    }

    private void ClearPendingOptimizerResult(string kind)
    {
        var previewKey = kind == "nuclei" ? "nucleiOptimizer" : "assignmentOptimizer";
        if (kind == "nuclei") _pendingNucleiRecommendation = null;
        else _pendingAssignmentRecommendation = null;
        _previewPaths.Remove(previewKey);
        HideTaggedDetailElement($"preview:{previewKey}");
        HideTaggedDetailElement($"recommendation:{kind}");
    }

    private bool IsNucleiParameterFixed(string key) =>
        (key == "min_diam_um" && _nucleiFixMinimumDiameter)
        || (key == "max_diam_um" && _nucleiFixMaximumDiameter);

    private bool IsAssignmentParameterFixed(string key) =>
        (key == "r_voronoi_um" && _assignmentFixVoronoiRadius)
        || (key == "r_buffer_um" && _assignmentFixBufferRadius);

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
        calibrationGrid.Children.Add(CreateNumberField(_localization["MicrometersWide"], _xMicrometers, value => _xMicrometers = value, "µm", "inputs"));
        calibrationGrid.Children.Add(CreateNumberField(_localization["MicrometersHigh"], _yMicrometers, value => _yMicrometers = value, "µm", "inputs"));
        calibrationGrid.Children.Add(CreateNumberField(_localization["PixelWidth"], _xPixels, value => _xPixels = value, "px", "inputs"));
        calibrationGrid.Children.Add(CreateNumberField(_localization["PixelHeight"], _yPixels, value => _yPixels = value, "px", "inputs"));

        var tools = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        tools.Children.Add(CreateButton(_localization["RescanCsv"], async (_, _) => await SaveConfigurationAsync()));
        tools.Children.Add(CreateButton(_localization["ResetMarkerNames"], (_, _) =>
        {
            foreach (var channel in _channels) channel.Marker = Path.GetFileNameWithoutExtension(channel.FileName);
            _pendingNucleiRecommendation = null;
            _pendingAssignmentRecommendation = null;
            InvalidateAfter("inputs");
        }));
        tools.Children.Add(CreateButton(_localization["ReassignColors"], (_, _) =>
        {
            for (var index = 0; index < _channels.Count; index++) _channels[index].ColorHex = ChannelPalette[index % ChannelPalette.Length];
            _pendingNucleiRecommendation = null;
            _pendingAssignmentRecommendation = null;
            InvalidateAfter("inputs");
        }));
        tools.Children.Add(CreateButton(_localization["SaveConfiguration"], async (_, _) => await SaveConfigurationAsync(), primary: true));

        var channelGrid = new DataGrid { ItemsSource = _channels, MinHeight = 245 };
        channelGrid.CellEditEnding += (_, eventArgs) =>
        {
            if (eventArgs.EditAction != DataGridEditAction.Commit) return;
            _pendingNucleiRecommendation = null;
            _pendingAssignmentRecommendation = null;
            InvalidateAfter("inputs");
        };
        channelGrid.Columns.Add(new DataGridCheckBoxColumn { Header = _localization["Overlay"], Binding = new Binding(nameof(ChannelRow.IncludeInOverlay)), Width = 80 });
        channelGrid.Columns.Add(new DataGridTextColumn { Header = _localization["CsvFile"], Binding = new Binding(nameof(ChannelRow.FileName)), IsReadOnly = true, Width = new DataGridLength(1, DataGridLengthUnitType.Star) });
        channelGrid.Columns.Add(new DataGridTextColumn { Header = _localization["Marker"], Binding = new Binding(nameof(ChannelRow.Marker)) { UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged }, Width = 220 });
        channelGrid.Columns.Add(new DataGridTemplateColumn
        {
            Header = _localization["Color"],
            CellTemplate = (DataTemplate)FindResource("ColorEditorTemplate"),
            Width = 86,
        });

        var registry = new StackPanel();
        registry.Children.Add(tools);
        registry.Children.Add(channelGrid);
        return CreatePage(
            CreateCard(_localization["DataLocations"], locations),
            CreateCard(_localization["SpatialCalibration"], calibrationGrid),
            CreateCard(_localization["ChannelRegistry"], registry));
    }

    private void ColorSwatch_Loaded(object sender, RoutedEventArgs e)
    {
        if (sender is Button button) UpdateColorSwatchAutomation(button);
    }

    private void ColorSwatch_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button) return;
        var currentHex = button.DataContext switch
        {
            ChannelRow channel => channel.ColorHex,
            CellTypeRow cellType => cellType.ColorHex,
            _ => null,
        };
        var current = Colors.White;
        if (!string.IsNullOrWhiteSpace(currentHex))
        {
            try
            {
                if (ColorConverter.ConvertFromString(currentHex) is Color parsedColor) current = parsedColor;
            }
            catch (FormatException)
            {
                // Corrupt legacy color values must never crash the picker.
            }
        }
        using var dialog = new System.Windows.Forms.ColorDialog
        {
            AllowFullOpen = true,
            AnyColor = true,
            FullOpen = true,
            SolidColorOnly = true,
            Color = System.Drawing.Color.FromArgb(current.A, current.R, current.G, current.B),
        };
        var owner = new Win32DialogOwner(new WindowInteropHelper(this).Handle);
        if (dialog.ShowDialog(owner) != System.Windows.Forms.DialogResult.OK)
        {
            button.Focus();
            return;
        }

        var selectedHex = $"#{dialog.Color.R:X2}{dialog.Color.G:X2}{dialog.Color.B:X2}";
        if (!string.Equals(currentHex, selectedHex, StringComparison.OrdinalIgnoreCase))
        {
            switch (button.DataContext)
            {
                case ChannelRow channel:
                    channel.ColorHex = selectedHex;
                    _pendingNucleiRecommendation = null;
                    _pendingAssignmentRecommendation = null;
                    InvalidateAfter("inputs");
                    break;
                case CellTypeRow cellType:
                    cellType.ColorHex = selectedHex;
                    _pendingAssignmentRecommendation = null;
                    InvalidateAfter("cellTypes");
                    break;
            }
        }
        UpdateColorSwatchAutomation(button);
        button.Focus();
    }

    private void UpdateColorSwatchAutomation(Button button)
    {
        var (name, color) = button.DataContext switch
        {
            ChannelRow channel => (channel.Marker, channel.ColorHex),
            CellTypeRow cellType => (cellType.Name, cellType.ColorHex),
            _ => (string.Empty, string.Empty),
        };
        var label = string.IsNullOrWhiteSpace(name)
            ? _localization["ChooseColor"]
            : $"{_localization["ChooseColor"]}: {name}";
        AutomationProperties.SetName(button, label);
        AutomationProperties.SetHelpText(button, $"{label}. {color}");
        button.ToolTip = label;
    }

    private void AddFolderRow(Grid grid, int row, string label, string path, Action choose)
    {
        var labelBlock = CreateFieldLabel(label);
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
        AutomationProperties.SetName(pathBox, label);
        AutomationProperties.SetHelpText(pathBox, _localization["ChooseFolder"]);
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

    private UIElement CreateNumberField(
        string label,
        double value,
        Action<double> setter,
        string unit,
        string? invalidationSectionKey = null,
        Func<double, double>? normalize = null,
        double minimum = double.Epsilon)
    {
        var committedValue = normalize?.Invoke(value) ?? value;
        var panel = new StackPanel { Margin = new Thickness(0, 0, 14, 0) };
        panel.Children.Add(CreateFieldLabel(label, new Thickness(0, 0, 0, 6)));
        var row = new DockPanel();
        var unitText = new TextBlock { Text = unit, Margin = new Thickness(7, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center, Foreground = (Brush)FindResource("SecondaryTextBrush") };
        DockPanel.SetDock(unitText, Dock.Right);
        row.Children.Add(unitText);
        var editor = new TextBox { Text = committedValue.ToString("0.###", CultureInfo.CurrentCulture) };
        AutomationProperties.SetName(editor, label);
        AutomationProperties.SetHelpText(editor, string.IsNullOrWhiteSpace(unit) ? label : $"{label} ({unit})");
        editor.LostFocus += (_, _) =>
        {
            if (TryReadDouble(editor.Text, out var parsed) && parsed >= minimum)
            {
                var normalized = normalize?.Invoke(parsed) ?? parsed;
                editor.Text = normalized.ToString("0.###", CultureInfo.CurrentCulture);
                if (AreClose(normalized, committedValue)) return;

                committedValue = normalized;
                setter(normalized);
                UpdateProgressMetadata();
                if (invalidationSectionKey is not null) InvalidateAfter(invalidationSectionKey);
            }
            else editor.Text = committedValue.ToString("0.###", CultureInfo.CurrentCulture);
        };
        row.Children.Add(editor);
        panel.Children.Add(row);
        return panel;
    }

    private void ChooseInputFolder()
    {
        if (_isBusy) return;
        var selected = ChooseFolder(_inputFolder);
        if (selected is null) return;
        _inputFolder = selected;
        _pendingNucleiRecommendation = null;
        _pendingAssignmentRecommendation = null;
        InvalidateAfter("inputs");
        RefreshSectionViewIfSelected("inputs");
    }

    private async void ChooseOutputFolder()
    {
        if (_isBusy) return;
        var selected = ChooseFolder(_outputFolder);
        if (selected is null) return;
        var restoreGeneration = ++_outputRestoreGeneration;
        _outputFolder = selected;
        var restoreResult = await TryRestoreExistingResultsAsync(selected, restoreGeneration);
        if (restoreGeneration != _outputRestoreGeneration || !PathsEqual(_outputFolder, selected)) return;
        if (restoreResult == true) return;
        ResetLoadedResults();
        InvalidateAfter("inputs");
        RefreshSectionViewIfSelected("inputs");
        if (restoreResult == false) SetLocalizedStatus("NoExistingResults");
    }

    private string? ChooseFolder(string initialPath)
    {
        var dialog = new OpenFolderDialog { Title = _localization["ChooseFolder"], Multiselect = false };
        if (Directory.Exists(initialPath)) dialog.InitialDirectory = initialPath;
        return dialog.ShowDialog(this) == true ? dialog.FolderName : null;
    }

    private async Task<bool?> TryRestoreExistingResultsAsync(string selectedFolder, int restoreGeneration)
    {
        if (string.IsNullOrWhiteSpace(selectedFolder)) return false;
        var alreadyBusy = _isBusy;
        if (!alreadyBusy) SetInteractionBusy(true);
        SetLocalizedStatus("CheckingExistingResults");
        try
        {
            var response = await _engine.SendAsync("restore", new { outputFolder = selectedFolder });
            if (restoreGeneration != _outputRestoreGeneration || !PathsEqual(_outputFolder, selectedFolder)) return null;
            if (!response.TryGetProperty("restored", out var restored) || !restored.GetBoolean()) return false;
            ValidateRestoredHistory(response);
            ApplyRestoredHistory(response, selectedFolder);
            try
            {
                await RefreshExactRegionPreviewsAsync(useWorkflowStatus: false);
            }
            catch (Exception previewException)
            {
                SetStatus(
                    $"{_localization["ExistingResultsRestored"]} "
                    + $"{_localization["RegionPreviewRefreshFailed"]}: "
                    + LocalizeEngineError(previewException.Message));
            }
            RefreshSectionViewIfSelected("region");
            return true;
        }
        catch (Exception exception)
        {
            if (restoreGeneration != _outputRestoreGeneration) return null;
            SetStatus($"{_localization["RestoreFailed"]}: {LocalizeEngineError(exception.Message)}", isError: true);
            return null;
        }
        finally
        {
            if (!alreadyBusy) SetInteractionBusy(false);
        }
    }

    private static void ValidateRestoredHistory(JsonElement response)
    {
        var configuration = response.GetProperty("configuration");
        if (configuration.ValueKind != JsonValueKind.Object
            || configuration.GetProperty("channels").ValueKind != JsonValueKind.Array
            || configuration.GetProperty("pixelSizeUm").ValueKind != JsonValueKind.Array
            || response.GetProperty("workflow").ValueKind != JsonValueKind.Object)
        {
            throw new InvalidDataException("The saved SpatialScope history has an invalid structure.");
        }
        foreach (var channel in configuration.GetProperty("channels").EnumerateArray())
        {
            _ = channel.GetProperty("file").GetString();
            _ = channel.GetProperty("channel").GetString();
            _ = channel.GetProperty("colorHex").GetString();
            _ = channel.GetProperty("includeOverlay").GetBoolean();
        }
    }

    private void ApplyRestoredHistory(JsonElement response, string selectedFolder)
    {
        ResetLoadedResults();
        _nucleusChannel = string.Empty;
        var configuration = response.GetProperty("configuration");
        _inputFolder = configuration.GetProperty("inputFolder").GetString() ?? string.Empty;
        // A result folder may have been copied or moved. The folder selected
        // in the current dialog is always authoritative for future outputs.
        _outputFolder = selectedFolder;
        if (configuration.TryGetProperty("pixelSizeUm", out var pixelSize) && pixelSize.GetArrayLength() >= 2)
        {
            _xPixels = 1000;
            _yPixels = 1000;
            _xMicrometers = pixelSize[0].GetDouble() * _xPixels;
            _yMicrometers = pixelSize[1].GetDouble() * _yPixels;
        }

        _channels.Clear();
        foreach (var item in configuration.GetProperty("channels").EnumerateArray())
        {
            _channels.Add(new ChannelRow
            {
                FileName = item.GetProperty("file").GetString() ?? string.Empty,
                Marker = item.GetProperty("channel").GetString() ?? string.Empty,
                ColorHex = item.GetProperty("colorHex").GetString() ?? "#FFFFFF",
                IncludeInOverlay = item.GetProperty("includeOverlay").GetBoolean(),
            });
        }

        if (response.TryGetProperty("nucleiParameters", out var nucleiParameters)
            && nucleiParameters.ValueKind == JsonValueKind.Object)
        {
            foreach (var parameter in ParameterCatalog.Nuclei)
            {
                if (TryGetJsonNumber(nucleiParameters, parameter.Key, out var value)
                    || TryGetJsonNumber(nucleiParameters, parameter.Key.ToUpperInvariant(), out value))
                {
                    _nucleiValues[parameter.Key] = value;
                }
            }
            if (TryGetJsonString(nucleiParameters, "nucleus_channel", out var nucleusChannel)
                || TryGetJsonString(nucleiParameters, "NUCLEUS_CHANNEL", out nucleusChannel))
            {
                _nucleusChannel = nucleusChannel;
            }
        }
        if (string.IsNullOrWhiteSpace(_nucleusChannel)
            || !_channels.Any(channel => string.Equals(channel.Marker, _nucleusChannel, StringComparison.Ordinal)))
        {
            _nucleusChannel = ChooseDefaultNucleusChannel();
        }

        if (response.TryGetProperty("assignmentParameters", out var assignmentParameters)
            && assignmentParameters.ValueKind == JsonValueKind.Object)
        {
            foreach (var parameter in ParameterCatalog.Assignment)
            {
                if (TryGetJsonNumber(assignmentParameters, parameter.Key, out var value))
                    _assignmentValues[parameter.Key] = value;
            }
            if (TryGetJsonString(assignmentParameters, "thresh_mode", out var thresholdMode))
                _thresholdMode = thresholdMode;
            if (assignmentParameters.TryGetProperty("resolve_ambiguous", out var resolve)
                && resolve.ValueKind is JsonValueKind.True or JsonValueKind.False)
                _resolveAmbiguous = resolve.GetBoolean();
        }

        _cellTypes.Clear();
        if (response.TryGetProperty("cellTypes", out var cellTypes) && cellTypes.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in cellTypes.EnumerateArray())
            {
                _cellTypes.Add(new CellTypeRow
                {
                    Name = item.TryGetProperty("name", out var name) ? name.GetString() ?? string.Empty : string.Empty,
                    ColorHex = item.TryGetProperty("color_hex", out var color) ? color.GetString() ?? "#FFFFFF" : "#FFFFFF",
                    AllPositive = WithDefaultNucleusMarker(JoinJsonStrings(item, "all_pos")),
                    AllNegative = JoinJsonStrings(item, "all_neg"),
                    AnyPositiveGroups = JoinJsonGroups(item, "any_pos_groups"),
                });
            }
        }
        EnsureDefaultCellTypes();

        _pendingNucleiRecommendation = response.TryGetProperty("nucleiRecommendation", out var nucleiRecommendation)
            && nucleiRecommendation.ValueKind == JsonValueKind.Object
            && nucleiRecommendation.EnumerateObject().Any()
                ? nucleiRecommendation.Clone()
                : null;
        _pendingAssignmentRecommendation = response.TryGetProperty("assignmentRecommendation", out var assignmentRecommendation)
            && assignmentRecommendation.ValueKind == JsonValueKind.Object
            && assignmentRecommendation.EnumerateObject().Any()
                ? assignmentRecommendation.Clone()
                : null;

        _previewPaths.Clear();
        if (response.TryGetProperty("previewPaths", out var previewPaths))
        {
            foreach (var property in previewPaths.EnumerateObject())
                if (property.Value.ValueKind == JsonValueKind.String) _previewPaths[property.Name] = property.Value.GetString() ?? string.Empty;
        }
        _exportPaths.Clear();
        CaptureExportPaths(response);

        _resolvedCellTypes.Clear();
        if (response.TryGetProperty("resolvedCellTypes", out var resolvedCellTypes))
            _resolvedCellTypes.AddRange(resolvedCellTypes.EnumerateArray().Select(item => item.GetString() ?? string.Empty).Where(item => item.Length > 0));

        var hasAnalysisParameters = response.TryGetProperty("analysisParameters", out var analysisParameters)
            && analysisParameters.ValueKind == JsonValueKind.Object;
        if (hasAnalysisParameters) ApplyRestoredAnalysisParameters(analysisParameters);
        ApplyRegionResultPayload(response, replaceBoundaries: true);

        _outputFiles.Clear();
        if (response.TryGetProperty("files", out var files))
        {
            foreach (var file in files.EnumerateArray())
            {
                _outputFiles.Add(new OutputFileRow
                {
                    Name = file.GetProperty("name").GetString() ?? string.Empty,
                    RelativePath = file.GetProperty("relative_path").GetString() ?? string.Empty,
                    SizeBytes = file.GetProperty("size_bytes").GetInt64(),
                });
            }
        }

        var workflow = response.GetProperty("workflow");
        var firstMissingDownstreamStep = int.MaxValue;
        foreach (var (key, step) in new[]
                 {
                     ("neighborhood", 5),
                     ("region", 6),
                     ("distribution", 7),
                     ("distance", 8),
                 })
        {
            var stageComplete = workflow.TryGetProperty(key, out var complete)
                && complete.ValueKind == JsonValueKind.True;
            if (stageComplete && (!hasAnalysisParameters || !HasRestoredStageSettings(analysisParameters, key)))
            {
                firstMissingDownstreamStep = step;
                break;
            }
        }
        var downstreamHistoryDowngraded = firstMissingDownstreamStep != int.MaxValue;
        var previousComplete = true;
        foreach (var section in _sections)
        {
            var savedComplete = workflow.TryGetProperty(section.Key, out var completeValue) && completeValue.GetBoolean();
            if (section.Number >= firstMissingDownstreamStep) savedComplete = false;
            if (previousComplete && savedComplete)
            {
                section.Status = WorkflowStatus.Complete;
            }
            else if (previousComplete)
            {
                section.Status = WorkflowStatus.Ready;
                previousComplete = false;
            }
            else
            {
                section.Status = WorkflowStatus.NotStarted;
            }
        }

        if (downstreamHistoryDowngraded)
        {
            foreach (var section in _sections.Where(section => section.Number >= firstMissingDownstreamStep))
                RemoveWorkflowPreviews(section.Key);
            if (firstMissingDownstreamStep <= 6)
            {
                _boundaries.Clear();
                _regionRows.Clear();
                _regionDominantCounts.Clear();
                _regionDisplayedBoundaries.Clear();
                _regionDisplayedCellTypes.Clear();
                _regionCustomizedBoundaries.Clear();
                _regionCustomizedCellTypes.Clear();
                _regionManualVisibleBoundaries.Clear();
                _regionManualSeedCellTypes.Clear();
                _regionManualPolygons = [];
            }
            _outputFiles.Clear();
        }

        UpdateProgressMetadata();
        var lastComplete = _sections.LastOrDefault(section => section.Status == WorkflowStatus.Complete) ?? _sections[0];
        SelectSection(lastComplete.Key);
        if (downstreamHistoryDowngraded)
        {
            SetStatus($"{_localization["ExistingResultsRestored"]} {_localization["RestoredDownstreamSettingsMissing"]}");
        }
        else if (response.TryGetProperty("warnings", out var warnings) && warnings.GetArrayLength() > 0)
        {
            SetStatus($"{_localization["ExistingResultsRestored"]} {warnings[0].GetString()}");
        }
        else
        {
            SetLocalizedStatus("ExistingResultsRestored");
        }
    }

    private void ApplyRestoredAnalysisParameters(JsonElement analysisParameters)
    {
        if (analysisParameters.TryGetProperty("neighborhood", out var neighborhood)
            && neighborhood.ValueKind == JsonValueKind.Object
            && TryGetJsonNumber(neighborhood, "gridSizeUm", out var gridSize)
            && gridSize > 0)
        {
            _neighborhoodGridSize = gridSize;
        }

        if (analysisParameters.TryGetProperty("region", out var region)
            && region.ValueKind == JsonValueKind.Object)
        {
            if (TryGetJsonStringArray(region, "selectedTypes", out var selectedTypes))
            {
                _regionSelectedCellTypes.Clear();
                _regionSelectedCellTypes.AddRange(selectedTypes);
                _regionSelectionInitialized = true;
            }
            if (TryGetJsonNumber(region, "closeUm", out var closeUm) && double.IsFinite(closeUm))
                _regionClosingRadius = Math.Clamp(closeUm, 0, 80);
            if (TryGetJsonNumber(region, "dilateUm", out var dilateUm) && double.IsFinite(dilateUm))
                _regionDilationRadius = Math.Clamp(dilateUm, 0, 80);
            if (TryGetJsonNumber(region, "minAreaUm2", out var minAreaUm2) && double.IsFinite(minAreaUm2))
                _regionMinimumArea = Math.Clamp(minAreaUm2, 0, 1_000_000_000);
            if (TryGetJsonNumber(region, "minCells", out var minCells) && double.IsFinite(minCells))
                _regionMinimumCells = Math.Round(Math.Clamp(minCells, 1, 1_000_000));
            if (TryGetJsonNumber(region, "contourDownsample", out var contourDownsample))
                _regionContourDownsample = NormalizeRegionContourDownsample(contourDownsample);
            if (TryGetJsonNumber(region, "lineWidth", out var lineWidth) && double.IsFinite(lineWidth))
                _regionLineWidth = Math.Clamp(lineWidth, 0.5, 10);
            if (TryGetJsonString(region, "lineStyle", out var lineStyle))
                _regionLineStyle = lineStyle is "-" or "--" or "-." or ":" ? lineStyle : "-";
            if (TryGetJsonString(region, "boundaryColor", out var boundaryColor))
                _regionBoundaryColor = boundaryColor;
            if (region.TryGetProperty("useTypeColors", out var useTypeColors)
                && useTypeColors.ValueKind is JsonValueKind.True or JsonValueKind.False)
                _regionUseTypeColors = useTypeColors.GetBoolean();
        }

        if (analysisParameters.TryGetProperty("distribution", out var distribution)
            && distribution.ValueKind == JsonValueKind.Object)
        {
            if (TryGetJsonString(distribution, "boundaryLabel", out var boundaryLabel))
                _distributionBoundaryLabel = boundaryLabel;
            if (TryGetJsonStringArray(distribution, "selectedCellTypes", out var selectedCellTypes))
            {
                _distributionSelectedCellTypes.Clear();
                _distributionSelectedCellTypes.AddRange(selectedCellTypes);
                _distributionSelectionInitialized = true;
            }
            if (TryGetJsonNumber(distribution, "bandWidthUm", out var bandWidthUm) && bandWidthUm > 0)
                _distributionBandWidth = bandWidthUm;
        }

        if (!analysisParameters.TryGetProperty("distance", out var distance)
            || distance.ValueKind != JsonValueKind.Object)
        {
            return;
        }

        if (distance.TryGetProperty("nearest", out var nearest) && nearest.ValueKind == JsonValueKind.Object)
        {
            if (TryGetJsonString(nearest, "targetType", out var targetType))
                _nearestDistanceTarget = targetType;
            if (TryGetJsonStringArray(nearest, "queryTypes", out var queryTypes))
            {
                _nearestDistanceQueries.Clear();
                _nearestDistanceQueries.AddRange(queryTypes);
                _nearestDistanceQueriesInitialized = true;
            }
        }

        if (distance.TryGetProperty("boundary", out var boundary) && boundary.ValueKind == JsonValueKind.Object)
        {
            // Older Windows sessions serialized a targetType for boundary
            // distance runs even though that calculation never uses a target.
            // Deliberately ignore that legacy value while restoring the fields
            // that still belong to the boundary workflow.
            if (TryGetJsonStringArray(boundary, "queryTypes", out var queryTypes))
            {
                _boundaryDistanceQueries.Clear();
                _boundaryDistanceQueries.AddRange(queryTypes);
                _boundaryDistanceQueriesInitialized = true;
            }
            if (TryGetJsonString(boundary, "boundaryLabel", out var boundaryLabel))
                _distanceBoundaryLabel = boundaryLabel;
        }

        if (TryGetJsonString(distance, "lastMode", out var lastMode))
            _distanceTabIndex = string.Equals(lastMode, "boundary", StringComparison.OrdinalIgnoreCase) ? 1 : 0;
        else if (!distance.TryGetProperty("nearest", out _) && distance.TryGetProperty("boundary", out _))
            _distanceTabIndex = 1;
    }

    private static bool HasRestoredStageSettings(JsonElement analysisParameters, string key)
    {
        if (!analysisParameters.TryGetProperty(key, out var settings)
            || settings.ValueKind != JsonValueKind.Object)
        {
            return false;
        }
        if (key != "distance") return settings.EnumerateObject().Any();
        return (settings.TryGetProperty("nearest", out var nearest)
                && nearest.ValueKind == JsonValueKind.Object
                && nearest.EnumerateObject().Any())
            || (settings.TryGetProperty("boundary", out var boundary)
                && boundary.ValueKind == JsonValueKind.Object
                && boundary.EnumerateObject().Any());
    }

    private void ResetLoadedResults()
    {
        _previewPaths.Clear();
        _exportPaths.Clear();
        _outputFiles.Clear();
        _resolvedCellTypes.Clear();
        _boundaries.Clear();
        _pendingNucleiRecommendation = null;
        _pendingAssignmentRecommendation = null;
        _neighborhoodGridSize = 20;
        _regionSelectedCellTypes.Clear();
        _regionSelectionInitialized = false;
        _regionClosingRadius = 15;
        _regionDilationRadius = 10;
        _regionMinimumArea = 20000;
        _regionMinimumCells = 5;
        _regionContourDownsample = 2;
        _regionLineWidth = 2;
        _regionLineStyle = "-";
        _regionBoundaryColor = "#A1D99B";
        _regionUseTypeColors = false;
        _regionSourceWidth = 0;
        _regionSourceHeight = 0;
        _regionRows.Clear();
        _regionDominantCounts.Clear();
        _regionDisplayedBoundaries.Clear();
        _regionDisplayedCellTypes.Clear();
        _regionCustomizedBoundaries.Clear();
        _regionCustomizedCellTypes.Clear();
        _regionManualVisibleBoundaries.Clear();
        _regionManualSeedCellTypes.Clear();
        _regionManualMode = "create";
        _regionManualTargetBoundary = null;
        _regionManualDisplayName = "manual_drawn_ROI";
        _regionManualDrawingMode = RegionDrawingMode.Polygon;
        _regionManualPolygons = [];
        _regionManualClosingRadius = 2;
        _regionManualDilationRadius = 0;
        _regionManualMinimumArea = 0;
        _regionManualMinimumCells = 1;
        _regionManualContourDownsample = 1;
        _regionPreviewGenerations.Clear();
        _regionMapRenderedBoundaryCount = null;
        _regionMapRenderedCellTypeCount = null;
        _regionMapRenderedCellCount = null;
        ++_regionManualPreviewGeneration;
        _distributionBoundaryLabel = null;
        _distributionSelectedCellTypes.Clear();
        _distributionSelectionInitialized = false;
        _distributionBandWidth = 10;
        _nearestDistanceTarget = null;
        _nearestDistanceQueries.Clear();
        _nearestDistanceQueriesInitialized = false;
        _boundaryDistanceQueries.Clear();
        _boundaryDistanceQueriesInitialized = false;
        _distanceBoundaryLabel = null;
        _distanceTabIndex = 0;
    }

    private async Task SaveConfigurationAsync()
    {
        if (_isBusy)
        {
            SetLocalizedStatus("Running");
            return;
        }
        if (string.IsNullOrWhiteSpace(_inputFolder) || string.IsNullOrWhiteSpace(_outputFolder))
        {
            SetLocalizedStatus("SelectFoldersFirst", isError: true);
            return;
        }
        ResetLoadedResults();
        InvalidateAfter("inputs");
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
        _nucleusChannel = ChooseDefaultNucleusChannel();
        EnsureDefaultCellTypes();
        SetLocalizedStatus("ConfigurationSaved");
        UpdateProgressMetadata();
        RefreshSectionViewIfSelected("inputs");
    }

    private UIElement BuildOverlayView()
    {
        var action = new StackPanel();
        var canRun = _sections.First(section => section.Key == "overlay").Status != WorkflowStatus.NotStarted;
        if (!canRun) action.Children.Add(CreateInlineNotice(_localization["CompletePreviousSteps"], warning: true));
        var generate = CreateButton(_localization["GenerateOverlay"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("overlay", "overlay", new { clipHighPercentile = 99.8 });
            if (result is null) return;
            var previews = result.Value.GetProperty("previewPaths");
            _previewPaths["overlay"] = previews.GetProperty("overlay").GetString() ?? string.Empty;
            _previewPaths["split"] = previews.GetProperty("splitChannels").GetString() ?? string.Empty;
            CaptureExportPaths(result.Value);
            RefreshSectionViewIfSelected("overlay");
        }, primary: true);
        SetActionAvailability(generate, canRun, _localization["CompletePreviousSteps"]);
        action.Children.Add(generate);

        var previewsPanel = new Grid();
        previewsPanel.ColumnDefinitions.Add(new ColumnDefinition());
        previewsPanel.ColumnDefinitions.Add(new ColumnDefinition());
        var overlayPanel = CreateImagePanel(
            _localization["OverlayPreview"],
            _previewPaths.GetValueOrDefault("overlay"),
            _exportPaths.GetValueOrDefault("overlay.png"),
            previewKey: "overlay");
        overlayPanel.Margin = new Thickness(0, 0, 9, 0);
        previewsPanel.Children.Add(overlayPanel);
        var splitPanel = CreateImagePanel(
            _localization["SplitChannelsPreview"],
            _previewPaths.GetValueOrDefault("split"),
            _exportPaths.GetValueOrDefault("split_channels.png"),
            previewKey: "split");
        splitPanel.Margin = new Thickness(9, 0, 0, 0);
        Grid.SetColumn(splitPanel, 1);
        previewsPanel.Children.Add(splitPanel);
        return CreatePage(
            CreateCard(_localization["GeneratePreview"], action),
            CreateCard(_localization["Preview"], previewsPanel));
    }

    private Border CreateImagePanel(
        string title,
        string? previewPath,
        string? originalPath = null,
        string? emptyDetail = null,
        string? previewKey = null)
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
        heading.Children.Add(CreateSubsectionTitle(title));
        panel.Children.Add(heading);
        if (!string.IsNullOrWhiteSpace(previewPath) && File.Exists(previewPath))
        {
            var imageTransform = new MatrixTransform(Matrix.Identity);
            var image = new Image
            {
                Source = LoadBoundedBitmap(previewPath, 3000),
                Stretch = Stretch.Uniform,
                // Fill the viewport's layout slot and let Stretch.Uniform fit the
                // entire bitmap inside it. Center alignment lets WPF retain the
                // bitmap's natural desired size, so wide/square scientific images
                // can be clipped by a narrower app window before zoom is applied.
                HorizontalAlignment = HorizontalAlignment.Stretch,
                VerticalAlignment = VerticalAlignment.Stretch,
                RenderTransform = imageTransform,
                RenderTransformOrigin = new Point(0, 0),
            };
            RenderOptions.SetBitmapScalingMode(image, BitmapScalingMode.HighQuality);

            var viewport = new Grid
            {
                Height = 430,
                Background = Brushes.Black,
                ClipToBounds = true,
                Focusable = true,
                Cursor = Cursors.Arrow,
            };
            viewport.Children.Add(image);
            var viewerFrame = new Border
            {
                Background = Brushes.Black,
                BorderBrush = (Brush)FindResource("PanelBorderBrush"),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(7),
                Padding = new Thickness(6),
                Child = viewport,
            };
            AutomationProperties.SetName(viewport, title);
            AutomationProperties.SetAutomationId(viewport, $"PlotViewer_{Path.GetFileNameWithoutExtension(previewPath)}");
            AutomationProperties.SetHelpText(viewport, _localization["PlotZoomHelp"]);

            Point? dragOrigin = null;
            Matrix dragMatrix = Matrix.Identity;
            Matrix ClampView(Matrix value)
            {
                var zoom = value.M11;
                if (zoom <= 1 || viewport.ActualWidth <= 0 || viewport.ActualHeight <= 0) return Matrix.Identity;
                value.OffsetX = Math.Clamp(value.OffsetX, viewport.ActualWidth * (1 - zoom), 0);
                value.OffsetY = Math.Clamp(value.OffsetY, viewport.ActualHeight * (1 - zoom), 0);
                return value;
            }
            void SetView(Matrix value)
            {
                imageTransform.Matrix = ClampView(value);
                viewport.Cursor = imageTransform.Matrix.M11 > 1 ? Cursors.Hand : Cursors.Arrow;
            }
            void ResetView()
            {
                imageTransform.Matrix = Matrix.Identity;
                viewport.Cursor = Cursors.Arrow;
            }
            void ZoomAt(Point position, double factor)
            {
                var current = imageTransform.Matrix;
                var oldScale = current.M11;
                var newScale = Math.Clamp(oldScale * factor, 1, 8);
                if (AreClose(oldScale, newScale)) return;
                if (AreClose(newScale, 1))
                {
                    ResetView();
                    return;
                }
                var applied = newScale / oldScale;
                SetView(new Matrix(
                    newScale,
                    0,
                    0,
                    newScale,
                    position.X - ((position.X - current.OffsetX) * applied),
                    position.Y - ((position.Y - current.OffsetY) * applied)));
            }
            void PanBy(double x, double y)
            {
                var value = imageTransform.Matrix;
                if (value.M11 <= 1) return;
                value.OffsetX += x;
                value.OffsetY += y;
                SetView(value);
            }
            void EndDrag()
            {
                dragOrigin = null;
                if (viewport.IsMouseCaptured) viewport.ReleaseMouseCapture();
                viewport.Cursor = imageTransform.Matrix.M11 > 1 ? Cursors.Hand : Cursors.Arrow;
            }

            viewport.PreviewMouseWheel += (_, e) =>
            {
                // The plot sits inside a vertically scrolling workflow page. A
                // plain wheel gesture must keep scrolling that page; otherwise
                // users accidentally zoom the plot while trying to reach it and
                // are left seeing only part of the scientific field. Ctrl+wheel
                // is the deliberate zoom gesture, alongside +/- keyboard zoom.
                if (!Keyboard.Modifiers.HasFlag(ModifierKeys.Control)) return;
                var oldScale = imageTransform.Matrix.M11;
                ZoomAt(e.GetPosition(viewport), e.Delta > 0 ? 1.18 : 1 / 1.18);
                e.Handled = !AreClose(oldScale, imageTransform.Matrix.M11);
            };
            viewport.PreviewMouseLeftButtonDown += (_, e) =>
            {
                viewport.Focus();
                if (e.ClickCount >= 2)
                {
                    ResetView();
                    e.Handled = true;
                    return;
                }
                if (imageTransform.Matrix.M11 <= 1) return;
                dragOrigin = e.GetPosition(viewport);
                dragMatrix = imageTransform.Matrix;
                viewport.CaptureMouse();
                viewport.Cursor = Cursors.SizeAll;
                e.Handled = true;
            };
            viewport.PreviewMouseMove += (_, e) =>
            {
                if (dragOrigin is null || e.LeftButton != MouseButtonState.Pressed) return;
                var current = e.GetPosition(viewport);
                var value = dragMatrix;
                value.OffsetX += current.X - dragOrigin.Value.X;
                value.OffsetY += current.Y - dragOrigin.Value.Y;
                imageTransform.Matrix = ClampView(value);
                e.Handled = true;
            };
            viewport.PreviewMouseLeftButtonUp += (_, e) =>
            {
                if (dragOrigin is null) return;
                EndDrag();
                e.Handled = true;
            };
            viewport.LostMouseCapture += (_, _) => EndDrag();
            viewport.SizeChanged += (_, _) => SetView(imageTransform.Matrix);
            viewport.GotKeyboardFocus += (_, _) => viewerFrame.BorderBrush = SystemColors.HighlightBrush;
            viewport.LostKeyboardFocus += (_, _) => viewerFrame.BorderBrush = (Brush)FindResource("PanelBorderBrush");
            viewport.PreviewKeyDown += (_, e) =>
            {
                var center = new Point(viewport.ActualWidth / 2, viewport.ActualHeight / 2);
                switch (e.Key)
                {
                    case Key.Add:
                    case Key.OemPlus:
                        ZoomAt(center, 1.18);
                        e.Handled = true;
                        break;
                    case Key.Subtract:
                    case Key.OemMinus:
                        ZoomAt(center, 1 / 1.18);
                        e.Handled = true;
                        break;
                    case Key.D0:
                    case Key.NumPad0:
                        ResetView();
                        e.Handled = true;
                        break;
                    case Key.Left when imageTransform.Matrix.M11 > 1:
                        PanBy(24, 0);
                        e.Handled = true;
                        break;
                    case Key.Right when imageTransform.Matrix.M11 > 1:
                        PanBy(-24, 0);
                        e.Handled = true;
                        break;
                    case Key.Up when imageTransform.Matrix.M11 > 1:
                        PanBy(0, 24);
                        e.Handled = true;
                        break;
                    case Key.Down when imageTransform.Matrix.M11 > 1:
                        PanBy(0, -24);
                        e.Handled = true;
                        break;
                    case Key.Escape:
                        EndDrag();
                        e.Handled = true;
                        break;
                }
            };

            panel.Children.Add(viewerFrame);
            panel.Children.Add(CreateSupportingText(_localization["PlotZoomHelp"], new Thickness(0, 7, 0, 0)));
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
                        new TextBlock { Text = _localization["NoPreview"], Style = (Style)FindResource("CardTitleTextStyle"), HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0) },
                        new TextBlock { Text = emptyDetail ?? _localization["NoPreviewDetail"], Style = (Style)FindResource("SupportingTextStyle"), Margin = new Thickness(0, 7, 0, 0), MaxWidth = 380, TextAlignment = TextAlignment.Center },
                    },
                },
            });
        }
        return new Border
        {
            Child = panel,
            Tag = string.IsNullOrWhiteSpace(previewKey) ? null : $"preview:{previewKey}",
        };
    }

    private void HideTaggedDetailElement(string tag)
    {
        if (DetailHost.Content is not DependencyObject root) return;

        static void HideMatchingDescendants(DependencyObject current, string expectedTag)
        {
            if (current is FrameworkElement { Tag: string value } element
                && string.Equals(value, expectedTag, StringComparison.Ordinal))
            {
                element.Visibility = Visibility.Collapsed;
            }

            for (var index = 0; index < VisualTreeHelper.GetChildrenCount(current); index++)
                HideMatchingDescendants(VisualTreeHelper.GetChild(current, index), expectedTag);
        }

        HideMatchingDescendants(root, tag);
    }

    private UIElement BuildNucleiView()
    {
        var canRun = _sections.First(section => section.Key == "nuclei").Status != WorkflowStatus.NotStarted;
        var channelRow = new Grid();
        channelRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(200) });
        channelRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(320) });
        channelRow.Children.Add(CreateFieldLabel(_localization["Channel"]));
        var channelPicker = new ComboBox { ItemsSource = _channels.Select(item => item.Marker).ToArray(), SelectedItem = _nucleusChannel };
        AutomationProperties.SetName(channelPicker, _localization["NucleusChannel"]);
        channelPicker.SelectionChanged += (_, _) =>
        {
            var selected = channelPicker.SelectedItem?.ToString() ?? string.Empty;
            if (string.Equals(selected, _nucleusChannel, StringComparison.Ordinal)) return;
            _nucleusChannel = selected;
            _pendingNucleiRecommendation = null;
            _pendingAssignmentRecommendation = null;
            InvalidateAfter("nuclei");
        };
        Grid.SetColumn(channelPicker, 1);
        channelRow.Children.Add(channelPicker);

        var parameterGrid = new UniformGrid { Columns = 2 };
        foreach (var parameter in ParameterCatalog.Nuclei) parameterGrid.Children.Add(CreateParameterEditor(parameter, _nucleiValues));
        var optimizerStack = new StackPanel();
        optimizerStack.Children.Add(CreateSupportingText(_localization["NucleiOptimizerHelp"], new Thickness(0, 0, 0, 10)));
        optimizerStack.Children.Add(CreateSubsectionTitle(
            _localization["FixedDuringScreening"],
            new Thickness(0, 4, 0, 6)));
        optimizerStack.Children.Add(CreateSupportingText(
            _localization["FixedDuringScreeningHelp"],
            new Thickness(0, 0, 0, 10)));
        var nucleiLocks = new UniformGrid { Columns = 2, Margin = new Thickness(0, 0, 0, 14) };
        nucleiLocks.Children.Add(CreateOptimizerLockSwitch(
            _localization["MinimumDiameter"],
            _localization["MinimumDiameterLockHelp"],
            _nucleiFixMinimumDiameter,
            "NucleiFixMinimumDiameter",
            value =>
            {
                if (_nucleiFixMinimumDiameter == value) return;
                _nucleiFixMinimumDiameter = value;
                ClearPendingOptimizerResult("nuclei");
            }));
        nucleiLocks.Children.Add(CreateOptimizerLockSwitch(
            _localization["MaximumDiameter"],
            _localization["MaximumDiameterLockHelp"],
            _nucleiFixMaximumDiameter,
            "NucleiFixMaximumDiameter",
            value =>
            {
                if (_nucleiFixMaximumDiameter == value) return;
                _nucleiFixMaximumDiameter = value;
                ClearPendingOptimizerResult("nuclei");
            }));
        optimizerStack.Children.Add(nucleiLocks);
        optimizerStack.Children.Add(CreateNumberField(
            _localization["OptimizerBudget"],
            _nucleiOptimizerBudget,
            value => _nucleiOptimizerBudget = value,
            "",
            normalize: value => Math.Clamp(Math.Round(value), 1, 4096)));
        var optimizerButton = CreateButton(_localization["RunOptimizer"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("nuclei", "nuclei_optimizer", new
            {
                parameters = BuildNucleiPayload(),
                fixedParameterKeys = new[]
                {
                    _nucleiFixMinimumDiameter ? "min_diam_um" : null,
                    _nucleiFixMaximumDiameter ? "max_diam_um" : null,
                }.Where(key => key is not null).Cast<string>().ToArray(),
                maxEvaluations = (int)_nucleiOptimizerBudget,
                parallelWorkers = Math.Max(1, Environment.ProcessorCount),
                parallelBackend = "threading",
                useFixedRoiSubset = true,
            }, completesSection: false);
            if (result is null) return;
            _pendingNucleiRecommendation = ExtractRecommendation(result.Value);
            _previewPaths["nucleiOptimizer"] = result.Value.TryGetProperty("previewPath", out var preview) && preview.ValueKind == JsonValueKind.String ? preview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            RefreshSectionViewIfSelected("nuclei");
        }, primary: true);
        optimizerButton.Margin = new Thickness(0, 12, 0, 0);
        SetActionAvailability(optimizerButton, canRun, _localization["CompletePreviousSteps"]);
        optimizerStack.Children.Add(optimizerButton);
        StackPanel? recommendationPanel = null;
        if (_pendingNucleiRecommendation is JsonElement nucleiRecommendation)
        {
            recommendationPanel = new StackPanel { Tag = "recommendation:nuclei" };
            recommendationPanel.Children.Add(CreateSupportingText(_localization["SuggestedComboReady"], new Thickness(0, 12, 0, 8)));
            var applySuggestion = CreateButton(_localization["ApplySuggestedCombo"], async (_, _) =>
            {
                if (_pendingNucleiRecommendation is not JsonElement currentRecommendation
                    || !string.Equals(currentRecommendation.GetRawText(), nucleiRecommendation.GetRawText(), StringComparison.Ordinal))
                {
                    HideTaggedDetailElement("recommendation:nuclei");
                    SetLocalizedStatus("SuggestedComboExpired", isError: true);
                    return;
                }
                var appliedParameters = _nucleiValues.ToDictionary(
                    item => item.Key,
                    item => (object?)item.Value);
                appliedParameters["nucleus_channel"] = _nucleusChannel;
                foreach (var property in nucleiRecommendation.EnumerateObject())
                {
                    if (property.Value.ValueKind == JsonValueKind.Number && !IsNucleiParameterFixed(property.Name))
                        appliedParameters[property.Name] = property.Value.GetDouble();
                }
                if (!await PersistAppliedRecommendationAsync("nuclei", appliedParameters)) return;
                ApplyNucleiRecommendation(nucleiRecommendation);
                _pendingNucleiRecommendation = null;
                SetLocalizedStatus("SuggestedComboApplied");
                RefreshSectionViewIfSelected("nuclei");
            }, primary: true);
            AutomationProperties.SetAutomationId(applySuggestion, "ApplyNucleiSuggestedCombo");
            recommendationPanel.Children.Add(applySuggestion);
        }
        if (_pendingNucleiRecommendation is not null
            || _previewPaths.TryGetValue("nucleiOptimizer", out _))
        {
            var optimizerPreview = _previewPaths.GetValueOrDefault("nucleiOptimizer");
            var previewPanel = CreateImagePanel(_localization["AdvancedScreening"], optimizerPreview, previewKey: "nucleiOptimizer");
            previewPanel.Margin = new Thickness(0, 16, 0, 0);
            optimizerStack.Children.Add(previewPanel);
        }
        if (recommendationPanel is not null)
        {
            recommendationPanel.Margin = new Thickness(0, 14, 0, 0);
            optimizerStack.Children.Add(recommendationPanel);
        }
        var actions = new WrapPanel { Margin = new Thickness(0, 14, 0, 0) };
        var runNuclei = CreateButton(_localization["RunNuclei"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("nuclei", "nuclei", new
            {
                parameters = BuildNucleiPayload(),
                nativeThreads = Math.Max(1, Environment.ProcessorCount),
            });
            if (result is null) return;
            _previewPaths["nuclei"] = result.Value.TryGetProperty("previewPath", out var preview) ? preview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            RefreshSectionViewIfSelected("nuclei");
        }, primary: true);
        SetActionAvailability(runNuclei, canRun, _localization["CompletePreviousSteps"]);
        actions.Children.Add(runNuclei);
        var parameterStack = new StackPanel();
        parameterStack.Children.Add(parameterGrid);
        var finalStack = new StackPanel();
        finalStack.Children.Add(actions);
        var finalPreview = CreateImagePanel(
            _localization["SegmentationPreview"],
            _previewPaths.GetValueOrDefault("nuclei"),
            emptyDetail: _localization["RunAnalysisForPreview"],
            previewKey: "nuclei");
        finalPreview.Margin = new Thickness(0, 16, 0, 0);
        finalStack.Children.Add(finalPreview);

        var optimizerCard = CreateCard(_localization["AdvancedScreening"], optimizerStack);
        optimizerCard.Visibility = _nucleiRunMode == ParameterRunMode.Advanced ? Visibility.Visible : Visibility.Collapsed;
        var modeSelector = CreateModeSelector(
            "NucleiRunMode",
            "NucleiModeManual",
            "NucleiModeAdvanced",
            _nucleiRunMode,
            _localization["NucleiManualModeHelp"],
            _localization["NucleiAdvancedModeHelp"],
            mode =>
            {
                _nucleiRunMode = mode;
                optimizerCard.Visibility = mode == ParameterRunMode.Advanced ? Visibility.Visible : Visibility.Collapsed;
            });

        return CreatePage(
            CreateCard(_localization["NucleusChannel"], channelRow),
            CreateCard(_localization["RunMode"], modeSelector),
            optimizerCard,
            CreateCard(_localization["FinalRunParameters"], parameterStack),
            CreateCard(_localization["RunAndReview"], finalStack));
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
        row.Children.Add(CreateFieldLabel(name));
        var editorRow = new DockPanel();
        if (!string.IsNullOrWhiteSpace(parameter.Unit))
        {
            var unit = new TextBlock { Text = parameter.Unit, Margin = new Thickness(6, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center, Foreground = (Brush)FindResource("SecondaryTextBrush") };
            DockPanel.SetDock(unit, Dock.Right);
            editorRow.Children.Add(unit);
        }
        var editor = new TextBox { Text = values[parameter.Key].ToString("0.###", CultureInfo.CurrentCulture), FontFamily = new FontFamily("Cascadia Mono, Consolas") };
        AutomationProperties.SetName(editor, name);
        AutomationProperties.SetHelpText(editor, $"{parameter.Minimum:0.###}–{parameter.Maximum:0.###} {parameter.Unit}".Trim());
        editor.LostFocus += (_, _) =>
        {
            if (TryReadDouble(editor.Text, out var parsed))
            {
                var normalized = Math.Clamp(parsed, parameter.Minimum, parameter.Maximum);
                editor.Text = normalized.ToString("0.###", CultureInfo.CurrentCulture);
                if (AreClose(normalized, values[parameter.Key])) return;

                values[parameter.Key] = normalized;
                if (ReferenceEquals(values, _assignmentValues))
                {
                    _pendingAssignmentRecommendation = null;
                    InvalidateAfter("cellTypes");
                }
                else
                {
                    _pendingNucleiRecommendation = null;
                    InvalidateAfter("nuclei");
                }
            }
            else editor.Text = values[parameter.Key].ToString("0.###", CultureInfo.CurrentCulture);
        };
        editor.ToolTip = $"{parameter.Minimum:0.###}–{parameter.Maximum:0.###}";
        editorRow.Children.Add(editor);
        Grid.SetColumn(editorRow, 1);
        row.Children.Add(editorRow);
        stack.Children.Add(row);
        stack.Children.Add(CreateSupportingText(
            _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? parameter.ChineseExplanation : parameter.EnglishExplanation,
            new Thickness(0, 6, 0, 0)));
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

    private static JsonElement? ExtractRecommendation(JsonElement result)
    {
        if (!result.TryGetProperty("recommendedParameters", out var recommended)
            || recommended.ValueKind != JsonValueKind.Object
            || !recommended.EnumerateObject().Any())
        {
            return null;
        }
        return recommended.Clone();
    }

    private void ApplyNucleiRecommendation(JsonElement recommended)
    {
        if (recommended.ValueKind != JsonValueKind.Object) return;
        foreach (var property in recommended.EnumerateObject())
        {
            if (_nucleiValues.ContainsKey(property.Name)
                && property.Value.ValueKind == JsonValueKind.Number
                && !IsNucleiParameterFixed(property.Name))
                _nucleiValues[property.Name] = property.Value.GetDouble();
        }
        InvalidateAfter("nuclei");
    }

    private UIElement BuildCellTypesView()
    {
        RefreshCellTypeMarkerOptions();
        EnsureDefaultCellTypes();
        var tabs = new TabControl
        {
            Margin = new Thickness(24, 22, 24, 24),
            Style = (Style)FindResource("WorkflowTabControlStyle"),
        };
        tabs.Items.Add(new TabItem
        {
            Header = _localization["MarkerRules"],
            Content = BuildCellTypeRulesPanel(),
            Style = (Style)FindResource("WorkflowTabItemStyle"),
        });
        tabs.Items.Add(new TabItem
        {
            Header = _localization["ScreeningAndAssignment"],
            Content = BuildAssignmentPanel(),
            Style = (Style)FindResource("WorkflowTabItemStyle"),
        });
        tabs.SelectedIndex = Math.Clamp(_cellTypesTabIndex, 0, tabs.Items.Count - 1);
        tabs.SelectionChanged += (_, _) =>
        {
            if (tabs.SelectedIndex >= 0) _cellTypesTabIndex = tabs.SelectedIndex;
        };
        return tabs;
    }

    private UIElement BuildCellTypeRulesPanel()
    {
        var stack = new StackPanel { Margin = new Thickness(0, 18, 0, 0) };
        var grid = new DataGrid
        {
            ItemsSource = _cellTypes,
            MinHeight = 420,
            RowHeight = 48,
            SelectionMode = DataGridSelectionMode.Single,
        };
        var actions = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        actions.Children.Add(CreateButton(_localization["AddCellType"], (_, _) =>
        {
            var added = new CellTypeRow
            {
                Name = $"Cell type {_cellTypes.Count + 1}",
                ColorHex = ChannelPalette[(_cellTypes.Count + 2) % ChannelPalette.Length],
                AllPositive = NucleusMarker,
            };
            _cellTypes.Add(added);
            grid.SelectedItem = added;
            InvalidateCellTypeInputs();
        }));
        var remove = CreateButton(_localization["Remove"], (_, _) =>
        {
            if (grid.SelectedItem is not CellTypeRow selected) return;
            _cellTypes.Remove(selected);
            InvalidateCellTypeInputs();
        });
        remove.IsEnabled = false;
        remove.ToolTip = _localization["SelectRowToRemove"];
        AutomationProperties.SetHelpText(remove, _localization["SelectRowToRemove"]);
        actions.Children.Add(remove);
        stack.Children.Add(actions);
        grid.SelectionChanged += (_, _) =>
        {
            var canRemove = grid.SelectedItem is CellTypeRow && !_isBusy;
            remove.IsEnabled = canRemove;
            remove.ToolTip = canRemove ? null : _localization["SelectRowToRemove"];
            AutomationProperties.SetHelpText(remove, canRemove ? string.Empty : _localization["SelectRowToRemove"]);
        };
        grid.CellEditEnding += (_, eventArgs) =>
        {
            if (eventArgs.EditAction == DataGridEditAction.Commit) InvalidateCellTypeInputs();
        };
        grid.Columns.Add(new DataGridTextColumn { Header = _localization["CellTypeName"], Binding = new Binding(nameof(CellTypeRow.Name)) { UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged }, Width = 170 });
        grid.Columns.Add(new DataGridTemplateColumn
        {
            Header = _localization["Color"],
            CellTemplate = (DataTemplate)FindResource("ColorEditorTemplate"),
            Width = 86,
        });
        grid.Columns.Add(new DataGridTemplateColumn
        {
            Header = _localization["AllPositive"],
            CellTemplate = (DataTemplate)FindResource("AllPositiveMarkerEditorTemplate"),
            Width = new DataGridLength(1, DataGridLengthUnitType.Star),
        });
        grid.Columns.Add(new DataGridTemplateColumn
        {
            Header = _localization["AllNegative"],
            CellTemplate = (DataTemplate)FindResource("AllNegativeMarkerEditorTemplate"),
            Width = new DataGridLength(1, DataGridLengthUnitType.Star),
        });
        grid.Columns.Add(new DataGridTemplateColumn
        {
            Header = _localization["AnyPositiveGroups"],
            CellTemplate = (DataTemplate)FindResource("AnyPositiveMarkerEditorTemplate"),
            Width = new DataGridLength(1, DataGridLengthUnitType.Star),
        });
        stack.Children.Add(grid);
        stack.Children.Add(CreateSupportingText(
            _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese
                ? "单击每个标记框可查看所有可用标记，并可选择多个。“全部阳性”要求每个所选标记均为阳性；“任一阳性”只需至少一个所选标记为阳性。"
                : "Click a marker box to see every available marker and select more than one. All-positive requires every selected marker; Any-positive requires at least one selected marker.",
            new Thickness(0, 10, 0, 0)));
        return new ScrollViewer
        {
            Content = CreateCard(_localization["MarkerRules"], stack),
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        };
    }

    private void CellTypeMarkerSelectionChanged(object sender, RoutedEventArgs e)
    {
        InvalidateCellTypeInputs();
        e.Handled = true;
    }

    private UIElement BuildAssignmentPanel()
    {
        var canRun = _sections.First(section => section.Key == "cellTypes").Status != WorkflowStatus.NotStarted;
        var parameters = new UniformGrid { Columns = 2 };
        foreach (var parameter in ParameterCatalog.Assignment) parameters.Children.Add(CreateParameterEditor(parameter, _assignmentValues));

        var thresholdPanel = new StackPanel { Margin = new Thickness(0, 16, 0, 0) };
        var thresholdLabel = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? "阈值模式" : "Threshold mode";
        thresholdPanel.Children.Add(CreateSubsectionTitle(thresholdLabel));
        var threshold = new ComboBox { Width = 280, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 6, 0, 0) };
        AutomationProperties.SetName(threshold, thresholdLabel);
        threshold.Items.Add(new ComboBoxItem { Content = "Global Otsu", Tag = "global_otsu" });
        threshold.Items.Add(new ComboBoxItem { Content = "Local", Tag = "local" });
        threshold.Items.Add(new ComboBoxItem { Content = "Yen", Tag = "yen" });
        threshold.Items.Add(new ComboBoxItem { Content = "Triangle", Tag = "triangle" });
        threshold.SelectedIndex = _thresholdMode switch { "local" => 1, "yen" => 2, "triangle" => 3, _ => 0 };
        threshold.SelectionChanged += (_, _) =>
        {
            var selectedMode = ((ComboBoxItem)threshold.SelectedItem).Tag?.ToString() ?? "global_otsu";
            if (string.Equals(selectedMode, _thresholdMode, StringComparison.Ordinal)) return;
            _thresholdMode = selectedMode;
            InvalidateCellTypeInputs();
        };
        thresholdPanel.Children.Add(threshold);
        thresholdPanel.Children.Add(CreateSupportingText(
            _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese
                ? "Global Otsu 是通用默认值；Local 与 macOS 兼容并使用相同的 Otsu 判定；Yen 对稀疏亮信号通常更严格；Triangle 保留用于兼容旧版 Windows 结果。"
                : "Global Otsu is the general default. Local matches the macOS-compatible Otsu behavior, Yen is often stricter for sparse bright signal, and Triangle remains available for older Windows results.",
            new Thickness(0, 6, 0, 0)));
        var ambiguousPanel = new StackPanel();
        var resolve = new CheckBox
        {
            IsChecked = _resolveAmbiguous,
            Content = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? "使用概率证据解析模糊细胞" : "Resolve ambiguous cells using probability evidence",
            FontWeight = FontWeights.SemiBold,
            Margin = new Thickness(0),
        };
        resolve.Checked += (_, _) =>
        {
            if (_resolveAmbiguous) return;
            _resolveAmbiguous = true;
            InvalidateCellTypeInputs();
        };
        resolve.Unchecked += (_, _) =>
        {
            if (!_resolveAmbiguous) return;
            _resolveAmbiguous = false;
            InvalidateCellTypeInputs();
        };
        ambiguousPanel.Children.Add(resolve);
        ambiguousPanel.Children.Add(CreateSupportingText(
            _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese
                ? "启用后，符合条件的多重匹配细胞可按概率重新分配；禁用后，所有多重匹配细胞都保留为 Ambiguous。"
                : "When enabled, eligible multi-match cells may be reassigned by probability; when disabled, every multi-match cell remains Ambiguous.",
            new Thickness(0, 6, 0, 0)));

        var optimizerStack = new StackPanel();
        optimizerStack.Children.Add(CreateSupportingText(_localization["AssignmentOptimizerHelp"], new Thickness(0, 0, 0, 10)));
        optimizerStack.Children.Add(CreateSubsectionTitle(
            _localization["FixedDuringScreening"],
            new Thickness(0, 4, 0, 6)));
        optimizerStack.Children.Add(CreateSupportingText(
            _localization["FixedDuringScreeningHelp"],
            new Thickness(0, 0, 0, 10)));
        var assignmentLocks = new UniformGrid { Columns = 2, Margin = new Thickness(0, 0, 0, 14) };
        assignmentLocks.Children.Add(CreateOptimizerLockSwitch(
            _localization["VoronoiRadius"],
            _localization["VoronoiRadiusLockHelp"],
            _assignmentFixVoronoiRadius,
            "AssignmentFixVoronoiRadius",
            value =>
            {
                if (_assignmentFixVoronoiRadius == value) return;
                _assignmentFixVoronoiRadius = value;
                ClearPendingOptimizerResult("assignment");
            }));
        assignmentLocks.Children.Add(CreateOptimizerLockSwitch(
            _localization["BufferRadius"],
            _localization["BufferRadiusLockHelp"],
            _assignmentFixBufferRadius,
            "AssignmentFixBufferRadius",
            value =>
            {
                if (_assignmentFixBufferRadius == value) return;
                _assignmentFixBufferRadius = value;
                ClearPendingOptimizerResult("assignment");
            }));
        optimizerStack.Children.Add(assignmentLocks);
        optimizerStack.Children.Add(CreateNumberField(
            _localization["OptimizerBudget"],
            _assignmentOptimizerBudget,
            value => _assignmentOptimizerBudget = value,
            "",
            normalize: value => Math.Clamp(Math.Round(value), 1, 4096)));
        var optimize = CreateButton(_localization["RunAssignmentOptimizer"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("cellTypes", "celltype_optimizer", new
            {
                cellTypes = BuildCellTypePayload(),
                parameters = BuildAssignmentPayload(),
                fixedParameterKeys = new[]
                {
                    _assignmentFixVoronoiRadius ? "r_voronoi_um" : null,
                    _assignmentFixBufferRadius ? "r_buffer_um" : null,
                }.Where(key => key is not null).Cast<string>().ToArray(),
                maxEvaluations = (int)_assignmentOptimizerBudget,
                parallelWorkers = Math.Max(1, Environment.ProcessorCount),
                parallelBackend = "threading",
                useFixedRoiSubset = true,
            }, completesSection: false);
            if (result is null) return;
            _pendingAssignmentRecommendation = ExtractRecommendation(result.Value);
            _previewPaths["assignmentOptimizer"] = result.Value.TryGetProperty("previewPath", out var preview) && preview.ValueKind == JsonValueKind.String ? preview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            RefreshSectionViewIfSelected("cellTypes");
        });
        optimize.Style = (Style)FindResource("PrimaryButtonStyle");
        optimize.Margin = new Thickness(0, 10, 0, 0);
        SetActionAvailability(optimize, canRun && _cellTypes.Count > 0, _localization["CompletePreviousSteps"]);
        optimizerStack.Children.Add(optimize);
        StackPanel? assignmentRecommendationPanel = null;
        if (_pendingAssignmentRecommendation is JsonElement assignmentRecommendation)
        {
            assignmentRecommendationPanel = new StackPanel { Tag = "recommendation:assignment" };
            assignmentRecommendationPanel.Children.Add(CreateSupportingText(_localization["SuggestedComboReady"], new Thickness(0, 12, 0, 8)));
            var applySuggestion = CreateButton(_localization["ApplySuggestedCombo"], async (_, _) =>
            {
                if (_pendingAssignmentRecommendation is not JsonElement currentRecommendation
                    || !string.Equals(currentRecommendation.GetRawText(), assignmentRecommendation.GetRawText(), StringComparison.Ordinal))
                {
                    HideTaggedDetailElement("recommendation:assignment");
                    SetLocalizedStatus("SuggestedComboExpired", isError: true);
                    return;
                }
                var appliedParameters = _assignmentValues.ToDictionary(
                    item => item.Key,
                    item => (object?)item.Value);
                appliedParameters["thresh_mode"] = _thresholdMode;
                appliedParameters["resolve_ambiguous"] = _resolveAmbiguous;
                foreach (var property in assignmentRecommendation.EnumerateObject())
                {
                    if (property.Value.ValueKind == JsonValueKind.Number && !IsAssignmentParameterFixed(property.Name))
                        appliedParameters[property.Name] = property.Value.GetDouble();
                    else if (property.Name == "thresh_mode" && property.Value.ValueKind == JsonValueKind.String)
                        appliedParameters[property.Name] = property.Value.GetString();
                    else if (property.Name == "resolve_ambiguous" && property.Value.ValueKind is JsonValueKind.True or JsonValueKind.False)
                        appliedParameters[property.Name] = property.Value.GetBoolean();
                }
                if (!await PersistAppliedRecommendationAsync("assignment", appliedParameters)) return;
                ApplyAssignmentRecommendation(assignmentRecommendation);
                _pendingAssignmentRecommendation = null;
                SetLocalizedStatus("SuggestedComboApplied");
                RefreshSectionViewIfSelected("cellTypes");
            }, primary: true);
            AutomationProperties.SetAutomationId(applySuggestion, "ApplyAssignmentSuggestedCombo");
            assignmentRecommendationPanel.Children.Add(applySuggestion);
        }
        if (_pendingAssignmentRecommendation is not null
            || _previewPaths.TryGetValue("assignmentOptimizer", out _))
        {
            var assignmentOptimizerPreview = _previewPaths.GetValueOrDefault("assignmentOptimizer");
            var previewPanel = CreateImagePanel(_localization["AdvancedScreening"], assignmentOptimizerPreview, previewKey: "assignmentOptimizer");
            previewPanel.Margin = new Thickness(0, 14, 0, 0);
            optimizerStack.Children.Add(previewPanel);
        }
        if (assignmentRecommendationPanel is not null)
        {
            assignmentRecommendationPanel.Margin = new Thickness(0, 14, 0, 0);
            optimizerStack.Children.Add(assignmentRecommendationPanel);
        }
        var run = CreateButton(_localization["RunAssignment"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("cellTypes", "celltype_assignment", new
            {
                nucleusChannel = _nucleusChannel,
                cellTypes = BuildCellTypePayload(),
                parameters = BuildAssignmentPayload(),
                nativeThreads = Math.Max(1, Environment.ProcessorCount),
                supportWorkers = Math.Max(1, Environment.ProcessorCount),
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
            RefreshSectionViewIfSelected("cellTypes");
        }, primary: true);
        run.Margin = new Thickness(0, 18, 0, 0);
        SetActionAvailability(run, canRun && _cellTypes.Count > 0, _localization["CompletePreviousSteps"]);

        var settingsStack = new StackPanel();
        settingsStack.Children.Add(parameters);
        settingsStack.Children.Add(thresholdPanel);

        var finalRunStack = new StackPanel();
        finalRunStack.Children.Add(CreateSupportingText(_localization["ResultsStayEnglish"]));
        finalRunStack.Children.Add(run);
        if (_previewPaths.TryGetValue("cellTypes", out var previewPath) && !string.IsNullOrWhiteSpace(previewPath))
        {
            var preview = CreateImagePanel(_localization["CellTypesTitle"], previewPath, previewKey: "cellTypes");
            preview.Margin = new Thickness(0, 18, 0, 0);
            finalRunStack.Children.Add(preview);
        }

        var optimizerCard = CreateCard(_localization["AdvancedScreening"], optimizerStack);
        optimizerCard.Visibility = _assignmentRunMode == ParameterRunMode.Advanced ? Visibility.Visible : Visibility.Collapsed;
        var modeSelector = CreateModeSelector(
            "AssignmentRunMode",
            "AssignmentModeManual",
            "AssignmentModeAdvanced",
            _assignmentRunMode,
            _localization["AssignmentManualModeHelp"],
            _localization["AssignmentAdvancedModeHelp"],
            mode =>
            {
                _assignmentRunMode = mode;
                optimizerCard.Visibility = mode == ParameterRunMode.Advanced ? Visibility.Visible : Visibility.Collapsed;
            });

        var content = new StackPanel { Margin = new Thickness(0, 18, 0, 0) };
        if (!canRun) content.Children.Add(CreateInlineNotice(_localization["CompletePreviousSteps"], warning: true));
        content.Children.Add(CreateCard(_localization["AssignmentMode"], modeSelector));
        content.Children.Add(optimizerCard);
        content.Children.Add(CreateCard(_localization["AssignmentSettings"], settingsStack));
        content.Children.Add(CreateCard(_localization["AmbiguousResolution"], ambiguousPanel));
        content.Children.Add(CreateCard(_localization["RunAndReview"], finalRunStack));
        return new ScrollViewer
        {
            Content = content,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
        };
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

    private void ApplyAssignmentRecommendation(JsonElement recommended)
    {
        if (recommended.ValueKind != JsonValueKind.Object) return;
        foreach (var property in recommended.EnumerateObject())
        {
            if (_assignmentValues.ContainsKey(property.Name)
                && property.Value.ValueKind == JsonValueKind.Number
                && !IsAssignmentParameterFixed(property.Name))
                _assignmentValues[property.Name] = property.Value.GetDouble();
            else if (property.Name == "thresh_mode" && property.Value.ValueKind == JsonValueKind.String)
                _thresholdMode = property.Value.GetString() ?? "global_otsu";
            else if (property.Name == "resolve_ambiguous" && property.Value.ValueKind is JsonValueKind.True or JsonValueKind.False)
                _resolveAmbiguous = property.Value.GetBoolean();
        }
        InvalidateAfter("cellTypes");
    }

    private async Task<bool> PersistAppliedRecommendationAsync(
        string kind,
        IReadOnlyDictionary<string, object?> parameters)
    {
        if (_isBusy)
        {
            SetLocalizedStatus("AnalysisAlreadyRunning", isError: true);
            return false;
        }

        SetInteractionBusy(true);
        UpdateHeader();
        try
        {
            await _engine.SendAsync("apply_recommendation", new { kind, parameters });
            return true;
        }
        catch (Exception exception)
        {
            SetStatus(LocalizeEngineError(exception.Message), isError: true);
            return false;
        }
        finally
        {
            SetInteractionBusy(false);
            UpdateHeader();
        }
    }

    private void InvalidateCellTypeInputs()
    {
        _pendingAssignmentRecommendation = null;
        InvalidateAfter("cellTypes");
    }

    private UIElement BuildNeighborhoodView()
    {
        var field = CreateNumberField(
            _localization["GridSize"],
            _neighborhoodGridSize,
            value => _neighborhoodGridSize = value,
            "µm",
            "neighborhood");
        var stack = new StackPanel();
        var hasCellTypes = _resolvedCellTypes.Count > 0;
        var workflowReady = _sections.First(section => section.Key == "neighborhood").Status != WorkflowStatus.NotStarted;
        if (!workflowReady) stack.Children.Add(CreateInlineNotice(_localization["CompletePreviousSteps"], warning: true));
        else if (!hasCellTypes) stack.Children.Add(CreateInlineNotice(_localization["PrerequisiteCellTypes"], warning: true));
        stack.Children.Add(field);
        var run = CreateButton(_localization["RunNeighborhood"], async (_, _) =>
        {
            var result = await RunWorkflowAsync("neighborhood", "neighborhood", new { gridSizeUm = _neighborhoodGridSize });
            if (result is null) return;
            _previewPaths["neighborhood"] = result.Value.TryGetProperty("previewPath", out var preview) && preview.ValueKind == JsonValueKind.String ? preview.GetString() ?? string.Empty : string.Empty;
            _previewPaths["neighborhoodLegend"] = result.Value.TryGetProperty("legendPreviewPath", out var legendPreview) && legendPreview.ValueKind == JsonValueKind.String ? legendPreview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            RefreshSectionViewIfSelected("neighborhood");
        }, primary: true);
        run.Margin = new Thickness(0, 14, 0, 0);
        SetActionAvailability(
            run,
            workflowReady && hasCellTypes,
            !workflowReady ? _localization["CompletePreviousSteps"] : _localization["PrerequisiteCellTypes"]);
        stack.Children.Add(run);
        var keyStack = new StackPanel();
        keyStack.Children.Add(CreateSupportingText(
            _localization["NeighborhoodClusterKeyHelp"],
            new Thickness(0, 0, 0, 10)));
        keyStack.Children.Add(CreateImagePanel(
            _localization["ColorLegend"],
            _previewPaths.GetValueOrDefault("neighborhoodLegend"),
            emptyDetail: _localization["NeighborhoodClusterKeyEmpty"],
            previewKey: "neighborhoodLegend"));

        return CreatePage(
            CreateCard(_localization["AnalysisSettings"], stack),
            CreateCard(_localization["NeighborhoodMap"], CreateImagePanel(
                _localization["FieldPlot"],
                _previewPaths.GetValueOrDefault("neighborhood"),
                emptyDetail: hasCellTypes ? _localization["RunAnalysisForPreview"] : _localization["PrerequisiteCellTypes"],
                previewKey: "neighborhood")),
            CreateCard(_localization["NeighborhoodClusterKey"], keyStack));
    }

    private UIElement BuildRegionView() => BuildRedesignedRegionView();

    private UIElement BuildDistanceView()
    {
        var tabs = new TabControl
        {
            Margin = new Thickness(24, 22, 24, 24),
            Style = (Style)FindResource("WorkflowTabControlStyle"),
        };
        var nearestTab = new TabItem
        {
            Header = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? "最近邻距离" : "Nearest-neighbor distances",
            Content = BuildDistancePanel("nearest"),
            Style = (Style)FindResource("WorkflowTabItemStyle"),
        };
        AutomationProperties.SetAutomationId(nearestTab, "DistanceModeNearest");
        AutomationProperties.SetName(nearestTab, nearestTab.Header.ToString());
        tabs.Items.Add(nearestTab);

        var boundaryTab = new TabItem
        {
            Header = _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? "细胞到边界距离" : "Cell-to-boundary distances",
            Content = BuildDistancePanel("boundary"),
            Style = (Style)FindResource("WorkflowTabItemStyle"),
        };
        AutomationProperties.SetAutomationId(boundaryTab, "DistanceModeBoundary");
        AutomationProperties.SetName(boundaryTab, boundaryTab.Header.ToString());
        tabs.Items.Add(boundaryTab);

        tabs.SelectedIndex = Math.Clamp(_distanceTabIndex, 0, tabs.Items.Count - 1);
        tabs.SelectionChanged += (_, _) =>
        {
            if (tabs.SelectedIndex >= 0) _distanceTabIndex = tabs.SelectedIndex;
        };
        return tabs;
    }

    private UIElement BuildDistancePanel(string mode)
    {
        var isBoundary = string.Equals(mode, "boundary", StringComparison.Ordinal);
        string? storedTarget = null;
        if (!isBoundary)
        {
            storedTarget = _nearestDistanceTarget;
            if (_resolvedCellTypes.Count > 0
                && (storedTarget is null || !_resolvedCellTypes.Contains(storedTarget, StringComparer.Ordinal)))
            {
                storedTarget = _resolvedCellTypes.FirstOrDefault();
            }
            _nearestDistanceTarget = storedTarget;
        }

        var storedQueries = isBoundary ? _boundaryDistanceQueries : _nearestDistanceQueries;
        var normalizedQueries = storedQueries
            .Where(item => _resolvedCellTypes.Contains(item, StringComparer.Ordinal))
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        if (!storedQueries.SequenceEqual(normalizedQueries, StringComparer.Ordinal))
        {
            storedQueries.Clear();
            storedQueries.AddRange(normalizedQueries);
        }

        var queriesInitialized = isBoundary ? _boundaryDistanceQueriesInitialized : _nearestDistanceQueriesInitialized;
        if (!queriesInitialized && _resolvedCellTypes.Count > 0)
        {
            if (storedQueries.Count == 0)
            {
                var defaultQuery = isBoundary
                    ? _resolvedCellTypes.FirstOrDefault()
                    : _resolvedCellTypes.FirstOrDefault(item => !string.Equals(item, storedTarget, StringComparison.Ordinal))
                      ?? _resolvedCellTypes.FirstOrDefault();
                if (defaultQuery is not null) storedQueries.Add(defaultQuery);
            }
            if (isBoundary) _boundaryDistanceQueriesInitialized = true;
            else _nearestDistanceQueriesInitialized = true;
        }

        var boundaryOptions = _boundaries.Select(item => item.Label).ToArray();
        if (boundaryOptions.Length > 0
            && (_distanceBoundaryLabel is null || !boundaryOptions.Contains(_distanceBoundaryLabel, StringComparer.Ordinal)))
        {
            _distanceBoundaryLabel = boundaryOptions.FirstOrDefault();
        }

        ComboBox? target = null;
        if (!isBoundary)
        {
            target = new ComboBox
            {
                ItemsSource = _resolvedCellTypes,
                SelectedItem = storedTarget,
                Width = 360,
                HorizontalAlignment = HorizontalAlignment.Left,
            };
            AutomationProperties.SetAutomationId(target, "NearestDistanceTarget");
            AutomationProperties.SetName(target, _localization["TargetCellType"]);
        }

        ComboBox? boundary = null;
        if (isBoundary)
        {
            boundary = new ComboBox
            {
                ItemsSource = boundaryOptions,
                SelectedItem = _distanceBoundaryLabel,
                Width = 420,
                HorizontalAlignment = HorizontalAlignment.Left,
            };
            AutomationProperties.SetAutomationId(boundary, "BoundaryDistanceBoundary");
            AutomationProperties.SetName(boundary, _localization["Boundary"]);
        }

        var stack = new StackPanel { Margin = new Thickness(0, 18, 0, 0) };
        var hasCellTypes = _resolvedCellTypes.Count > 0;
        var hasBoundaries = boundaryOptions.Length > 0;
        var workflowReady = _sections.First(section => section.Key == "distance").Status != WorkflowStatus.NotStarted;
        if (!workflowReady) stack.Children.Add(CreateInlineNotice(_localization["CompletePreviousSteps"], warning: true));
        else
        {
            if (!hasCellTypes) stack.Children.Add(CreateInlineNotice(_localization["PrerequisiteCellTypes"], warning: true));
            if (isBoundary && !hasBoundaries) stack.Children.Add(CreateInlineNotice(_localization["PrerequisiteRegion"], warning: true));
        }

        if (isBoundary)
        {
            stack.Children.Add(CreateFieldLabel(_localization["Boundary"], new Thickness(0, 0, 0, 6)));
            stack.Children.Add(boundary!);
        }
        else
        {
            stack.Children.Add(CreateFieldLabel(_localization["TargetCellType"], new Thickness(0, 0, 0, 6)));
            stack.Children.Add(target!);
        }

        Button? runButton = null;
        void RefreshRunAvailability()
        {
            if (runButton is null) return;
            var available = workflowReady
                && hasCellTypes
                && (isBoundary || !string.IsNullOrWhiteSpace(_nearestDistanceTarget))
                && storedQueries.Count > 0
                && (!isBoundary || (hasBoundaries && !string.IsNullOrWhiteSpace(_distanceBoundaryLabel)));
            SetActionAvailability(
                runButton,
                available,
                !workflowReady
                    ? _localization["CompletePreviousSteps"]
                    : isBoundary && !hasBoundaries ? _localization["PrerequisiteRegion"] : _localization["SelectCellTypeHelp"]);
        }

        var querySelection = CreateDistanceQuerySelectionGroup(mode, storedQueries, () =>
        {
            if (isBoundary) _boundaryDistanceQueriesInitialized = true;
            else _nearestDistanceQueriesInitialized = true;
            InvalidateAfter("distance");
            RefreshRunAvailability();
        });
        querySelection.Margin = new Thickness(0, 16, 0, 0);
        stack.Children.Add(querySelection);

        var runLabel = isBoundary ? _localization["RunBoundaryDistance"] : _localization["RunNearestDistance"];
        runButton = CreateButton(runLabel, async (_, _) =>
        {
            object payload = isBoundary
                ? new
                {
                    mode,
                    queryTypes = storedQueries.ToArray(),
                    boundaryLabel = _distanceBoundaryLabel,
                }
                : new
                {
                    mode,
                    targetType = _nearestDistanceTarget,
                    queryTypes = storedQueries.ToArray(),
                };
            var result = await RunWorkflowAsync("distance", "distance", payload);
            if (result is null) return;
            _previewPaths[$"distance_{mode}"] = result.Value.TryGetProperty("previewPath", out var preview) && preview.ValueKind == JsonValueKind.String ? preview.GetString() ?? string.Empty : string.Empty;
            CaptureExportPaths(result.Value);
            RefreshSectionViewIfSelected("distance");
        }, primary: true);
        runButton.Margin = new Thickness(0, 16, 0, 0);
        AutomationProperties.SetAutomationId(runButton, isBoundary ? "RunBoundaryDistance" : "RunNearestDistance");
        AutomationProperties.SetName(runButton, runLabel);
        RefreshRunAvailability();

        if (!isBoundary)
        {
            target!.SelectionChanged += (_, _) =>
            {
                var selected = target.SelectedItem?.ToString();
                if (string.Equals(selected, _nearestDistanceTarget, StringComparison.Ordinal)) return;
                _nearestDistanceTarget = selected;
                InvalidateAfter("distance");
                RefreshRunAvailability();
            };
        }

        if (isBoundary)
        {
            boundary!.SelectionChanged += (_, _) =>
            {
                var selected = boundary.SelectedItem?.ToString();
                if (string.Equals(selected, _distanceBoundaryLabel, StringComparison.Ordinal)) return;
                _distanceBoundaryLabel = selected;
                InvalidateAfter("distance");
                RefreshRunAvailability();
            };
        }

        stack.Children.Add(runButton);
        var content = new StackPanel();
        content.Children.Add(CreateCard(
            isBoundary ? _localization["BoundaryDistanceSettings"] : _localization["NearestDistanceSettings"],
            stack));
        content.Children.Add(CreateCard(_localization["Preview"], CreateImagePanel(
            runLabel,
            _previewPaths.GetValueOrDefault($"distance_{mode}"),
            emptyDetail: isBoundary && !hasBoundaries
                ? _localization["PrerequisiteRegion"]
                : hasCellTypes ? _localization["RunAnalysisForPreview"] : _localization["PrerequisiteCellTypes"],
            previewKey: $"distance_{mode}")));
        return new ScrollViewer
        {
            Content = content,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
        };
    }

    private FrameworkElement CreateDistanceQuerySelectionGroup(
        string mode,
        List<string> selection,
        Action onChanged)
    {
        var isBoundary = string.Equals(mode, "boundary", StringComparison.Ordinal);
        var queriesAutomationId = isBoundary ? "BoundaryDistanceQueries" : "NearestDistanceQueries";
        var available = _resolvedCellTypes.Distinct(StringComparer.Ordinal).ToArray();
        var host = new StackPanel();
        var heading = new DockPanel { LastChildFill = true, Margin = new Thickness(0, 0, 0, 8) };
        var choicesPanelFactory = new FrameworkElementFactory(typeof(WrapPanel));
        var choices = new ItemsControl
        {
            ItemsPanel = new ItemsPanelTemplate(choicesPanelFactory),
        };
        AutomationProperties.SetAutomationId(choices, queriesAutomationId);
        AutomationProperties.SetName(choices, _localization["QueryCellTypes"]);
        AutomationProperties.SetHelpText(choices, _localization["SelectCellTypeHelp"]);
        var count = CreateSupportingText($"{selection.Count} / {available.Length}");
        count.Margin = new Thickness(10, 0, 0, 0);
        count.VerticalAlignment = VerticalAlignment.Center;
        AutomationProperties.SetLiveSetting(count, AutomationLiveSetting.Polite);
        DockPanel.SetDock(count, Dock.Right);
        heading.Children.Add(count);

        var selectAll = CreateButton(_localization["SelectAll"], (_, _) =>
        {
            if (selection.ToHashSet(StringComparer.Ordinal).SetEquals(available)) return;
            selection.Clear();
            selection.AddRange(available);
            foreach (var checkBox in choices.Items.OfType<CheckBox>()) checkBox.IsChecked = true;
            count.Text = $"{selection.Count} / {available.Length}";
            onChanged();
        });
        selectAll.Padding = new Thickness(10, 4, 10, 4);
        selectAll.Margin = new Thickness(12, 0, 0, 0);
        AutomationProperties.SetAutomationId(selectAll, $"{queriesAutomationId}SelectAll");
        AutomationProperties.SetName(selectAll, _localization["SelectAll"]);
        DockPanel.SetDock(selectAll, Dock.Right);
        heading.Children.Add(selectAll);
        heading.Children.Add(CreateSubsectionTitle(_localization["QueryCellTypes"]));
        host.Children.Add(heading);

        for (var index = 0; index < available.Length; index++)
        {
            var cellType = available[index];
            var checkBox = new CheckBox
            {
                Content = cellType,
                DataContext = cellType,
                IsChecked = selection.Contains(cellType, StringComparer.Ordinal),
                Margin = new Thickness(0, 0, 16, 8),
                MinWidth = 170,
                VerticalAlignment = VerticalAlignment.Center,
            };
            AutomationProperties.SetAutomationId(checkBox, $"{queriesAutomationId}Option{index:D2}");
            AutomationProperties.SetName(checkBox, cellType);
            AutomationProperties.SetHelpText(checkBox, _localization["SelectCellTypeHelp"]);
            checkBox.Click += (_, _) =>
            {
                var changed = false;
                if (checkBox.IsChecked == true)
                {
                    if (!selection.Contains(cellType, StringComparer.Ordinal))
                    {
                        selection.Add(cellType);
                        changed = true;
                    }
                }
                else if (selection.Count <= 1 && selection.Contains(cellType, StringComparer.Ordinal))
                {
                    checkBox.IsChecked = true;
                    return;
                }
                else
                {
                    changed = selection.RemoveAll(value => string.Equals(value, cellType, StringComparison.Ordinal)) > 0;
                }

                if (!changed) return;
                count.Text = $"{selection.Count} / {available.Length}";
                onChanged();
            };
            choices.Items.Add(checkBox);
        }

        var choicesFrame = new Border
        {
            Background = new SolidColorBrush(Color.FromRgb(247, 249, 250)),
            BorderBrush = (Brush)FindResource("PanelBorderBrush"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(14, 12, 14, 4),
            Child = choices,
        };
        host.Children.Add(choicesFrame);
        return host;
    }

    private UIElement BuildOutputsView()
    {
        var stack = new StackPanel();
        var grid = new DataGrid { ItemsSource = _outputFiles, MinHeight = 520, IsReadOnly = true, SelectionMode = DataGridSelectionMode.Single };
        grid.Columns.Add(new DataGridTextColumn { Header = _localization["Name"], Binding = new Binding(nameof(OutputFileRow.Name)), Width = 240 });
        grid.Columns.Add(new DataGridTextColumn { Header = _localization["GeneratedFiles"], Binding = new Binding(nameof(OutputFileRow.RelativePath)), Width = new DataGridLength(1, DataGridLengthUnitType.Star) });
        grid.Columns.Add(new DataGridTextColumn { Header = _localization["Size"], Binding = new Binding(nameof(OutputFileRow.SizeText)), Width = 110 });
        void OpenSelectedFile()
        {
            if (grid.SelectedItem is OutputFileRow row) OpenPath(Path.Combine(_outputFolder, row.RelativePath));
        }
        var actions = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        var refresh = CreateButton(_localization["RefreshOutputs"], async (_, _) => await RefreshOutputsAsync(), primary: true);
        SetActionAvailability(
            refresh,
            _sections.First(section => section.Key == "outputs").Status != WorkflowStatus.NotStarted,
            _localization["CompletePreviousSteps"]);
        actions.Children.Add(refresh);
        var openFolder = CreateButton(_localization["OpenOutput"], (_, _) => OpenPath(_outputFolder));
        SetActionAvailability(openFolder, Directory.Exists(_outputFolder), _localization["ChooseFolder"]);
        actions.Children.Add(openFolder);
        var openSelected = CreateButton(_localization["OpenSelected"], (_, _) => OpenSelectedFile());
        var selectFileHelp = _outputFiles.Count == 0
            ? _localization["NoGeneratedFiles"]
            : _localization["SelectGeneratedFile"];
        SetActionAvailability(openSelected, false, selectFileHelp);
        actions.Children.Add(openSelected);
        stack.Children.Add(actions);
        if (_outputFiles.Count == 0) stack.Children.Add(CreateInlineNotice(_localization["ResultsEmptyHint"]));
        grid.SelectionChanged += (_, _) => SetActionAvailability(
            openSelected,
            grid.SelectedItem is OutputFileRow,
            selectFileHelp);
        grid.MouseDoubleClick += (_, _) =>
        {
            OpenSelectedFile();
        };
        grid.PreviewKeyDown += (_, eventArgs) =>
        {
            if (eventArgs.Key != Key.Enter || grid.SelectedItem is not OutputFileRow) return;
            OpenSelectedFile();
            eventArgs.Handled = true;
        };
        stack.Children.Add(grid);
        stack.Children.Add(CreateSupportingText(_localization["ResultsStayEnglish"], new Thickness(0, 10, 0, 0)));
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
        RefreshSectionViewIfSelected("outputs");
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
        var previousStatus = section.Status;
        var refreshInvalidatedViewAfterFailure = false;
        SetInteractionBusy(true);
        if (completesSection) InvalidateAfter(sectionKey);
        _activeSectionKey = sectionKey;
        section.Status = WorkflowStatus.Running;
        OperationProgress.Value = 0;
        OperationProgress.Visibility = Visibility.Visible;
        UpdateHeader();
        try
        {
            var result = await _engine.SendAsync(command, payload);
            section.Status = completesSection ? WorkflowStatus.Complete : previousStatus;
            if (completesSection) MarkNextReady(sectionKey);
            SetLocalizedStatus("AnalysisComplete");
            UpdateProgressMetadata();
            UpdateHeader();
            return result;
        }
        catch (Exception exception)
        {
            section.Status = completesSection ? WorkflowStatus.Error : previousStatus;
            refreshInvalidatedViewAfterFailure = completesSection;
            SetStatus(LocalizeEngineError(exception.Message), isError: true);
            UpdateHeader();
            return null;
        }
        finally
        {
            SetInteractionBusy(false);
            _activeSectionKey = null;
            OperationProgress.Visibility = Visibility.Collapsed;
            UpdateProgressMetadata();
            if (refreshInvalidatedViewAfterFailure) RefreshSectionViewIfSelected(sectionKey);
        }
    }

    private void MarkNextReady(string sectionKey)
    {
        var index = _sections.ToList().FindIndex(section => section.Key == sectionKey);
        if (index >= 0 && index + 1 < _sections.Count && _sections[index + 1].Status == WorkflowStatus.NotStarted)
            _sections[index + 1].Status = WorkflowStatus.Ready;
    }

    private void RemoveWorkflowPreviews(string sectionKey)
    {
        if (!WorkflowPreviewKeys.TryGetValue(sectionKey, out var keys)) return;
        foreach (var key in keys)
        {
            _previewPaths.Remove(key);
            HideTaggedDetailElement($"preview:{key}");
        }
    }

    private void InvalidateAfter(string sectionKey)
    {
        if (sectionKey == "inputs") ++_outputRestoreGeneration;
        var index = _sections.ToList().FindIndex(section => section.Key == sectionKey);
        if (index < 0) return;

        if (index <= _sections.ToList().FindIndex(section => section.Key == "overlay"))
        {
            RemoveWorkflowPreviews("overlay");
            _exportPaths.Clear();
        }
        if (index <= _sections.ToList().FindIndex(section => section.Key == "nuclei"))
        {
            RemoveWorkflowPreviews("nuclei");
            _pendingNucleiRecommendation = null;
            HideTaggedDetailElement("recommendation:nuclei");
        }
        if (index <= _sections.ToList().FindIndex(section => section.Key == "cellTypes"))
        {
            RemoveWorkflowPreviews("cellTypes");
            _pendingAssignmentRecommendation = null;
            HideTaggedDetailElement("recommendation:assignment");
            _resolvedCellTypes.Clear();
        }
        if (index <= _sections.ToList().FindIndex(section => section.Key == "neighborhood"))
            RemoveWorkflowPreviews("neighborhood");
        if (index <= _sections.ToList().FindIndex(section => section.Key == "region"))
        {
            RemoveWorkflowPreviews("region");
            _boundaries.Clear();
            _regionRows.Clear();
            _regionDominantCounts.Clear();
            _regionDisplayedBoundaries.Clear();
            _regionDisplayedCellTypes.Clear();
            _regionCustomizedBoundaries.Clear();
            _regionCustomizedCellTypes.Clear();
            _regionManualVisibleBoundaries.Clear();
            _regionManualSeedCellTypes.Clear();
            _regionManualTargetBoundary = null;
            _regionManualPolygons = [];
            _regionPreviewGenerations.Clear();
            _regionMapRenderedBoundaryCount = null;
            _regionMapRenderedCellTypeCount = null;
            _regionMapRenderedCellCount = null;
            ++_regionManualPreviewGeneration;
        }
        if (index <= _sections.ToList().FindIndex(section => section.Key == "distribution"))
            RemoveWorkflowPreviews("distribution");
        if (index <= _sections.ToList().FindIndex(section => section.Key == "distance"))
            RemoveWorkflowPreviews("distance");
        _outputFiles.Clear();

        for (var position = index + 1; position < _sections.Count; position++) _sections[position].Status = WorkflowStatus.NotStarted;
        if (_sections[index].Status == WorkflowStatus.Complete) _sections[index].Status = WorkflowStatus.Ready;
        UpdateProgressMetadata();
        UpdateHeader();
    }

    private void EnsureDefaultCellTypes()
    {
        RefreshCellTypeMarkerOptions();
        if (_cellTypes.Count > 0 || _channels.Count == 0) return;
        foreach (var channel in _channels.Where(channel =>
                     !string.IsNullOrWhiteSpace(channel.Marker)
                     && !string.Equals(channel.Marker, _nucleusChannel, StringComparison.OrdinalIgnoreCase)))
        {
            _cellTypes.Add(new CellTypeRow
            {
                Name = channel.Marker,
                ColorHex = channel.ColorHex,
                AllPositive = WithDefaultNucleusMarker(channel.Marker),
                AllNegative = string.Empty,
                AnyPositiveGroups = string.Empty,
            });
        }
    }

    private void RefreshCellTypeMarkerOptions()
    {
        var options = new List<string>();
        var canonicalNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        void AddOption(string? value)
        {
            var marker = value?.Trim() ?? string.Empty;
            if (marker.Length == 0) return;
            var canonical = CanonicalMarkerName(marker);
            if (canonical.Length == 0 || !canonicalNames.Add(canonical)) return;
            options.Add(canonical == "nucleus" ? NucleusMarker : marker);
        }

        AddOption(NucleusMarker);
        foreach (var channel in _channels)
        {
            // Keep every source marker selectable. The derived Nucleus option
            // is separate evidence produced by segmentation and remains the
            // default All-positive rule for each cell type.
            AddOption(channel.Marker);
        }

        if (_cellTypeMarkerOptions.SequenceEqual(options, StringComparer.Ordinal)) return;
        _cellTypeMarkerOptions.Clear();
        foreach (var option in options) _cellTypeMarkerOptions.Add(option);
    }

    private static string WithDefaultNucleusMarker(string? selection)
    {
        var markers = new List<string> { NucleusMarker };
        var canonicalNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "nucleus" };
        foreach (var marker in SplitMarkers(selection ?? string.Empty))
        {
            var canonical = CanonicalMarkerName(marker);
            if (canonical.Length == 0 || !canonicalNames.Add(canonical)) continue;
            markers.Add(canonical == "nucleus" ? NucleusMarker : marker);
        }
        return string.Join(", ", markers);
    }

    private static string CanonicalMarkerName(string value)
    {
        var canonical = string.Concat(value.Where(char.IsLetterOrDigit)).ToLowerInvariant();
        return canonical is "nuclearsegmentationsignal" or "nucleus" ? "nucleus" : canonical;
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
        ApplyStatusTone(null, isError);
    }

    private string ChooseDefaultNucleusChannel()
    {
        static bool ContainsAny(string value, params string[] candidates) =>
            candidates.Any(candidate => value.Contains(candidate, StringComparison.OrdinalIgnoreCase));

        var usable = _channels
            .Where(channel => !string.IsNullOrWhiteSpace(channel.Marker))
            .ToList();
        return usable.FirstOrDefault(channel => ContainsAny(channel.Marker, "DAPI", "Hoechst"))?.Marker
            ?? usable.FirstOrDefault(channel => ContainsAny(channel.Marker, "nucleus", "nuclei"))?.Marker
            ?? usable.FirstOrDefault(channel => ContainsAny(channel.Marker, "DNA", "histone", "Ir191", "Ir193"))?.Marker
            ?? usable.FirstOrDefault()?.Marker
            ?? string.Empty;
    }

    private void SetLocalizedStatus(string resourceKey, bool isError = false)
    {
        _statusResourceKey = resourceKey;
        StatusText.Text = _localization[resourceKey];
        ApplyStatusTone(resourceKey, isError);
    }

    private void ApplyStatusTone(string? resourceKey, bool isError)
    {
        if (isError)
        {
            StatusSurface.Background = new SolidColorBrush(Color.FromRgb(253, 242, 242));
            StatusText.Foreground = (Brush)FindResource("ErrorBrush");
            StatusIcon.Foreground = (Brush)FindResource("ErrorBrush");
            StatusIcon.Text = "\uE783";
            AutomationProperties.SetLiveSetting(StatusSurface, AutomationLiveSetting.Assertive);
            return;
        }

        var isSuccess = resourceKey is "AnalysisComplete" or "ConfigurationSaved" or "ExistingResultsRestored" or "SuggestedComboApplied";
        var isRunning = resourceKey is "Running" or "CheckingExistingResults";
        if (isSuccess)
        {
            StatusSurface.Background = new SolidColorBrush(Color.FromRgb(234, 247, 239));
            StatusText.Foreground = (Brush)FindResource("CompleteBrush");
            StatusIcon.Foreground = (Brush)FindResource("CompleteBrush");
            StatusIcon.Text = "\uE73E";
        }
        else if (isRunning)
        {
            StatusSurface.Background = new SolidColorBrush(Color.FromRgb(255, 244, 229));
            StatusText.Foreground = (Brush)FindResource("RunningBrush");
            StatusIcon.Foreground = (Brush)FindResource("RunningBrush");
            StatusIcon.Text = "\uE895";
        }
        else
        {
            StatusSurface.Background = new SolidColorBrush(Color.FromRgb(239, 246, 247));
            StatusText.Foreground = (Brush)FindResource("TextBrush");
            StatusIcon.Foreground = (Brush)FindResource("AccentBrush");
            StatusIcon.Text = "\uE946";
        }
        AutomationProperties.SetLiveSetting(StatusSurface, AutomationLiveSetting.Polite);
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

    private static bool AreClose(double left, double right) =>
        Math.Abs(left - right) <= 1e-9 * Math.Max(1, Math.Max(Math.Abs(left), Math.Abs(right)));

    private static bool PathsEqual(string left, string right)
    {
        try
        {
            return string.Equals(
                Path.GetFullPath(left).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                Path.GetFullPath(right).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return string.Equals(left, right, StringComparison.OrdinalIgnoreCase);
        }
    }

    private static bool TryGetJsonNumber(JsonElement element, string propertyName, out double value)
    {
        value = 0;
        return element.TryGetProperty(propertyName, out var property)
            && property.ValueKind == JsonValueKind.Number
            && property.TryGetDouble(out value);
    }

    private static bool TryGetJsonString(JsonElement element, string propertyName, out string value)
    {
        value = string.Empty;
        if (!element.TryGetProperty(propertyName, out var property) || property.ValueKind != JsonValueKind.String) return false;
        value = property.GetString() ?? string.Empty;
        return value.Length > 0;
    }

    private static bool TryGetJsonStringArray(JsonElement element, string propertyName, out List<string> values)
    {
        values = [];
        if (!element.TryGetProperty(propertyName, out var property) || property.ValueKind != JsonValueKind.Array)
            return false;
        values = property.EnumerateArray()
            .Where(item => item.ValueKind == JsonValueKind.String)
            .Select(item => item.GetString()?.Trim() ?? string.Empty)
            .Where(item => item.Length > 0)
            .Distinct(StringComparer.Ordinal)
            .ToList();
        return true;
    }

    private static string JoinJsonStrings(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var values) || values.ValueKind != JsonValueKind.Array) return string.Empty;
        return string.Join(", ", values.EnumerateArray().Select(value => value.GetString()).Where(value => !string.IsNullOrWhiteSpace(value)));
    }

    private static string JoinJsonGroups(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var groups) || groups.ValueKind != JsonValueKind.Array) return string.Empty;
        return string.Join(" | ", groups.EnumerateArray().Where(group => group.ValueKind == JsonValueKind.Array).Select(group =>
            string.Join(", ", group.EnumerateArray().Select(value => value.GetString()).Where(value => !string.IsNullOrWhiteSpace(value)))));
    }

    private static string[] SplitMarkers(string value) => value
        .Split([',', ';'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
        .Where(item => !string.IsNullOrWhiteSpace(item))
        .ToArray();
}
