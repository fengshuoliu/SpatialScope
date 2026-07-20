using SpatialScope.Windows.Controls;
using SpatialScope.Windows.Models;
using System.Collections.ObjectModel;
using System.Globalization;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace SpatialScope.Windows;

public partial class MainWindow
{
    private static readonly int[] RegionContourDownsampleOptions = [1, 2, 4, 8];
    private readonly Dictionary<string, ContentControl> _regionPreviewHosts = [];
    private readonly Dictionary<string, TextBlock> _regionSummaryLabels = [];
    private readonly List<Func<bool>> _regionComputationalParameterCommitters = [];
    private readonly List<Func<bool>> _regionDisplayParameterCommitters = [];
    private readonly List<Func<bool>> _regionManualParameterCommitters = [];
    private readonly HashSet<TextBox> _regionInvalidNumericEditors = [];
    private RegionDrawingCanvas? _activeRegionDrawingCanvas;

    private UIElement BuildRedesignedRegionView()
    {
        _regionComputationalParameterCommitters.Clear();
        _regionDisplayParameterCommitters.Clear();
        _regionManualParameterCommitters.Clear();
        _regionInvalidNumericEditors.Clear();
        if (_statusResourceKey == "InvalidRegionParameters")
            SetLocalizedStatus("RegionParametersValid");
        NormalizeRegionSelections();
        var cards = new List<UIElement>
        {
            BuildRegionParameterCard(),
            BuildRegionComputationalCard(),
        };

        if (_regionRows.Count == 0)
        {
            cards.Add(BuildRegionEmptyStateCard());
        }
        else
        {
            cards.Add(BuildRegionMapCard());
            cards.Add(BuildRegionManualCard());
            cards.Add(BuildRegionCustomizedCard());
            cards.Add(BuildRegionCountsCard());
            cards.Add(BuildRegionTableCard());
        }
        return CreatePage(cards.ToArray());
    }

    private void NormalizeRegionSelections()
    {
        var availableTypes = _resolvedCellTypes
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        NormalizeRequiredSelection(_regionSelectedCellTypes, availableTypes, ref _regionSelectionInitialized);

        var boundaryLabels = _regionRows
            .Select(row => row.Label)
            .Concat(_boundaries.Select(item => item.Label))
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        NormalizeRequiredSelection(_regionDisplayedBoundaries, boundaryLabels);
        NormalizeRequiredSelection(_regionCustomizedBoundaries, boundaryLabels);
        NormalizeRequiredSelection(_regionManualVisibleBoundaries, boundaryLabels);
        NormalizeRequiredSelection(_regionDisplayedCellTypes, availableTypes);
        NormalizeRequiredSelection(_regionCustomizedCellTypes, availableTypes);

        _regionManualSeedCellTypes.RemoveAll(value => !availableTypes.Contains(value, StringComparer.Ordinal));
        if (_regionManualSeedCellTypes.Count == 0 && availableTypes.Length > 0)
        {
            var target = _regionRows.FirstOrDefault(row =>
                string.Equals(row.Label, _regionManualTargetBoundary, StringComparison.Ordinal));
            var preferred = new[] { target?.Label, target?.DominantType }
                .FirstOrDefault(value => value is not null && availableTypes.Contains(value, StringComparer.Ordinal));
            _regionManualSeedCellTypes.Add(
                preferred is not null && availableTypes.Contains(preferred, StringComparer.Ordinal)
                    ? preferred
                    : availableTypes[0]);
        }

        if (_regionManualTargetBoundary is null
            || !boundaryLabels.Contains(_regionManualTargetBoundary, StringComparer.Ordinal))
        {
            _regionManualTargetBoundary = boundaryLabels.FirstOrDefault();
        }
        if (_regionManualMode is not ("create" or "include" or "exclude"))
            _regionManualMode = "create";
        if (string.IsNullOrWhiteSpace(_regionManualDisplayName))
            _regionManualDisplayName = "manual_drawn_ROI";
    }

    private static void NormalizeRequiredSelection(List<string> selection, IReadOnlyList<string> available)
    {
        selection.RemoveAll(value => !available.Contains(value, StringComparer.Ordinal));
        if (selection.Count == 0 && available.Count > 0) selection.AddRange(available);
    }

    private static void NormalizeRequiredSelection(
        List<string> selection,
        IReadOnlyList<string> available,
        ref bool initialized)
    {
        selection.RemoveAll(value => !available.Contains(value, StringComparer.Ordinal));
        if (!initialized && available.Count > 0)
        {
            selection.Clear();
            selection.AddRange(available);
            initialized = true;
        }
        else if (selection.Count == 0 && available.Count > 0)
        {
            selection.AddRange(available);
        }
    }

