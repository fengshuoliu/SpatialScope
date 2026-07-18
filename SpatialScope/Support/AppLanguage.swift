import Foundation

enum AppLanguage: String, CaseIterable, Identifiable {
    case system = "system"
    case english = "en"
    case simplifiedChinese = "zh-Hans"

    static let preferenceKey = "SpatialScope.uiLanguage"

    var id: String { rawValue }

    var locale: Locale {
        Locale(identifier: resolvedLanguage.rawValue)
    }

    func displayName(in interfaceLanguage: AppLanguage) -> String {
        switch self {
        case .system: interfaceLanguage.localized("Follow System")
        case .english: "English"
        case .simplifiedChinese: "简体中文"
        }
    }

    var resolvedLanguage: AppLanguage {
        guard self == .system else { return self }
        let identifier = Locale.preferredLanguages.first?
            .replacingOccurrences(of: "_", with: "-")
            .lowercased() ?? "en"
        let usesSimplifiedChinese = identifier.hasPrefix("zh-hans")
            || identifier.hasPrefix("zh-cn")
            || identifier.hasPrefix("zh-sg")
        return usesSimplifiedChinese ? .simplifiedChinese : .english
    }

    static func initialValue(defaults: UserDefaults = .standard) -> AppLanguage {
        if let argument = CommandLine.arguments.first(where: { $0.hasPrefix("--ui-language=") }) {
            let identifier = String(argument.dropFirst("--ui-language=".count))
            if let language = AppLanguage(rawValue: identifier) {
                return language
            }
        }
        return defaults.string(forKey: preferenceKey)
            .flatMap(AppLanguage.init(rawValue:))
            ?? .system
    }

    func localized(_ key: String) -> String {
        let language = resolvedLanguage
        guard language != .english,
              let path = Bundle.main.path(forResource: language.rawValue, ofType: "lproj"),
              let bundle = Bundle(path: path) else {
            return key
        }
        return bundle.localizedString(forKey: key, value: key, table: nil)
    }

    func localizedStatusMessage(_ message: String) -> String {
        guard resolvedLanguage == .simplifiedChinese else { return message }
        let source = message.isEmpty ? "Ready" : message
        if let translated = Self.statusTranslations[source] {
            return translated
        }

        let replacements: [(String, String)] = [
            ("Detected ", "检测到 "),
            (" CSV channel(s).", " 个 CSV 通道。"),
            ("Applied combo ", "已应用组合 "),
            ("Applied assignment combo ", "已应用分配组合 "),
            ("Running advanced nuclei parameter scan for ", "正在运行高级细胞核参数筛选，共 "),
            (" combinations...", " 个组合……"),
            ("Final nuclei segmentation complete: ", "最终细胞核分割完成："),
            (" nuclei.", " 个细胞核。"),
            ("Cell-type assignment complete: ", "细胞类型分配完成："),
            (" assigned of ", " 个已分配，共 "),
            ("Neighborhood analysis complete: ", "邻域分析完成："),
            (" occupied grid squares, ", " 个已占用网格，"),
            (" cells.", " 个细胞。"),
            ("Region analysis complete: ", "区域分析完成："),
            (" ROIs, ", " 个 ROI，"),
            ("Cell distribution complete: ", "细胞分布分析完成："),
            (" regions, ", " 个区域，"),
            ("Nearest-neighbor distance analysis complete: ", "最近邻距离分析完成："),
            ("Cell-to-boundary distance analysis complete: ", "细胞到边界距离分析完成："),
            (" rows.", " 行。")
        ]

        var localized = source
        for (english, chinese) in replacements where localized.contains(english) {
            localized = localized.replacingOccurrences(of: english, with: chinese)
        }
        if localized != source {
            return localized
        }

        let lowercased = source.lowercased()
        if lowercased.contains("failed") || lowercased.contains("error") || lowercased.contains("not found") {
            return "错误：\(source)"
        }
        return source
    }

