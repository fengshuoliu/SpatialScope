using System.Globalization;
using System.IO;
using System.Text.Json;

namespace SpatialScope.Windows.Services;

public enum InterfaceLanguage
{
    System,
    English,
    SimplifiedChinese,
}

public sealed class LocalizationService
{
    private static readonly IReadOnlyDictionary<string, string> Chinese = new Dictionary<string, string>
    {
        ["Tagline"] = "空间图像分析",
        ["WorkflowProgress"] = "工作流程进度",
        ["Language"] = "语言",
        ["FollowSystem"] = "跟随系统",
        ["English"] = "English",
        ["SimplifiedChinese"] = "简体中文",
        ["Dataset"] = "数据集",
        ["Channels"] = "个通道",
        ["Scale"] = "比例",
        ["NotSet"] = "未设置",
        ["Compute"] = "计算",
        ["AppCpu"] = "应用 CPU",
        ["Cpus"] = "个 CPU",
        ["Gpus"] = "个 GPU",
        ["GpuAuto"] = "GPU 自动",
        ["CpuAnalysis"] = "CPU 分析",
        ["Step"] = "步骤",
        ["Of"] = "/",
        ["Ready"] = "就绪",
        ["Running"] = "运行中",
        ["Complete"] = "完成",
        ["NeedsAttention"] = "需要处理",
        ["NotStarted"] = "尚未运行",
        ["Choose"] = "选择",
        ["ChooseFolder"] = "选择文件夹",
        ["ChooseColor"] = "选择颜色",
        ["PlotZoomHelp"] = "鼠标滚轮缩放；放大后拖动平移；双击或按 0 恢复完整视图。",
        ["OpenOutput"] = "打开输出文件夹",
        ["OpenSelected"] = "打开所选文件",
        ["Cancel"] = "取消",
        ["Remove"] = "删除",
        ["InputsTitle"] = "输入与校准",
        ["InputsSubtitle"] = "文件夹、通道与空间比例",
        ["OverlayTitle"] = "复合预览",
        ["OverlaySubtitle"] = "多重叠加与拆分通道",
        ["NucleiTitle"] = "细胞核分割",
        ["NucleiSubtitle"] = "检测并分离细胞核",
        ["CellTypesTitle"] = "细胞类型分配",
        ["CellTypesSubtitle"] = "根据标记规则对细胞分类",
        ["NeighborhoodTitle"] = "邻域分析",
        ["NeighborhoodSubtitle"] = "量化局部细胞邻域",
        ["RegionTitle"] = "区域分析",
        ["RegionSubtitle"] = "ROI 掩膜与边界",
        ["DistributionTitle"] = "细胞分布",
        ["DistributionSubtitle"] = "测量区域密度模式",
        ["DistanceTitle"] = "距离分析",
        ["DistanceSubtitle"] = "细胞和边界距离",
        ["OutputsTitle"] = "结果与导出",
        ["OutputsSubtitle"] = "查看生成的分析文件",
        ["DataLocations"] = "数据位置",
        ["InputFolder"] = "输入文件夹",
        ["OutputFolder"] = "输出文件夹",
        ["SpatialCalibration"] = "空间校准",
        ["PixelWidth"] = "像素宽度",
        ["PixelHeight"] = "像素高度",
        ["MicrometersWide"] = "宽度（微米）",
        ["MicrometersHigh"] = "高度（微米）",
        ["RescanCsv"] = "重新扫描 CSV 文件",
        ["ResetMarkerNames"] = "重置标记名称",
        ["ReassignColors"] = "重新分配颜色",
        ["ChannelRegistry"] = "通道注册表",
        ["Overlay"] = "叠加",
        ["CsvFile"] = "CSV 文件",
        ["Marker"] = "标记",
        ["Color"] = "颜色",
        ["SaveConfiguration"] = "保存配置",
        ["CompositePreview"] = "复合预览",
        ["GenerateOverlay"] = "生成叠加图和拆分通道",
        ["OverlayPreview"] = "叠加图预览",
        ["SplitChannelsPreview"] = "拆分通道预览",
        ["OpenOriginal"] = "打开原始文件",
        ["NoPreview"] = "尚无预览",
        ["NoPreviewDetail"] = "请先保存输入配置并生成预览。",
        ["RunAnalysisForPreview"] = "运行此分析后，将在这里显示预览。",
        ["ManualParameters"] = "手动参数",
        ["RunMode"] = "运行模式",
        ["ManualMode"] = "手动",
        ["AdvancedScreening"] = "高级筛选",
        ["FinalRunParameters"] = "最终运行参数",
        ["NucleiManualModeHelp"] = "手动模式会严格使用下方显示的参数控件。",
        ["NucleiAdvancedModeHelp"] = "高级筛选会在代表性 ROI 上搜索参数。运行最终分割前，请确认并应用建议组合；筛选本身不会替代最终分割。",
        ["FinalSegmentation"] = "最终分割",
        ["NucleusChannel"] = "细胞核通道",
        ["RunNuclei"] = "运行最终细胞核分割",
        ["RunOptimizer"] = "运行参数优化器",
        ["OptimizerBudget"] = "最多评估组合数",
        ["NucleiOptimizerHelp"] = "在代表性的五个 ROI 上筛选参数组合。扫描完成后，请点击按钮确认并将建议组合应用于最终运行；筛选本身不会替代最终分割。",
        ["ApplyRecommended"] = "应用推荐参数",
        ["Parameter"] = "参数",
        ["Value"] = "数值",
        ["Effect"] = "作用与增减影响",
        ["CellTypeDefinitions"] = "细胞类型定义",
        ["AddCellType"] = "添加细胞类型",
        ["CellTypeName"] = "细胞类型名称",
        ["AllPositive"] = "全部阳性标记",
        ["AllNegative"] = "全部阴性标记",
        ["AnyPositiveGroups"] = "任一阳性标记",
        ["SelectMarkers"] = "选择标记",
        ["ClearSelection"] = "清除",
        ["MarkerPickerHelp"] = "打开标记列表，然后选择一个或多个标记。",
        ["MarkerSelectionCountFormat"] = "已选择 {0} 个",
        ["AssignmentParameters"] = "分配参数与最终细胞类型分配",
        ["AssignmentMode"] = "分配模式",
        ["AssignmentManualModeHelp"] = "手动模式会使用下方参数直接运行最终细胞类型分配。",
        ["AssignmentAdvancedModeHelp"] = "高级筛选会在代表性 ROI 上搜索参数。扫描完成后，请先确认并应用建议组合，再运行最终分配。",
        ["ApplySuggestedCombo"] = "将建议的参数组合应用于最终运行",
        ["SuggestedComboReady"] = "参数扫描已生成建议组合。确认后再将其应用于最终运行参数。",
        ["SuggestedComboApplied"] = "建议的参数组合已应用于最终运行。",
        ["SuggestedComboExpired"] = "输入已更改。请重新运行参数扫描以生成新的建议组合。",
        ["RunAssignment"] = "运行细胞类型分配",
        ["RunAssignmentOptimizer"] = "运行分配参数优化器",
        ["AssignmentOptimizerHelp"] = "在代表性的五个 ROI 上筛选分配参数。扫描完成后，请点击按钮确认并将建议组合应用于最终运行；随后仍需运行最终细胞类型分配。",
        ["GridSize"] = "网格大小",
        ["RunNeighborhood"] = "运行邻域分析",
        ["SelectedCellTypes"] = "选定细胞类型",
        ["ClosingRadius"] = "闭运算半径",
        ["DilationRadius"] = "膨胀半径",
        ["MinimumArea"] = "最小面积",
        ["MinimumCells"] = "最少细胞数",
        ["RunRegion"] = "运行区域分析",
        ["Boundary"] = "边界",
        ["BandWidth"] = "距离带宽",
        ["RunDistribution"] = "运行细胞分布分析",
        ["TargetCellType"] = "目标细胞类型",
        ["QueryCellTypes"] = "查询细胞类型",
        ["RunNearestDistance"] = "运行最近邻距离分析",
        ["RunBoundaryDistance"] = "运行细胞到边界距离分析",
        ["GeneratedFiles"] = "生成的文件",
        ["RefreshOutputs"] = "刷新结果",
        ["Open"] = "打开",
        ["Name"] = "名称",
        ["Type"] = "类型",
        ["Size"] = "大小",
        ["Modified"] = "修改时间",
        ["EngineStarting"] = "正在启动分析引擎…",
        ["EngineReady"] = "分析引擎已就绪。",
        ["CheckingExistingResults"] = "正在检查已有 SpatialScope 结果…",
        ["ExistingResultsRestored"] = "已自动载入此文件夹中的已有 SpatialScope 结果。",
        ["RestoredDownstreamSettingsMissing"] = "已载入结果文件，但旧会话未保存第 5–8 步的设置；请从邻域分析重新运行，以确保结果与当前设置一致。",
        ["NoExistingResults"] = "所选文件夹中没有已有的 SpatialScope 结果。",
        ["RestoreFailed"] = "无法载入已有结果",
        ["ConfigurationSaved"] = "配置已保存。",
        ["AnalysisComplete"] = "分析已完成。",
        ["SelectFoldersFirst"] = "请先选择输入和输出文件夹。",
        ["CompletePreviousSteps"] = "请先完成前面的步骤。",
        ["Status"] = "状态",
        ["Progress"] = "进度",
        ["WorkerLimit"] = "CPU 工作线程上限",
        ["Percent"] = "百分比",
        ["MarkerRules"] = "标记规则",
        ["ScreeningAndAssignment"] = "筛选与分配",
        ["GeneratePreview"] = "生成预览",
        ["Preview"] = "预览",
        ["AnalysisSettings"] = "分析设置",
        ["RunAndReview"] = "运行与查看",
        ["AmbiguousResolution"] = "模糊细胞解析",
        ["SegmentationPreview"] = "分割预览",
        ["PrerequisiteCellTypes"] = "请先完成细胞类型分配，才能进行此分析。",
        ["PrerequisiteRegion"] = "请先完成区域分析，才能使用区域边界。",
        ["SelectCellTypeHelp"] = "请选择一个或多个要纳入此分析的细胞类型。",
        ["NoGeneratedFiles"] = "尚无生成的文件",
        ["SelectGeneratedFile"] = "请选择一个要打开的已生成文件。",
        ["Channel"] = "通道",
        ["SelectRowToRemove"] = "请选择要删除的细胞类型行。",
        ["AssignmentSettings"] = "分配设置",
        ["DistributionCellTypes"] = "纳入分析的细胞类型",
        ["MissingCellTypes"] = "尚无可用的细胞类型。",
        ["MissingBoundaries"] = "尚无可用的区域边界。",
        ["ResultsEmptyHint"] = "完成分析后刷新，即可在此查看生成的文件。",
        ["NearestDistanceSettings"] = "最近邻距离设置",
        ["BoundaryDistanceSettings"] = "细胞到边界距离设置",
        ["ResultsStayEnglish"] = "分析文件、表格列名和导出内容保持英文。",
    };

