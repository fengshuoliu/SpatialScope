import Foundation

enum AnalysisSection: String, CaseIterable, Identifiable {
    case inputs
    case overlay
    case nuclei
    case cellTypes
    case neighborhood
    case region
    case cellDistribution
    case distance
    case outputs

    var id: String { rawValue }

    var title: String {
        switch self {
        case .inputs: "Inputs & Calibration"
        case .overlay: "Composite Preview"
        case .nuclei: "Nuclei Segmentation"
        case .cellTypes: "Cell Type Assignment"
        case .neighborhood: "Neighborhood Analysis"
        case .region: "Region Analysis"
        case .cellDistribution: "Cell Distribution"
        case .distance: "Distance Analysis"
        case .outputs: "Results & Exports"
        }
    }

    var subtitle: String {
        switch self {
        case .inputs: "Folders, channels, and spatial scale"
        case .overlay: "Multiplex and split channels"
        case .nuclei: "Detect and separate nuclei"
        case .cellTypes: "Classify cells from marker rules"
        case .neighborhood: "Quantify local cell neighborhoods"
        case .region: "ROI masks and boundaries"
        case .cellDistribution: "Measure regional density patterns"
        case .distance: "Cell and boundary distances"
        case .outputs: "Review generated analysis files"
        }
    }

    var systemImage: String {
        switch self {
        case .inputs: "folder"
        case .overlay: "square.stack.3d.up"
        case .nuclei: "circle.grid.cross"
        case .cellTypes: "tag"
        case .neighborhood: "square.grid.3x3"
        case .region: "lasso"
        case .cellDistribution: "chart.xyaxis.line"
        case .distance: "ruler"
        case .outputs: "tray.full"
        }
    }

    var outputSectionKey: String {
        switch self {
        case .inputs: "config"
        case .overlay: "overlay"
        case .nuclei: "nuclei"
        case .cellTypes: "celltype_definition"
        case .neighborhood: "neighborhood_analysis"
        case .region: "region_analysis"
        case .cellDistribution: "cell_distribution_analysis"
        case .distance: "distance_analysis"
        case .outputs: "outputs"
        }
    }

    var stepNumber: Int {
        (Self.allCases.firstIndex(of: self) ?? 0) + 1
    }
}