    private static let statusTranslations: [String: String] = [
        "Ready": "就绪",
        "Configuration saved.": "配置已保存。",
        "Cell-type configuration saved.": "细胞类型配置已保存。",
        "Cancelling current operation...": "正在取消当前操作……",
        "Operation cancelled.": "操作已取消。",
        "Saving configuration...": "正在保存配置……",
        "Refreshing outputs...": "正在刷新输出……",
        "Loading CSV matrices and generating overlay...": "正在加载 CSV 矩阵并生成叠加图……",
        "Overlay and split-channel previews saved.": "叠加图和拆分通道预览已保存。",
        "Choose a nucleus channel first.": "请先选择细胞核通道。",
        "Run final nuclei segmentation before assignment screening.": "请先运行最终细胞核分割，再进行分配筛选。",
        "Run final nuclei segmentation before cell-type assignment.": "请先运行最终细胞核分割，再进行细胞类型分配。",
        "Run cell-type assignment before neighborhood analysis.": "请先运行细胞类型分配，再进行邻域分析。",
        "Neighborhood colors shuffled and saved.": "邻域颜色已重新分配并保存。",
        "Run cell-type assignment before region analysis.": "请先运行细胞类型分配，再进行区域分析。",
        "Cell-type assignment did not produce any assigned cell types for region analysis.": "细胞类型分配未生成可用于区域分析的已分配类型。",
        "Run cell-type assignment before saving a customized region display.": "请先运行细胞类型分配，再保存自定义区域显示。",
        "Run region analysis before saving a customized region display.": "请先运行区域分析，再保存自定义区域显示。",
        "Run cell-type assignment before saving an adjusted ROI.": "请先运行细胞类型分配，再保存调整后的 ROI。",
        "Run region analysis before saving an adjusted ROI.": "请先运行区域分析，再保存调整后的 ROI。",
        "Run cell-type assignment before cell distribution analysis.": "请先运行细胞类型分配，再进行细胞分布分析。",
        "Run region analysis before cell distribution analysis.": "请先运行区域分析，再进行细胞分布分析。",
        "Run neighborhood analysis before cell cluster distribution.": "请先运行邻域分析，再进行细胞簇分布分析。",
        "Set figure resolution in Inputs before running Cell Distribution analysis.": "请先在输入部分设置图像分辨率，再运行细胞分布分析。",
        "Run cell-type assignment before distance analysis.": "请先运行细胞类型分配，再进行距离分析。",
        "Run region analysis before distance analysis.": "请先运行区域分析，再进行距离分析。",
        "Run cell-type assignment before nearest-neighbor distance analysis.": "请先运行细胞类型分配，再进行最近邻距离分析。",
        "Select a target cell type and at least one query cell type.": "请选择目标细胞类型和至少一种查询细胞类型。",
        "Run cell-type assignment before cell-to-boundary distance analysis.": "请先运行细胞类型分配，再进行细胞到边界距离分析。",
        "Select at least one query cell type.": "请至少选择一种查询细胞类型。",
        "Selected boundary mask file was not found.": "未找到所选边界掩膜文件。",
        "Imported previous output folder.": "已导入先前的输出文件夹。",
        "Running final nuclei segmentation...": "正在运行最终细胞核分割……",
        "Running native cell-type assignment...": "正在运行原生细胞类型分配……",
        "Running native neighborhood analysis...": "正在运行原生邻域分析……",
        "Running native region analysis...": "正在运行原生区域分析……",
        "Saving customized region display...": "正在保存自定义区域显示……",
        "Saving adjusted ROI...": "正在保存调整后的 ROI……",
        "Running cell distribution analysis...": "正在运行细胞分布分析……",
        "Running native distance analysis...": "正在运行原生距离分析……",
        "Computing nearest-neighbor distances...": "正在计算最近邻距离……",
        "Computing cell-to-boundary distances...": "正在计算细胞到边界距离……"
    ]
}
