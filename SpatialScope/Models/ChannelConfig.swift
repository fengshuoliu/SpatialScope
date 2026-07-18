import AppKit
import Foundation

struct ChannelConfig: Identifiable, Codable, Equatable {
    var id = UUID()
    var fileName: String
    var marker: String
    var colorHex: String
    var overlayEnabled: Bool = true

    var channelName: String {
        marker.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? URL(fileURLWithPath: fileName).deletingPathExtension().lastPathComponent
            : marker.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

struct CellTypeDefinition: Identifiable, Codable, Equatable {
    var id = UUID()
    var name: String
    var colorHex: String
    var allPositiveMarkers: String = ""
    var allNegativeMarkers: String = ""
    var anyPositiveGroups: String = ""
}

enum GeneratedMarkerNames {
    static let nuclearSegmentationSignal = "Nucleus"
    static let legacyNuclearSegmentationSignal = "Nuclear segmentation signal"
}

struct NucleiParameters: Codable, Equatable {
    var minDiamUm: Double = 6.0
    var maxDiamUm: Double = 60.0
    var tophatRadiusUm: Double = 2.0
    var gaussSigmaUm: Double = 0.5
    var localWinUm: Double = 25.0
    var localOffset: Double = -0.03
    var hMaximaUm: Double = 0.25
    var seedMinDistUm: Double = 0.1
    var watershedCompactness: Double = 0.5
    var postResplitMult: Double = 0.5
}

enum NucleiRunMode: String, CaseIterable, Identifiable, Codable {
    case manual
    case advanced

    var id: String { rawValue }

    var title: String {
        switch self {
        case .manual: "Manual"
        case .advanced: "Advanced Screening"
        }
    }
}

struct NucleiDetection: Identifiable, Codable, Equatable {
    var id: Int
    var centroidX: Double
    var centroidY: Double
    var areaPx: Int
    var meanIntensity: Double
}

struct NucleiLabelMap: Codable, Equatable {
    var width: Int
    var height: Int
    var labels: [Int]
}

struct NucleiSegmentationResult: Identifiable, Equatable {
    var id = UUID()
    var count: Int
    var params: NucleiParameters
    var channelName: String
    var image: NSImage
    var detections: [NucleiDetection]
    var labelMap: NucleiLabelMap? = nil
}

struct NucleiScanRecord: Identifiable, Codable, Equatable {
    var id: Int { comboIndex }
    var comboIndex: Int
    var stage: String
    var count: Int
    var params: NucleiParameters

    var summary: String {
        "Combo \(comboIndex): \(count) nuclei"
    }
}

struct CellTypeAssignment: Identifiable, Codable, Equatable {
    var id: Int { nucleusID }
    var nucleusID: Int
    var centroidX: Double
    var centroidY: Double
    var areaPx: Int
    var assignedType: String
    var colorHex: String
    var score: Double
    var probability: Double
    var matchedPositiveMarkers: [String]
    var blockedNegativeMarkers: [String]
    var markerMeans: [String: Double]
    var cellBoundaryPoints: [CellBoundaryPoint]? = nil
}

struct CellBoundaryPoint: Codable, Equatable {
    var x: Double
    var y: Double
}

struct CellTypeCount: Identifiable, Codable, Equatable {
    var id: String { name }
    var name: String
    var count: Int
    var colorHex: String
}

struct UInt16Raster: Codable, Equatable {
    var width: Int
    var height: Int
    var values: [UInt16]

    subscript(x: Int, y: Int) -> UInt16 {
        get {
            guard x >= 0, x < width, y >= 0, y < height else { return 0 }
            return values[y * width + x]
        }
        set {
            guard x >= 0, x < width, y >= 0, y < height else { return }
            values[y * width + x] = newValue
        }
    }

    func mask(for value: UInt16) -> RasterMask {
        RasterMask(
            width: width,
            height: height,
            pixels: values.map { $0 == value }
        )
    }

    mutating func fillDisk(centerX: Double, centerY: Double, radius: Double, value: UInt16) {
        let r = max(1.0, radius)
        let minX = max(0, Int(floor(centerX - r)))
        let maxX = min(width - 1, Int(ceil(centerX + r)))
        let minY = max(0, Int(floor(centerY - r)))
        let maxY = min(height - 1, Int(ceil(centerY + r)))
        guard minX <= maxX, minY <= maxY else { return }
        let r2 = r * r
        for y in minY...maxY {
            for x in minX...maxX {
                let dx = Double(x) + 0.5 - centerX
                let dy = Double(y) + 0.5 - centerY
                if dx * dx + dy * dy <= r2 {
                    self[x, y] = value
                }
            }
        }
    }

    mutating func fillPolygon(_ points: [CellBoundaryPoint], value: UInt16) {
        guard points.count >= 3 else { return }
        let clipped = points.map {
            CellBoundaryPoint(
                x: min(max($0.x, 0.0), Double(width - 1)),
                y: min(max($0.y, 0.0), Double(height - 1))
            )
        }
        let minY = max(0, Int(floor(clipped.map(\.y).min() ?? 0)))
        let maxY = min(height - 1, Int(ceil(clipped.map(\.y).max() ?? 0)))
        guard minY <= maxY else { return }

        for y in minY...maxY {
            let scanY = Double(y) + 0.5
            var intersections: [Double] = []
            for index in clipped.indices {
                let p1 = clipped[index]
                let p2 = clipped[(index + 1) % clipped.count]
                let crosses = (p1.y <= scanY && p2.y > scanY) || (p2.y <= scanY && p1.y > scanY)
                guard crosses else { continue }
                let t = (scanY - p1.y) / max(1e-9, p2.y - p1.y)
                intersections.append(p1.x + t * (p2.x - p1.x))
            }
            intersections.sort()
            var i = 0
            while i + 1 < intersections.count {
                let start = max(0, Int(ceil(intersections[i])))
                let end = min(width - 1, Int(floor(intersections[i + 1])))
                if start <= end {
                    for x in start...end {
                        self[x, y] = value
                    }
                }
                i += 2
            }
        }
    }
}

struct CellTypeAssignmentResult: Identifiable {
    var id = UUID()
    var assignments: [CellTypeAssignment]
    var counts: [CellTypeCount]
    var parameters: AssignmentParameters
    var image: NSImage
    var statsImage: NSImage
    var width: Int
    var height: Int
    var cellTypeMask: UInt16Raster? = nil
    var cellTypeIDByName: [String: UInt16] = [:]

    var totalAssigned: Int {
        assignments.filter { $0.assignedType != "Unassigned" && $0.assignedType != "Ambiguous" }.count
    }
}

enum AssignmentRunMode: String, CaseIterable, Identifiable, Codable {
    case manual
    case screening

    var id: String { rawValue }

    var title: String {
        switch self {
        case .manual: "Manual"
        case .screening: "Advanced Screening"
        }
    }
}

enum AssignmentScreeningSubsetMode: String, CaseIterable, Identifiable, Codable {
    case randomThree
    case oddBands
    case evenBands

    var id: String { rawValue }

    var title: String {
        switch self {
        case .randomThree: "Random 3 bands"
        case .oddBands: "Odd bands"
        case .evenBands: "Even bands"
        }
    }
}

struct AssignmentScanRecord: Identifiable, Codable, Equatable {
    var id: Int { comboIndex }
    var comboIndex: Int
    var stage: String
    var unassignedCount: Int
    var ambiguousCount: Int
    var assignedCount: Int
    var parameters: AssignmentParameters

    var unresolvedCount: Int {
        unassignedCount + ambiguousCount
    }
}

struct NeighborhoodTile: Identifiable, Codable, Equatable {
    var id: String { "\(row)-\(column)" }
    var row: Int
    var column: Int
    var xPx: Double
    var yPx: Double
    var widthPx: Double
    var heightPx: Double
    var dominantType: String
    var colorHex: String
    var totalCells: Int
    var assignedCells: Int
    var countsByType: [String: Int]
    var clusterID: Int? = nil
    var clusterKey: String? = nil
    var clusterLabel: String? = nil

    var effectiveClusterKey: String {
        let stored = (clusterKey ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !stored.isEmpty { return stored }
        return Self.makeClusterKey(from: countsByType)
    }

    var effectiveClusterLabel: String {
        let stored = (clusterLabel ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !stored.isEmpty { return stored }
        return Self.makeClusterLabel(from: countsByType)
    }

    static func makeClusterKey(from counts: [String: Int]) -> String {
        clusterTypes(from: counts).joined(separator: "|")
    }

    static func makeClusterLabel(from counts: [String: Int]) -> String {
        clusterTypes(from: counts).joined(separator: " + ")
    }

    private static func clusterTypes(from counts: [String: Int]) -> [String] {
        counts
            .filter { name, count in
                count > 0 && name != "Unassigned" && name != "Ambiguous"
            }
            .map(\.key)
            .sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }
}

struct NeighborhoodTypeCount: Identifiable, Codable, Equatable {
    var id: String { name }
    var name: String
    var count: Int
    var colorHex: String
}

struct NeighborhoodClusterCount: Identifiable, Codable, Equatable {
    var id: Int { clusterID }
    var clusterID: Int
    var clusterKey: String
    var clusterLabel: String
    var tileCount: Int
    var cellCount: Int
    var tileFraction: Double
    var colorHex: String
}

struct NeighborhoodAnalysisResult: Identifiable {
    var id = UUID()
    var tiles: [NeighborhoodTile]
    var dominantCounts: [NeighborhoodTypeCount]
    var clusterCounts: [NeighborhoodClusterCount]
    var gridSizeUm: Double
    var gridSizePx: Double
    var gridWidthPx: Double
    var gridHeightPx: Double
    var image: NSImage
    var clusterKeyImage: NSImage
    var statsImage: NSImage
    var width: Int
    var height: Int

    var occupiedTileCount: Int {
        tiles.count
    }

    var totalCells: Int {
        tiles.reduce(0) { $0 + $1.totalCells }
    }
}

struct RegionROI: Identifiable, Codable, Equatable {
    var id: Int
    var name: String? = nil
    var sourceType: String? = nil
    var xPx: Double
    var yPx: Double
    var widthPx: Double
    var heightPx: Double
    var centroidX: Double
    var centroidY: Double
    var areaPx: Double
    var areaUm2: Double
    var cellCount: Int
    var assignedCellCount: Int
    var dominantType: String
    var colorHex: String
    var countsByType: [String: Int]
    var maskRuns: [MaskRun]? = nil
    var manualEditMode: String? = nil
    var originalRegionID: Int? = nil
    var originalSourceType: String? = nil
}

struct MaskRun: Codable, Equatable, Hashable {
    var y: Int
    var xStart: Int
    var xEnd: Int
}

struct RegionTypeCount: Identifiable, Codable, Equatable {
    var id: String { name }
    var name: String
    var count: Int
    var colorHex: String
}

struct RegionAnalysisResult: Identifiable {
    var id = UUID()
    var regions: [RegionROI]
    var dominantCounts: [RegionTypeCount]
    var parameters: RegionParameters
    var image: NSImage
    var statsImage: NSImage
    var width: Int
    var height: Int

    var totalCells: Int {
        regions.reduce(0) { $0 + $1.cellCount }
    }
}

struct CellDistributionRegionSummary: Identifiable, Codable, Equatable {
    var id: Int { regionID }
    var regionID: Int
    var xPx: Double
    var yPx: Double
    var widthPx: Double
    var heightPx: Double
    var dominantType: String
    var colorHex: String
    var areaUm2: Double
    var totalCells: Int
    var assignedCells: Int
    var densityCellsPerMm2: Double
    var boundaryBandCells: Int
    var coreCells: Int
    var countsByType: [String: Int]
}

struct CellDistributionBandMetric: Identifiable, Codable, Equatable {
    var id: String { "\(regionID)-\(regionKey)-\(bandIndex)-\(cellType)" }
    var regionID: Int
    var regionName: String
    var regionKey: String
    var side: String
    var bandIndex: Int
    var distLoUm: Double
    var distHiUm: Double
    var cellType: String
    var cellCount: Int
    var areaPx: Int
    var areaUm2: Double
    var densityCellsPerUm2: Double
    var densityCellsPerMm2: Double
}

struct CellDistributionClusterMetric: Identifiable, Codable, Equatable {
    var id: String { "\(regionID)-\(regionKey)-\(clusterID)" }
    var regionID: Int
    var regionName: String
    var regionKey: String
    var clusterID: Int
    var clusterLabel: String
    var occupiedTileCount: Int
    var totalCellsInTiles: Int
    var meanInsideFraction: Double
}

struct CellDistributionTileClassification: Identifiable, Codable, Equatable {
    var id: String { "\(regionID)-\(tileRow)-\(tileColumn)-\(clusterID)-\(regionKey)" }
    var regionID: Int
    var regionName: String
    var regionKey: String
    var regionDisplayName: String
    var tileRow: Int
    var tileColumn: Int
    var tileIndex: Int
    var x0Px: Int
    var x1Px: Int
    var y0Px: Int
    var y1Px: Int
    var tileAreaPx: Int
    var insidePx: Int
    var insideFraction: Double
    var clusterID: Int
    var clusterKey: String
    var clusterLabel: String
    var cellCount: Int
}

struct CellDistributionTypeSummary: Identifiable, Codable, Equatable {
    var id: String { cellType }
    var cellType: String
    var colorHex: String
    var totalCount: Int
    var regionsPresent: Int
    var meanCountPerRegion: Double
    var maxRegionCount: Int
}

struct CellDistributionAnalysisResult: Identifiable {
    var id = UUID()
    var regionSummaries: [CellDistributionRegionSummary]
    var typeSummaries: [CellDistributionTypeSummary]
    var bandMetrics: [CellDistributionBandMetric] = []
    var clusterMetrics: [CellDistributionClusterMetric] = []
    var tileClassifications: [CellDistributionTileClassification] = []
    var bandWidthUm: Double
    var bandWidthPx: Double
    var image: NSImage
    var densityImage: NSImage
    var clusterImage: NSImage
    var width: Int
    var height: Int
    var imageID: String = "FieldA"
    var pixelSizeUm: (Double, Double) = (1.0, 1.0)

    var totalCells: Int {
        regionSummaries.reduce(0) { $0 + $1.totalCells }
    }
}

struct NearestNeighborDistance: Identifiable, Codable, Equatable {
    var id: String { "\(nucleusID)|\(nearestType ?? "")|\(nearestNucleusID ?? -1)" }
    var nucleusID: Int
    var assignedType: String
    var colorHex: String
    var centroidX: Double
    var centroidY: Double
    var nearestNucleusID: Int?
    var nearestType: String?
    var nearestDistancePx: Double
    var nearestDistanceUm: Double
}

struct BoundaryDistance: Identifiable, Codable, Equatable {
    var id: String { "\(boundaryName ?? "")|\(nucleusID)" }
    var nucleusID: Int
    var assignedType: String
    var colorHex: String
    var centroidX: Double
    var centroidY: Double
    var regionID: Int
    var boundaryName: String? = nil
    var insideRegion: Bool? = nil
    var distanceToBoundaryPx: Double
    var distanceToBoundaryUm: Double
}

enum DistanceBoundaryRegionFilter: String, CaseIterable, Identifiable, Codable {
    case all
    case inside
    case outside

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all:
            return "All cells"
        case .inside:
            return "Only cells inside region"
        case .outside:
            return "Only cells outside region"
        }
    }
}

struct DistanceTTest: Identifiable, Codable, Equatable {
    var id: String { "\(test)|\(ref)|\(cmp)" }
    var ref: String
    var cmp: String
    var nRef: Int? = nil
    var nCmp: Int? = nil
    var nPairs: Int? = nil
    var t: Double
    var p: Double
    var test: String
}

struct DistanceSummary: Identifiable, Codable, Equatable {
    var id: String { metric }
    var metric: String
    var count: Int
    var meanUm: Double
    var medianUm: Double
    var minUm: Double
    var maxUm: Double
}

struct DistanceAnalysisResult: Identifiable {
    var id = UUID()
    var nearestDistances: [NearestNeighborDistance]
    var boundaryDistances: [BoundaryDistance]
    var nearestTTests: [DistanceTTest] = []
    var boundaryTTests: [DistanceTTest] = []
    var summaries: [DistanceSummary]
    var image: NSImage
    var nearestHistogramImage: NSImage
    var boundaryHistogramImage: NSImage
    var width: Int
    var height: Int
    var nearestTargetType: String? = nil
    var nearestQueryTypes: [String] = []
    var boundaryName: String? = nil
    var boundaryQueryTypes: [String] = []
    var boundaryFilter: DistanceBoundaryRegionFilter? = nil

    var measuredCellCount: Int {
        nearestDistances.isEmpty ? boundaryDistances.count : nearestDistances.count
    }
}

struct ResourceSnapshot: Equatable {
    var cpuCoreCount: Int
    var activeCPUCoreCount: Int
    var gpuCount: Int
    var gpuNames: [String]
    var cpuUsagePercent: Double
    var gpuUsagePercent: Double?
    var timestamp: Date = Date()
}

struct AssignmentParameters: Codable, Equatable {
    var rVoronoiUm: Double = 3.0
    var rBufferUm: Double = 2.0
    var rVoteUm: Double = 3.0
    var tophatRUm: Double = 1.0
    var gaussSigmaUm: Double = 0.5
    var threshMode: String = "global_otsu"
    var minPosObjectSizePx: Int = 9
    var minPosPix: Int = 5
    var resolveAmbiguous: Bool = true
    var ambiguousMinProbability: Double = 0.60
    var ambiguousMinGap: Double = 0.10
}

struct RegionParameters: Codable, Equatable {
    var selectedTypes: [String] = []
    var closeUm: Double = 15.0
    var dilateUm: Double = 10.0
    var minAreaUm2: Double = 20_000.0
    var minCells: Int = 5
    var contourDownsample: Int = 2
    var lineWidth: Double = 2.0
    var lineStyle: String = "-"
    var boundaryColor: String = "#a1d99b"
    var useTypeColors: Bool = false
}

enum RegionManualEditMode: String, Codable, CaseIterable, Identifiable {
    case redraw = "Create new region"
    case include = "Inclusion"
    case exclude = "Exclusion"

    var id: String { rawValue }
}
