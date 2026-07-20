using SpatialScope.Windows.Models;
using SpatialScope.Windows.Services;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;

namespace SpatialScope.Windows;

public partial class MainWindow
{
    private UIElement BuildDistributionView()
    {
        var boundaryOptions = _boundaries.Select(item => item.Label).ToArray();
        if (boundaryOptions.Length > 0
            && (_distributionBoundaryLabel is null
                || !boundaryOptions.Contains(_distributionBoundaryLabel, StringComparer.Ordinal)))
        {
            _distributionBoundaryLabel = boundaryOptions[0];
        }

        if (_resolvedCellTypes.Count > 0)
        {
            _distributionSelectedCellTypes.RemoveAll(
                item => !_resolvedCellTypes.Contains(item, StringComparer.Ordinal));
        }
        if (!_distributionSelectionInitialized && _resolvedCellTypes.Count > 0)
        {
            if (_distributionSelectedCellTypes.Count == 0)
                _distributionSelectedCellTypes.AddRange(_resolvedCellTypes);
            _distributionSelectionInitialized = true;
        }

        var workflowReady = _sections.First(section => section.Key == "distribution").Status
            != WorkflowStatus.NotStarted;
        var hasBoundaries = boundaryOptions.Length > 0;
        var hasCellTypes = _resolvedCellTypes.Count > 0;
        Button? runButton = null;

        bool CanRun() => workflowReady
                         && hasBoundaries
                         && hasCellTypes
                         && _distributionSelectedCellTypes.Count > 0
                         && !string.IsNullOrWhiteSpace(_distributionBoundaryLabel);

        string RunBlocker() => !workflowReady
            ? _localization["CompletePreviousSteps"]
            : !hasBoundaries
                ? _localization["PrerequisiteRegion"]
                : _localization["SelectCellTypeHelp"];

        void RefreshRunAvailability()
        {
            if (runButton is not null) SetActionAvailability(runButton, CanRun(), RunBlocker());
        }

        var introduction = new StackPanel();
        introduction.Children.Add(CreateSupportingText(DistributionText(
            "One run creates two linked results: boundary bands first, then a density profile calculated from those exact bands.",
            "一次运行会生成两个相互关联的结果：先生成边界距离带，再使用完全相同的距离带计算密度曲线。")));
        if (!workflowReady)
            introduction.Children.Add(CreateInlineNotice(_localization["CompletePreviousSteps"], warning: true));
        else
        {
            if (!hasBoundaries)
                introduction.Children.Add(CreateInlineNotice(_localization["PrerequisiteRegion"], warning: true));
            if (!hasCellTypes)
                introduction.Children.Add(CreateInlineNotice(_localization["PrerequisiteCellTypes"], warning: true));
        }

        var boundaryStack = new StackPanel();
        boundaryStack.Children.Add(CreateSupportingText(
            DistributionText(
                "Choose one saved ROI boundary. SpatialScope creates equal-width distance bands on both sides and preserves the band map for review.",
                "选择一个已保存的 ROI 边界。SpatialScope 会在边界两侧生成等宽距离带，并保存分带图供检查。"),
            new Thickness(0, 0, 0, 15)));

        var boundaryControls = new Grid { Margin = new Thickness(0, 0, 0, 18) };
        boundaryControls.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        boundaryControls.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(18) });
        boundaryControls.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(0.55, GridUnitType.Star) });

        var boundaryField = new StackPanel();
        boundaryField.Children.Add(CreateFieldLabel(
            DistributionText("Boundary from ROI analysis", "ROI 分析边界"),
            new Thickness(0, 0, 0, 6)));
        var boundaryPicker = new ComboBox
        {
            ItemsSource = boundaryOptions,
            SelectedItem = _distributionBoundaryLabel,
            MinWidth = 300,
            HorizontalAlignment = HorizontalAlignment.Stretch,
        };
        AutomationProperties.SetName(boundaryPicker, DistributionText("Boundary from ROI analysis", "ROI 分析边界"));
        boundaryPicker.SelectionChanged += (_, _) =>
        {
            var selected = boundaryPicker.SelectedItem?.ToString();
            if (string.Equals(selected, _distributionBoundaryLabel, StringComparison.Ordinal)) return;
            _distributionBoundaryLabel = selected;
            InvalidateAfter("distribution");
            RefreshRunAvailability();
        };
        boundaryField.Children.Add(boundaryPicker);
        Grid.SetColumn(boundaryField, 0);
        boundaryControls.Children.Add(boundaryField);

        var bandWidthField = CreateNumberField(
            _localization["BandWidth"],
            _distributionBandWidth,
            value => _distributionBandWidth = value,
            "µm",
            "distribution");
        Grid.SetColumn(bandWidthField, 2);
        boundaryControls.Children.Add(bandWidthField);
        boundaryStack.Children.Add(boundaryControls);

        var bandMapPath = _previewPaths.GetValueOrDefault("distributionBandMap");
        if (string.IsNullOrWhiteSpace(bandMapPath))
            bandMapPath = _previewPaths.GetValueOrDefault("distribution");
        boundaryStack.Children.Add(CreateImagePanel(
            DistributionText("Boundary band map", "边界分带图"),
            bandMapPath,
            bandMapPath,
            DistributionText(
                "Run the analysis to generate the inside/outside boundary bands.",
                "运行分析后，将在此处显示边界内外的距离带。"),
            previewKey: "distributionBandMap"));

        var densityStack = new StackPanel();
        densityStack.Children.Add(CreateSupportingText(
            DistributionText(
                "Choose the cell types to compare. The line plot uses real cell counts and the measured area of each distance band to calculate density.",
                "选择要比较的细胞类型。折线图使用真实细胞计数和每个距离带的实测面积计算密度。"),
            new Thickness(0, 0, 0, 15)));

        if (hasCellTypes)
        {
            densityStack.Children.Add(CreateDistributionCellTypePicker(() =>
            {
                _distributionSelectionInitialized = true;
                InvalidateAfter("distribution");
                RefreshRunAvailability();
            }));
        }

        densityStack.Children.Add(CreateSupportingText(
            DistributionText(
                "Negative distances are inside the boundary; positive distances are outside.",
                "负距离位于边界内，正距离位于边界外。"),
            new Thickness(0, 10, 0, 14)));

        runButton = CreateButton(
            DistributionText(
                "Generate boundary bands and density profile",
                "生成边界距离带和密度曲线"),
            async (_, _) =>
            {
                var result = await RunWorkflowAsync("distribution", "cell_distribution", new
                {
                    boundaryLabel = _distributionBoundaryLabel,
                    bandWidthUm = _distributionBandWidth,
                    selectedCellTypes = _distributionSelectedCellTypes.ToArray(),
                });
                if (result is null) return;

                var bandPreview = JsonString(result.Value, "bandMapPreviewPath")
                                  ?? JsonString(result.Value, "previewPath");
                var densityPreview = JsonString(result.Value, "densityPlotPreviewPath");
                if (!string.IsNullOrWhiteSpace(bandPreview))
                {
                    _previewPaths["distribution"] = bandPreview;
                    _previewPaths["distributionBandMap"] = bandPreview;
                }
                if (!string.IsNullOrWhiteSpace(densityPreview))
                    _previewPaths["distributionDensity"] = densityPreview;

                CaptureExportPaths(result.Value);
                RefreshSectionViewIfSelected("distribution");
            },
            primary: true);
        runButton.Margin = new Thickness(0, 0, 0, 18);
        RefreshRunAvailability();
        densityStack.Children.Add(runButton);

        var hasCurrentBandMap = !string.IsNullOrWhiteSpace(bandMapPath) && File.Exists(bandMapPath);
        var densityPlotPath = hasCurrentBandMap
            ? _previewPaths.GetValueOrDefault("distributionDensity")
            : null;
        densityStack.Children.Add(CreateImagePanel(
            DistributionText("Cell density line plot", "细胞密度折线图"),
            densityPlotPath,
            densityPlotPath,
            DistributionText(
                "Select cell types and run the analysis to plot density by signed distance from the boundary.",
                "选择细胞类型并运行分析后，将在此处显示按有符号边界距离计算的密度曲线。"),
            previewKey: "distributionDensity"));

        return CreatePage(
            CreateCard(DistributionText("Cell Distribution workflow", "细胞分布流程"), introduction),
            CreateCard(DistributionText("1. Boundary-banded regions", "1. 边界分带区域"), boundaryStack),
            CreateCard(DistributionText("2. Cell density by boundary distance", "2. 按边界距离统计细胞密度"), densityStack));
    }

    private UIElement CreateDistributionCellTypePicker(Action onChanged)
    {
        var host = new StackPanel();
        var heading = new DockPanel { LastChildFill = true, Margin = new Thickness(0, 0, 0, 8) };
        var count = CreateSupportingText($"{_distributionSelectedCellTypes.Count} / {_resolvedCellTypes.Count}");
        count.Margin = new Thickness(10, 0, 0, 0);
        count.VerticalAlignment = VerticalAlignment.Center;
        DockPanel.SetDock(count, Dock.Right);
        heading.Children.Add(count);

        var selectAll = CreateButton(
            DistributionText("Select all cell types", "选择全部细胞类型"),
            (_, _) =>
            {
                var next = _resolvedCellTypes.Distinct(StringComparer.Ordinal).ToArray();
                if (_distributionSelectedCellTypes.ToHashSet(StringComparer.Ordinal).SetEquals(next)) return;
                _distributionSelectedCellTypes.Clear();
                _distributionSelectedCellTypes.AddRange(next);
                foreach (var checkBox in FindDistributionCellTypeCheckBoxes(host)) checkBox.IsChecked = true;
                count.Text = $"{_distributionSelectedCellTypes.Count} / {_resolvedCellTypes.Count}";
                onChanged();
            });
        selectAll.Padding = new Thickness(10, 4, 10, 4);
        selectAll.Margin = new Thickness(12, 0, 0, 0);
        DockPanel.SetDock(selectAll, Dock.Right);
        heading.Children.Add(selectAll);
        heading.Children.Add(CreateSubsectionTitle(
            DistributionText("Cell types in line plot", "折线图中的细胞类型")));
        host.Children.Add(heading);

        var choices = new WrapPanel();
        foreach (var cellType in _resolvedCellTypes)
        {
            var checkBox = new CheckBox
            {
                Content = cellType,
                Tag = "distribution-cell-type",
                DataContext = cellType,
                IsChecked = _distributionSelectedCellTypes.Contains(cellType, StringComparer.Ordinal),
                Margin = new Thickness(0, 0, 18, 10),
                MinWidth = 170,
                VerticalAlignment = VerticalAlignment.Center,
            };
            AutomationProperties.SetName(checkBox, cellType);
            checkBox.Click += (_, _) =>
            {
                var changed = false;
                if (checkBox.IsChecked == true)
                {
                    if (!_distributionSelectedCellTypes.Contains(cellType, StringComparer.Ordinal))
                    {
                        _distributionSelectedCellTypes.Add(cellType);
                        changed = true;
                    }
                }
                else if (_distributionSelectedCellTypes.Count <= 1)
                {
                    checkBox.IsChecked = true;
                    return;
                }
                else
                {
                    changed = _distributionSelectedCellTypes.RemoveAll(
                        value => string.Equals(value, cellType, StringComparison.Ordinal)) > 0;
                }
                if (!changed) return;
                count.Text = $"{_distributionSelectedCellTypes.Count} / {_resolvedCellTypes.Count}";
                onChanged();
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

    private static IEnumerable<CheckBox> FindDistributionCellTypeCheckBoxes(DependencyObject root)
    {
        for (var index = 0; index < VisualTreeHelper.GetChildrenCount(root); index++)
        {
            var child = VisualTreeHelper.GetChild(root, index);
            if (child is CheckBox { Tag: "distribution-cell-type" } checkBox) yield return checkBox;
            foreach (var nested in FindDistributionCellTypeCheckBoxes(child)) yield return nested;
        }
    }

    private static string? JsonString(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private string DistributionText(string english, string simplifiedChinese) =>
        _localization.EffectiveLanguage == InterfaceLanguage.SimplifiedChinese
            ? simplifiedChinese
            : english;
}