    private void ApplyRegionResultPayload(
        JsonElement result,
        bool replaceBoundaries,
        bool replaceBasePreview = true)
    {
        if (result.TryGetProperty("width", out var width) && width.ValueKind == JsonValueKind.Number)
            _regionSourceWidth = Math.Max(0, width.GetInt32());
        if (result.TryGetProperty("height", out var height) && height.ValueKind == JsonValueKind.Number)
            _regionSourceHeight = Math.Max(0, height.GetInt32());

        if (replaceBasePreview)
        {
            StoreRegionPreviewPaths(result, "region", exactPrimary: false);
            foreach (var suffix in new[] { string.Empty, "Overlay", "Mask" })
            {
                if (_previewPaths.TryGetValue($"region{suffix}", out var path))
                    _previewPaths[$"regionOriginal{suffix}"] = path;
            }
        }
        else
        {
            StoreRegionPreviewPaths(result, "regionManualAdjusted", exactPrimary: true);
        }

        if (result.TryGetProperty("boundaries", out var boundaries)
            && boundaries.ValueKind == JsonValueKind.Array)
        {
            if (replaceBoundaries) _boundaries.Clear();
            foreach (var item in boundaries.EnumerateArray())
            {
                var label = RegionJsonString(item, "label");
                var path = RegionJsonString(item, "path");
                if (label.Length == 0 || path.Length == 0) continue;
                var existing = _boundaries.FindIndex(boundary =>
                    string.Equals(boundary.Label, label, StringComparison.Ordinal));
                if (existing >= 0) _boundaries[existing] = (label, path);
                else _boundaries.Add((label, path));
            }
        }

        if (result.TryGetProperty("regions", out var regions)
            && regions.ValueKind == JsonValueKind.Array)
        {
            _regionRows.Clear();
            foreach (var item in regions.EnumerateArray())
            {
                var counts = new Dictionary<string, int>(StringComparer.Ordinal);
                if (item.TryGetProperty("countsByType", out var countsElement)
                    && countsElement.ValueKind == JsonValueKind.Object)
                {
                    foreach (var property in countsElement.EnumerateObject())
                        if (property.Value.ValueKind == JsonValueKind.Number)
                            counts[property.Name] = property.Value.GetInt32();
                }
                _regionRows.Add(new RegionSummaryRow
                {
                    Id = RegionJsonScalar(item, "id"),
                    Label = RegionJsonString(item, "label"),
                    SourceType = RegionJsonString(item, "sourceType"),
                    DominantType = RegionJsonString(item, "dominantType"),
                    CellCount = RegionJsonInt(item, "cellCount"),
                    AreaUm2 = RegionJsonDouble(item, "areaUm2"),
                    ColorHex = RegionJsonString(item, "colorHex", "#A1D99B"),
                    CountsByType = new ReadOnlyDictionary<string, int>(counts),
                });
            }
        }

        if (_regionRows.Count == 0 && _boundaries.Count > 0)
        {
            var ordinal = 1;
            foreach (var boundary in _boundaries)
            {
                _regionRows.Add(new RegionSummaryRow
                {
                    Id = ordinal++.ToString(CultureInfo.InvariantCulture),
                    Label = boundary.Label,
                    SourceType = "computational",
                    ColorHex = _regionBoundaryColor,
                });
            }
        }

        _regionDominantCounts.Clear();
        if (result.TryGetProperty("dominantCounts", out var dominantCounts)
            && dominantCounts.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in dominantCounts.EnumerateArray())
            {
                _regionDominantCounts.Add(new RegionDominantCountRow
                {
                    Name = RegionJsonString(item, "name"),
                    Count = RegionJsonInt(item, "count"),
                    ColorHex = RegionJsonString(item, "colorHex", "#A1D99B"),
                });
            }
        }
        if (_regionDominantCounts.Count == 0)
        {
            foreach (var group in _regionRows
                         .Where(row => !string.IsNullOrWhiteSpace(row.DominantType))
                         .GroupBy(row => row.DominantType, StringComparer.Ordinal)
                         .OrderByDescending(group => group.Count())
                         .ThenBy(group => group.Key, StringComparer.Ordinal))
            {
                var color = _cellTypes.FirstOrDefault(row =>
                    string.Equals(row.Name, group.Key, StringComparison.Ordinal))?.ColorHex ?? "#A1D99B";
                _regionDominantCounts.Add(new RegionDominantCountRow
                {
                    Name = group.Key,
                    Count = group.Count(),
                    ColorHex = color,
                });
            }
        }
        NormalizeRegionSelections();
    }

    private void StoreRegionPreviewPaths(JsonElement result, string targetKey, bool exactPrimary)
    {
        static string? ReadPath(JsonElement source, string property)
        {
            if (!source.TryGetProperty(property, out var value)
                || value.ValueKind != JsonValueKind.String
                || string.IsNullOrWhiteSpace(value.GetString()))
            {
                return null;
            }
            return value.GetString();
        }

        var primaryPath = exactPrimary
            ? ReadPath(result, "previewPath")
            : ReadPath(result, "comparisonPreviewPath") ?? ReadPath(result, "previewPath");
        if (primaryPath is not null) _previewPaths[targetKey] = primaryPath;

        var overlayPath = ReadPath(result, "overlayPreviewPath");
        if (overlayPath is not null) _previewPaths[$"{targetKey}Overlay"] = overlayPath;
        var maskPath = ReadPath(result, "maskPreviewPath");
        if (maskPath is not null) _previewPaths[$"{targetKey}Mask"] = maskPath;
    }

    private static string RegionJsonString(JsonElement item, string property, string fallback = "") =>
        item.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? fallback
            : fallback;

    private static string RegionJsonScalar(JsonElement item, string property)
    {
        if (!item.TryGetProperty(property, out var value)) return string.Empty;
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? string.Empty,
            JsonValueKind.Number => value.GetRawText(),
            _ => string.Empty,
        };
    }

    private static int RegionJsonInt(JsonElement item, string property) =>
        item.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Number
            ? value.GetInt32()
            : 0;

    private static int? RegionJsonOptionalInt(JsonElement item, string property) =>
        item.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Number
            ? value.GetInt32()
            : null;

    private static double RegionJsonDouble(JsonElement item, string property) =>
        item.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Number
            ? value.GetDouble()
            : 0;

    private Border BuildRegionParameterCard()
    {
        var stack = new StackPanel();
        stack.Children.Add(CreateSupportingText(_localization["RegionParametersIntro"], new Thickness(0, 0, 0, 16)));
        var fields = new WrapPanel { ItemWidth = 318 };
        fields.Children.Add(CreateRegionNumericTile(
            _localization["ClosingRadius"], _localization["ClosingRadiusHelp"], _regionClosingRadius,
            value => _regionClosingRadius = value, "µm", 0, 80, invalidateMasks: true));
        fields.Children.Add(CreateRegionNumericTile(
            _localization["DilationRadius"], _localization["DilationRadiusHelp"], _regionDilationRadius,
            value => _regionDilationRadius = value, "µm", 0, 80, invalidateMasks: true));
        fields.Children.Add(CreateRegionNumericTile(
            _localization["MinimumArea"], _localization["MinimumAreaHelp"], _regionMinimumArea,
            value => _regionMinimumArea = value, "µm²", 0, 1_000_000_000, invalidateMasks: true));
        fields.Children.Add(CreateRegionNumericTile(
            _localization["MinimumCells"], _localization["MinimumCellsHelp"], _regionMinimumCells,
            value => _regionMinimumCells = Math.Round(value), string.Empty, 1, 1_000_000, invalidateMasks: true,
            normalize: Math.Round));

        var contour = new ComboBox
        {
            ItemsSource = RegionContourDownsampleOptions,
            SelectedItem = NormalizeRegionContourDownsample(_regionContourDownsample),
            MinWidth = 150,
            HorizontalAlignment = HorizontalAlignment.Left,
        };
        AutomationProperties.SetName(contour, _localization["ContourDownsample"]);
        contour.SelectionChanged += (_, _) =>
        {
            if (contour.SelectedItem is not int value || AreClose(_regionContourDownsample, value)) return;
            _regionContourDownsample = value;
            RegionMorphologyChanged();
        };
        fields.Children.Add(CreateRegionParameterTile(
            _localization["ContourDownsample"], _localization["ContourDownsampleHelp"], contour));

        fields.Children.Add(CreateRegionNumericTile(
            _localization["BoundaryLineWidth"], _localization["BoundaryLineWidthHelp"], _regionLineWidth,
            value => _regionLineWidth = value, "px", 0.5, 10, invalidateMasks: false));

        var lineStyle = new ComboBox { MinWidth = 170, HorizontalAlignment = HorizontalAlignment.Left };
        lineStyle.Items.Add(new ComboBoxItem { Content = _localization["SolidLine"], Tag = "-" });
        lineStyle.Items.Add(new ComboBoxItem { Content = _localization["DashedLine"], Tag = "--" });
        lineStyle.Items.Add(new ComboBoxItem { Content = _localization["DashDotLine"], Tag = "-." });
        lineStyle.Items.Add(new ComboBoxItem { Content = _localization["DottedLine"], Tag = ":" });
        lineStyle.SelectedItem = lineStyle.Items.Cast<ComboBoxItem>()
            .FirstOrDefault(item => string.Equals(item.Tag?.ToString(), _regionLineStyle, StringComparison.Ordinal))
            ?? lineStyle.Items[0];
        AutomationProperties.SetName(lineStyle, _localization["BoundaryLineStyle"]);
        lineStyle.SelectionChanged += (_, _) =>
        {
            if (lineStyle.SelectedItem is not ComboBoxItem { Tag: string value }
                || string.Equals(_regionLineStyle, value, StringComparison.Ordinal)) return;
            _regionLineStyle = value;
            RegionDisplaySettingsChanged();
        };
        fields.Children.Add(CreateRegionParameterTile(
            _localization["BoundaryLineStyle"], _localization["BoundaryLineStyleHelp"], lineStyle));

        var boundaryColorPicker = CreateRegionColorPicker();
        boundaryColorPicker.IsEnabled = !_regionUseTypeColors;
        fields.Children.Add(CreateRegionParameterTile(
            _localization["BoundaryColor"], _localization["BoundaryColorHelp"], boundaryColorPicker));

        var typeColors = new CheckBox
        {
            Content = _localization["UseCellTypeColors"],
            IsChecked = _regionUseTypeColors,
            VerticalAlignment = VerticalAlignment.Center,
        };
        AutomationProperties.SetHelpText(typeColors, _localization["UseCellTypeColorsHelp"]);
        typeColors.Click += (_, _) =>
        {
            _regionUseTypeColors = typeColors.IsChecked == true;
            boundaryColorPicker.IsEnabled = !_regionUseTypeColors;
            RegionDisplaySettingsChanged();
        };
        fields.Children.Add(CreateRegionParameterTile(
            _localization["UseCellTypeColors"], _localization["UseCellTypeColorsHelp"], typeColors));
        stack.Children.Add(fields);
        return CreateCard(_localization["RegionParameters"], stack);
    }

    private Border CreateRegionParameterTile(string title, string help, UIElement editor)
    {
        var stack = new StackPanel();
        stack.Children.Add(CreateFieldLabel(title, new Thickness(0, 0, 0, 7)));
        stack.Children.Add(editor);
        stack.Children.Add(CreateSupportingText(help, new Thickness(0, 8, 0, 0)));
        return new Border
        {
            Width = 304,
            MinHeight = 132,
            Background = new SolidColorBrush(Color.FromRgb(246, 249, 250)),
            BorderBrush = (Brush)FindResource("PanelBorderBrush"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(14, 12, 14, 12),
            Margin = new Thickness(0, 0, 14, 14),
            Child = stack,
        };
    }

    private Border CreateRegionNumericTile(
        string title,
        string help,
        double value,
        Action<double> setter,
        string unit,
        double minimum,
        double maximum,
        bool invalidateMasks,
        Func<double, double>? normalize = null)
    {
        var committed = normalize?.Invoke(value) ?? value;
        var row = new DockPanel { LastChildFill = true };
        if (!string.IsNullOrWhiteSpace(unit))
        {
            var unitText = CreateSupportingText(unit);
            unitText.Margin = new Thickness(8, 0, 0, 0);
            unitText.VerticalAlignment = VerticalAlignment.Center;
            DockPanel.SetDock(unitText, Dock.Right);
            row.Children.Add(unitText);
        }
        var editor = new TextBox
        {
            Text = committed.ToString("0.###", CultureInfo.CurrentCulture),
            MinWidth = 120,
            HorizontalAlignment = HorizontalAlignment.Stretch,
        };
        AutomationProperties.SetName(editor, title);
        AutomationProperties.SetHelpText(editor, help);
        var validation = CreateRegionNumericValidation(minimum, maximum);
        bool Commit()
        {
            if (!TryReadDouble(editor.Text, out var parsed)
                || !double.IsFinite(parsed)
                || parsed < minimum
                || parsed > maximum)
            {
                _regionInvalidNumericEditors.Add(editor);
                validation.Visibility = Visibility.Visible;
                editor.BorderBrush = (Brush)FindResource("ErrorBrush");
                AutomationProperties.SetHelpText(editor, $"{help} {validation.Text}");
                return false;
            }
            _regionInvalidNumericEditors.Remove(editor);
            validation.Visibility = Visibility.Collapsed;
            editor.ClearValue(Control.BorderBrushProperty);
            AutomationProperties.SetHelpText(editor, help);
            var normalized = normalize?.Invoke(parsed) ?? parsed;
            editor.Text = normalized.ToString("0.###", CultureInfo.CurrentCulture);
            ClearRegionParameterErrorIfResolved();
            if (AreClose(normalized, committed)) return true;
            committed = normalized;
            setter(normalized);
            if (invalidateMasks) RegionMorphologyChanged();
            else RegionDisplaySettingsChanged();
            return true;
        }
        _regionComputationalParameterCommitters.Add(Commit);
        if (!invalidateMasks) _regionDisplayParameterCommitters.Add(Commit);
        editor.LostKeyboardFocus += (_, _) => Commit();
        editor.PreviewKeyDown += (_, eventArgs) =>
        {
            if (eventArgs.Key is not (Key.Enter or Key.Return)) return;
            if (Commit()) Keyboard.ClearFocus();
            else SetLocalizedStatus("InvalidRegionParameters", isError: true);
            eventArgs.Handled = true;
        };
        row.Children.Add(editor);
        var editorAndValidation = new StackPanel();
        editorAndValidation.Children.Add(row);
        editorAndValidation.Children.Add(validation);
        return CreateRegionParameterTile(title, help, editorAndValidation);
    }

    private TextBlock CreateRegionNumericValidation(double minimum, double maximum)
    {
        var validation = new TextBlock
        {
            Text = string.Format(
                CultureInfo.CurrentCulture,
                _localization["ValueRangeValidation"],
                minimum,
                maximum),
            Foreground = (Brush)FindResource("ErrorBrush"),
            FontSize = 12,
            FontWeight = FontWeights.SemiBold,
            Margin = new Thickness(0, 6, 0, 0),
            TextWrapping = TextWrapping.Wrap,
            Visibility = Visibility.Collapsed,
        };
        AutomationProperties.SetLiveSetting(validation, AutomationLiveSetting.Assertive);
        return validation;
    }

    private UIElement CreateRegionColorPicker()
    {
        var swatch = new Border
        {
            Width = 24,
            Height = 24,
            CornerRadius = new CornerRadius(4),
            Background = RegionBrush(_regionBoundaryColor),
            BorderBrush = Brushes.White,
            BorderThickness = new Thickness(2),
            Margin = new Thickness(0, 0, 9, 0),
        };
        var value = new TextBlock
        {
            Text = _regionBoundaryColor.ToUpperInvariant(),
            VerticalAlignment = VerticalAlignment.Center,
            FontFamily = new FontFamily("Cascadia Mono, Consolas"),
            FontSize = 13,
        };
        var content = new StackPanel { Orientation = Orientation.Horizontal };
        content.Children.Add(swatch);
        content.Children.Add(value);
        var button = CreateButton(_localization["BoundaryColor"], (_, _) => { });
        button.Content = content;
        button.Click += (_, _) =>
        {
            var current = Colors.LightGreen;
            try
            {
                if (ColorConverter.ConvertFromString(_regionBoundaryColor) is Color parsed) current = parsed;
            }
            catch (FormatException)
            {
                // Preserve a usable picker if a legacy color value is malformed.
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
            if (dialog.ShowDialog(owner) != System.Windows.Forms.DialogResult.OK) return;
            var selected = $"#{dialog.Color.R:X2}{dialog.Color.G:X2}{dialog.Color.B:X2}";
            if (string.Equals(selected, _regionBoundaryColor, StringComparison.OrdinalIgnoreCase)) return;
            _regionBoundaryColor = selected;
            RegionDisplaySettingsChanged();
            RefreshSectionViewIfSelected("region");
        };
        AutomationProperties.SetName(button, _localization["BoundaryColor"]);
        return button;
    }

    private void RegionMorphologyChanged()
    {
        InvalidateAfter("region");
        Dispatcher.BeginInvoke(
            () => RefreshSectionViewIfSelected("region"),
            DispatcherPriority.Background);
    }

    private void RegionDisplaySettingsChanged()
    {
        if (_regionRows.Count == 0) return;
        _ = RefreshAllRegionDisplayPreviewsAsync();
    }

    private Border BuildRegionComputationalCard()
    {
        var stack = new StackPanel();
        stack.Children.Add(CreateSupportingText(_localization["ComputationalRoiIntro"], new Thickness(0, 0, 0, 14)));
        var workflowReady = _sections.First(section => section.Key == "region").Status != WorkflowStatus.NotStarted;
        if (!workflowReady)
            stack.Children.Add(CreateInlineNotice(_localization["CompletePreviousSteps"], warning: true));
        else if (_resolvedCellTypes.Count == 0)
            stack.Children.Add(CreateInlineNotice(_localization["PrerequisiteCellTypes"], warning: true));

        stack.Children.Add(CreateRegionSelectionGroup(
            _localization["CellTypesDefiningRois"],
            _resolvedCellTypes,
            _regionSelectedCellTypes,
            value => value,
            onChanged: () =>
            {
                if (_regionRows.Count > 0) RegionMorphologyChanged();
            }));

        var run = CreateButton(_localization["RunRoiIdentification"], async (_, _) =>
        {
            if (!CommitRegionNumericEditors(_regionComputationalParameterCommitters))
            {
                SetLocalizedStatus("InvalidRegionParameters", isError: true);
                return;
            }
            var result = await RunWorkflowAsync("region", "region", RegionAnalysisPayload());
            if (result is null) return;
            ApplyRegionResultPayload(result.Value, replaceBoundaries: true);
            CaptureExportPaths(result.Value);
            ApplyRegionWarnings(result.Value);
            await RefreshExactRegionPreviewsAsync(useWorkflowStatus: true);
            RefreshSectionViewIfSelected("region");
        }, primary: true);
        run.Margin = new Thickness(0, 16, 0, 0);
        SetActionAvailability(
            run,
            workflowReady && _regionSelectedCellTypes.Count > 0,
            !workflowReady ? _localization["CompletePreviousSteps"] : _localization["PrerequisiteCellTypes"]);
        stack.Children.Add(run);
        return CreateCard(_localization["ComputationalRoiIdentification"], stack);
    }

    private object RegionAnalysisPayload() => new
    {
        selectedTypes = _regionSelectedCellTypes.ToArray(),
        closeUm = _regionClosingRadius,
        dilateUm = _regionDilationRadius,
        minAreaUm2 = _regionMinimumArea,
        minCells = (int)Math.Round(_regionMinimumCells),
        contourDownsample = (int)Math.Round(_regionContourDownsample),
        lineWidth = _regionLineWidth,
        lineStyle = _regionLineStyle,
        boundaryColor = _regionBoundaryColor,
        useTypeColors = _regionUseTypeColors,
    };

    private Border BuildRegionEmptyStateCard()
    {
        var regionStatus = _sections.First(section => section.Key == "region").Status;
        var noRegionsDetected = regionStatus == WorkflowStatus.Error;
        var stack = new StackPanel
        {
            HorizontalAlignment = HorizontalAlignment.Center,
            Margin = new Thickness(30, 24, 30, 24),
        };
        stack.Children.Add(new TextBlock
        {
            Text = noRegionsDetected ? _localization["NoRegionsDetectedTitle"] : _localization["NoRegionResultTitle"],
            Style = (Style)FindResource("CardTitleTextStyle"),
            FontSize = 18,
            HorizontalAlignment = HorizontalAlignment.Center,
        });
        stack.Children.Add(new TextBlock
        {
            Text = noRegionsDetected ? _localization["NoRegionsDetected"] : _localization["NoRegionResultHelp"],
            Style = (Style)FindResource("SupportingTextStyle"),
            Margin = new Thickness(0, 8, 0, 0),
            MaxWidth = 640,
            TextAlignment = TextAlignment.Center,
            TextWrapping = TextWrapping.Wrap,
        });
        return CreateCard(_localization["RegionMap"], stack);
    }

    private UIElement CreateRegionSelectionGroup(
        string title,
        IReadOnlyList<string> available,
        List<string> selection,
        Func<string, string> display,
        Action? onChanged,
        string? selectAllText = null,
        Func<IEnumerable<string>>? selectAllValues = null)
    {
        var host = new StackPanel();
        var heading = new DockPanel { LastChildFill = true, Margin = new Thickness(0, 0, 0, 8) };
        var count = CreateSupportingText($"{selection.Count} / {available.Count}");
        count.Tag = "region-selection-count";
        count.Margin = new Thickness(10, 0, 0, 0);
        count.VerticalAlignment = VerticalAlignment.Center;
        DockPanel.SetDock(count, Dock.Right);
        heading.Children.Add(count);
        var selectAll = CreateButton(selectAllText ?? _localization["SelectAll"], (_, _) =>
        {
            var previous = selection.ToHashSet(StringComparer.Ordinal);
            var next = (selectAllValues?.Invoke() ?? available)
                .Where(value => available.Contains(value, StringComparer.Ordinal))
                .Distinct(StringComparer.Ordinal)
                .ToList();
            if (next.Count == 0 && available.Count > 0) next.Add(available[0]);
            if (previous.SetEquals(next)) return;
            selection.Clear();
            selection.AddRange(next);
            foreach (var checkBox in FindRegionSelectionCheckBoxes(host))
                checkBox.IsChecked = checkBox.DataContext is string value
                                     && selection.Contains(value, StringComparer.Ordinal);
            count.Text = $"{selection.Count} / {available.Count}";
            onChanged?.Invoke();
        });
        selectAll.Padding = new Thickness(10, 4, 10, 4);
        selectAll.Margin = new Thickness(12, 0, 0, 0);
        DockPanel.SetDock(selectAll, Dock.Right);
        heading.Children.Add(selectAll);
        heading.Children.Add(CreateSubsectionTitle(title));
        host.Children.Add(heading);

        var choices = new WrapPanel();
        foreach (var item in available)
        {
            var checkBox = new CheckBox
            {
                Content = display(item),
                Tag = "region-selection",
                DataContext = item,
                IsChecked = selection.Contains(item, StringComparer.Ordinal),
                Margin = new Thickness(0, 0, 18, 10),
                MinWidth = 190,
                VerticalAlignment = VerticalAlignment.Center,
            };
            AutomationProperties.SetName(checkBox, display(item));
            checkBox.Click += (_, _) =>
            {
                var changed = false;
                if (checkBox.IsChecked == true)
                {
                    if (!selection.Contains(item, StringComparer.Ordinal))
                    {
                        selection.Add(item);
                        changed = true;
                    }
                }
                else
                {
                    var selectedCount = selection.Distinct(StringComparer.Ordinal).Count();
                    if (selectedCount <= 1 && selection.Contains(item, StringComparer.Ordinal))
                    {
                        checkBox.IsChecked = true;
                        return;
                    }
                    changed = selection.RemoveAll(value => string.Equals(value, item, StringComparison.Ordinal)) > 0;
                }
                if (!changed) return;
                count.Text = $"{selection.Count} / {available.Count}";
                onChanged?.Invoke();
            };
            choices.Children.Add(checkBox);
        }
        host.Children.Add(new Border
        {
            Background = new SolidColorBrush(Color.FromRgb(247, 249, 250)),
            BorderBrush = (Brush)FindResource("PanelBorderBrush"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(14, 13, 14, 3),
            Child = choices,
        });
        return host;
    }

    private static IEnumerable<CheckBox> FindRegionSelectionCheckBoxes(DependencyObject root)
    {
        for (var index = 0; index < VisualTreeHelper.GetChildrenCount(root); index++)
        {
            var child = VisualTreeHelper.GetChild(root, index);
            if (child is CheckBox { Tag: "region-selection" } checkBox) yield return checkBox;
            foreach (var nested in FindRegionSelectionCheckBoxes(child)) yield return nested;
        }
    }

    private static void SyncRegionSelectionGroup(
        DependencyObject root,
        IReadOnlyCollection<string> selection)
    {
        var selected = selection.ToHashSet(StringComparer.Ordinal);
        var checkBoxes = FindRegionSelectionCheckBoxes(root).ToArray();
        foreach (var checkBox in checkBoxes)
            checkBox.IsChecked = checkBox.DataContext is string value && selected.Contains(value);

        foreach (var count in FindRegionSelectionCountLabels(root))
            count.Text = $"{selected.Count} / {checkBoxes.Length}";
    }

    private static IEnumerable<TextBlock> FindRegionSelectionCountLabels(DependencyObject root)
    {
        for (var index = 0; index < VisualTreeHelper.GetChildrenCount(root); index++)
        {
            var child = VisualTreeHelper.GetChild(root, index);
            if (child is TextBlock { Tag: "region-selection-count" } count) yield return count;
            foreach (var nested in FindRegionSelectionCountLabels(child)) yield return nested;
        }
    }

    private Border BuildRegionMapCard()
    {
        var stack = new StackPanel();
        stack.Children.Add(CreateSupportingText(_localization["RegionMapIntro"], new Thickness(0, 0, 0, 16)));

        var filters = new Grid { Margin = new Thickness(0, 0, 0, 16) };
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(18) });
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var boundaryFilters = CreateRegionSelectionGroup(
            _localization["ComputationalRoisToDisplay"],
            _regionRows.Select(row => row.Label).ToArray(),
            _regionDisplayedBoundaries,
            RegionBoundaryDisplayTitle,
            () =>
            {
                UpdateRegionMapSummary();
                _ = RefreshRegionPreviewAsync(
                    "regionMap",
                    _regionDisplayedBoundaries,
                    _regionDisplayedCellTypes,
                    "display");
            },
            _localization["ShowAllRois"]);
        Grid.SetColumn(boundaryFilters, 0);
        filters.Children.Add(boundaryFilters);
        var typeFilters = CreateRegionSelectionGroup(
            _localization["CellTypesToDisplay"],
            _resolvedCellTypes,
            _regionDisplayedCellTypes,
            value => value,
            () =>
            {
                UpdateRegionMapSummary();
                _ = RefreshRegionPreviewAsync(
                    "regionMap",
                    _regionDisplayedBoundaries,
                    _regionDisplayedCellTypes,
                    "display");
            },
            _localization["ShowAllCellTypes"]);
        Grid.SetColumn(typeFilters, 2);
        filters.Children.Add(typeFilters);
        stack.Children.Add(filters);

        var summary = CreateSupportingText(RegionMapSummaryText(), new Thickness(0, 0, 0, 12));
        summary.FontWeight = FontWeights.SemiBold;
        _regionSummaryLabels["regionMap"] = summary;
        stack.Children.Add(summary);
        stack.Children.Add(CreateRegionPreviewPair("regionMap"));
        return CreateCard(_localization["RegionMap"], stack);
    }

    private string RegionBoundaryDisplayTitle(string label)
    {
        var row = _regionRows.FirstOrDefault(item =>
            string.Equals(item.Label, label, StringComparison.Ordinal));
        if (row is null) return label;
        try
        {
            return string.Format(
                CultureInfo.CurrentCulture,
                _localization["RoiBoundaryDisplayTitle"],
                row.Label,
                row.CellCount);
        }
        catch (FormatException)
        {
            return $"{row.Label} ({row.CellCount:N0} cells)";
        }
    }

    private string RegionMapSummaryText()
    {
        try
        {
            return string.Format(
                CultureInfo.CurrentCulture,
                _localization["RegionsShownSummary"],
                _regionMapRenderedBoundaryCount ?? _regionDisplayedBoundaries.Count,
                _regionRows.Count,
                _regionMapRenderedCellTypeCount ?? _regionDisplayedCellTypes.Count,
                _resolvedCellTypes.Count,
                _regionMapRenderedCellCount ?? _regionRows.Sum(row => row.CellCount));
        }
        catch (FormatException)
        {
            return $"{_regionDisplayedBoundaries.Count}/{_regionRows.Count} ROIs · "
                   + $"{_regionDisplayedCellTypes.Count}/{_resolvedCellTypes.Count} cell types";
        }
    }

    private void UpdateRegionMapSummary()
    {
        if (_regionSummaryLabels.TryGetValue("regionMap", out var summary))
            summary.Text = RegionMapSummaryText();
    }

    private UIElement CreateRegionPreviewPair(string previewKey)
    {
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(18) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var overlayKey = $"{previewKey}Overlay";
        var overlayHost = new ContentControl
        {
            Content = CreateImagePanel(
                _localization["OverlayPreview"],
                _previewPaths.GetValueOrDefault(overlayKey),
                emptyDetail: _localization["RunRegionFirst"],
                previewKey: overlayKey),
        };
        _regionPreviewHosts[overlayKey] = overlayHost;
        Grid.SetColumn(overlayHost, 0);
        grid.Children.Add(overlayHost);

        var maskKey = $"{previewKey}Mask";
        var maskHost = new ContentControl
        {
            Content = CreateImagePanel(
                _localization["CellTypeMaskPreview"],
                _previewPaths.GetValueOrDefault(maskKey),
                emptyDetail: _localization["RunRegionFirst"],
                previewKey: maskKey),
        };
        _regionPreviewHosts[maskKey] = maskHost;
        Grid.SetColumn(maskHost, 2);
        grid.Children.Add(maskHost);
        return grid;
    }

    private async Task RefreshAllRegionDisplayPreviewsAsync()
    {
        await RefreshRegionPreviewAsync(
            "regionMap",
            _regionDisplayedBoundaries,
            _regionDisplayedCellTypes,
            "display");
        if (_regionRows.Count == 0) return;
        await RefreshRegionPreviewAsync(
            "regionCustomized",
            _regionCustomizedBoundaries,
            _regionCustomizedCellTypes,
            "customized");
        await RefreshRegionPreviewAsync(
            "regionManualEditor",
            _regionManualVisibleBoundaries,
            _regionManualSeedCellTypes,
            "manual_editor");
        if (_regionManualPolygons.Count > 0) await RefreshRegionManualPreviewAsync();
    }

    private async Task RefreshExactRegionPreviewsAsync(bool useWorkflowStatus)
    {
        if (_regionRows.Count == 0) return;
        if (useWorkflowStatus)
        {
            await RefreshRegionPreviewAsync(
                "regionMap",
                _regionDisplayedBoundaries,
                _regionDisplayedCellTypes,
                "display");
            await RefreshRegionPreviewAsync(
                "regionCustomized",
                _regionCustomizedBoundaries,
                _regionCustomizedCellTypes,
                "customized");
            await RefreshRegionPreviewAsync(
                "regionManualEditor",
                _regionManualVisibleBoundaries,
                _regionManualSeedCellTypes,
                "manual_editor");
            return;
        }

        await RefreshRegionPreviewDirectAsync(
            "regionMap",
            _regionDisplayedBoundaries,
            _regionDisplayedCellTypes,
            "display");
        await RefreshRegionPreviewDirectAsync(
            "regionCustomized",
            _regionCustomizedBoundaries,
            _regionCustomizedCellTypes,
            "customized");
        await RefreshRegionPreviewDirectAsync(
            "regionManualEditor",
            _regionManualVisibleBoundaries,
            _regionManualSeedCellTypes,
            "manual_editor");
    }

    private object RegionPreviewPayload(
        IReadOnlyCollection<string> boundaryLabels,
        IReadOnlyCollection<string> cellTypes,
        string previewKey) => new
    {
        selectedBoundaryLabels = boundaryLabels.ToArray(),
        selectedCellTypes = cellTypes.ToArray(),
        previewKey,
        boundaryCellTypeMode = string.Equals(previewKey, "manual_editor", StringComparison.Ordinal)
            ? "source"
            : "content",
        lineWidth = _regionLineWidth,
        lineStyle = _regionLineStyle,
        boundaryColor = _regionBoundaryColor,
        useTypeColors = _regionUseTypeColors,
    };

    private async Task RefreshRegionPreviewDirectAsync(
        string targetKey,
        IReadOnlyCollection<string> boundaryLabels,
        IReadOnlyCollection<string> cellTypes,
        string previewKey)
    {
        if (boundaryLabels.Count == 0 || cellTypes.Count == 0) return;
        var result = await _engine.SendAsync(
            "region_preview",
            RegionPreviewPayload(boundaryLabels, cellTypes, previewKey));
        ApplyRegionPreviewResponse(targetKey, result);
    }

    private async Task RefreshRegionPreviewAsync(
        string targetKey,
        IReadOnlyCollection<string> boundaryLabels,
        IReadOnlyCollection<string> cellTypes,
        string previewKey)
    {
        if (_regionRows.Count == 0 || boundaryLabels.Count == 0 || cellTypes.Count == 0) return;
        if (targetKey == "regionMap")
        {
            _regionMapRenderedBoundaryCount = null;
            _regionMapRenderedCellTypeCount = null;
            _regionMapRenderedCellCount = null;
            UpdateRegionMapSummary();
        }
        var generation = _regionPreviewGenerations.GetValueOrDefault(targetKey) + 1;
        _regionPreviewGenerations[targetKey] = generation;
        try
        {
            var result = await _engine.SendAsync(
                "region_preview",
                RegionPreviewPayload(boundaryLabels, cellTypes, previewKey));
            if (!_regionPreviewGenerations.TryGetValue(targetKey, out var currentGeneration)
                || generation != currentGeneration) return;
            ApplyRegionPreviewResponse(targetKey, result);
        }
        catch (Exception exception)
        {
            if (_regionPreviewGenerations.TryGetValue(targetKey, out var currentGeneration)
                && generation == currentGeneration)
                SetStatus(LocalizeEngineError(exception.Message), isError: true);
        }
    }

    private void ApplyRegionPreviewResponse(string targetKey, JsonElement result)
    {
        StoreRegionPreviewPaths(
            result,
            targetKey,
            exactPrimary: string.Equals(targetKey, "regionManualEditor", StringComparison.Ordinal));
        if (targetKey == "regionMap")
        {
            _regionMapRenderedBoundaryCount = RegionJsonOptionalInt(result, "boundaryCount");
            if (result.TryGetProperty("summary", out var summary)
                && summary.ValueKind == JsonValueKind.Object)
            {
                _regionMapRenderedBoundaryCount ??= RegionJsonOptionalInt(summary, "boundaryCount");
                _regionMapRenderedCellTypeCount = RegionJsonOptionalInt(summary, "cellTypeCount");
                _regionMapRenderedCellCount = RegionJsonOptionalInt(summary, "cellCount");
            }
            _regionMapRenderedCellCount ??= RegionJsonOptionalInt(result, "cellCount");
            UpdateRegionMapSummary();
        }
        if (result.TryGetProperty("width", out var width) && width.ValueKind == JsonValueKind.Number)
            _regionSourceWidth = Math.Max(0, width.GetInt32());
        if (result.TryGetProperty("height", out var height) && height.ValueKind == JsonValueKind.Number)
            _regionSourceHeight = Math.Max(0, height.GetInt32());
        ApplyRegionWarnings(result);
        UpdateRegionPreviewSurface(targetKey);
    }

    private void UpdateRegionPreviewSurface(string targetKey)
    {
        if (targetKey == "regionManualEditor" && _activeRegionDrawingCanvas is not null)
        {
            var path = _previewPaths.GetValueOrDefault(targetKey);
            if (!string.IsNullOrWhiteSpace(path) && File.Exists(path))
            {
                var bitmap = LoadBoundedBitmap(path, 3000);
                _activeRegionDrawingCanvas.BackgroundImage = bitmap;
                var dimensionsMatch = RegionPreviewMatchesSource(bitmap, _regionSourceWidth, _regionSourceHeight);
                _activeRegionDrawingCanvas.SourcePixelWidth = dimensionsMatch ? _regionSourceWidth : 0;
                _activeRegionDrawingCanvas.SourcePixelHeight = dimensionsMatch ? _regionSourceHeight : 0;
                _activeRegionDrawingCanvas.IsEnabled = dimensionsMatch;
            }
            else
            {
                _activeRegionDrawingCanvas.BackgroundImage = null;
                _activeRegionDrawingCanvas.SourcePixelWidth = 0;
                _activeRegionDrawingCanvas.SourcePixelHeight = 0;
                _activeRegionDrawingCanvas.IsEnabled = false;
            }
            return;
        }
        if (targetKey is "regionMap" or "regionCustomized")
        {
            UpdateRegionPreviewImageHost($"{targetKey}Overlay", _localization["OverlayPreview"]);
            UpdateRegionPreviewImageHost($"{targetKey}Mask", _localization["CellTypeMaskPreview"]);
            return;
        }
        var title = targetKey switch
        {
            "regionManualAdjusted" => _localization["ManualPreview"],
            _ => _localization["Preview"],
        };
        UpdateRegionPreviewImageHost(targetKey, title);
    }

    private void UpdateRegionPreviewImageHost(string targetKey, string title)
    {
        if (!_regionPreviewHosts.TryGetValue(targetKey, out var host)) return;
        host.Content = CreateImagePanel(
            title,
            _previewPaths.GetValueOrDefault(targetKey),
            emptyDetail: _localization["RunRegionFirst"],
            previewKey: targetKey);
    }

    private Border BuildRegionManualCard()
    {
        var stack = new StackPanel();
        stack.Children.Add(CreateSupportingText(_localization["ManualRoiIntro"], new Thickness(0, 0, 0, 16)));

        RegionDrawingCanvas? drawingCanvas = null;
        Button? closeAreaButton = null;
        Button? saveButton = null;
        TextBlock? drawingStatus = null;

        var modeAndName = new Grid { Margin = new Thickness(0, 0, 0, 16) };
        modeAndName.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        modeAndName.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(18) });
        modeAndName.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

        var modePanel = new StackPanel();
        modePanel.Children.Add(CreateFieldLabel(_localization["AdjustmentMode"], new Thickness(0, 0, 0, 7)));
        var modePicker = new ComboBox { MinWidth = 250, HorizontalAlignment = HorizontalAlignment.Left };
        modePicker.Items.Add(new ComboBoxItem { Content = _localization["CreateNewRegion"], Tag = "create" });
        modePicker.Items.Add(new ComboBoxItem { Content = _localization["IncludeInRegion"], Tag = "include" });
        modePicker.Items.Add(new ComboBoxItem { Content = _localization["ExcludeFromRegion"], Tag = "exclude" });
        modePicker.SelectedItem = modePicker.Items.Cast<ComboBoxItem>()
            .First(item => string.Equals(item.Tag?.ToString(), _regionManualMode, StringComparison.Ordinal));
        AutomationProperties.SetName(modePicker, _localization["AdjustmentMode"]);
        AutomationProperties.SetHelpText(modePicker, _localization["ManualRoiIntro"]);
        modePanel.Children.Add(modePicker);

        modePanel.Children.Add(CreateFieldLabel(_localization["BoundaryToEdit"], new Thickness(0, 14, 0, 7)));
        var targetPicker = new ComboBox
        {
            ItemsSource = _regionRows.Select(row => row.Label).ToArray(),
            SelectedItem = _regionManualTargetBoundary,
            MinWidth = 330,
            HorizontalAlignment = HorizontalAlignment.Left,
            IsEnabled = _regionManualMode != "create",
        };
        AutomationProperties.SetName(targetPicker, _localization["BoundaryToEdit"]);
        AutomationProperties.SetHelpText(targetPicker, _localization["BoundaryToEdit"]);
        modePanel.Children.Add(targetPicker);
        Grid.SetColumn(modePanel, 0);
        modeAndName.Children.Add(modePanel);

        var namePanel = new StackPanel();
        namePanel.Children.Add(CreateFieldLabel(_localization["NewBoundaryName"], new Thickness(0, 0, 0, 7)));
        var nameEditor = new TextBox
        {
            Text = _regionManualDisplayName,
            MinWidth = 300,
            HorizontalAlignment = HorizontalAlignment.Stretch,
        };
        AutomationProperties.SetName(nameEditor, _localization["NewBoundaryName"]);
        AutomationProperties.SetHelpText(nameEditor, _localization["NewBoundaryName"]);
        nameEditor.TextChanged += (_, _) =>
        {
            _regionManualDisplayName = nameEditor.Text.Trim();
            if (saveButton is not null) UpdateRegionManualActionAvailability(closeAreaButton, saveButton);
        };
        namePanel.Children.Add(nameEditor);
        namePanel.Children.Add(CreateFieldLabel(_localization["DrawingMode"], new Thickness(0, 14, 0, 7)));
        var drawingModePicker = new ComboBox { MinWidth = 220, HorizontalAlignment = HorizontalAlignment.Left };
        drawingModePicker.Items.Add(new ComboBoxItem
        {
            Content = _localization["Polygon"],
            Tag = RegionDrawingMode.Polygon,
        });
        drawingModePicker.Items.Add(new ComboBoxItem
        {
            Content = _localization["FreeDraw"],
            Tag = RegionDrawingMode.FreeDraw,
        });
        drawingModePicker.SelectedItem = drawingModePicker.Items.Cast<ComboBoxItem>()
            .First(item => item.Tag is RegionDrawingMode value && value == _regionManualDrawingMode);
        AutomationProperties.SetName(drawingModePicker, _localization["DrawingMode"]);
        AutomationProperties.SetHelpText(drawingModePicker, _localization["DrawingHelpPolygon"]);
        namePanel.Children.Add(drawingModePicker);
        Grid.SetColumn(namePanel, 2);
        modeAndName.Children.Add(namePanel);
        stack.Children.Add(modeAndName);

        var filters = new Grid { Margin = new Thickness(0, 0, 0, 16) };
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(18) });
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var visibleBoundaries = CreateRegionSelectionGroup(
            _localization["BoundariesVisibleWhileEditing"],
            _regionRows.Select(row => row.Label).ToArray(),
            _regionManualVisibleBoundaries,
            RegionBoundaryDisplayTitle,
            () => _ = RefreshRegionManualContextAsync(),
            _localization["ShowAllBoundaries"]);
        Grid.SetColumn(visibleBoundaries, 0);
        filters.Children.Add(visibleBoundaries);
        var seedTypes = CreateRegionSelectionGroup(
            _localization["SeedCellTypes"],
            _resolvedCellTypes,
            _regionManualSeedCellTypes,
            value => value,
            () => _ = RefreshRegionManualContextAsync(),
            _localization["UseTargetCellType"],
            PreferredRegionManualSeedTypes);
        Grid.SetColumn(seedTypes, 2);
        filters.Children.Add(seedTypes);
        stack.Children.Add(filters);

        stack.Children.Add(CreateSubsectionTitle(_localization["ManualBoundaryParameters"], new Thickness(0, 0, 0, 7)));
        stack.Children.Add(CreateSupportingText(
            _localization["ManualBoundaryParametersIntro"],
            new Thickness(0, 0, 0, 12)));
        var manualParameters = new WrapPanel { ItemWidth = 318, Margin = new Thickness(0, 0, 0, 4) };
        manualParameters.Children.Add(CreateRegionManualNumericTile(
            _localization["ClosingRadius"], _localization["ManualClosingRadiusHelp"], _regionManualClosingRadius,
            value => _regionManualClosingRadius = value, "µm", 0, 30));
        manualParameters.Children.Add(CreateRegionManualNumericTile(
            _localization["DilationRadius"], _localization["ManualDilationRadiusHelp"], _regionManualDilationRadius,
            value => _regionManualDilationRadius = value, "µm", 0, 30));
        manualParameters.Children.Add(CreateRegionManualNumericTile(
            _localization["MinimumArea"], _localization["ManualMinimumAreaHelp"], _regionManualMinimumArea,
            value => _regionManualMinimumArea = value, "µm²", 0, 1_000_000_000));
        manualParameters.Children.Add(CreateRegionManualNumericTile(
            _localization["MinimumCells"], _localization["ManualMinimumCellsHelp"], _regionManualMinimumCells,
            value => _regionManualMinimumCells = Math.Round(value), string.Empty, 1, 1_000_000, Math.Round));
        var manualContour = new ComboBox
        {
            ItemsSource = RegionContourDownsampleOptions,
            SelectedItem = NormalizeRegionContourDownsample(_regionManualContourDownsample),
            MinWidth = 150,
            HorizontalAlignment = HorizontalAlignment.Left,
        };
        AutomationProperties.SetName(manualContour, _localization["ContourDownsample"]);
        AutomationProperties.SetHelpText(manualContour, _localization["ManualContourDownsampleHelp"]);
        manualContour.SelectionChanged += (_, _) =>
        {
            if (manualContour.SelectedItem is not int value || AreClose(_regionManualContourDownsample, value)) return;
            _regionManualContourDownsample = value;
            if (_regionManualPolygons.Count > 0) _ = RefreshRegionManualPreviewAsync();
        };
        manualParameters.Children.Add(CreateRegionParameterTile(
            _localization["ContourDownsample"],
            _localization["ManualContourDownsampleHelp"],
            manualContour));
        stack.Children.Add(manualParameters);

        stack.Children.Add(CreateSubsectionTitle(_localization["DrawEditRoi"], new Thickness(0, 4, 0, 7)));
        var help = CreateSupportingText(
            _regionManualDrawingMode == RegionDrawingMode.Polygon
                ? _localization["DrawingHelpPolygon"]
                : _localization["DrawingHelpFreeDraw"],
            new Thickness(0, 0, 0, 10));
        stack.Children.Add(help);

        var backgroundPath = _previewPaths.GetValueOrDefault("regionManualEditor");
        BitmapImage? background = null;
        if (!string.IsNullOrWhiteSpace(backgroundPath) && File.Exists(backgroundPath))
            background = LoadBoundedBitmap(backgroundPath, 3000);
        var backgroundMatchesSource = RegionPreviewMatchesSource(background, _regionSourceWidth, _regionSourceHeight);
        drawingCanvas = new RegionDrawingCanvas
        {
            Height = 510,
            BackgroundImage = background,
            SourcePixelWidth = backgroundMatchesSource ? _regionSourceWidth : 0,
            SourcePixelHeight = backgroundMatchesSource ? _regionSourceHeight : 0,
            DrawingMode = _regionManualDrawingMode,
            InitialPolygons = _regionManualPolygons,
            IsEnabled = backgroundMatchesSource,
        };
        _activeRegionDrawingCanvas = drawingCanvas;
        AutomationProperties.SetName(drawingCanvas, _localization["DrawEditRoi"]);
        AutomationProperties.SetHelpText(drawingCanvas, _localization["DrawingCanvasAutomationHelp"]);
        stack.Children.Add(new Border
        {
            Background = Brushes.Black,
            BorderBrush = (Brush)FindResource("PanelBorderBrush"),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            Padding = new Thickness(6),
            Child = drawingCanvas,
        });
        if (!drawingCanvas.IsEnabled)
            stack.Children.Add(CreateInlineNotice(_localization["RunRegionFirst"], warning: true));

        drawingStatus = CreateSupportingText(string.Empty, new Thickness(0, 9, 0, 0));
        AutomationProperties.SetLiveSetting(drawingStatus, AutomationLiveSetting.Polite);
        stack.Children.Add(drawingStatus);
        UpdateRegionDrawingStatus(drawingStatus, drawingCanvas);

        var actions = new WrapPanel { Margin = new Thickness(0, 14, 0, 16) };
        var reset = CreateButton(_localization["ResetDrawing"], (_, _) =>
        {
            drawingCanvas.ResetDrawing();
            _regionManualPolygons = [];
            _previewPaths.Remove("regionManualAdjusted");
            UpdateRegionPreviewSurface("regionManualAdjusted");
            UpdateRegionDrawingStatus(drawingStatus, drawingCanvas);
            UpdateRegionManualActionAvailability(closeAreaButton, saveButton);
        });
        actions.Children.Add(reset);
        closeAreaButton = CreateButton(_localization["CloseCurrentArea"], (_, _) =>
        {
            if (!drawingCanvas.CloseCurrentArea())
            {
                SetStatus(_localization["DrawAtLeastOneArea"], isError: true);
                return;
            }
            CopyRegionManualPolygons(drawingCanvas.CompletedPolygons);
        });
        actions.Children.Add(closeAreaButton);
        saveButton = CreateButton(_localization["SaveAdjustedRoi"], async (_, _) =>
        {
            await SaveRegionManualRoiAsync();
        }, primary: true);
        actions.Children.Add(saveButton);
        stack.Children.Add(actions);
        UpdateRegionManualActionAvailability(closeAreaButton, saveButton);
        if (!drawingCanvas.IsEnabled)
            SetActionAvailability(closeAreaButton, false, _localization["RunRegionFirst"]);

        var manualPreviewHost = new ContentControl
        {
            Content = CreateImagePanel(
                _localization["ManualPreview"],
                _previewPaths.GetValueOrDefault("regionManualAdjusted"),
                emptyDetail: _localization["DrawAtLeastOneArea"],
                previewKey: "regionManualAdjusted"),
        };
        _regionPreviewHosts["regionManualAdjusted"] = manualPreviewHost;
        stack.Children.Add(manualPreviewHost);

        var previousCompletedAreaCount = _regionManualPolygons.Count;
        drawingCanvas.DrawingChanged += async (_, eventArgs) =>
        {
            CopyRegionManualPolygons(eventArgs.CompletedPolygons);
            UpdateRegionDrawingStatus(drawingStatus, drawingCanvas);
            UpdateRegionManualActionAvailability(closeAreaButton, saveButton);
            if (eventArgs.CompletedPolygons.Count > previousCompletedAreaCount)
                await RefreshRegionManualPreviewAsync();
            previousCompletedAreaCount = eventArgs.CompletedPolygons.Count;
        };

        modePicker.SelectionChanged += (_, _) =>
        {
            if (modePicker.SelectedItem is not ComboBoxItem { Tag: string value }
                || string.Equals(value, _regionManualMode, StringComparison.Ordinal)) return;
            _regionManualMode = value;
            targetPicker.IsEnabled = value != "create";
            if (value != "create" && _regionManualTargetBoundary is null)
                _regionManualTargetBoundary = _regionRows.FirstOrDefault()?.Label;
            _regionManualSeedCellTypes.Clear();
            _regionManualSeedCellTypes.AddRange(PreferredRegionManualSeedTypes());
            SyncRegionSelectionGroup(seedTypes, _regionManualSeedCellTypes);
            ResetRegionManualDrawing(drawingCanvas, drawingStatus, closeAreaButton, saveButton);
            _ = RefreshRegionManualContextAsync();
        };
        targetPicker.SelectionChanged += (_, _) =>
        {
            if (targetPicker.SelectedItem is not string value
                || string.Equals(value, _regionManualTargetBoundary, StringComparison.Ordinal)) return;
            _regionManualTargetBoundary = value;
            _regionManualSeedCellTypes.Clear();
            _regionManualSeedCellTypes.AddRange(PreferredRegionManualSeedTypes());
            SyncRegionSelectionGroup(seedTypes, _regionManualSeedCellTypes);
            ResetRegionManualDrawing(drawingCanvas, drawingStatus, closeAreaButton, saveButton);
            _ = RefreshRegionManualContextAsync();
        };
        drawingModePicker.SelectionChanged += (_, _) =>
        {
            if (drawingModePicker.SelectedItem is not ComboBoxItem { Tag: RegionDrawingMode value }
                || value == _regionManualDrawingMode) return;
            _regionManualDrawingMode = value;
            drawingCanvas.DrawingMode = value;
            ResetRegionManualDrawing(drawingCanvas, drawingStatus, closeAreaButton, saveButton);
            help.Text = value == RegionDrawingMode.Polygon
                ? _localization["DrawingHelpPolygon"]
                : _localization["DrawingHelpFreeDraw"];
            AutomationProperties.SetHelpText(drawingModePicker, help.Text);
            AutomationProperties.SetHelpText(drawingCanvas, _localization["DrawingCanvasAutomationHelp"]);
        };
        return CreateCard(_localization["ManualRoiAdjustment"], stack);
    }

    private static bool RegionPreviewMatchesSource(BitmapSource? preview, int sourceWidth, int sourceHeight)
    {
        if (preview is null || sourceWidth <= 0 || sourceHeight <= 0
            || preview.PixelWidth <= 0 || preview.PixelHeight <= 0)
        {
            return false;
        }
        var sourceAspect = (double)sourceWidth / sourceHeight;
        var previewAspect = (double)preview.PixelWidth / preview.PixelHeight;
        return Math.Abs(sourceAspect - previewAspect) <= Math.Max(0.001, sourceAspect * 0.001);
    }

    private Border CreateRegionManualNumericTile(
        string title,
        string help,
        double value,
        Action<double> setter,
        string unit,
        double minimum,
        double maximum,
        Func<double, double>? normalize = null)
    {
        var committed = normalize?.Invoke(value) ?? value;
        var row = new DockPanel { LastChildFill = true };
        if (!string.IsNullOrWhiteSpace(unit))
        {
            var unitText = CreateSupportingText(unit);
            unitText.Margin = new Thickness(8, 0, 0, 0);
            unitText.VerticalAlignment = VerticalAlignment.Center;
            DockPanel.SetDock(unitText, Dock.Right);
            row.Children.Add(unitText);
        }
        var editor = new TextBox { Text = committed.ToString("0.###", CultureInfo.CurrentCulture) };
        AutomationProperties.SetName(editor, title);
        AutomationProperties.SetHelpText(editor, help);
        var validation = CreateRegionNumericValidation(minimum, maximum);
        bool Commit()
        {
            if (!TryReadDouble(editor.Text, out var parsed)
                || !double.IsFinite(parsed)
                || parsed < minimum
                || parsed > maximum)
            {
                _regionInvalidNumericEditors.Add(editor);
                validation.Visibility = Visibility.Visible;
                editor.BorderBrush = (Brush)FindResource("ErrorBrush");
                AutomationProperties.SetHelpText(editor, $"{help} {validation.Text}");
                return false;
            }
            _regionInvalidNumericEditors.Remove(editor);
            validation.Visibility = Visibility.Collapsed;
            editor.ClearValue(Control.BorderBrushProperty);
            AutomationProperties.SetHelpText(editor, help);
            var normalized = normalize?.Invoke(parsed) ?? parsed;
            editor.Text = normalized.ToString("0.###", CultureInfo.CurrentCulture);
            ClearRegionParameterErrorIfResolved();
            if (AreClose(normalized, committed)) return true;
            committed = normalized;
            setter(normalized);
            if (_regionManualPolygons.Count > 0) _ = RefreshRegionManualPreviewAsync();
            return true;
        }
        _regionManualParameterCommitters.Add(Commit);
        editor.LostKeyboardFocus += (_, _) => Commit();
        editor.PreviewKeyDown += (_, eventArgs) =>
        {
            if (eventArgs.Key is not (Key.Enter or Key.Return)) return;
            if (Commit()) Keyboard.ClearFocus();
            else SetLocalizedStatus("InvalidRegionParameters", isError: true);
            eventArgs.Handled = true;
        };
        row.Children.Add(editor);
        var editorAndValidation = new StackPanel();
        editorAndValidation.Children.Add(row);
        editorAndValidation.Children.Add(validation);
        return CreateRegionParameterTile(title, help, editorAndValidation);
    }

    private void CopyRegionManualPolygons(IReadOnlyList<IReadOnlyList<Point>> polygons)
    {
        _regionManualPolygons = polygons
            .Where(polygon => polygon.Count >= 3)
            .Select(polygon => polygon.ToList())
            .ToList();
    }

    private void ResetRegionManualDrawing(
        RegionDrawingCanvas drawingCanvas,
        TextBlock drawingStatus,
        Button? closeAreaButton,
        Button? saveButton)
    {
        drawingCanvas.ResetDrawing();
        _regionManualPolygons = [];
        _previewPaths.Remove("regionManualAdjusted");
        UpdateRegionPreviewSurface("regionManualAdjusted");
        UpdateRegionDrawingStatus(drawingStatus, drawingCanvas);
        UpdateRegionManualActionAvailability(closeAreaButton, saveButton);
    }

    private void UpdateRegionDrawingStatus(TextBlock status, RegionDrawingCanvas canvas)
    {
        status.Text = string.Format(
                          CultureInfo.CurrentCulture,
                          _localization["CompletedAreas"],
                          canvas.CompletedPolygons.Count)
                      + " · "
                      + string.Format(
                          CultureInfo.CurrentCulture,
                          _localization["CurrentDrawingPoints"],
                          canvas.CurrentPointCount);
    }

    private void UpdateRegionManualActionAvailability(Button? closeAreaButton, Button? saveButton)
    {
        if (closeAreaButton is not null)
        {
            var canClose = _activeRegionDrawingCanvas is { IsEnabled: true, CurrentPointCount: >= 3 };
            SetActionAvailability(closeAreaButton, canClose, _localization["DrawAtLeastOneArea"]);
        }
        if (saveButton is null) return;
        var targetReady = _regionManualMode == "create" || !string.IsNullOrWhiteSpace(_regionManualTargetBoundary);
        var canSave = _regionManualPolygons.Count > 0
                      && targetReady
                      && !string.IsNullOrWhiteSpace(_regionManualDisplayName);
        SetActionAvailability(saveButton, canSave, _localization["DrawAtLeastOneArea"]);
    }

    private async Task RefreshRegionManualContextAsync()
    {
        await RefreshRegionPreviewAsync(
            "regionManualEditor",
            _regionManualVisibleBoundaries,
            _regionManualSeedCellTypes,
            "manual_editor");
        if (_regionManualPolygons.Count > 0) await RefreshRegionManualPreviewAsync();
    }

    private IEnumerable<string> PreferredRegionManualSeedTypes()
    {
        var available = _resolvedCellTypes.ToArray();
        if (available.Length == 0) return [];
        var target = _regionRows.FirstOrDefault(row =>
            string.Equals(row.Label, _regionManualTargetBoundary, StringComparison.Ordinal));
        foreach (var candidate in new[] { target?.Label, target?.DominantType })
        {
            if (!string.IsNullOrWhiteSpace(candidate)
                && available.Contains(candidate, StringComparer.Ordinal))
                return [candidate];
        }
        return [available[0]];
    }

    private async Task RefreshRegionManualPreviewAsync()
    {
        if (_regionManualPolygons.Count == 0) return;
        var generation = ++_regionManualPreviewGeneration;
        var result = await RunWorkflowAsync(
            "region",
            "region_manual_preview",
            RegionManualPayload(includeDisplayName: false),
            completesSection: false);
        if (result is null || generation != _regionManualPreviewGeneration) return;
        if (result.Value.TryGetProperty("previewPath", out var preview)
            && preview.ValueKind == JsonValueKind.String
            && !string.IsNullOrWhiteSpace(preview.GetString()))
        {
            _previewPaths["regionManualAdjusted"] = preview.GetString()!;
        }
        ApplyRegionWarnings(result.Value);
        UpdateRegionPreviewSurface("regionManualAdjusted");
    }

    private object RegionManualPayload(bool includeDisplayName) => new
    {
        mode = _regionManualMode,
        targetBoundaryLabel = _regionManualMode == "create" ? null : _regionManualTargetBoundary,
        displayName = includeDisplayName ? _regionManualDisplayName : null,
        polygons = _regionManualPolygons.Select(polygon =>
            polygon.Select(point => new { x = point.X, y = point.Y }).ToArray()).ToArray(),
        seedCellTypes = _regionManualSeedCellTypes.ToArray(),
        selectedCellTypes = _regionManualSeedCellTypes.ToArray(),
        previewKey = "manual_adjusted",
        closeUm = _regionManualClosingRadius,
        dilateUm = _regionManualDilationRadius,
        minAreaUm2 = _regionManualMinimumArea,
        minCells = (int)Math.Round(_regionManualMinimumCells),
        contourDownsample = (int)Math.Round(_regionManualContourDownsample),
        lineWidth = _regionLineWidth,
        lineStyle = _regionLineStyle,
        boundaryColor = _regionBoundaryColor,
        useTypeColors = _regionUseTypeColors,
    };

    private async Task SaveRegionManualRoiAsync()
    {
        var displayParametersValid = CommitRegionNumericEditors(_regionDisplayParameterCommitters);
        var manualParametersValid = CommitRegionNumericEditors(_regionManualParameterCommitters);
        if (!displayParametersValid || !manualParametersValid)
        {
            SetLocalizedStatus("InvalidRegionParameters", isError: true);
            return;
        }
        if (_regionManualPolygons.Count == 0 || string.IsNullOrWhiteSpace(_regionManualDisplayName))
        {
            SetStatus(_localization["DrawAtLeastOneArea"], isError: true);
            return;
        }
        if (_regionManualMode != "create" && string.IsNullOrWhiteSpace(_regionManualTargetBoundary))
        {
            SetStatus(_localization["AllSelectionsRequired"], isError: true);
            return;
        }
        var result = await RunWorkflowAsync(
            "region",
            "region_manual_save",
            RegionManualPayload(includeDisplayName: true),
            completesSection: false);
        if (result is null) return;
        ApplyRegionResultPayload(result.Value, replaceBoundaries: true, replaceBasePreview: false);
        var savedLabel = result.Value.TryGetProperty("summary", out var saveSummary)
                         && saveSummary.ValueKind == JsonValueKind.Object
                         && saveSummary.TryGetProperty("savedRegionLabel", out var savedLabelElement)
                         && savedLabelElement.ValueKind == JsonValueKind.String
            ? savedLabelElement.GetString()
            : null;
        if (!string.IsNullOrWhiteSpace(savedLabel))
        {
            foreach (var selection in new[]
                     {
                         _regionDisplayedBoundaries,
                         _regionCustomizedBoundaries,
                         _regionManualVisibleBoundaries,
                     })
            {
                if (!selection.Contains(savedLabel, StringComparer.Ordinal)) selection.Add(savedLabel);
            }
        }
        CaptureExportPaths(result.Value);
        InvalidateDownstreamAfterRegionCatalogChange();
        _regionManualPolygons = [];
        ApplyRegionWarnings(result.Value);
        await RefreshRegionPreviewAsync(
            "regionMap",
            _regionDisplayedBoundaries,
            _regionDisplayedCellTypes,
            "display");
        await RefreshRegionPreviewAsync(
            "regionCustomized",
            _regionCustomizedBoundaries,
            _regionCustomizedCellTypes,
            "customized");
        await RefreshRegionPreviewAsync(
            "regionManualEditor",
            _regionManualVisibleBoundaries,
            _regionManualSeedCellTypes,
            "manual_editor");
        SetLocalizedStatus("CreatedRoiSaved");
        RefreshSectionViewIfSelected("region");
    }

    private void InvalidateDownstreamAfterRegionCatalogChange()
    {
        RemoveWorkflowPreviews("distribution");
        RemoveWorkflowPreviews("distance");
        _outputFiles.Clear();
        _distributionBoundaryLabel = null;
        _distanceBoundaryLabel = null;
        var regionIndex = _sections.ToList().FindIndex(section => section.Key == "region");
        for (var index = regionIndex + 1; index < _sections.Count; index++)
            _sections[index].Status = index == regionIndex + 1 ? WorkflowStatus.Ready : WorkflowStatus.NotStarted;
        UpdateProgressMetadata();
        UpdateHeader();
    }

    private Border BuildRegionCustomizedCard()
    {
        var stack = new StackPanel();
        stack.Children.Add(CreateSupportingText(_localization["CustomizedDisplayIntro"], new Thickness(0, 0, 0, 16)));
        var filters = new Grid { Margin = new Thickness(0, 0, 0, 16) };
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(18) });
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var boundaries = CreateRegionSelectionGroup(
            _localization["BoundariesToInclude"],
            _regionRows.Select(row => row.Label).ToArray(),
            _regionCustomizedBoundaries,
            RegionBoundaryDisplayTitle,
            () => _ = RefreshRegionPreviewAsync(
                "regionCustomized",
                _regionCustomizedBoundaries,
                _regionCustomizedCellTypes,
                "customized"),
            _localization["UseAllBoundaries"]);
        Grid.SetColumn(boundaries, 0);
        filters.Children.Add(boundaries);
        var types = CreateRegionSelectionGroup(
            _localization["CellTypesToShow"],
            _resolvedCellTypes,
            _regionCustomizedCellTypes,
            value => value,
            () => _ = RefreshRegionPreviewAsync(
                "regionCustomized",
                _regionCustomizedBoundaries,
                _regionCustomizedCellTypes,
                "customized"),
            _localization["UseAllCellTypes"]);
        Grid.SetColumn(types, 2);
        filters.Children.Add(types);
        stack.Children.Add(filters);

        stack.Children.Add(CreateRegionPreviewPair("regionCustomized"));

        var save = CreateButton(_localization["SaveCustomizedDisplay"], async (_, _) =>
        {
            if (!CommitRegionNumericEditors(_regionDisplayParameterCommitters))
            {
                SetLocalizedStatus("InvalidRegionParameters", isError: true);
                return;
            }
            var result = await RunWorkflowAsync("region", "region_custom_export", new
            {
                selectedBoundaryLabels = _regionCustomizedBoundaries.ToArray(),
                selectedCellTypes = _regionCustomizedCellTypes.ToArray(),
                previewKey = "customized_export",
                boundaryCellTypeMode = "content",
                lineWidth = _regionLineWidth,
                lineStyle = _regionLineStyle,
                boundaryColor = _regionBoundaryColor,
                useTypeColors = _regionUseTypeColors,
            }, completesSection: false);
            if (result is null) return;
            StoreRegionPreviewPaths(result.Value, "regionCustomized", exactPrimary: false);
            CaptureExportPaths(result.Value);
            ApplyRegionWarnings(result.Value);
            SetLocalizedStatus("CustomizedDisplaySaved");
            UpdateRegionPreviewSurface("regionCustomized");
        }, primary: true);
        save.Margin = new Thickness(0, 16, 0, 0);
        SetActionAvailability(
            save,
            _regionCustomizedBoundaries.Count > 0 && _regionCustomizedCellTypes.Count > 0,
            _localization["AllSelectionsRequired"]);
        stack.Children.Add(save);
        return CreateCard(_localization["CustomizedDisplayAndSave"], stack);
    }

    private Border BuildRegionCountsCard()
    {
        var stack = new StackPanel();
        stack.Children.Add(CreateSupportingText(_localization["RegionCountsHelp"], new Thickness(0, 0, 0, 14)));
        if (_regionDominantCounts.Count == 0)
        {
            stack.Children.Add(CreateInlineNotice(_localization["NoRegionsDetected"]));
            return CreateCard(_localization["RegionCounts"], stack);
        }

        var maximum = Math.Max(1, _regionDominantCounts.Max(row => row.Count));
        foreach (var row in _regionDominantCounts)
        {
            var grid = new Grid { Margin = new Thickness(0, 0, 0, 10) };
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(215) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(70) });
            var labelPanel = new StackPanel { Orientation = Orientation.Horizontal };
            labelPanel.Children.Add(new Border
            {
                Width = 12,
                Height = 12,
                CornerRadius = new CornerRadius(6),
                Background = RegionBrush(row.ColorHex),
                Margin = new Thickness(0, 2, 9, 0),
            });
            labelPanel.Children.Add(CreateFieldLabel(row.Name));
            Grid.SetColumn(labelPanel, 0);
            grid.Children.Add(labelPanel);
            var bar = new ProgressBar
            {
                Minimum = 0,
                Maximum = maximum,
                Value = row.Count,
                Height = 14,
                Foreground = RegionBrush(row.ColorHex),
                Background = new SolidColorBrush(Color.FromRgb(232, 238, 240)),
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(8, 0, 14, 0),
            };
            AutomationProperties.SetName(bar, $"{row.Name}: {row.Count}");
            Grid.SetColumn(bar, 1);
            grid.Children.Add(bar);
            var count = new TextBlock
            {
                Text = row.Count.ToString("N0", CultureInfo.CurrentCulture),
                FontWeight = FontWeights.SemiBold,
                HorizontalAlignment = HorizontalAlignment.Right,
                VerticalAlignment = VerticalAlignment.Center,
            };
            Grid.SetColumn(count, 2);
            grid.Children.Add(count);
            stack.Children.Add(grid);
        }
        return CreateCard(_localization["RegionCounts"], stack);
    }

    private Border BuildRegionTableCard()
    {
        var grid = new DataGrid
        {
            ItemsSource = _regionRows,
            IsReadOnly = true,
            CanUserAddRows = false,
            CanUserDeleteRows = false,
            AutoGenerateColumns = false,
            MinHeight = 230,
            MaxHeight = 520,
            SelectionMode = DataGridSelectionMode.Single,
        };
        grid.Columns.Add(new DataGridTextColumn
        {
            Header = _localization["RoiName"],
            Binding = new Binding(nameof(RegionSummaryRow.Label)),
            Width = new DataGridLength(1, DataGridLengthUnitType.Star),
        });
        grid.Columns.Add(new DataGridTextColumn
        {
            Header = _localization["DominantType"],
            Binding = new Binding(nameof(RegionSummaryRow.DominantType)),
            Width = new DataGridLength(0.75, DataGridLengthUnitType.Star),
        });
        grid.Columns.Add(new DataGridTextColumn
        {
            Header = _localization["Cells"],
            Binding = new Binding(nameof(RegionSummaryRow.CellCount)) { StringFormat = "N0" },
            Width = 95,
        });
        grid.Columns.Add(new DataGridTextColumn
        {
            Header = _localization["AreaUm2"],
            Binding = new Binding(nameof(RegionSummaryRow.AreaUm2)) { StringFormat = "N0" },
            Width = 130,
        });
        return CreateCard(_localization["RoiTable"], grid);
    }

    private void ApplyRegionWarnings(JsonElement result)
    {
        if (!result.TryGetProperty("warnings", out var warnings)
            || warnings.ValueKind != JsonValueKind.Array) return;
        var warning = warnings.EnumerateArray()
            .FirstOrDefault(item => item.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(item.GetString()));
        if (warning.ValueKind == JsonValueKind.String)
            SetStatus(warning.GetString() ?? string.Empty);
    }

    private static Brush RegionBrush(string? value)
    {
        try
        {
            if (!string.IsNullOrWhiteSpace(value)
                && ColorConverter.ConvertFromString(value) is Color color)
            {
                var brush = new SolidColorBrush(color);
                brush.Freeze();
                return brush;
            }
        }
        catch (FormatException)
        {
            // Fall through to the app's Region default.
        }
        var fallback = new SolidColorBrush(Color.FromRgb(161, 217, 155));
        fallback.Freeze();
        return fallback;
    }

    private static bool CommitRegionNumericEditors(IEnumerable<Func<bool>> committers)
    {
        var valid = true;
        foreach (var commit in committers)
        {
            if (!commit()) valid = false;
        }
        return valid;
    }

    private void ClearRegionParameterErrorIfResolved()
    {
        if (_regionInvalidNumericEditors.Count == 0
            && _statusResourceKey == "InvalidRegionParameters")
            SetLocalizedStatus("RegionParametersValid");
    }

    private static int NormalizeRegionContourDownsample(double value)
    {
        if (!double.IsFinite(value)) return RegionContourDownsampleOptions[0];
        return RegionContourDownsampleOptions
            .OrderBy(option => Math.Abs(option - value))
            .ThenBy(option => option)
            .First();
    }
}