    private static readonly IReadOnlyDictionary<string, string> English = new Dictionary<string, string>
    {
        ["Tagline"] = "Spatial image analysis",
        ["WorkflowProgress"] = "Workflow progress",
        ["Language"] = "Language",
        ["FollowSystem"] = "Follow System",
        ["English"] = "English",
        ["SimplifiedChinese"] = "简体中文",
        ["Dataset"] = "Dataset",
        ["Channels"] = "channels",
        ["Scale"] = "Scale",
        ["NotSet"] = "Not set",
        ["Compute"] = "Compute",
        ["AppCpu"] = "app CPU",
        ["Cpus"] = "CPUs",
        ["Gpus"] = "GPUs",
        ["GpuAuto"] = "GPU auto",
        ["CpuAnalysis"] = "CPU analysis",
        ["Step"] = "STEP",
        ["Of"] = "OF",
        ["Ready"] = "Ready",
        ["Running"] = "Running",
        ["Complete"] = "Complete",
        ["NeedsAttention"] = "Needs attention",
        ["NotStarted"] = "Not started",
        ["Choose"] = "Choose",
        ["ChooseFolder"] = "Choose folder",
        ["ChooseColor"] = "Choose color",
        ["PlotZoomHelp"] = "Use the mouse wheel to zoom; drag to pan when zoomed; double-click or press 0 to fit the image.",
        ["OpenOutput"] = "Open output folder",
        ["OpenSelected"] = "Open selected file",
        ["Cancel"] = "Cancel",
        ["Remove"] = "Remove",
        ["InputsTitle"] = "Inputs & Calibration",
        ["InputsSubtitle"] = "Folders, channels, and spatial scale",
        ["OverlayTitle"] = "Composite Preview",
        ["OverlaySubtitle"] = "Multiplex and split channels",
        ["NucleiTitle"] = "Nuclei Segmentation",
        ["NucleiSubtitle"] = "Detect and separate nuclei",
        ["CellTypesTitle"] = "Cell Type Assignment",
        ["CellTypesSubtitle"] = "Classify cells from marker rules",
        ["NeighborhoodTitle"] = "Neighborhood Analysis",
        ["NeighborhoodSubtitle"] = "Quantify local cell neighborhoods",
        ["RegionTitle"] = "Region Analysis",
        ["RegionSubtitle"] = "ROI masks and boundaries",
        ["DistributionTitle"] = "Cell Distribution",
        ["DistributionSubtitle"] = "Measure regional density patterns",
        ["DistanceTitle"] = "Distance Analysis",
        ["DistanceSubtitle"] = "Cell and boundary distances",
        ["OutputsTitle"] = "Results & Exports",
        ["OutputsSubtitle"] = "Review generated analysis files",
        ["DataLocations"] = "Data locations",
        ["InputFolder"] = "Input folder",
        ["OutputFolder"] = "Output folder",
        ["SpatialCalibration"] = "Spatial calibration",
        ["PixelWidth"] = "Pixel width",
        ["PixelHeight"] = "Pixel height",
        ["MicrometersWide"] = "Width in micrometers",
        ["MicrometersHigh"] = "Height in micrometers",
        ["RescanCsv"] = "Rescan CSV Files",
        ["ResetMarkerNames"] = "Reset Marker Names",
        ["ReassignColors"] = "Reassign Colors",
        ["ChannelRegistry"] = "Channel registry",
        ["Overlay"] = "Overlay",
        ["CsvFile"] = "CSV file",
        ["Marker"] = "Marker",
        ["Color"] = "Color",
        ["SaveConfiguration"] = "Save configuration",
        ["CompositePreview"] = "Composite preview",
        ["GenerateOverlay"] = "Generate overlay and split channels",
        ["OverlayPreview"] = "Overlay preview",
        ["SplitChannelsPreview"] = "Split channels preview",
        ["OpenOriginal"] = "Open original",
        ["NoPreview"] = "No preview yet",
        ["NoPreviewDetail"] = "Save the input configuration, then generate a preview.",
        ["RunAnalysisForPreview"] = "Run this analysis to generate a preview here.",
        ["ManualParameters"] = "Manual parameters",
        ["RunMode"] = "Run mode",
        ["ManualMode"] = "Manual",
        ["AdvancedScreening"] = "Advanced screening",
        ["FinalRunParameters"] = "Parameters for final run",
        ["NucleiManualModeHelp"] = "Manual mode uses the parameter controls exactly as shown below.",
        ["NucleiAdvancedModeHelp"] = "Advanced screening searches parameters on representative ROIs. Confirm and apply its suggested combination before running final segmentation; screening does not replace the final run.",
        ["FinalSegmentation"] = "Final segmentation",
        ["NucleusChannel"] = "Nucleus channel",
        ["RunNuclei"] = "Run final nuclei segmentation",
        ["RunOptimizer"] = "Run parameter optimizer",
        ["OptimizerBudget"] = "Maximum combinations to evaluate",
        ["NucleiOptimizerHelp"] = "Screen parameter combinations on five representative ROIs. When scanning finishes, confirm the suggestion with the Apply button; screening does not replace final segmentation.",
        ["ApplyRecommended"] = "Apply recommended parameters",
        ["Parameter"] = "Parameter",
        ["Value"] = "Value",
        ["Effect"] = "Purpose and effect of smaller/larger values",
        ["CellTypeDefinitions"] = "Cell type definitions",
        ["AddCellType"] = "Add cell type",
        ["CellTypeName"] = "Cell type name",
        ["AllPositive"] = "All-positive markers",
        ["AllNegative"] = "All-negative markers",
        ["AnyPositiveGroups"] = "Any-positive markers",
        ["SelectMarkers"] = "Select markers",
        ["ClearSelection"] = "Clear",
        ["MarkerPickerHelp"] = "Open the marker list and select one or more markers.",
        ["MarkerSelectionCountFormat"] = "{0} selected",
        ["AssignmentParameters"] = "Assignment parameters and final cell type assignment",
        ["AssignmentMode"] = "Assignment mode",
        ["AssignmentManualModeHelp"] = "Manual mode runs final cell type assignment directly with the parameters below.",
        ["AssignmentAdvancedModeHelp"] = "Advanced screening searches parameters on representative ROIs. Confirm and apply its suggested combination before running final assignment.",
        ["ApplySuggestedCombo"] = "Apply the suggested combo to final run",
        ["SuggestedComboReady"] = "Parameter scanning produced a suggested combination. Review it, then apply it to the final-run parameters.",
        ["SuggestedComboApplied"] = "The suggested parameter combination was applied to the final run.",
        ["SuggestedComboExpired"] = "Inputs changed. Run parameter screening again to generate a current suggestion.",
        ["RunAssignment"] = "Run cell type assignment",
        ["RunAssignmentOptimizer"] = "Run assignment parameter optimizer",
        ["AssignmentOptimizerHelp"] = "Screen assignment parameters on five representative ROIs. When scanning finishes, confirm the suggestion with the Apply button; final cell type assignment still runs afterward.",
        ["GridSize"] = "Grid size",
        ["RunNeighborhood"] = "Run neighborhood analysis",
        ["SelectedCellTypes"] = "Selected cell types",
        ["ClosingRadius"] = "Closing radius",
        ["DilationRadius"] = "Dilation radius",
        ["MinimumArea"] = "Minimum area",
        ["MinimumCells"] = "Minimum cells",
        ["RunRegion"] = "Run region analysis",
        ["Boundary"] = "Boundary",
        ["BandWidth"] = "Distance band width",
        ["RunDistribution"] = "Run cell distribution analysis",
        ["TargetCellType"] = "Target cell type",
        ["QueryCellTypes"] = "Query cell types",
        ["RunNearestDistance"] = "Run nearest-neighbor distance analysis",
        ["RunBoundaryDistance"] = "Run cell-to-boundary distance analysis",
        ["GeneratedFiles"] = "Generated files",
        ["RefreshOutputs"] = "Refresh outputs",
        ["Open"] = "Open",
        ["Name"] = "Name",
        ["Type"] = "Type",
        ["Size"] = "Size",
        ["Modified"] = "Modified",
        ["EngineStarting"] = "Starting the analysis engine…",
        ["EngineReady"] = "Analysis engine is ready.",
        ["CheckingExistingResults"] = "Checking for existing SpatialScope results…",
        ["ExistingResultsRestored"] = "Existing SpatialScope results were loaded automatically from this folder.",
        ["RestoredDownstreamSettingsMissing"] = "Result files were loaded, but this session did not save the settings for Steps 5–8. Rerun from Neighborhood Analysis so results match the visible settings.",
        ["NoExistingResults"] = "No existing SpatialScope results were found in the selected folder.",
        ["RestoreFailed"] = "Could not load existing results",
        ["ConfigurationSaved"] = "Configuration saved.",
        ["AnalysisComplete"] = "Analysis complete.",
        ["SelectFoldersFirst"] = "Choose the input and output folders first.",
        ["CompletePreviousSteps"] = "Complete the previous steps first.",
        ["Status"] = "Status",
        ["Progress"] = "Progress",
        ["WorkerLimit"] = "CPU worker limit",
        ["Percent"] = "Percent",
        ["MarkerRules"] = "Marker rules",
        ["ScreeningAndAssignment"] = "Screening & assignment",
        ["GeneratePreview"] = "Generate preview",
        ["Preview"] = "Preview",
        ["AnalysisSettings"] = "Analysis settings",
        ["RunAndReview"] = "Run and review",
        ["AmbiguousResolution"] = "Ambiguous-cell resolution",
        ["SegmentationPreview"] = "Segmentation preview",
        ["PrerequisiteCellTypes"] = "Complete cell type assignment before running this analysis.",
        ["PrerequisiteRegion"] = "Complete region analysis before using region boundaries.",
        ["SelectCellTypeHelp"] = "Select one or more cell types to include in this analysis.",
        ["NoGeneratedFiles"] = "No generated files yet",
        ["SelectGeneratedFile"] = "Select a generated file to open.",
        ["Channel"] = "Channel",
        ["SelectRowToRemove"] = "Select a cell type row to remove.",
        ["AssignmentSettings"] = "Assignment settings",
        ["DistributionCellTypes"] = "Included cell types",
        ["MissingCellTypes"] = "No cell types are available yet.",
        ["MissingBoundaries"] = "No region boundaries are available yet.",
        ["ResultsEmptyHint"] = "Complete analyses, then refresh to list generated files here.",
        ["NearestDistanceSettings"] = "Nearest-neighbor settings",
        ["BoundaryDistanceSettings"] = "Cell-to-boundary settings",
        ["ResultsStayEnglish"] = "Analysis files, table column names, and exported content remain in English.",
    };

    private readonly string _settingsPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "SpatialScope",
        "settings.json");

    public InterfaceLanguage Language { get; private set; } = InterfaceLanguage.System;
    public event EventHandler? LanguageChanged;

    public LocalizationService() => LoadPreference();

    public string this[string key]
    {
        get
        {
            var source = EffectiveLanguage == InterfaceLanguage.SimplifiedChinese ? Chinese : English;
            return source.TryGetValue(key, out var value) ? value : key;
        }
    }

    public InterfaceLanguage EffectiveLanguage
    {
        get
        {
            if (Language != InterfaceLanguage.System) return Language;
            return CultureInfo.CurrentUICulture.Name.StartsWith("zh", StringComparison.OrdinalIgnoreCase)
                ? InterfaceLanguage.SimplifiedChinese
                : InterfaceLanguage.English;
        }
    }

    public void SetLanguage(InterfaceLanguage language)
    {
        if (Language == language) return;
        Language = language;
        try
        {
            SavePreference();
        }
        catch (IOException)
        {
            // The preference is optional; keep the in-memory language change.
        }
        catch (UnauthorizedAccessException)
        {
            // A locked or read-only settings folder must not crash the app.
        }
        LanguageChanged?.Invoke(this, EventArgs.Empty);
    }

    private void LoadPreference()
    {
        try
        {
            if (!File.Exists(_settingsPath)) return;
            using var document = JsonDocument.Parse(File.ReadAllText(_settingsPath));
            var value = document.RootElement.GetProperty("uiLanguage").GetString();
            if (Enum.TryParse(value, true, out InterfaceLanguage parsed)) Language = parsed;
        }
        catch
        {
            Language = InterfaceLanguage.System;
        }
    }

    private void SavePreference()
    {
        var directory = Path.GetDirectoryName(_settingsPath)!;
        Directory.CreateDirectory(directory);
        File.WriteAllText(_settingsPath, JsonSerializer.Serialize(new { uiLanguage = Language.ToString() }));
    }
}
