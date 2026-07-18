import AppKit
import Foundation

enum CellDistributionOutputMode {
    case regionMasks
    case cellDensity
    case regionMasksAndDensity
    case cellClusterDistribution
}

struct CellDistributionRegionMaskArtifacts {
    var boundaryLabel: String
    var insideLabel: String
    var outsideLabel: String
    var bandWidthUm: Double
    var pixelSize: (Double, Double)
    var arraysURL: URL
    var bandMapImage: NSImage
    var insideBandIndex: NPZExportService.Int16Array2D
    var outsideBandIndex: NPZExportService.Int16Array2D
}

struct CellDistributionBoundaryChoice: Identifiable, Hashable {
    var id: Int
    var label: String
    var maskPath: String
    var source: String
    var groupName: String
    var maskKey: String
}

struct LoadedAppConfiguration {
    var imageID: String
    var inputFolder: URL
    var outputFolder: URL
    var channels: [ChannelConfig]
    var whiteChannelName: String?
    var whiteWeight: Double
    var pixelSize: (Double, Double)?
    var figureSizeUm: (Double, Double)?
    var figureSizePx: (Int, Int)?
    var nucleusChannelName: String?
    var nucleiRunMode: NucleiRunMode?
    var nucleiParameters: NucleiParameters?
    var nucleiScanCombinationBudget: Int?
    var assignmentRunMode: AssignmentRunMode?
    var assignmentParameters: AssignmentParameters?
    var assignmentScanCombinationBudget: Int?
    var assignmentScreeningBandCount: Int?
    var assignmentScreeningSubsetMode: AssignmentScreeningSubsetMode?
    var cpuAllocationPercent: Double?
    var gpuAllocationPercent: Double?
}

struct RegionDisplaySaveSummary {
    var customizedDirectory: URL
    var originalDirectory: URL
    var customizedFiles: [URL]
    var originalFiles: [URL]
}

enum OutputWriter {
    static let sectionOutputSubdirs: [(String, String)] = [
        ("config", "00_config"),
        ("overlay", "01_overlay_preview"),
        ("nuclei", "02_nuclei_segmentation"),
        ("celltype_definition", "03_cell_type_definition"),
        ("celltype_assignment_parameters", "04_cell_type_assignment_parameters"),
        ("celltype_assignment", "05_cell_type_assignment"),
        ("neighborhood_analysis", "06_neighborhood_analysis"),
        ("region_analysis", "07_region_analysis"),
        ("integrated_region_analysis", "08_adjusted_region_analysis"),
        ("distance_analysis", "09_distance_analysis"),
        ("cell_distribution_analysis", "10_cell_distribution_analysis")
    ]

    static func ensureSectionDirectories(outputFolder: URL) throws {
        for (_, directoryName) in sectionOutputSubdirs {
            try FileManager.default.createDirectory(
                at: outputFolder.appendingPathComponent(directoryName),
                withIntermediateDirectories: true
            )
        }
    }

    static func sectionURL(_ key: String, outputFolder: URL) -> URL {
        let directoryName = sectionOutputSubdirs.first { $0.0 == key }?.1 ?? key
        return outputFolder.appendingPathComponent(directoryName)
    }

    static func writeConfiguration(
        inputFolder: URL,
        outputFolder: URL,
        channels: [ChannelConfig],
        overlayChannels: [ChannelConfig],
        whiteChannel: ChannelConfig?,
        whiteWeight: Double,
        pixelSize: (Double, Double)?,
        imageID: String? = nil,
        figureSizeUm: (Double, Double)? = nil,
        figureSizePx: (Int, Int)? = nil,
        nucleusChannel: ChannelConfig? = nil,
        nucleiRunMode: NucleiRunMode? = nil,
        nucleiParameters: NucleiParameters? = nil,
        nucleiScanCombinationBudget: Int? = nil,
        assignmentRunMode: AssignmentRunMode? = nil,
        assignmentParameters: AssignmentParameters? = nil,
        assignmentScanCombinationBudget: Int? = nil,
        assignmentScreeningBandCount: Int? = nil,
        assignmentScreeningSubsetMode: AssignmentScreeningSubsetMode? = nil,
        cpuAllocationPercent: Double? = nil,
        gpuAllocationPercent: Double? = nil
    ) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let configURL = sectionURL("config", outputFolder: outputFolder).appendingPathComponent("pipeline_config.json")
        let resolvedImageID = sanitizedImageID(imageID)
            ?? configuredImageID(outputFolder: outputFolder)
        let payload = PipelineConfigPayload(
            appName: "SpatialScope",
            inputMode: "local_native",
            imageID: resolvedImageID,
            folder: inputFolder.path,
            saveDir: outputFolder.path,
            pixelSizeUm: pixelSize.map { [$0.0, $0.1] } ?? [],
            figureSizeUm: figureSizeUm.map { [$0.0, $0.1] },
            figureSizePx: figureSizePx.map { [$0.0, $0.1] },
            channels: channels.map {
                PipelineChannelPayload(file: $0.fileName, channel: $0.channelName, colorHex: $0.colorHex)
            },
            overlayChannels: overlayChannels.map(\.channelName),
            whiteChannel: whiteChannel?.channelName,
            whiteWeight: whiteWeight,
            nucleusChannel: nucleusChannel?.channelName,
            nucleiRunMode: nucleiRunMode?.rawValue,
            nucleiParameters: nucleiParameters,
            nucleiScanCombinationBudget: nucleiScanCombinationBudget,
            assignmentRunMode: assignmentRunMode?.rawValue,
            assignmentParameters: assignmentParameters,
            assignmentScanCombinationBudget: assignmentScanCombinationBudget,
            assignmentScreeningBandCount: assignmentScreeningBandCount,
            assignmentScreeningSubsetMode: assignmentScreeningSubsetMode?.rawValue,
            cpuAllocationPercent: cpuAllocationPercent,
            gpuAllocationPercent: gpuAllocationPercent
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(payload).write(to: configURL)
    }

    static func configuredImageID(outputFolder: URL, fallback: String = "FieldA") -> String {
        let configURL = sectionURL("config", outputFolder: outputFolder).appendingPathComponent("pipeline_config.json")
        guard let data = try? Data(contentsOf: configURL),
              let payload = try? JSONDecoder().decode(PipelineConfigPayload.self, from: data),
              let imageID = sanitizedImageID(payload.imageID) else {
            return fallback
        }
        return imageID
    }

    private static func sanitizedImageID(_ value: String?) -> String? {
        let trimmed = (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    static func writeCellTypeConfig(_ cellTypes: [CellTypeDefinition], outputFolder: URL) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let payload = cellTypes.map { cellType in
            CellTypePayload(
                name: cellType.name,
                colorHex: cellType.colorHex,
                mode: "simple",
                allPositiveMarkers: parseMarkerList(cellType.allPositiveMarkers),
                allNegativeMarkers: parseMarkerList(cellType.allNegativeMarkers),
                anyPositiveGroups: parseGroupList(cellType.anyPositiveGroups)
            )
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let url = sectionURL("celltype_definition", outputFolder: outputFolder)
            .appendingPathComponent("celltype_config.json")
        try encoder.encode(payload).write(to: url)
    }

    static func writeCellTypeAssignmentOutputs(result: CellTypeAssignmentResult, outputFolder: URL) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let assignmentDir = sectionURL("celltype_assignment", outputFolder: outputFolder)
        try ImageExportService.writePNG(result.image, to: assignmentDir.appendingPathComponent("celltype_assignment_map.png"), dpi: 300)
        try ImageExportService.writeAI(result.image, to: assignmentDir.appendingPathComponent("celltype_assignment_map.ai"), dpi: 300)
        try ImageExportService.writeVectorSVG(
            result.image,
            title: "Cell type assignment map",
            to: assignmentDir.appendingPathComponent("celltype_assignment_map.svg")
        )
        try ImageExportService.writePNG(result.statsImage, to: assignmentDir.appendingPathComponent("celltype_assignment_counts.png"), dpi: 300)
        try ImageExportService.writeAI(result.statsImage, to: assignmentDir.appendingPathComponent("celltype_assignment_counts.ai"), dpi: 300)
        try EditableSVGWriter.writeCellTypeCountsSVG(
            counts: result.counts,
            to: assignmentDir.appendingPathComponent("celltype_assignment_counts.svg")
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(result.assignments).write(to: assignmentDir.appendingPathComponent("celltype_assignments.json"))
        try encoder.encode(result.counts).write(to: assignmentDir.appendingPathComponent("celltype_assignment_counts.json"))
        try encoder.encode(result.parameters).write(to: assignmentDir.appendingPathComponent("celltype_assignment_parameters.json"))
        let cellTypeIDByName = assignmentCellTypeIDByName(result: result, outputFolder: outputFolder)
        let cellTypeMask = makeAssignmentCellTypeMaskFromRenderedMap(
            image: result.image,
            assignments: result.assignments,
            counts: result.counts,
            cellTypeIDByName: cellTypeIDByName
        ) ?? result.cellTypeMask ?? makeAssignmentCellTypeMask(
            assignments: result.assignments,
            cellTypeIDByName: cellTypeIDByName,
            width: result.width,
            height: result.height
        )
        try ImageExportService.writeUInt16TIFF(
            width: cellTypeMask.width,
            height: cellTypeMask.height,
            values: cellTypeMask.values,
            to: assignmentDir.appendingPathComponent("celltypes_mask_uint16.tiff")
        )
        try ImageExportService.writeUInt16RasterRaw(
            width: cellTypeMask.width,
            height: cellTypeMask.height,
            values: cellTypeMask.values,
            to: assignmentDir.appendingPathComponent("celltypes_mask_uint16.raw")
        )
        let maskIDRows = cellTypeIDByName
            .map { CellTypeMaskIDPayload(id: Int($0.value), name: $0.key) }
            .sorted {
                if $0.id == $1.id {
                    return $0.name.localizedStandardCompare($1.name) == .orderedAscending
                }
                return $0.id < $1.id
            }
        try encoder.encode(maskIDRows).write(to: assignmentDir.appendingPathComponent("celltype_mask_ids.json"))
        var maskIDCSV = ["celltype_id,celltype"]
        for row in maskIDRows {
            maskIDCSV.append(["\(row.id)", row.name].map(csvField).joined(separator: ","))
        }
        try maskIDCSV.joined(separator: "\n").write(
            to: assignmentDir.appendingPathComponent("celltype_mask_ids.csv"),
            atomically: true,
            encoding: .utf8
        )

        let markerColumns = Array(
            Set(result.assignments.flatMap { $0.markerMeans.keys })
        ).sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        var csvRows: [String] = [
            (["nucleus_id", "centroid_x_px", "centroid_y_px", "area_px", "assigned_type", "score", "probability", "matched_positive_markers", "blocked_negative_markers"] + markerColumns)
                .map(csvField)
                .joined(separator: ",")
        ]
        for assignment in result.assignments {
            let base = [
                "\(assignment.nucleusID)",
                "\(assignment.centroidX)",
                "\(assignment.centroidY)",
                "\(assignment.areaPx)",
                assignment.assignedType,
                "\(assignment.score)",
                "\(assignment.probability)",
                assignment.matchedPositiveMarkers.joined(separator: "; "),
                assignment.blockedNegativeMarkers.joined(separator: "; ")
            ]
            let markerValues = markerColumns.map { marker in
                assignment.markerMeans[marker].map { "\($0)" } ?? ""
            }
            csvRows.append((base + markerValues).map(csvField).joined(separator: ","))
        }
        try csvRows.joined(separator: "\n").write(
            to: assignmentDir.appendingPathComponent("celltype_assignments.csv"),
            atomically: true,
            encoding: .utf8
        )

        var countRows = ["cell_type,count,color_hex"]
        for row in result.counts {
            countRows.append([row.name, "\(row.count)", row.colorHex].map(csvField).joined(separator: ","))
        }
        try countRows.joined(separator: "\n").write(
            to: assignmentDir.appendingPathComponent("celltype_assignment_counts.csv"),
            atomically: true,
            encoding: .utf8
        )

        let manifest = NativeAnalysisManifestPayload(
            sectionKey: "celltype_assignment",
            sectionTitle: "Cell Type Assignments",
            nativeEngineActive: true,
            status: "complete",
            message: "Native cell-type assignment complete.",
            resultCount: result.assignments.count,
            outputFiles: [
                "celltype_assignment_map.png",
                "celltype_assignment_map.ai",
                "celltype_assignment_map.svg",
                "celltype_assignments.csv",
                "celltype_assignments.json",
                "celltypes_mask_uint16.tiff",
                "celltype_mask_ids.csv",
                "celltype_mask_ids.json",
                "celltype_assignment_counts.png",
                "celltype_assignment_counts.ai",
                "celltype_assignment_counts.svg",
                "celltype_assignment_counts.csv"
            ],
            timestamp: ISO8601DateFormatter().string(from: Date())
        )
        try encoder.encode(manifest).write(to: assignmentDir.appendingPathComponent("analysis_run_manifest.json"))
    }

    static func writeNeighborhoodAnalysisOutputs(result: NeighborhoodAnalysisResult, outputFolder: URL) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let neighborhoodDir = sectionURL("neighborhood_analysis", outputFolder: outputFolder)
        for staleName in [
            "neighborhood_dominant_counts.png",
            "neighborhood_dominant_counts.ai",
            "neighborhood_dominant_counts.svg",
            "neighborhood_dominant_counts.csv",
            "neighborhood_dominant_counts.json"
        ] {
            try? FileManager.default.removeItem(at: neighborhoodDir.appendingPathComponent(staleName))
        }
        try ImageExportService.writePNG(result.image, to: neighborhoodDir.appendingPathComponent("neighborhood_map.png"), dpi: 300)
        try ImageExportService.writeAI(result.image, to: neighborhoodDir.appendingPathComponent("neighborhood_map.ai"), dpi: 300)
        try ImageExportService.writePNG(result.image, to: neighborhoodDir.appendingPathComponent("neighborhood_clusters.png"), dpi: 300)
        try ImageExportService.writeAI(result.image, to: neighborhoodDir.appendingPathComponent("neighborhood_clusters.ai"), dpi: 300)
        try ImageExportService.writeTIFF(result.image, to: neighborhoodDir.appendingPathComponent("neighborhood_clusters.tiff"))
        try EditableSVGWriter.writeNeighborhoodMapSVG(
            result: result,
            to: neighborhoodDir.appendingPathComponent("neighborhood_map.svg")
        )
        try EditableSVGWriter.writeNeighborhoodMapSVG(
            result: result,
            to: neighborhoodDir.appendingPathComponent("neighborhood_clusters.svg")
        )
        try ImageExportService.writePNG(result.clusterKeyImage, to: neighborhoodDir.appendingPathComponent("neighborhood_cluster_key.png"), dpi: 300)
        try ImageExportService.writeAI(result.clusterKeyImage, to: neighborhoodDir.appendingPathComponent("neighborhood_cluster_key.ai"), dpi: 300)
        try EditableSVGWriter.writeNeighborhoodClusterKeySVG(
            counts: result.clusterCounts,
            to: neighborhoodDir.appendingPathComponent("neighborhood_cluster_key.svg")
        )
        try ImageExportService.writePNG(result.statsImage, to: neighborhoodDir.appendingPathComponent("neighborhood_cluster_summary.png"), dpi: 300)
        try ImageExportService.writeAI(result.statsImage, to: neighborhoodDir.appendingPathComponent("neighborhood_cluster_summary.ai"), dpi: 300)
        try EditableSVGWriter.writeNeighborhoodClusterSummarySVG(
            counts: result.clusterCounts,
            to: neighborhoodDir.appendingPathComponent("neighborhood_cluster_summary.svg")
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(result.tiles).write(to: neighborhoodDir.appendingPathComponent("neighborhood_tiles.json"))
        try encoder.encode(result.clusterCounts).write(to: neighborhoodDir.appendingPathComponent("neighborhood_cluster_summary.json"))
        try encoder.encode(
            NeighborhoodParametersPayload(
                gridSizeUm: result.gridSizeUm,
                gridSizePx: result.gridSizePx,
                gridWidthPx: result.gridWidthPx,
                gridHeightPx: result.gridHeightPx
            )
        ).write(to: neighborhoodDir.appendingPathComponent("neighborhood_parameters.json"))
        let columns = max(1, Int(ceil(Double(max(1, result.width)) / max(1.0, result.gridWidthPx))))
        let rows = max(1, Int(ceil(Double(max(1, result.height)) / max(1.0, result.gridHeightPx))))
        let clusterColors = Dictionary(uniqueKeysWithValues: result.clusterCounts.map { ($0.clusterLabel, $0.colorHex) })
        let paramsPayload = NeighborhoodParamsCompatPayload(
            gridSizeUm: result.gridSizeUm,
            tileWidthPx: Int(round(result.gridWidthPx)),
            tileHeightPx: Int(round(result.gridHeightPx)),
            nTilesX: columns,
            nTilesY: rows,
            excludedCelltypes: ["Unassigned", "Ambiguous"],
            displayClusterLabels: result.clusterCounts.map(\.clusterLabel),
            clusterColors: clusterColors
        )
        try encoder.encode(paramsPayload).write(to: neighborhoodDir.appendingPathComponent("neighborhood_params.json"))

        var tileRows = ["row,column,x_px,y_px,width_px,height_px,cluster_id,cluster_key,cluster_label,dominant_type,total_cells,assigned_cells,counts_by_type"]
        var streamlitTileRows = ["tile_row,tile_col,tile_index,x0_px,x1_px,y0_px,y1_px,n_cells,celltypes,cluster_key,cluster_label,cluster_id"]
        var clusterMask = [UInt16](repeating: 0, count: max(1, result.width * result.height))
        for tile in result.tiles {
            let composition = tile.countsByType
                .sorted { $0.key.localizedStandardCompare($1.key) == .orderedAscending }
                .map { "\($0.key)=\($0.value)" }
                .joined(separator: "; ")
            tileRows.append([
                "\(tile.row)",
                "\(tile.column)",
                "\(tile.xPx)",
                "\(tile.yPx)",
                "\(tile.widthPx)",
                "\(tile.heightPx)",
                tile.clusterID.map(String.init) ?? "",
                tile.effectiveClusterKey,
                tile.effectiveClusterLabel,
                tile.dominantType,
                "\(tile.totalCells)",
                "\(tile.assignedCells)",
                composition
            ].map(csvField).joined(separator: ","))
            let clusterID = tile.clusterID ?? 0
            let x0 = max(0, min(result.width, Int(floor(tile.xPx))))
            let y0 = max(0, min(result.height, Int(floor(tile.yPx))))
            let x1 = max(x0, min(result.width, Int(ceil(tile.xPx + tile.widthPx))))
            let y1 = max(y0, min(result.height, Int(ceil(tile.yPx + tile.heightPx))))
            if clusterID > 0 && tile.assignedCells > 0 && x0 < x1 && y0 < y1 {
                for y in y0..<y1 {
                    let rowOffset = y * result.width
                    for x in x0..<x1 {
                        clusterMask[rowOffset + x] = UInt16(min(clusterID, Int(UInt16.max)))
                    }
                }
            }
            streamlitTileRows.append([
                "\(tile.row)",
                "\(tile.column)",
                "\(tile.row * columns + tile.column)",
                "\(x0)",
                "\(x1)",
                "\(y0)",
                "\(y1)",
                "\(tile.assignedCells)",
                tile.effectiveClusterLabel,
                tile.effectiveClusterKey,
                tile.effectiveClusterLabel,
                clusterID > 0 ? "\(clusterID)" : ""
            ].map(csvField).joined(separator: ","))
        }
        try tileRows.joined(separator: "\n").write(
            to: neighborhoodDir.appendingPathComponent("neighborhood_tiles.csv"),
            atomically: true,
            encoding: .utf8
        )
        try streamlitTileRows.joined(separator: "\n").write(
            to: neighborhoodDir.appendingPathComponent("neighborhood_tile_assignments.csv"),
            atomically: true,
            encoding: .utf8
        )
        try ImageExportService.writeUInt16TIFF(
            width: result.width,
            height: result.height,
            values: clusterMask,
            to: neighborhoodDir.appendingPathComponent("neighborhood_cluster_mask_uint16.tiff")
        )

        var keyRows = ["number,cluster_id,cluster_key,cluster_label,tile_count,cell_count,tile_fraction,color_hex"]
        var keyTextRows = ["Neighborhood Cluster Key", "Number\tCluster ID\tCluster Key\tCluster Type\tTiles\tCells\tTile Fraction\tColor"]
        for row in result.clusterCounts {
            keyRows.append([
                "\(row.clusterID)",
                "\(row.clusterID)",
                row.clusterKey,
                row.clusterLabel,
                "\(row.tileCount)",
                "\(row.cellCount)",
                "\(row.tileFraction)",
                row.colorHex
            ].map(csvField).joined(separator: ","))
            keyTextRows.append([
                "\(row.clusterID)",
                "\(row.clusterID)",
                row.clusterKey,
                row.clusterLabel,
                "\(row.tileCount)",
                "\(row.cellCount)",
                String(format: "%.6f", row.tileFraction),
                row.colorHex
            ].joined(separator: "\t"))
        }
        try keyRows.joined(separator: "\n").write(
            to: neighborhoodDir.appendingPathComponent("neighborhood_cluster_key.csv"),
            atomically: true,
            encoding: .utf8
        )
        try keyTextRows.joined(separator: "\n").write(
            to: neighborhoodDir.appendingPathComponent("neighborhood_cluster_key.txt"),
            atomically: true,
            encoding: .utf8
        )

        var countRows = ["cluster_id,cluster_key,cluster_label,tile_count,cell_count,tile_fraction,color_hex"]
        for row in result.clusterCounts {
            countRows.append([
                "\(row.clusterID)",
                row.clusterKey,
                row.clusterLabel,
                "\(row.tileCount)",
                "\(row.cellCount)",
                "\(row.tileFraction)",
                row.colorHex
            ].map(csvField).joined(separator: ","))
        }
        try countRows.joined(separator: "\n").write(
            to: neighborhoodDir.appendingPathComponent("neighborhood_cluster_summary.csv"),
            atomically: true,
            encoding: .utf8
        )

        let manifest = NativeAnalysisManifestPayload(
            sectionKey: "neighborhood_analysis",
            sectionTitle: "Neighborhood Analysis",
            nativeEngineActive: true,
            status: "complete",
            message: "Native square-grid neighborhood analysis complete.",
            resultCount: result.occupiedTileCount,
            outputFiles: [
                "neighborhood_map.png",
                "neighborhood_map.ai",
                "neighborhood_map.svg",
                "neighborhood_clusters.png",
                "neighborhood_clusters.ai",
                "neighborhood_clusters.svg",
                "neighborhood_clusters.tiff",
                "neighborhood_tiles.csv",
                "neighborhood_tiles.json",
                "neighborhood_tile_assignments.csv",
                "neighborhood_cluster_mask_uint16.tiff",
                "neighborhood_params.json",
                "neighborhood_cluster_key.png",
                "neighborhood_cluster_key.ai",
                "neighborhood_cluster_key.svg",
                "neighborhood_cluster_key.csv",
                "neighborhood_cluster_key.txt",
                "neighborhood_cluster_summary.png",
                "neighborhood_cluster_summary.ai",
                "neighborhood_cluster_summary.svg",
                "neighborhood_cluster_summary.csv",
                "neighborhood_cluster_summary.json"
            ],
            timestamp: ISO8601DateFormatter().string(from: Date())
        )
        try encoder.encode(manifest).write(to: neighborhoodDir.appendingPathComponent("analysis_run_manifest.json"))
    }

    static func writeRegionAnalysisOutputs(
        result: RegionAnalysisResult,
        outputFolder: URL,
        assignments: [CellTypeAssignment] = [],
        cellTypeMask: UInt16Raster? = nil,
        cellTypeIDByName: [String: UInt16] = [:],
        overlayImage: NSImage? = nil,
        renderRegions: [RegionROI]? = nil
    ) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let regionDir = sectionURL("region_analysis", outputFolder: outputFolder)
        removeGeneratedFiles(in: regionDir) { name in
            name == "region_map.png"
                || name == "region_map.ai"
                || name == "region_map.svg"
                || name == "region_comparison_map.png"
                || name == "region_comparison_map.ai"
                || name == "region_comparison_map.svg"
                || name == "region_comparison_map.tiff"
                || name == "regions.json"
                || name == "regions.csv"
                || name == "region_mask_runs.csv"
                || name == "region_area_summary.csv"
                || name == "region_dominant_counts.json"
                || name == "region_dominant_counts.png"
                || name == "region_dominant_counts.ai"
                || name == "region_dominant_counts.svg"
                || name == "region_dominant_counts.csv"
                || name == "region_parameters.json"
                || name == "boundary_mask_registry.json"
                || name == "analysis_run_manifest.json"
                || name.hasPrefix("celltypes_with_boundaries__")
                || name.hasPrefix("celltype_counts_by_region__")
                || name.hasPrefix("cell_region_assignments__")
                || name.hasPrefix("region_area_summary__")
                || name.hasPrefix("region_params__")
                || name.hasSuffix("_region_mask_uint8.tiff")
        }
        let mapRegions = renderRegions ?? result.regions
        var mapResult = result
        mapResult.regions = mapRegions
        var outputResult = result
        outputResult.regions = mapRegions
        if renderRegions != nil {
            outputResult.dominantCounts = mapRegions.map { region in
                RegionTypeCount(
                    name: region.sourceType ?? region.dominantType,
                    count: region.cellCount,
                    colorHex: region.colorHex
                )
            }
            outputResult.statsImage = RegionAnalyzer.renderDominantCountsPlot(counts: outputResult.dominantCounts)
        }
        let regionMapImage = RegionAnalyzer.renderRegionMap(
            assignments: assignments,
            regions: mapRegions,
            width: outputResult.width,
            height: outputResult.height,
            parameters: outputResult.parameters,
            cellTypeMask: cellTypeMask,
            cellTypeIDByName: cellTypeIDByName
        )
        outputResult.image = regionMapImage
        try ImageExportService.writePNG(regionMapImage, to: regionDir.appendingPathComponent("region_map.png"), dpi: 300)
        try ImageExportService.writeAI(regionMapImage, to: regionDir.appendingPathComponent("region_map.ai"), dpi: 300)
        try EditableSVGWriter.writeRegionMapSVG(
            result: mapResult,
            assignments: assignments,
            to: regionDir.appendingPathComponent("region_map.svg")
        )
        let resolvedOverlayImage = overlayImage ?? loadImage(outputFolder: outputFolder, section: "overlay", name: "overlay.png")
        let comparisonImage = RegionAnalyzer.renderRegionComparisonMap(
            overlayImage: resolvedOverlayImage,
            assignments: assignments,
            regions: mapRegions,
            width: outputResult.width,
            height: outputResult.height,
            parameters: outputResult.parameters,
            cellTypeMask: cellTypeMask,
            cellTypeIDByName: cellTypeIDByName,
            title: "Computed ROIs"
        )
        try ImageExportService.writePNG(comparisonImage, to: regionDir.appendingPathComponent("region_comparison_map.png"), dpi: 300)
        try ImageExportService.writeAI(comparisonImage, to: regionDir.appendingPathComponent("region_comparison_map.ai"), dpi: 300)
        try ImageExportService.writeVectorSVG(comparisonImage, title: "Computed ROIs", to: regionDir.appendingPathComponent("region_comparison_map.svg"))
        try ImageExportService.writeTIFF(comparisonImage, to: regionDir.appendingPathComponent("region_comparison_map.tiff"))
        try ImageExportService.writePNG(outputResult.statsImage, to: regionDir.appendingPathComponent("region_dominant_counts.png"), dpi: 300)
        try ImageExportService.writeAI(outputResult.statsImage, to: regionDir.appendingPathComponent("region_dominant_counts.ai"), dpi: 300)
        try EditableSVGWriter.writeRegionCountsSVG(
            counts: outputResult.dominantCounts,
            to: regionDir.appendingPathComponent("region_dominant_counts.svg")
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(outputResult.regions).write(to: regionDir.appendingPathComponent("regions.json"))
        try encoder.encode(outputResult.dominantCounts).write(to: regionDir.appendingPathComponent("region_dominant_counts.json"))
        try encoder.encode(outputResult.parameters).write(to: regionDir.appendingPathComponent("region_parameters.json"))

        var regionRows = ["region_id,region_name,source_type,x_px,y_px,width_px,height_px,centroid_x_px,centroid_y_px,area_px,area_um2,cell_count,assigned_cell_count,dominant_type,counts_by_type,manual_edit_mode,original_region_id,original_source_type"]
        var areaRows = ["boundary_type,area_px,area_um2,total_field_area_px,total_field_area_um2,area_fraction,n_cells_inside"]
        var maskRunRows = ["region_id,region_name,source_type,y,x_start,x_end"]
        var registryEntries: [BoundaryMaskRegistryEntryPayload] = []
        let totalFieldAreaPx = max(1, outputResult.width * outputResult.height)
        let totalFieldAreaUm2 = outputResult.regions.first.map { $0.areaUm2 / max(1.0, $0.areaPx) * Double(totalFieldAreaPx) } ?? Double(totalFieldAreaPx)
        let regionSourceTypes = outputResult.regions.map { $0.sourceType ?? $0.dominantType }
        let manualAdjustedExport = renderRegions != nil && outputResult.regions.contains { $0.manualEditMode != nil }
        let regionSeedTypes = Array(Set(outputResult.regions.map { region -> String in
            if manualAdjustedExport {
                let originalType = (region.originalSourceType ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                if !originalType.isEmpty {
                    return originalType
                }
            }
            return region.sourceType ?? region.dominantType
        })).sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        let outputSelectedTypes = manualAdjustedExport
            ? (regionSeedTypes.isEmpty ? regionSourceTypes : regionSeedTypes)
            : (outputResult.parameters.selectedTypes.isEmpty ? regionSourceTypes : outputResult.parameters.selectedTypes)
        let basePrefix = safeName(
            outputSelectedTypes.joined(separator: "__"),
            fallback: "region"
        )
        var compatMaskNames: [String] = []
        for region in outputResult.regions {
            let composition = region.countsByType
                .sorted { $0.key.localizedStandardCompare($1.key) == .orderedAscending }
                .map { "\($0.key)=\($0.value)" }
                .joined(separator: "; ")
            let regionName = region.name ?? "\(region.dominantType) region"
            let sourceType = region.sourceType ?? region.dominantType
            regionRows.append([
                "\(region.id)",
                regionName,
                sourceType,
                "\(region.xPx)",
                "\(region.yPx)",
                "\(region.widthPx)",
                "\(region.heightPx)",
                "\(region.centroidX)",
                "\(region.centroidY)",
                "\(region.areaPx)",
                "\(region.areaUm2)",
                "\(region.cellCount)",
                "\(region.assignedCellCount)",
                region.dominantType,
                composition,
                region.manualEditMode ?? "",
                region.originalRegionID.map(String.init) ?? "",
                region.originalSourceType ?? ""
            ].map(csvField).joined(separator: ","))
            areaRows.append([
                sourceType,
                "\(Int(region.areaPx.rounded()))",
                "\(region.areaUm2)",
                "\(totalFieldAreaPx)",
                "\(totalFieldAreaUm2)",
                "\(region.areaPx / Double(totalFieldAreaPx))",
                "\(region.cellCount)"
            ].map(csvField).joined(separator: ","))
            for run in region.maskRuns ?? [] {
                maskRunRows.append([
                    "\(region.id)",
                    regionName,
                    sourceType,
                    "\(run.y)",
                    "\(run.xStart)",
                    "\(run.xEnd)"
                ].map(csvField).joined(separator: ","))
            }
            let regionGroupPrefix: String
            if manualAdjustedExport {
                let originalType = (region.originalSourceType ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                regionGroupPrefix = safeName(originalType.isEmpty ? sourceType : originalType, fallback: basePrefix)
            } else {
                regionGroupPrefix = basePrefix
            }
            let maskName = "\(safeName(sourceType, fallback: "region"))__\(regionGroupPrefix)_region_mask_uint8.tiff"
            let maskURL = regionDir.appendingPathComponent(maskName)
            var values = [UInt8](repeating: 0, count: max(1, outputResult.width * outputResult.height))
            for run in region.maskRuns ?? [] where run.y >= 0 && run.y < outputResult.height {
                let start = max(0, min(outputResult.width, run.xStart))
                let end = max(start, min(outputResult.width, run.xEnd))
                guard start < end else { continue }
                for x in start..<end {
                    values[run.y * outputResult.width + x] = 1
                }
            }
            try ImageExportService.writeUInt8TIFF(width: outputResult.width, height: outputResult.height, values: values, to: maskURL)
            compatMaskNames.append(maskName)
            let lowerRegionName = "\(regionName) \(sourceType)".lowercased()
            let registrySource = region.manualEditMode != nil || lowerRegionName.contains("manual") || lowerRegionName.contains("adjusted")
                ? "manual_region_adjustment"
                : "computational_roi_identification"
            registryEntries.append(
                BoundaryMaskRegistryEntryPayload(
                    maskPath: maskName,
                    displayName: sourceType,
                    source: registrySource,
                    groupName: regionGroupPrefix,
                    maskKey: sourceType
                )
            )
        }
        try regionRows.joined(separator: "\n").write(
            to: regionDir.appendingPathComponent("regions.csv"),
            atomically: true,
            encoding: .utf8
        )
        try areaRows.joined(separator: "\n").write(
            to: regionDir.appendingPathComponent("region_area_summary.csv"),
            atomically: true,
            encoding: .utf8
        )
        let compatAreaName = "region_area_summary__\(basePrefix).csv"
        try areaRows.joined(separator: "\n").write(
            to: regionDir.appendingPathComponent(compatAreaName),
            atomically: true,
            encoding: .utf8
        )
        try maskRunRows.joined(separator: "\n").write(
            to: regionDir.appendingPathComponent("region_mask_runs.csv"),
            atomically: true,
            encoding: .utf8
        )
        try encoder.encode(BoundaryMaskRegistryPayload(entries: registryEntries)).write(
            to: regionDir.appendingPathComponent("boundary_mask_registry.json")
        )
        let compatOverlayPNG = "celltypes_with_boundaries__\(basePrefix).png"
        let compatOverlayAI = "celltypes_with_boundaries__\(basePrefix).ai"
        let compatOverlaySVG = "celltypes_with_boundaries__\(basePrefix).svg"
        let compatOverlayTIFF = "celltypes_with_boundaries__\(basePrefix).tiff"
        try ImageExportService.writePNG(regionMapImage, to: regionDir.appendingPathComponent(compatOverlayPNG), dpi: 300)
        try ImageExportService.writeAI(regionMapImage, to: regionDir.appendingPathComponent(compatOverlayAI), dpi: 300)
        try EditableSVGWriter.writeRegionMapSVG(
            result: outputResult,
            assignments: assignments,
            to: regionDir.appendingPathComponent(compatOverlaySVG)
        )
        try ImageExportService.writeTIFF(regionMapImage, to: regionDir.appendingPathComponent(compatOverlayTIFF))

        let compatCountsName = "celltype_counts_by_region__\(basePrefix).csv"
        let compatAssignmentsName = "cell_region_assignments__\(basePrefix).csv"
        let compatParamsName = "region_params__\(basePrefix).json"
        let compatibility = regionCompatibilityTables(result: outputResult, assignments: assignments)
        try compatibility.countRows.joined(separator: "\n").write(
            to: regionDir.appendingPathComponent(compatCountsName),
            atomically: true,
            encoding: .utf8
        )
        try compatibility.assignmentRows.joined(separator: "\n").write(
            to: regionDir.appendingPathComponent(compatAssignmentsName),
            atomically: true,
            encoding: .utf8
        )
        try encoder.encode(
            RegionParamsCompatPayload(
                workflow: "computational_roi_identification",
                selectedTypes: outputSelectedTypes,
                closeUm: outputResult.parameters.closeUm,
                dilateUm: outputResult.parameters.dilateUm,
                minAreaUm2: outputResult.parameters.minAreaUm2,
                minCells: outputResult.parameters.minCells,
                contourDownsample: outputResult.parameters.contourDownsample,
                lineWidth: outputResult.parameters.lineWidth,
                lineStyle: outputResult.parameters.lineStyle,
                boundaryColor: outputResult.parameters.boundaryColor,
                useTypeColors: outputResult.parameters.useTypeColors
            )
        ).write(to: regionDir.appendingPathComponent(compatParamsName))

        var countRows = ["dominant_type,region_count,color_hex"]
        for row in outputResult.dominantCounts {
            countRows.append([row.name, "\(row.count)", row.colorHex].map(csvField).joined(separator: ","))
        }
        try countRows.joined(separator: "\n").write(
            to: regionDir.appendingPathComponent("region_dominant_counts.csv"),
            atomically: true,
            encoding: .utf8
        )

        let manifest = NativeAnalysisManifestPayload(
            sectionKey: "region_analysis",
            sectionTitle: "Region Analysis",
            nativeEngineActive: true,
            status: "complete",
            message: "Native computational ROI identification and counts complete.",
            resultCount: outputResult.regions.count,
            outputFiles: [
                "region_map.png",
                "region_map.ai",
                "region_map.svg",
                "region_comparison_map.png",
                "region_comparison_map.ai",
                "region_comparison_map.svg",
                "region_comparison_map.tiff",
                compatOverlayPNG,
                compatOverlayAI,
                compatOverlaySVG,
                compatOverlayTIFF,
                "regions.csv",
                "regions.json",
                "region_area_summary.csv",
                compatAreaName,
                compatCountsName,
                compatAssignmentsName,
                compatParamsName,
                "region_mask_runs.csv",
                "boundary_mask_registry.json",
                "region_dominant_counts.png",
                "region_dominant_counts.ai",
                "region_dominant_counts.svg",
                "region_dominant_counts.csv"
            ] + compatMaskNames,
            timestamp: ISO8601DateFormatter().string(from: Date())
        )
        try encoder.encode(manifest).write(to: regionDir.appendingPathComponent("analysis_run_manifest.json"))
    }

    private static func originalRegionsForCustomizedDisplay(
        selectedRegions: [RegionROI],
        allRegions: [RegionROI]
    ) -> (regions: [RegionROI], skippedMaskLabels: [String]) {
        var output: [RegionROI] = []
        var usedIDs: Set<Int> = []
        var skipped: [String] = []

        func appendUnique(_ region: RegionROI) {
            guard !usedIDs.contains(region.id) else { return }
            output.append(region)
            usedIDs.insert(region.id)
        }

        for region in selectedRegions {
            let label = region.sourceType ?? region.dominantType
            if let originalID = region.originalRegionID,
               let original = allRegions.first(where: { $0.id == originalID && $0.id != region.id }) {
                appendUnique(original)
                continue
            }
            if let originalSourceType = region.originalSourceType,
               let original = allRegions.first(where: {
                   ($0.sourceType ?? $0.dominantType) == originalSourceType && !isManualOrAdjustedRegion($0)
               }) {
                appendUnique(original)
                continue
            }
            if !isManualOrAdjustedRegion(region) {
                appendUnique(region)
            } else {
                skipped.append(label)
            }
        }

        if output.isEmpty {
            for region in allRegions where !isManualOrAdjustedRegion(region) {
                appendUnique(region)
            }
        }

        return (output, skipped)
    }

    private static func isManualOrAdjustedRegion(_ region: RegionROI) -> Bool {
        if region.manualEditMode != nil { return true }
        let label = "\(region.name ?? "") \(region.sourceType ?? "") \(region.dominantType)".lowercased()
        return label.contains("manual") || label.contains("adjusted")
    }

    private static func preferredRegionCellType(_ region: RegionROI) -> String {
        for candidate in [region.originalSourceType, region.sourceType, region.dominantType] {
            let name = (candidate ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if !name.isEmpty, name != "Unassigned", name != "Ambiguous" {
                return name
            }
        }
        return region.sourceType ?? region.dominantType
    }

    private static func regionMatchesSelectedCellTypes(_ region: RegionROI, selectedCellTypes: Set<String>) -> Bool {
        if selectedCellTypes.contains(preferredRegionCellType(region)) {
            return true
        }
        let countedTypes = Set(region.countsByType.filter { $0.value > 0 }.keys)
        return !countedTypes.isDisjoint(with: selectedCellTypes)
    }

    static func writeCustomizedRegionDisplayOutputs(
        result: RegionAnalysisResult,
        outputFolder: URL,
        assignments: [CellTypeAssignment],
        selectedRegionIDs: Set<Int>,
        selectedCellTypes: Set<String>,
        cellTypeMask: UInt16Raster? = nil,
        cellTypeIDByName: [String: UInt16] = [:],
        overlayImage: NSImage? = nil
    ) throws -> RegionDisplaySaveSummary {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let rootDir = sectionURL("integrated_region_analysis", outputFolder: outputFolder)
        let originalDir = rootDir.appendingPathComponent("01_original_unmodified")
        let customizedDir = rootDir.appendingPathComponent("02_customized_display")
        try FileManager.default.createDirectory(at: originalDir, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: customizedDir, withIntermediateDirectories: true)
        removeGeneratedFiles(in: originalDir) { name in
            name.hasPrefix("original_unmodified__")
        }
        removeGeneratedFiles(in: customizedDir) { name in
            name.hasPrefix("customized_display__")
        }

        let allRegionIDs = Set(result.regions.map(\.id))
        let allCellTypes = Set(assignments.map(\.assignedType).filter { $0 != "Unassigned" && $0 != "Ambiguous" })
        let resolvedRegionIDs = selectedRegionIDs.intersection(allRegionIDs).isEmpty
            ? allRegionIDs
            : selectedRegionIDs.intersection(allRegionIDs)
        let resolvedCellTypes = selectedCellTypes.intersection(allCellTypes).isEmpty
            ? allCellTypes
            : selectedCellTypes.intersection(allCellTypes)
        guard !resolvedRegionIDs.isEmpty else {
            throw SpatialScopeError.message("Select at least one region boundary to save.")
        }
        guard !resolvedCellTypes.isEmpty else {
            throw SpatialScopeError.message("Select at least one cell type to display.")
        }

        let selectedRegions = result.regions
            .filter { region in
                resolvedRegionIDs.contains(region.id)
                    && regionMatchesSelectedCellTypes(region, selectedCellTypes: resolvedCellTypes)
            }
            .sorted { $0.id < $1.id }
        guard !selectedRegions.isEmpty else {
            throw SpatialScopeError.message("No selected boundary matches the selected cell type.")
        }
        let selectedAssignments = assignments.filter { resolvedCellTypes.contains($0.assignedType) }
        let selectedMaskLabels = selectedRegions.map { $0.sourceType ?? $0.dominantType }
        let selectedCellTypeLabels = Array(resolvedCellTypes).sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        let originalExport = originalRegionsForCustomizedDisplay(
            selectedRegions: selectedRegions,
            allRegions: result.regions
        )
        let originalRegions = originalExport.regions
        let originalMaskLabels = originalRegions.map { $0.sourceType ?? $0.dominantType }

        let resolvedOverlayImage = overlayImage ?? loadImage(outputFolder: outputFolder, section: "overlay", name: "overlay.png")
        let selectedCellTypeIDByName = cellTypeIDByName.filter { resolvedCellTypes.contains($0.key) }
        let selectedMaskIDs = Set(selectedCellTypeIDByName.values)
        let selectedCellTypeMask = cellTypeMask.map { mask in
            UInt16Raster(
                width: mask.width,
                height: mask.height,
                values: mask.values.map { selectedMaskIDs.contains($0) ? $0 : 0 }
            )
        }
        let customizedImage = RegionAnalyzer.renderRegionComparisonMap(
            overlayImage: resolvedOverlayImage,
            assignments: selectedAssignments,
            regions: selectedRegions,
            width: result.width,
            height: result.height,
            parameters: result.parameters,
            cellTypeMask: selectedCellTypeMask,
            cellTypeIDByName: selectedCellTypeIDByName,
            title: "Customized display"
        )
        let originalImage = RegionAnalyzer.renderRegionComparisonMap(
            overlayImage: resolvedOverlayImage,
            assignments: selectedAssignments,
            regions: originalRegions,
            width: result.width,
            height: result.height,
            parameters: result.parameters,
            cellTypeMask: selectedCellTypeMask,
            cellTypeIDByName: selectedCellTypeIDByName,
            title: "Original unmodified display"
        )

        let customizedBase = "customized_display__\(shortStableHash("customized|\(selectedMaskLabels.joined(separator: "|"))|\(selectedCellTypeLabels.joined(separator: "|"))"))"
        let originalBase = "original_unmodified__\(shortStableHash("original|\(originalMaskLabels.joined(separator: "|"))|\(selectedCellTypeLabels.joined(separator: "|"))"))"
        let customizedFiles = try writeRegionDisplayArtifactSet(
            directory: customizedDir,
            baseName: customizedBase,
            image: customizedImage,
            workflow: "customized_display",
            title: "Customized display",
            requestedMasks: selectedMaskLabels,
            exportedMasks: selectedMaskLabels,
            selectedCellTypes: selectedCellTypeLabels,
            extraMetadata: ["export_folder_type": ["customized_display"]]
        )
        let originalFiles = try writeRegionDisplayArtifactSet(
            directory: originalDir,
            baseName: originalBase,
            image: originalImage,
            workflow: "original_unmodified",
            title: "Original unmodified display",
            requestedMasks: selectedMaskLabels,
            exportedMasks: originalMaskLabels,
            selectedCellTypes: selectedCellTypeLabels,
            extraMetadata: [
                "export_folder_type": ["original_unmodified"],
                "customized_request_masks": selectedMaskLabels,
                "skipped_requested_masks_without_original_counterpart": originalExport.skippedMaskLabels
            ]
        )

        let manifest = NativeAnalysisManifestPayload(
            sectionKey: "integrated_region_analysis",
            sectionTitle: "Adjusted Region Analysis",
            nativeEngineActive: true,
            status: "complete",
            message: "Customized region display and original unmodified export complete.",
            resultCount: selectedRegions.count,
            outputFiles: customizedFiles.map { "02_customized_display/\($0.lastPathComponent)" }
                + originalFiles.map { "01_original_unmodified/\($0.lastPathComponent)" },
            timestamp: ISO8601DateFormatter().string(from: Date())
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(manifest).write(to: rootDir.appendingPathComponent("analysis_run_manifest.json"))

        return RegionDisplaySaveSummary(
            customizedDirectory: customizedDir,
            originalDirectory: originalDir,
            customizedFiles: customizedFiles,
            originalFiles: originalFiles
        )
    }

    private static func streamlitCellDistributionExporterURL() -> URL? {
        if let bundledURL = Bundle.main.url(
            forResource: "cell_distribution_streamlit_export",
            withExtension: "py",
            subdirectory: "CellDistributionRuntime"
        ) {
            return bundledURL
        }
        let sourceURL = URL(fileURLWithPath: #filePath)
        let projectRoot = sourceURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let helperURL = projectRoot.appendingPathComponent("script/cell_distribution_streamlit_export.py")
        return FileManager.default.fileExists(atPath: helperURL.path) ? helperURL : nil
    }

    private static func bundledCellDistributionExecutableURL() -> URL? {
        #if arch(arm64)
        let architecture = "arm64"
        #elseif arch(x86_64)
        let architecture = "x86_64"
        #else
        return nil
        #endif

        guard let resourceURL = Bundle.main.resourceURL else { return nil }
        let executableURL = resourceURL
            .appendingPathComponent("CellDistributionRuntime", isDirectory: true)
            .appendingPathComponent(architecture, isDirectory: true)
            .appendingPathComponent("cell_distribution_exporter")
        return FileManager.default.isExecutableFile(atPath: executableURL.path) ? executableURL : nil
    }

    static func loadCellDistributionBoundaryChoices(outputFolder: URL) -> [CellDistributionBoundaryChoice] {
        let regionDir = sectionURL("region_analysis", outputFolder: outputFolder)
        let registryURL = regionDir.appendingPathComponent("boundary_mask_registry.json")
        guard let data = try? Data(contentsOf: registryURL),
              let registry = try? JSONDecoder().decode(BoundaryMaskRegistryPayload.self, from: data) else {
            return []
        }

        var choices: [CellDistributionBoundaryChoice] = []
        var seen = Set<String>()
        for entry in registry.entries {
            let displayName = entry.displayName.trimmingCharacters(in: .whitespacesAndNewlines)
            let maskKey = entry.maskKey.trimmingCharacters(in: .whitespacesAndNewlines)
            let groupName = entry.groupName.trimmingCharacters(in: .whitespacesAndNewlines)
            let label = !displayName.isEmpty ? displayName : (!maskKey.isEmpty ? maskKey : groupName)
            guard !label.isEmpty else { continue }
            let maskURL = urlForRegistryMask(entry.maskPath, relativeTo: regionDir)
            guard FileManager.default.fileExists(atPath: maskURL.path) else { continue }
            let dedupeKey = "\(label.lowercased())|\(maskURL.path)"
            guard !seen.contains(dedupeKey) else { continue }
            seen.insert(dedupeKey)
            choices.append(
                CellDistributionBoundaryChoice(
                    id: choices.count + 1,
                    label: label,
                    maskPath: maskURL.path,
                    source: entry.source,
                    groupName: groupName,
                    maskKey: maskKey
                )
            )
        }
        return choices
    }

    private static func streamlitPythonExecutableURL() -> URL? {
        var candidatePaths: [String] = []
        if let configuredPath = ProcessInfo.processInfo.environment["SPATIALSCOPE_PYTHON"],
           !configuredPath.isEmpty {
            candidatePaths.append(configuredPath)
        }
        candidatePaths += [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3"
        ]
        let candidates = candidatePaths.map(URL.init(fileURLWithPath:))
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0.path) }
    }

    private static func runStreamlitCellDistributionExporterIfAvailable(
        outputFolder: URL,
        mode: CellDistributionOutputMode,
        selectedCellTypes: [String],
        selectedBoundaryLabels: [String],
        selectedClusterLabels: [String],
        selectedBoundaryMaskPaths: [String] = [],
        bandWidthUm: Double = 10.0
    ) throws {
        let executableURL: URL
        var arguments: [String]
        if let bundledURL = bundledCellDistributionExecutableURL() {
            executableURL = bundledURL
            arguments = []
        } else if let helperURL = streamlitCellDistributionExporterURL(),
                  let pythonURL = streamlitPythonExecutableURL() {
            executableURL = pythonURL
            arguments = [helperURL.path]
        } else {
            return
        }

        let cliMode: String
        switch mode {
        case .regionMasks:
            cliMode = "region-masks"
        case .cellDensity:
            cliMode = "cell-density"
        case .regionMasksAndDensity:
            cliMode = "region-masks-and-density"
        case .cellClusterDistribution:
            cliMode = "cell-cluster-distribution"
        }

        arguments += [
            "--output-folder",
            outputFolder.path,
            "--mode",
            cliMode,
            "--band-width-um",
            "\(bandWidthUm)"
        ]
        for boundaryLabel in selectedBoundaryLabels {
            arguments.append("--boundary-label")
            arguments.append(boundaryLabel)
        }
        for maskPath in selectedBoundaryMaskPaths {
            arguments.append("--boundary-mask-path")
            arguments.append(maskPath)
        }
        for cellType in selectedCellTypes {
            arguments.append("--selected-celltype")
            arguments.append(cellType)
        }
        if mode == .cellClusterDistribution {
            for clusterLabel in selectedClusterLabels {
                arguments.append("--selected-cluster")
                arguments.append(clusterLabel)
            }
        }

        let process = Process()
        process.executableURL = executableURL
        process.arguments = arguments
        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe
        try process.run()
        process.waitUntilExit()

        guard process.terminationStatus == 0 else {
            let stderr = String(data: errorPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            let stdout = String(data: outputPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            let details = [stderr, stdout]
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
                .joined(separator: "\n")
            throw SpatialScopeError.message("Cell Distribution export failed: \(details)")
        }
    }

    static func runStreamlitCellDistributionExport(
        outputFolder: URL,
        mode: CellDistributionOutputMode,
        selectedBoundaryLabels: [String],
        selectedCellTypes: [String],
        selectedClusterLabels: [String],
        bandWidthUm: Double,
        selectedBoundaryMaskPaths: [String] = []
    ) throws {
        let hasBundledRuntime = bundledCellDistributionExecutableURL() != nil
        let hasDevelopmentRuntime = streamlitCellDistributionExporterURL() != nil
            && streamlitPythonExecutableURL() != nil
        guard hasBundledRuntime || hasDevelopmentRuntime else {
            throw SpatialScopeError.message("The Cell Distribution runtime is missing from this app build.")
        }
        try runStreamlitCellDistributionExporterIfAvailable(
            outputFolder: outputFolder,
            mode: mode,
            selectedCellTypes: selectedCellTypes,
            selectedBoundaryLabels: selectedBoundaryLabels,
            selectedClusterLabels: selectedClusterLabels,
            selectedBoundaryMaskPaths: selectedBoundaryMaskPaths,
            bandWidthUm: bandWidthUm
        )
    }

    static func writeDistanceAnalysisOutputs(
        result: DistanceAnalysisResult,
        regions: [RegionROI],
        outputFolder: URL
    ) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let distanceDir = sectionURL("distance_analysis", outputFolder: outputFolder)
        try ImageExportService.writePNG(result.image, to: distanceDir.appendingPathComponent("distance_map.png"), dpi: 300)
        try ImageExportService.writeAI(result.image, to: distanceDir.appendingPathComponent("distance_map.ai"), dpi: 300)
        try EditableSVGWriter.writeDistanceMapSVG(
            result: result,
            regions: regions,
            to: distanceDir.appendingPathComponent("distance_map.svg")
        )
        if !result.nearestDistances.isEmpty {
            try ImageExportService.writePNG(
                result.nearestHistogramImage,
                to: distanceDir.appendingPathComponent("nearest_neighbor_distances.png"),
                dpi: 300
            )
            try ImageExportService.writeAI(
                result.nearestHistogramImage,
                to: distanceDir.appendingPathComponent("nearest_neighbor_distances.ai"),
                dpi: 300
            )
            try ImageExportService.writeVectorSVG(
                result.nearestHistogramImage,
                title: "Nearest-neighbor distances",
                to: distanceDir.appendingPathComponent("nearest_neighbor_distances.svg")
            )
        }
        if !result.boundaryDistances.isEmpty {
            try ImageExportService.writePNG(
                result.boundaryHistogramImage,
                to: distanceDir.appendingPathComponent("cell_to_boundary_distances.png"),
                dpi: 300
            )
            try ImageExportService.writeAI(
                result.boundaryHistogramImage,
                to: distanceDir.appendingPathComponent("cell_to_boundary_distances.ai"),
                dpi: 300
            )
            try ImageExportService.writeVectorSVG(
                result.boundaryHistogramImage,
                title: "Cell-to-boundary distances",
                to: distanceDir.appendingPathComponent("cell_to_boundary_distances.svg")
            )
        }

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(result.nearestDistances).write(to: distanceDir.appendingPathComponent("nearest_neighbor_distances.json"))
        try encoder.encode(result.boundaryDistances).write(to: distanceDir.appendingPathComponent("cell_to_boundary_distances.json"))
        try encoder.encode(result.nearestTTests).write(to: distanceDir.appendingPathComponent("nearest_neighbor_ttests.json"))
        try encoder.encode(result.boundaryTTests).write(to: distanceDir.appendingPathComponent("cell_to_boundary_ttests.json"))
        try encoder.encode(result.summaries).write(to: distanceDir.appendingPathComponent("distance_summary.json"))

        var nearestRows = ["target_label,target_type,query_label,query_type,target_x_px,target_y_px,nearest_distance_px,dist_um,color_hex"]
        for row in result.nearestDistances {
            nearestRows.append([
                "\(row.nucleusID)",
                row.assignedType,
                row.nearestNucleusID.map(String.init) ?? "",
                row.nearestType ?? "",
                "\(row.centroidX)",
                "\(row.centroidY)",
                "\(row.nearestDistancePx)",
                "\(row.nearestDistanceUm)",
                row.colorHex
            ].map(csvField).joined(separator: ","))
        }
        try nearestRows.joined(separator: "\n").write(
            to: distanceDir.appendingPathComponent("nearest_neighbor_distances.csv"),
            atomically: true,
            encoding: .utf8
        )

        var boundaryRows = ["boundary_name,query_label,query_celltype,centroid_x_px,centroid_y_px,inside_region,dist_to_boundary_px,dist_to_boundary_um,color_hex"]
        for row in result.boundaryDistances {
            boundaryRows.append([
                row.boundaryName ?? "",
                "\(row.nucleusID)",
                row.assignedType,
                "\(row.centroidX)",
                "\(row.centroidY)",
                row.insideRegion.map { $0 ? "true" : "false" } ?? "",
                "\(row.distanceToBoundaryPx)",
                "\(row.distanceToBoundaryUm)",
                row.colorHex
            ].map(csvField).joined(separator: ","))
        }
        try boundaryRows.joined(separator: "\n").write(
            to: distanceDir.appendingPathComponent("cell_to_boundary_distances.csv"),
            atomically: true,
            encoding: .utf8
        )

        var nearestTTestRows = ["test,ref,cmp,n_pairs,t,p"]
        for row in result.nearestTTests {
            nearestTTestRows.append([
                row.test,
                row.ref,
                row.cmp,
                row.nPairs.map(String.init) ?? "",
                "\(row.t)",
                "\(row.p)"
            ].map(csvField).joined(separator: ","))
        }
        try nearestTTestRows.joined(separator: "\n").write(
            to: distanceDir.appendingPathComponent("nearest_neighbor_ttests.csv"),
            atomically: true,
            encoding: .utf8
        )

        var boundaryTTestRows = ["test,ref,cmp,n_ref,n_cmp,t,p"]
        for row in result.boundaryTTests {
            boundaryTTestRows.append([
                row.test,
                row.ref,
                row.cmp,
                row.nRef.map(String.init) ?? "",
                row.nCmp.map(String.init) ?? "",
                "\(row.t)",
                "\(row.p)"
            ].map(csvField).joined(separator: ","))
        }
        try boundaryTTestRows.joined(separator: "\n").write(
            to: distanceDir.appendingPathComponent("cell_to_boundary_ttests.csv"),
            atomically: true,
            encoding: .utf8
        )

        var summaryRows = ["metric,count,mean_um,median_um,min_um,max_um"]
        for row in result.summaries {
            summaryRows.append([
                row.metric,
                "\(row.count)",
                "\(row.meanUm)",
                "\(row.medianUm)",
                "\(row.minUm)",
                "\(row.maxUm)"
            ].map(csvField).joined(separator: ","))
        }
        try summaryRows.joined(separator: "\n").write(
            to: distanceDir.appendingPathComponent("distance_summary.csv"),
            atomically: true,
            encoding: .utf8
        )

        var parameterizedFiles: [String] = []
        if !result.nearestDistances.isEmpty {
            let nearestBase = safeName(
                "nearest_neighbor_distances__\(result.nearestTargetType ?? "target")__to__\(result.nearestQueryTypes.joined(separator: "__"))",
                fallback: "nearest_neighbor_distances"
            )
            parameterizedFiles += try writeDistanceFigureSet(
                directory: distanceDir,
                baseName: nearestBase,
                image: result.nearestHistogramImage,
                title: "Nearest-neighbor distances",
                csvRows: nearestRows,
                jsonData: encoder.encode(result.nearestDistances)
            ).map(\.lastPathComponent)
        }
        if !result.boundaryDistances.isEmpty {
            let boundaryBase = safeName(
                "dist_to_boundary__\(result.boundaryName ?? "boundary")__\(result.boundaryQueryTypes.joined(separator: "__"))__\(result.boundaryFilter?.rawValue ?? "all")",
                fallback: "dist_to_boundary"
            )
            parameterizedFiles += try writeDistanceFigureSet(
                directory: distanceDir,
                baseName: boundaryBase,
                image: result.boundaryHistogramImage,
                title: "Cell-to-boundary distances",
                csvRows: boundaryRows,
                jsonData: encoder.encode(result.boundaryDistances)
            ).map(\.lastPathComponent)
        }

        let manifest = NativeAnalysisManifestPayload(
            sectionKey: "distance_analysis",
            sectionTitle: "Distance Analysis",
            nativeEngineActive: true,
            status: "complete",
            message: "Native nearest-neighbor and cell-to-boundary distance analysis complete.",
            resultCount: result.measuredCellCount,
            outputFiles: [
                "distance_map.png",
                "distance_map.ai",
                "distance_map.svg",
                "nearest_neighbor_distances.png",
                "nearest_neighbor_distances.ai",
                "nearest_neighbor_distances.svg",
                "nearest_neighbor_distances.csv",
                "nearest_neighbor_distances.json",
                "nearest_neighbor_ttests.csv",
                "nearest_neighbor_ttests.json",
                "cell_to_boundary_distances.png",
                "cell_to_boundary_distances.ai",
                "cell_to_boundary_distances.svg",
                "cell_to_boundary_distances.csv",
                "cell_to_boundary_distances.json",
                "cell_to_boundary_ttests.csv",
                "cell_to_boundary_ttests.json",
                "distance_summary.csv",
                "distance_summary.json"
            ] + parameterizedFiles,
            timestamp: ISO8601DateFormatter().string(from: Date())
        )
        try encoder.encode(manifest).write(to: distanceDir.appendingPathComponent("analysis_run_manifest.json"))
    }

    private static func writeDistanceFigureSet(
        directory: URL,
        baseName: String,
        image: NSImage,
        title: String,
        csvRows: [String],
        jsonData: Data
    ) throws -> [URL] {
        let pngURL = directory.appendingPathComponent("\(baseName).png")
        let aiURL = directory.appendingPathComponent("\(baseName).ai")
        let svgURL = directory.appendingPathComponent("\(baseName).svg")
        let csvURL = directory.appendingPathComponent("\(baseName).csv")
        let jsonURL = directory.appendingPathComponent("\(baseName).json")
        try ImageExportService.writePNG(image, to: pngURL, dpi: 300)
        try ImageExportService.writeAI(image, to: aiURL, dpi: 300)
        try ImageExportService.writeVectorSVG(image, title: title, to: svgURL)
        try csvRows.joined(separator: "\n").write(to: csvURL, atomically: true, encoding: .utf8)
        try jsonData.write(to: jsonURL)
        return [pngURL, aiURL, svgURL, csvURL, jsonURL]
    }

    static func writeOverlayImages(
        result: OverlayRenderResult,
        outputFolder: URL,
        pixelSizeXUm: Double?
    ) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let overlayDir = sectionURL("overlay", outputFolder: outputFolder)
        try ImageExportService.writePNG(result.overlayImage, to: overlayDir.appendingPathComponent("overlay.png"), dpi: 300)
        try ImageExportService.writeAI(result.overlayImage, to: overlayDir.appendingPathComponent("overlay.ai"), dpi: 300)
        try EditableSVGWriter.writeOverlaySVG(
            baseImage: result.overlayBaseImage,
            channels: result.overlayChannels,
            pixelSizeXUm: pixelSizeXUm,
            to: overlayDir.appendingPathComponent("overlay.svg")
        )
        try ImageExportService.writePNG(result.splitImage, to: overlayDir.appendingPathComponent("split_channels.png"), dpi: 300)
        try ImageExportService.writeAI(result.splitImage, to: overlayDir.appendingPathComponent("split_channels.ai"), dpi: 300)
        try EditableSVGWriter.writeSplitChannelsSVG(
            tiles: result.splitTiles,
            to: overlayDir.appendingPathComponent("split_channels.svg")
        )
    }

    static func writeNucleiOutputs(result: NucleiSegmentationResult, outputFolder: URL) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let nucleiDir = sectionURL("nuclei", outputFolder: outputFolder)
        try ImageExportService.writePNG(result.image, to: nucleiDir.appendingPathComponent("nuclei_segmentation.png"), dpi: 300)
        try ImageExportService.writeAI(result.image, to: nucleiDir.appendingPathComponent("nuclei_segmentation.ai"), dpi: 300)
        try EditableSVGWriter.writeNucleiSegmentationSVG(
            result: result,
            to: nucleiDir.appendingPathComponent("nuclei_segmentation.svg")
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let paramsURL = nucleiDir.appendingPathComponent("nuclei_segmentation_parameters.json")
        try encoder.encode(result.params).write(to: paramsURL)
        if let labelMap = result.labelMap {
            try encoder.encode(labelMap).write(to: nucleiDir.appendingPathComponent("nuclei_label_map.json"))
        }

        var csv = "label,centroid_x_px,centroid_y_px,area_px,mean_intensity\n"
        for detection in result.detections {
            csv += "\(detection.id),\(detection.centroidX),\(detection.centroidY),\(detection.areaPx),\(detection.meanIntensity)\n"
        }
        try csv.write(to: nucleiDir.appendingPathComponent("nuclei_summary.csv"), atomically: true, encoding: .utf8)
    }

    static func writeNucleiScanResults(_ records: [NucleiScanRecord], outputFolder: URL) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let nucleiDir = sectionURL("nuclei", outputFolder: outputFolder)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(records).write(to: nucleiDir.appendingPathComponent("nuclei_parameter_scan_results.json"))

        var csv = "combo_index,stage,nuclei_count,min_diam_um,max_diam_um,tophat_radius_um,gauss_sigma_um,local_win_um,local_offset,h_maxima_um,seed_min_dist_um,watershed_compactness,post_resplit_mult\n"
        for record in records {
            let p = record.params
            csv += "\(record.comboIndex),\(record.stage),\(record.count),\(p.minDiamUm),\(p.maxDiamUm),\(p.tophatRadiusUm),\(p.gaussSigmaUm),\(p.localWinUm),\(p.localOffset),\(p.hMaximaUm),\(p.seedMinDistUm),\(p.watershedCompactness),\(p.postResplitMult)\n"
        }
        try csv.write(to: nucleiDir.appendingPathComponent("nuclei_parameter_scan_results.csv"), atomically: true, encoding: .utf8)

        if let plot = NucleiScanPlotRenderer.render(records: records) {
            try ImageExportService.writePNG(plot, to: nucleiDir.appendingPathComponent("nuclei_parameter_scan.png"), dpi: 300)
            try ImageExportService.writeAI(plot, to: nucleiDir.appendingPathComponent("nuclei_parameter_scan.ai"), dpi: 300)
        }
        try EditableSVGWriter.writeNucleiScanSVG(
            records: records,
            to: nucleiDir.appendingPathComponent("nuclei_parameter_scan.svg")
        )
    }

    static func writeNucleiScanMetadata(
        records: [NucleiScanRecord],
        outputFolder: URL,
        plannedCombinationCount: Int,
        totalSearchSpace: Int,
        searchIntervalCount: Int,
        estimatedSecondsAtStart: Double?,
        elapsedSeconds: Double,
        cpuAllocationPercent: Double,
        gpuAllocationPercent: Double,
        snapshot: ResourceSnapshot
    ) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let nucleiDir = sectionURL("nuclei", outputFolder: outputFolder)
        let best = records.max {
            if $0.count == $1.count { return $0.comboIndex > $1.comboIndex }
            return $0.count < $1.count
        }
        let stageCounts = Dictionary(grouping: records, by: \.stage)
            .mapValues(\.count)
        let fixedParams = records.first?.params
        let payload = NucleiScanMetadataPayload(
            strategy: "hierarchical coarse-to-refine search: broad 5-level parameter intervals, refine around high-count trends, fill remaining budget from global coarse grid",
            plannedCombinationCount: plannedCombinationCount,
            actualCombinationCount: records.count,
            totalSearchSpace: totalSearchSpace,
            searchIntervalCount: searchIntervalCount,
            stageCounts: stageCounts,
            fixedMinDiamUm: fixedParams?.minDiamUm,
            fixedMaxDiamUm: fixedParams?.maxDiamUm,
            bestComboIndex: best?.comboIndex,
            bestNucleiCount: best?.count,
            estimatedSecondsAtStart: estimatedSecondsAtStart,
            elapsedSeconds: elapsedSeconds,
            secondsPerCombination: records.isEmpty ? nil : elapsedSeconds / Double(records.count),
            cpuAllocationPercent: cpuAllocationPercent,
            effectiveCPUWorkerCount: effectiveCPUWorkerCount(
                activeCPUCoreCount: snapshot.activeCPUCoreCount,
                cpuAllocationPercent: cpuAllocationPercent
            ),
            gpuAllocationPercent: snapshot.gpuCount > 0 ? gpuAllocationPercent : 0,
            gpuCount: snapshot.gpuCount,
            timestamp: ISO8601DateFormatter().string(from: Date())
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(payload).write(to: nucleiDir.appendingPathComponent("nuclei_parameter_scan_metadata.json"))
    }

    static func writeStagedAnalysisManifest(
        outputFolder: URL,
        sectionKey: String,
        sectionTitle: String,
        message: String,
        parameters: [String: String],
        cpuAllocationPercent: Double,
        gpuAllocationPercent: Double,
        snapshot: ResourceSnapshot
    ) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let sectionDir = sectionURL(sectionKey, outputFolder: outputFolder)
        let payload = StagedAnalysisManifestPayload(
            sectionKey: sectionKey,
            sectionTitle: sectionTitle,
            nativeEngineActive: false,
            status: "staged",
            message: message,
            parameters: parameters,
            cpuAllocationPercent: cpuAllocationPercent,
            effectiveCPUWorkerCount: effectiveCPUWorkerCount(
                activeCPUCoreCount: snapshot.activeCPUCoreCount,
                cpuAllocationPercent: cpuAllocationPercent
            ),
            gpuAllocationPercent: snapshot.gpuCount > 0 ? gpuAllocationPercent : 0,
            gpuCount: snapshot.gpuCount,
            timestamp: ISO8601DateFormatter().string(from: Date())
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(payload).write(to: sectionDir.appendingPathComponent("analysis_run_manifest.json"))
    }

    static func writeResourceMetadata(
        outputFolder: URL,
        section: String,
        cpuAllocationPercent: Double,
        gpuAllocationPercent: Double,
        snapshot: ResourceSnapshot
    ) throws {
        try ensureSectionDirectories(outputFolder: outputFolder)
        let sectionDir = sectionURL(section, outputFolder: outputFolder)
        let payload = ResourceMetadataPayload(
            cpuCoreCount: snapshot.cpuCoreCount,
            activeCPUCoreCount: snapshot.activeCPUCoreCount,
            gpuCount: snapshot.gpuCount,
            gpuNames: snapshot.gpuNames,
            cpuAllocationPercent: cpuAllocationPercent,
            effectiveCPUWorkerCount: effectiveCPUWorkerCount(
                activeCPUCoreCount: snapshot.activeCPUCoreCount,
                cpuAllocationPercent: cpuAllocationPercent
            ),
            gpuAllocationPercent: snapshot.gpuCount > 0 ? gpuAllocationPercent : 0,
            cpuUsagePercentAtWrite: snapshot.cpuUsagePercent,
            gpuUsagePercentAtWrite: snapshot.gpuUsagePercent,
            timestamp: ISO8601DateFormatter().string(from: snapshot.timestamp)
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(payload).write(to: sectionDir.appendingPathComponent("resource_metadata.json"))
    }

    static func loadConfiguration(outputFolder: URL) -> LoadedAppConfiguration? {
        let configURL = sectionURL("config", outputFolder: outputFolder).appendingPathComponent("pipeline_config.json")
        guard let data = try? Data(contentsOf: configURL),
              let payload = try? JSONDecoder().decode(PipelineConfigPayload.self, from: data) else {
            return nil
        }
        let overlaySet = Set(payload.overlayChannels)
        let channels = payload.channels.map { channel in
            ChannelConfig(
                fileName: channel.file,
                marker: channel.channel,
                colorHex: channel.colorHex,
                overlayEnabled: overlaySet.isEmpty || overlaySet.contains(channel.channel)
            )
        }
        let pixelSize: (Double, Double)? = payload.pixelSizeUm.count >= 2
            ? (payload.pixelSizeUm[0], payload.pixelSizeUm[1])
            : nil
        let figureSizeUm: (Double, Double)? = {
            if let values = payload.figureSizeUm, values.count >= 2 {
                return (values[0], values[1])
            }
            return nil
        }()
        let figureSizePx: (Int, Int)? = {
            if let values = payload.figureSizePx, values.count >= 2 {
                return (values[0], values[1])
            }
            return nil
        }()
        return LoadedAppConfiguration(
            imageID: payload.imageID,
            inputFolder: URL(fileURLWithPath: payload.folder),
            outputFolder: URL(fileURLWithPath: payload.saveDir),
            channels: channels,
            whiteChannelName: payload.whiteChannel,
            whiteWeight: payload.whiteWeight,
            pixelSize: pixelSize,
            figureSizeUm: figureSizeUm,
            figureSizePx: figureSizePx,
            nucleusChannelName: payload.nucleusChannel,
            nucleiRunMode: payload.nucleiRunMode.flatMap(NucleiRunMode.init(rawValue:)),
            nucleiParameters: payload.nucleiParameters,
            nucleiScanCombinationBudget: payload.nucleiScanCombinationBudget,
            assignmentRunMode: payload.assignmentRunMode.flatMap(AssignmentRunMode.init(rawValue:)),
            assignmentParameters: payload.assignmentParameters,
            assignmentScanCombinationBudget: payload.assignmentScanCombinationBudget,
            assignmentScreeningBandCount: payload.assignmentScreeningBandCount,
            assignmentScreeningSubsetMode: payload.assignmentScreeningSubsetMode.flatMap(AssignmentScreeningSubsetMode.init(rawValue:)),
            cpuAllocationPercent: payload.cpuAllocationPercent,
            gpuAllocationPercent: payload.gpuAllocationPercent
        )
    }

    static func loadImage(outputFolder: URL, section: String, name: String) -> NSImage? {
        let url = sectionURL(section, outputFolder: outputFolder).appendingPathComponent(name)
        return NSImage(contentsOf: url)
    }

    static func loadCellTypeConfig(outputFolder: URL) -> [CellTypeDefinition]? {
        let url = sectionURL("celltype_definition", outputFolder: outputFolder)
            .appendingPathComponent("celltype_config.json")
        guard let data = try? Data(contentsOf: url),
              let payload = try? JSONDecoder().decode([CellTypePayload].self, from: data) else {
            return nil
        }
        return payload.map { cellType in
            CellTypeDefinition(
                name: cellType.name,
                colorHex: cellType.colorHex,
                allPositiveMarkers: cellType.allPositiveMarkers.joined(separator: ", "),
                allNegativeMarkers: cellType.allNegativeMarkers.joined(separator: ", "),
                anyPositiveGroups: cellType.anyPositiveGroups
                    .map { $0.joined(separator: ", ") }
                    .joined(separator: "\n")
            )
        }
    }

    static func loadCellTypeMask(outputFolder: URL) -> UInt16Raster? {
        let url = sectionURL("celltype_assignment", outputFolder: outputFolder)
            .appendingPathComponent("celltypes_mask_uint16.raw")
        return ImageExportService.loadUInt16RasterRaw(from: url)
    }

    static func loadCellTypeMaskIDMap(outputFolder: URL, assignments: [CellTypeAssignment] = []) -> [String: UInt16] {
        let assignmentDir = sectionURL("celltype_assignment", outputFolder: outputFolder)
        let idsURL = assignmentDir.appendingPathComponent("celltype_mask_ids.json")
        if let data = try? Data(contentsOf: idsURL),
           let rows = try? JSONDecoder().decode([CellTypeMaskIDPayload].self, from: data) {
            let mapped = Dictionary(uniqueKeysWithValues: rows.compactMap { row -> (String, UInt16)? in
                guard row.id > 0, !row.name.isEmpty else { return nil }
                return (row.name, UInt16(clamping: row.id))
            })
            if !mapped.isEmpty {
                return mapped
            }
        }

        if let config = loadCellTypeConfig(outputFolder: outputFolder) {
            let mapped = Dictionary(uniqueKeysWithValues: config.enumerated().map { index, cellType in
                (cellType.name, UInt16(index + 1))
            })
            if !mapped.isEmpty {
                return mapped
            }
        }

        let names = Array(Set(assignments.map(\.assignedType).filter {
            $0 != "Unassigned" && $0 != "Ambiguous"
        }))
        .sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        return Dictionary(uniqueKeysWithValues: names.enumerated().map { index, name in
            (name, UInt16(index + 1))
        })
    }

    static func loadNucleiSummary(outputFolder: URL) -> [NucleiDetection] {
        let url = sectionURL("nuclei", outputFolder: outputFolder).appendingPathComponent("nuclei_summary.csv")
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        return text
            .split(whereSeparator: \.isNewline)
            .dropFirst()
            .compactMap { line -> NucleiDetection? in
                let fields = line.split(separator: ",", omittingEmptySubsequences: false)
                guard fields.count >= 5,
                      let id = Int(fields[0]),
                      let centroidX = Double(fields[1]),
                      let centroidY = Double(fields[2]),
                      let areaPx = Int(fields[3]),
                      let meanIntensity = Double(fields[4]) else {
                    return nil
                }
                return NucleiDetection(
                    id: id,
                    centroidX: centroidX,
                    centroidY: centroidY,
                    areaPx: areaPx,
                    meanIntensity: meanIntensity
                )
            }
    }

    static func loadNucleiLabelMap(outputFolder: URL) -> NucleiLabelMap? {
        let url = sectionURL("nuclei", outputFolder: outputFolder).appendingPathComponent("nuclei_label_map.json")
        guard let data = try? Data(contentsOf: url),
              let labelMap = try? JSONDecoder().decode(NucleiLabelMap.self, from: data),
              labelMap.width > 0,
              labelMap.height > 0,
              labelMap.labels.count == labelMap.width * labelMap.height else {
            return nil
        }
        return labelMap
    }

    static func loadCellTypeAssignmentResult(outputFolder: URL) -> CellTypeAssignmentResult? {
        let assignmentDir = sectionURL("celltype_assignment", outputFolder: outputFolder)
        let assignmentsURL = assignmentDir.appendingPathComponent("celltype_assignments.json")
        let countsURL = assignmentDir.appendingPathComponent("celltype_assignment_counts.json")
        let paramsURL = assignmentDir.appendingPathComponent("celltype_assignment_parameters.json")
        guard let assignmentsData = try? Data(contentsOf: assignmentsURL),
              let assignments = try? JSONDecoder().decode([CellTypeAssignment].self, from: assignmentsData),
              let mapImage = NSImage(contentsOf: assignmentDir.appendingPathComponent("celltype_assignment_map.png")) else {
            return nil
        }
        let counts = (try? Data(contentsOf: countsURL))
            .flatMap { try? JSONDecoder().decode([CellTypeCount].self, from: $0) }
            ?? []
        let params = (try? Data(contentsOf: paramsURL))
            .flatMap { try? JSONDecoder().decode(AssignmentParameters.self, from: $0) }
            ?? AssignmentParameters()
        let statsImage = NSImage(contentsOf: assignmentDir.appendingPathComponent("celltype_assignment_counts.png"))
            ?? NSImage(size: NSSize(width: 1, height: 1))
        let cellTypeIDByName = loadCellTypeMaskIDMap(outputFolder: outputFolder, assignments: assignments)
        let loadedCellTypeMask = loadCellTypeMask(outputFolder: outputFolder)
        let imageCellTypeMask = makeAssignmentCellTypeMaskFromRenderedMap(
            image: mapImage,
            assignments: assignments,
            counts: counts,
            cellTypeIDByName: cellTypeIDByName
        )
        let imageCanvas = (
            width: max(1, mapImage.representations.first?.pixelsWide ?? Int(round(mapImage.size.width))),
            height: max(1, mapImage.representations.first?.pixelsHigh ?? Int(round(mapImage.size.height)))
        )
        let canvas = loadedCellTypeMask.map { (width: $0.width, height: $0.height) }
            ?? imageCellTypeMask.map { (width: $0.width, height: $0.height) }
            ?? imageCanvas
        let cellTypeMask = loadedCellTypeMask ?? imageCellTypeMask ?? makeAssignmentCellTypeMask(
            assignments: assignments,
            cellTypeIDByName: cellTypeIDByName,
            width: canvas.width,
            height: canvas.height
        )
        return CellTypeAssignmentResult(
            assignments: assignments,
            counts: counts,
            parameters: params,
            image: mapImage,
            statsImage: statsImage,
            width: canvas.width,
            height: canvas.height,
            cellTypeMask: cellTypeMask,
            cellTypeIDByName: cellTypeIDByName
        )
    }

    static func loadNeighborhoodAnalysisResult(outputFolder: URL) -> NeighborhoodAnalysisResult? {
        let neighborhoodDir = sectionURL("neighborhood_analysis", outputFolder: outputFolder)
        let tilesURL = neighborhoodDir.appendingPathComponent("neighborhood_tiles.json")
        let countsURL = neighborhoodDir.appendingPathComponent("neighborhood_dominant_counts.json")
        let clusterCountsURL = neighborhoodDir.appendingPathComponent("neighborhood_cluster_summary.json")
        let paramsURL = neighborhoodDir.appendingPathComponent("neighborhood_parameters.json")
        guard let tilesData = try? Data(contentsOf: tilesURL),
              let tiles = try? JSONDecoder().decode([NeighborhoodTile].self, from: tilesData),
              let mapImage = NSImage(contentsOf: neighborhoodDir.appendingPathComponent("neighborhood_map.png")) else {
            return nil
        }
        let counts = (try? Data(contentsOf: countsURL))
            .flatMap { try? JSONDecoder().decode([NeighborhoodTypeCount].self, from: $0) }
            ?? []
        let clusterCounts = (try? Data(contentsOf: clusterCountsURL))
            .flatMap { try? JSONDecoder().decode([NeighborhoodClusterCount].self, from: $0) }
            ?? NeighborhoodAnalyzer.makeClusterCounts(tiles: tiles)
        let params = (try? Data(contentsOf: paramsURL))
            .flatMap { try? JSONDecoder().decode(NeighborhoodParametersPayload.self, from: $0) }
            ?? NeighborhoodParametersPayload(gridSizeUm: 20, gridSizePx: 20)
        let clusterKeyImage = NSImage(contentsOf: neighborhoodDir.appendingPathComponent("neighborhood_cluster_key.png"))
            ?? NeighborhoodAnalyzer.renderClusterKeyImage(counts: clusterCounts)
        let statsImage = NSImage(contentsOf: neighborhoodDir.appendingPathComponent("neighborhood_cluster_summary.png"))
            ?? NSImage(contentsOf: neighborhoodDir.appendingPathComponent("neighborhood_dominant_counts.png"))
            ?? NSImage(size: NSSize(width: 1, height: 1))
        let canvas = inferredCanvasSize(tiles: tiles)
        return NeighborhoodAnalysisResult(
            tiles: tiles,
            dominantCounts: counts,
            clusterCounts: clusterCounts,
            gridSizeUm: params.gridSizeUm,
            gridSizePx: params.gridSizePx,
            gridWidthPx: params.gridWidthPx ?? params.gridSizePx,
            gridHeightPx: params.gridHeightPx ?? params.gridSizePx,
            image: mapImage,
            clusterKeyImage: clusterKeyImage,
            statsImage: statsImage,
            width: canvas.width,
            height: canvas.height
        )
    }

    static func loadRegionAnalysisResult(outputFolder: URL) -> RegionAnalysisResult? {
        let regionDir = sectionURL("region_analysis", outputFolder: outputFolder)
        let regionsURL = regionDir.appendingPathComponent("regions.json")
        let countsURL = regionDir.appendingPathComponent("region_dominant_counts.json")
        let paramsURL = regionDir.appendingPathComponent("region_parameters.json")
        let decodedRegions = (try? Data(contentsOf: regionsURL))
            .flatMap { try? JSONDecoder().decode([RegionROI].self, from: $0) }
            ?? []
        let registryRegions = loadRegistryRegionROIs(
            regionDir: regionDir,
            existingRegions: decodedRegions
        )
        let regions = (decodedRegions + registryRegions).enumerated().map { index, region -> RegionROI in
            var copy = region
            copy.id = index + 1
            return copy
        }
        guard !regions.isEmpty else {
            return nil
        }
        let mapImage = NSImage(contentsOf: regionDir.appendingPathComponent("region_map.png"))
            ?? NSImage(size: NSSize(width: regions.map { $0.xPx + $0.widthPx }.max() ?? 1, height: regions.map { $0.yPx + $0.heightPx }.max() ?? 1))
        let counts = (try? Data(contentsOf: countsURL))
            .flatMap { try? JSONDecoder().decode([RegionTypeCount].self, from: $0) }
            ?? loadedRegionTypeCounts(regions)
        let params = (try? Data(contentsOf: paramsURL))
            .flatMap { try? JSONDecoder().decode(RegionParameters.self, from: $0) }
            ?? RegionParameters()
        let statsImage = NSImage(contentsOf: regionDir.appendingPathComponent("region_dominant_counts.png"))
            ?? NSImage(size: NSSize(width: 1, height: 1))
        let mapSize = ImageExportService.pixelSize(for: mapImage)
        let regionCanvas = inferredCanvasSize(regions: regions)
        let maskCanvas = loadCellTypeMask(outputFolder: outputFolder).map {
            (width: $0.width, height: $0.height)
        }
        let canvas = maskCanvas ?? (
            width: max(regionCanvas.width, Int(mapSize.width)),
            height: max(regionCanvas.height, Int(mapSize.height))
        )
        return RegionAnalysisResult(
            regions: regions,
            dominantCounts: counts,
            parameters: params,
            image: mapImage,
            statsImage: statsImage,
            width: max(1, canvas.width),
            height: max(1, canvas.height)
        )
    }

    private static func loadedRegionTypeCounts(_ regions: [RegionROI]) -> [RegionTypeCount] {
        regions.map { region in
            RegionTypeCount(
                name: region.sourceType ?? region.dominantType,
                count: region.cellCount,
                colorHex: region.colorHex
            )
        }
        .sorted {
            if $0.count == $1.count {
                return $0.name.localizedStandardCompare($1.name) == .orderedAscending
            }
            return $0.count > $1.count
        }
    }

    private static func loadRegistryRegionROIs(
        regionDir: URL,
        existingRegions: [RegionROI]
    ) -> [RegionROI] {
        let registryURL = regionDir.appendingPathComponent("boundary_mask_registry.json")
        guard let data = try? Data(contentsOf: registryURL),
              let registry = try? JSONDecoder().decode(BoundaryMaskRegistryPayload.self, from: data) else {
            return []
        }

        let existingKeys = Set(
            existingRegions.flatMap { region in
                [region.name, region.sourceType, region.dominantType]
                    .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            }
        )
        var usedLabels = Set(
            existingRegions.compactMap { ($0.name ?? $0.sourceType ?? $0.dominantType).lowercased() }
        )
        var loaded: [RegionROI] = []

        for entry in registry.entries {
            let displayName = entry.displayName.trimmingCharacters(in: .whitespacesAndNewlines)
            let maskKey = entry.maskKey.trimmingCharacters(in: .whitespacesAndNewlines)
            let baseLabel = displayName.isEmpty ? (maskKey.isEmpty ? "Region" : maskKey) : displayName
            let lookupKeys = [displayName, maskKey]
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
                .filter { !$0.isEmpty }
            if lookupKeys.contains(where: { existingKeys.contains($0) }) {
                continue
            }

            let maskURL = urlForRegistryMask(entry.maskPath, relativeTo: regionDir)
            guard let loadedMask = loadUInt8MaskRuns(from: maskURL),
                  let box = RasterMask(
                    width: loadedMask.width,
                    height: loadedMask.height,
                    runs: loadedMask.runs
                  ).boundingBox() else {
                continue
            }

            let label = uniqueRegionRegistryLabel(baseLabel, source: entry.source, usedLabels: &usedLabels)
            let id = existingRegions.count + loaded.count + 1
            loaded.append(
                RegionROI(
                    id: id,
                    name: label,
                    sourceType: baseLabel,
                    xPx: Double(box.x),
                    yPx: Double(box.y),
                    widthPx: Double(box.width),
                    heightPx: Double(box.height),
                    centroidX: Double(box.x) + Double(box.width) / 2.0,
                    centroidY: Double(box.y) + Double(box.height) / 2.0,
                    areaPx: Double(loadedMask.areaPx),
                    areaUm2: Double(loadedMask.areaPx),
                    cellCount: 0,
                    assignedCellCount: 0,
                    dominantType: baseLabel,
                    colorHex: ColorPalette.color(at: id + 6),
                    countsByType: [:],
                    maskRuns: loadedMask.runs
                )
            )
        }
        return loaded
    }

    private static func urlForRegistryMask(_ path: String, relativeTo regionDir: URL) -> URL {
        if path.hasPrefix("/") {
            return URL(fileURLWithPath: path)
        }
        return regionDir.appendingPathComponent(path)
    }

    private static func uniqueRegionRegistryLabel(
        _ base: String,
        source: String,
        usedLabels: inout Set<String>
    ) -> String {
        let trimmedBase = base.trimmingCharacters(in: .whitespacesAndNewlines)
        let fallback = trimmedBase.isEmpty ? "Region" : trimmedBase
        let sourceSuffix = source.trimmingCharacters(in: .whitespacesAndNewlines)
        var candidate = fallback
        if usedLabels.contains(candidate.lowercased()), !sourceSuffix.isEmpty {
            candidate = "\(fallback) (\(sourceSuffix))"
        }
        var counter = 2
        while usedLabels.contains(candidate.lowercased()) {
            candidate = "\(fallback) \(counter)"
            counter += 1
        }
        usedLabels.insert(candidate.lowercased())
        return candidate
    }

    private static func loadUInt8MaskRuns(from url: URL) -> (width: Int, height: Int, runs: [MaskRun], areaPx: Int)? {
        guard let image = NSImage(contentsOf: url),
              let tiff = image.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff) else {
            return nil
        }

        let width = max(1, rep.pixelsWide)
        let height = max(1, rep.pixelsHigh)
        var runs: [MaskRun] = []
        var areaPx = 0

        for y in 0..<height {
            var runStart: Int?
            for x in 0..<width {
                let isOn = maskPixelIsOn(rep, x: x, y: y)
                if isOn {
                    areaPx += 1
                    if runStart == nil {
                        runStart = x
                    }
                } else if let start = runStart {
                    runs.append(MaskRun(y: y, xStart: start, xEnd: x))
                    runStart = nil
                }
            }
            if let start = runStart {
                runs.append(MaskRun(y: y, xStart: start, xEnd: width))
            }
        }

        guard !runs.isEmpty else { return nil }
        return (width: width, height: height, runs: runs, areaPx: areaPx)
    }

    private static func maskPixelIsOn(_ rep: NSBitmapImageRep, x: Int, y: Int) -> Bool {
        guard let color = rep.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) else {
            return false
        }
        return color.alphaComponent > 0.001
            && max(color.redComponent, max(color.greenComponent, color.blueComponent)) > 0.001
    }

    static func loadCellDistributionResult(outputFolder: URL) -> CellDistributionAnalysisResult? {
        let distributionDir = sectionURL("cell_distribution_analysis", outputFolder: outputFolder)
        return loadStreamlitCellDistributionResult(distributionDir: distributionDir)
    }

    static func loadLatestCellDistributionRegionMaskArtifacts(outputFolder: URL) throws -> CellDistributionRegionMaskArtifacts {
        let distributionDir = sectionURL("cell_distribution_analysis", outputFolder: outputFolder)
        let regionMasksDir = distributionDir.appendingPathComponent("01_region_masks")
        let inputsURL = firstFile(in: regionMasksDir, prefix: "region_bands__", suffix: "__inputs.json")
        guard let arraysURL = matchingRegionMaskArtifact(
            inputsURL: inputsURL,
            suffix: "__arrays.npz"
        ) ?? firstFile(in: regionMasksDir, prefix: "region_bands__", suffix: "__arrays.npz") else {
            throw SpatialScopeError.message("Generate Region masks before running Cell density.")
        }
        guard let bandMapURL = matchingRegionMaskArtifact(
            inputsURL: inputsURL,
            suffix: "__band_map.png"
        ) ?? firstFile(in: regionMasksDir, prefix: "region_bands__", suffix: "__band_map.png"),
              let bandMapImage = NSImage(contentsOf: bandMapURL) else {
            throw SpatialScopeError.message("The saved Region masks band map could not be loaded.")
        }
        let inputs = inputsURL
            .flatMap { try? Data(contentsOf: $0) }
            .flatMap { try? JSONDecoder().decode(RegionBandInputsCompatPayload.self, from: $0) }
        func clean(_ text: String?) -> String? {
            let trimmed = (text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
        let fallbackLabel = streamlitBoundaryLabel(from: bandMapURL)
        let boundaryLabel = clean(inputs?.boundaryLabel) ?? fallbackLabel
        let bandWidthUm = inputs?.bandWidthUm ?? streamlitBandWidth(from: bandMapURL) ?? 10.0
        let insideLabel = clean(inputs?.insideLabel) ?? "Inside \(boundaryLabel)"
        let outsideLabel = clean(inputs?.outsideLabel) ?? "Outside \(boundaryLabel)"
        let pixelValues = inputs?.pixelSizeUm ?? []
        let pixelX = pixelValues.first ?? 1.0
        let pixelY = pixelValues.count > 1 ? pixelValues[1] : pixelX
        let insideBandIndex = try NPZExportService.readInt16Array(named: "inside_band_index", from: arraysURL)
        let outsideBandIndex = try NPZExportService.readInt16Array(named: "outside_band_index", from: arraysURL)
        guard insideBandIndex.width == outsideBandIndex.width,
              insideBandIndex.height == outsideBandIndex.height else {
            throw SpatialScopeError.message("Saved Region masks band arrays have mismatched dimensions.")
        }
        return CellDistributionRegionMaskArtifacts(
            boundaryLabel: boundaryLabel,
            insideLabel: insideLabel,
            outsideLabel: outsideLabel,
            bandWidthUm: bandWidthUm,
            pixelSize: (pixelX, pixelY),
            arraysURL: arraysURL,
            bandMapImage: bandMapImage,
            insideBandIndex: insideBandIndex,
            outsideBandIndex: outsideBandIndex
        )
    }

    private static func loadStreamlitCellDistributionResult(distributionDir: URL) -> CellDistributionAnalysisResult? {
        let regionMasksDir = distributionDir.appendingPathComponent("01_region_masks")
        let densityDir = distributionDir.appendingPathComponent("02_cell_density")
        let clusterDir = distributionDir.appendingPathComponent("03_cell_cluster_distribution")
        let inputsURL = firstFile(in: regionMasksDir, prefix: "region_bands__", suffix: "__inputs.json")
        guard let bandMapURL = matchingRegionMaskArtifact(
            inputsURL: inputsURL,
            suffix: "__band_map.png"
        ) ?? firstFile(in: regionMasksDir, prefix: "region_bands__", suffix: "__band_map.png"),
              let mapImage = NSImage(contentsOf: bandMapURL) else {
            return nil
        }
        let densityImage = firstFile(in: densityDir, prefix: "cell_density__", suffix: "__plot.png")
            .flatMap { NSImage(contentsOf: $0) }
            ?? NSImage(size: NSSize(width: 1, height: 1))
        let clusterImage = firstFile(in: clusterDir, prefix: "cell_cluster_distribution__", suffix: "__heatmap.png")
            .flatMap { NSImage(contentsOf: $0) }
            ?? NSImage(size: NSSize(width: 1, height: 1))
        let size = ImageExportService.pixelSize(for: mapImage)
        let inputs = inputsURL
            .flatMap { try? Data(contentsOf: $0) }
            .flatMap { try? JSONDecoder().decode(RegionBandInputsCompatPayload.self, from: $0) }
        let savedBoundaryLabel = inputs?.boundaryLabel.trimmingCharacters(in: .whitespacesAndNewlines)
        let boundaryLabel = savedBoundaryLabel.flatMap { $0.isEmpty ? nil : $0 }
            ?? streamlitBoundaryLabel(from: bandMapURL)
        let bandWidthUm = inputs?.bandWidthUm ?? streamlitBandWidth(from: bandMapURL) ?? 10.0
        let regionCSV = firstFile(in: densityDir, prefix: "cell_density__", suffix: "__region.csv")
        let regionRows = regionCSV.flatMap { csvTable(from: $0) } ?? []
        let regionSummaries = streamlitRegionSummaries(
            rows: regionRows,
            boundaryLabel: boundaryLabel,
            width: Int(size.width),
            height: Int(size.height)
        )
        let longCSV = firstFile(in: densityDir, prefix: "cell_density__", suffix: "__long.csv")
        let bandMetrics = streamlitBandMetrics(
            rows: longCSV.flatMap { csvTable(from: $0) } ?? [],
            boundaryLabel: boundaryLabel
        )
        let clusterRegionCSV = firstFile(in: clusterDir, prefix: "cell_cluster_distribution__", suffix: "__cluster_region.csv")
        let clusterMetrics = streamlitClusterMetrics(rows: clusterRegionCSV.flatMap { csvTable(from: $0) } ?? [])
        let tilesCSV = firstFile(in: clusterDir, prefix: "cell_cluster_distribution__", suffix: "__tiles.csv")
        let tileClassifications = streamlitTileClassifications(rows: tilesCSV.flatMap { csvTable(from: $0) } ?? [])
        return CellDistributionAnalysisResult(
            regionSummaries: regionSummaries,
            typeSummaries: streamlitTypeSummaries(clusterMetrics: clusterMetrics),
            bandMetrics: bandMetrics,
            clusterMetrics: clusterMetrics,
            tileClassifications: tileClassifications,
            bandWidthUm: bandWidthUm,
            bandWidthPx: bandWidthUm,
            image: mapImage,
            densityImage: densityImage,
            clusterImage: clusterImage,
            width: max(1, Int(size.width)),
            height: max(1, Int(size.height))
        )
    }

    static func loadDistanceAnalysisResult(outputFolder: URL) -> DistanceAnalysisResult? {
        let distanceDir = sectionURL("distance_analysis", outputFolder: outputFolder)
        let nearestURL = distanceDir.appendingPathComponent("nearest_neighbor_distances.json")
        let boundaryURL = distanceDir.appendingPathComponent("cell_to_boundary_distances.json")
        let nearestTTestsURL = distanceDir.appendingPathComponent("nearest_neighbor_ttests.json")
        let boundaryTTestsURL = distanceDir.appendingPathComponent("cell_to_boundary_ttests.json")
        let summaryURL = distanceDir.appendingPathComponent("distance_summary.json")
        let nearestDistances = (try? Data(contentsOf: nearestURL))
            .flatMap { try? JSONDecoder().decode([NearestNeighborDistance].self, from: $0) }
            ?? []
        let boundaryDistances = (try? Data(contentsOf: boundaryURL))
            .flatMap { try? JSONDecoder().decode([BoundaryDistance].self, from: $0) }
            ?? []
        guard !nearestDistances.isEmpty || !boundaryDistances.isEmpty else {
            return nil
        }
        let nearestTTests = (try? Data(contentsOf: nearestTTestsURL))
            .flatMap { try? JSONDecoder().decode([DistanceTTest].self, from: $0) }
            ?? []
        let boundaryTTests = (try? Data(contentsOf: boundaryTTestsURL))
            .flatMap { try? JSONDecoder().decode([DistanceTTest].self, from: $0) }
            ?? []
        let summaries = (try? Data(contentsOf: summaryURL))
            .flatMap { try? JSONDecoder().decode([DistanceSummary].self, from: $0) }
            ?? []
        let nearestHistogram = NSImage(contentsOf: distanceDir.appendingPathComponent("nearest_neighbor_distances.png"))
            ?? NSImage(size: NSSize(width: 1, height: 1))
        let boundaryHistogram = NSImage(contentsOf: distanceDir.appendingPathComponent("cell_to_boundary_distances.png"))
            ?? NSImage(size: NSSize(width: 1, height: 1))
        let mapImage = NSImage(contentsOf: distanceDir.appendingPathComponent("distance_map.png"))
            ?? (!nearestDistances.isEmpty ? nearestHistogram : boundaryHistogram)
        let size = ImageExportService.pixelSize(for: mapImage)
        return DistanceAnalysisResult(
            nearestDistances: nearestDistances,
            boundaryDistances: boundaryDistances,
            nearestTTests: nearestTTests,
            boundaryTTests: boundaryTTests,
            summaries: summaries,
            image: mapImage,
            nearestHistogramImage: nearestHistogram,
            boundaryHistogramImage: boundaryHistogram,
            width: max(1, Int(size.width)),
            height: max(1, Int(size.height)),
            nearestTargetType: nearestDistances.first?.assignedType,
            nearestQueryTypes: Array(Set(nearestDistances.compactMap(\.nearestType))).sorted {
                $0.localizedStandardCompare($1) == .orderedAscending
            },
            boundaryName: boundaryDistances.first?.boundaryName,
            boundaryQueryTypes: Array(Set(boundaryDistances.map(\.assignedType))).sorted {
                $0.localizedStandardCompare($1) == .orderedAscending
            }
        )
    }

    static func listOutputFiles(outputFolder: URL) -> [OutputFileInfo] {
        guard let enumerator = FileManager.default.enumerator(
            at: outputFolder,
            includingPropertiesForKeys: [.isRegularFileKey, .fileSizeKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }

        var rows: [OutputFileInfo] = []
        for case let url as URL in enumerator {
            let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
            guard values?.isRegularFile == true else { continue }
            let relative = url.path.replacingOccurrences(of: outputFolder.path + "/", with: "")
            rows.append(
                OutputFileInfo(
                    name: url.lastPathComponent,
                    relativePath: relative,
                    sizeBytes: Int64(values?.fileSize ?? 0)
                )
            )
        }
        return rows.sorted { $0.relativePath.localizedStandardCompare($1.relativePath) == .orderedAscending }
    }

    private static func parseMarkerList(_ text: String) -> [String] {
        text.split { char in
            char == "," || char == ";" || char.isNewline
        }
        .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
    }

    private static func parseGroupList(_ text: String) -> [[String]] {
        text.split(whereSeparator: \.isNewline)
            .map { line in parseMarkerList(String(line)) }
            .filter { !$0.isEmpty }
    }

    private static func regionCompatibilityTables(
        result: RegionAnalysisResult,
        assignments: [CellTypeAssignment]
    ) -> (assignmentRows: [String], countRows: [String]) {
        var assignmentRows = [
            "label,celltype,centroid_x_px,centroid_y_px,boundary_type,inside_region,region"
        ]
        var counts: [String: Int] = [:]
        for region in result.regions {
            let boundaryType = region.sourceType ?? region.dominantType
            let insideLabel = "\(boundaryType)_region"
            let outsideLabel = "adjacent_region"
            let mask = RegionAnalyzer.mask(for: region, width: result.width, height: result.height)
            for cell in assignments {
                let inside = mask.contains(x: cell.centroidX, y: cell.centroidY)
                let regionLabel = inside ? insideLabel : outsideLabel
                assignmentRows.append([
                    "\(cell.nucleusID)",
                    cell.assignedType,
                    "\(cell.centroidX)",
                    "\(cell.centroidY)",
                    boundaryType,
                    inside ? "true" : "false",
                    regionLabel
                ].map(csvField).joined(separator: ","))
                counts["\(boundaryType)\u{1f}\(regionLabel)\u{1f}\(cell.assignedType)", default: 0] += 1
            }
        }

        var countRows = ["boundary_type,region,celltype,count"]
        for key in counts.keys.sorted() {
            let parts = key.split(separator: "\u{1f}", omittingEmptySubsequences: false).map(String.init)
            guard parts.count == 3 else { continue }
            countRows.append([
                parts[0],
                parts[1],
                parts[2],
                "\(counts[key] ?? 0)"
            ].map(csvField).joined(separator: ","))
        }
        return (assignmentRows, countRows)
    }

    private struct RGBMaskKey: Hashable {
        var r: UInt8
        var g: UInt8
        var b: UInt8

        var brightness: Int {
            Int(r) + Int(g) + Int(b)
        }
    }

    private static func makeAssignmentCellTypeMaskFromRenderedMap(
        image: NSImage,
        assignments: [CellTypeAssignment],
        counts: [CellTypeCount],
        cellTypeIDByName: [String: UInt16]
    ) -> UInt16Raster? {
        guard !cellTypeIDByName.isEmpty else { return nil }

        var colorHexByName: [String: String] = [:]
        for count in counts {
            colorHexByName[count.name] = count.colorHex
        }
        for assignment in assignments where assignment.assignedType != "Unassigned" && assignment.assignedType != "Ambiguous" {
            colorHexByName[assignment.assignedType] = assignment.colorHex
        }

        var idByColor: [RGBMaskKey: UInt16] = [:]
        for (name, typeID) in cellTypeIDByName {
            guard let hex = colorHexByName[name],
                  let color = NSColor(hex: hex)?.usingColorSpace(.sRGB) else {
                continue
            }
            let key = RGBMaskKey(
                r: UInt8(max(0, min(255, Int(round(color.redComponent * 255.0))))),
                g: UInt8(max(0, min(255, Int(round(color.greenComponent * 255.0))))),
                b: UInt8(max(0, min(255, Int(round(color.blueComponent * 255.0)))))
            )
            idByColor[key] = typeID
        }
        guard !idByColor.isEmpty else { return nil }

        var rect = NSRect(origin: .zero, size: image.size)
        guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
            return nil
        }
        let width = max(1, cgImage.width)
        let height = max(1, cgImage.height)
        var rgba = [UInt8](repeating: 0, count: width * height * 4)
        guard let context = CGContext(
            data: &rgba,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            return nil
        }
        context.interpolationQuality = .none
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))

        let colorCandidates = Array(idByColor)
        var values = [UInt16](repeating: 0, count: width * height)
        for index in 0..<(width * height) {
            let offset = index * 4
            let key = RGBMaskKey(r: rgba[offset], g: rgba[offset + 1], b: rgba[offset + 2])
            if key.brightness < 8 {
                continue
            }
            if let exactID = idByColor[key] {
                values[index] = exactID
                continue
            }

            var bestID: UInt16?
            var bestDistance = Int.max
            for (candidate, typeID) in colorCandidates {
                let dr = Int(key.r) - Int(candidate.r)
                let dg = Int(key.g) - Int(candidate.g)
                let db = Int(key.b) - Int(candidate.b)
                let distance = dr * dr + dg * dg + db * db
                if distance < bestDistance {
                    bestDistance = distance
                    bestID = typeID
                }
            }
            if let bestID, bestDistance <= 50_000 {
                values[index] = bestID
            }
        }

        guard values.contains(where: { $0 > 0 }) else { return nil }
        return UInt16Raster(width: width, height: height, values: values)
    }

    private static func assignmentCellTypeIDByName(
        result: CellTypeAssignmentResult,
        outputFolder: URL
    ) -> [String: UInt16] {
        if !result.cellTypeIDByName.isEmpty {
            return result.cellTypeIDByName
        }
        let fromConfig = loadCellTypeMaskIDMap(outputFolder: outputFolder, assignments: result.assignments)
        if !fromConfig.isEmpty {
            return fromConfig
        }
        let names = Array(Set(result.assignments.map(\.assignedType).filter {
            $0 != "Unassigned" && $0 != "Ambiguous"
        }))
        .sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        return Dictionary(uniqueKeysWithValues: names.enumerated().map { index, name in
            (name, UInt16(index + 1))
        })
    }

    private static func makeAssignmentCellTypeMask(
        assignments: [CellTypeAssignment],
        cellTypeIDByName: [String: UInt16],
        width: Int,
        height: Int
    ) -> UInt16Raster {
        var raster = UInt16Raster(
            width: max(1, width),
            height: max(1, height),
            values: [UInt16](repeating: 0, count: max(1, width) * max(1, height))
        )
        for assignment in assignments {
            guard assignment.assignedType != "Unassigned",
                  assignment.assignedType != "Ambiguous",
                  let typeID = cellTypeIDByName[assignment.assignedType] else {
                continue
            }
            if let points = assignment.cellBoundaryPoints, points.count >= 3 {
                raster.fillPolygon(points, value: typeID)
            } else {
                let radius = max(2.0, sqrt(Double(max(1, assignment.areaPx)) / Double.pi))
                raster.fillDisk(
                    centerX: assignment.centroidX,
                    centerY: assignment.centroidY,
                    radius: radius,
                    value: typeID
                )
            }
        }
        return raster
    }

    private static func removeGeneratedFiles(in directory: URL, where shouldRemove: (String) -> Bool) {
        guard let urls = try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        ) else {
            return
        }
        for url in urls {
            let values = try? url.resourceValues(forKeys: [.isRegularFileKey])
            guard values?.isRegularFile == true, shouldRemove(url.lastPathComponent) else { continue }
            try? FileManager.default.removeItem(at: url)
        }
    }

    private static func writeRegionDisplayArtifactSet(
        directory: URL,
        baseName: String,
        image: NSImage,
        workflow: String,
        title: String,
        requestedMasks: [String],
        exportedMasks: [String],
        selectedCellTypes: [String],
        extraMetadata: [String: [String]] = [:]
    ) throws -> [URL] {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let pngURL = directory.appendingPathComponent("\(baseName).png")
        let aiURL = directory.appendingPathComponent("\(baseName).ai")
        let svgURL = directory.appendingPathComponent("\(baseName).svg")
        let tiffURL = directory.appendingPathComponent("\(baseName).tiff")
        let jsonURL = directory.appendingPathComponent("\(baseName).json")
        try ImageExportService.writePNG(image, to: pngURL, dpi: 300)
        try ImageExportService.writeAI(image, to: aiURL, dpi: 300)
        try ImageExportService.writeVectorSVG(image, title: title, to: svgURL)
        try ImageExportService.writeTIFF(image, to: tiffURL)

        let metadata = RegionDisplayMetadataPayload(
            workflow: workflow,
            title: title,
            requestedMasks: requestedMasks,
            exportedMasks: exportedMasks,
            selectedCellTypes: selectedCellTypes,
            extra: extraMetadata,
            timestamp: ISO8601DateFormatter().string(from: Date())
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(metadata).write(to: jsonURL)
        return [pngURL, aiURL, svgURL, tiffURL, jsonURL]
    }

    private static func csvField(_ value: String) -> String {
        if value.contains(",") || value.contains("\"") || value.contains("\n") {
            return "\"\(value.replacingOccurrences(of: "\"", with: "\"\""))\""
        }
        return value
    }

    private static func firstFile(in directory: URL, prefix: String, suffix: String) -> URL? {
        guard let urls = try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        ) else {
            return nil
        }
        return urls
            .filter { url in
                let name = url.lastPathComponent
                let values = try? url.resourceValues(forKeys: [.isRegularFileKey])
                return values?.isRegularFile == true
                    && name.hasPrefix(prefix)
                    && name.hasSuffix(suffix)
            }
            .sorted { left, right in
                let leftDate = (try? left.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                let rightDate = (try? right.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                if leftDate != rightDate {
                    return leftDate > rightDate
                }
                return left.lastPathComponent.localizedStandardCompare(right.lastPathComponent) == .orderedAscending
            }
            .first
    }

    private static func matchingRegionMaskArtifact(inputsURL: URL?, suffix: String) -> URL? {
        guard let inputsURL else { return nil }
        let inputsSuffix = "__inputs.json"
        let fileName = inputsURL.lastPathComponent
        guard fileName.hasSuffix(inputsSuffix) else { return nil }
        let baseName = String(fileName.dropLast(inputsSuffix.count))
        let candidate = inputsURL.deletingLastPathComponent()
            .appendingPathComponent(baseName + suffix)
        return FileManager.default.fileExists(atPath: candidate.path) ? candidate : nil
    }

    private static func csvTable(from url: URL) -> [[String: String]] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        let lines = text
            .split(whereSeparator: \.isNewline)
            .map(String.init)
        guard let headerLine = lines.first else { return [] }
        let headers = parseCSVRow(headerLine)
        return lines.dropFirst().map { line in
            let values = parseCSVRow(line)
            var row: [String: String] = [:]
            for (index, header) in headers.enumerated() {
                row[header] = index < values.count ? values[index] : ""
            }
            return row
        }
    }

    private static func streamlitBoundaryLabel(from bandMapURL: URL) -> String {
        let name = bandMapURL.deletingPathExtension().lastPathComponent
        guard name.hasPrefix("region_bands__"),
              name.hasSuffix("__band_map") else {
            return "Selected boundary"
        }
        let prefixRemoved = String(name.dropFirst("region_bands__".count))
        let suffixRemoved = String(prefixRemoved.dropLast("__band_map".count))
        let core = suffixRemoved
        if let bandRange = core.range(of: "__", options: .backwards) {
            return String(core[..<bandRange.lowerBound]).replacingOccurrences(of: "_", with: " ")
        }
        return core.replacingOccurrences(of: "_", with: " ")
    }

    private static func streamlitBandWidth(from bandMapURL: URL) -> Double? {
        let name = bandMapURL.deletingPathExtension().lastPathComponent
        guard name.hasSuffix("__band_map") else { return nil }
        let suffixRemoved = String(name.dropLast("__band_map".count))
        guard let bandRange = suffixRemoved.range(of: "__", options: .backwards) else { return nil }
        let token = String(suffixRemoved[bandRange.upperBound...])
            .replacingOccurrences(of: "um", with: "")
            .replacingOccurrences(of: "p", with: ".")
        return Double(token)
    }

    private static func streamlitRegionSummaries(
        rows: [[String: String]],
        boundaryLabel: String,
        width: Int,
        height: Int
    ) -> [CellDistributionRegionSummary] {
        guard let row = rows.first else {
            return [
                CellDistributionRegionSummary(
                    regionID: 1,
                    xPx: 0,
                    yPx: 0,
                    widthPx: Double(width),
                    heightPx: Double(height),
                    dominantType: boundaryLabel,
                    colorHex: "#ffffff",
                    areaUm2: 0,
                    totalCells: 0,
                    assignedCells: 0,
                    densityCellsPerMm2: 0,
                    boundaryBandCells: 0,
                    coreCells: 0,
                    countsByType: [:]
                )
            ]
        }
        return [
            CellDistributionRegionSummary(
                regionID: 1,
                xPx: 0,
                yPx: 0,
                widthPx: Double(width),
                heightPx: Double(height),
                dominantType: row["boundary_label"] ?? boundaryLabel,
                colorHex: "#ffffff",
                areaUm2: Double(row["area_um2"] ?? "") ?? 0,
                totalCells: Int(row["total_cells"] ?? "") ?? 0,
                assignedCells: Int(row["assigned_cells"] ?? "") ?? 0,
                densityCellsPerMm2: Double(row["density_cells_per_mm2"] ?? "") ?? 0,
                boundaryBandCells: Int(row["boundary_band_cells"] ?? "") ?? 0,
                coreCells: Int(row["core_cells"] ?? "") ?? 0,
                countsByType: parseCountsByType(row["counts_by_type"] ?? "")
            )
        ]
    }

    private static func streamlitBandMetrics(
        rows: [[String: String]],
        boundaryLabel: String
    ) -> [CellDistributionBandMetric] {
        rows.map { row in
            CellDistributionBandMetric(
                regionID: 1,
                regionName: row["boundary_label"] ?? boundaryLabel,
                regionKey: row["region_key"] ?? "",
                side: row["side"] ?? "",
                bandIndex: Int(row["band_index"] ?? "") ?? 0,
                distLoUm: Double(row["dist_lo_um"] ?? "") ?? 0,
                distHiUm: Double(row["dist_hi_um"] ?? "") ?? 0,
                cellType: row["celltype"] ?? "",
                cellCount: Int(row["cell_count"] ?? "") ?? 0,
                areaPx: Int(row["area_px"] ?? "") ?? 0,
                areaUm2: Double(row["area_um2"] ?? "") ?? 0,
                densityCellsPerUm2: Double(row["density_cells_per_um2"] ?? "") ?? 0,
                densityCellsPerMm2: Double(row["density_cells_per_mm2"] ?? "") ?? 0
            )
        }
    }

    private static func streamlitClusterMetrics(rows: [[String: String]]) -> [CellDistributionClusterMetric] {
        let regionIDs = streamlitRegionIDs(rows: rows)
        return rows.map { row in
            let boundaryLabel = streamlitValue(row, "boundary_label", fallback: "Selected boundary")
            let regionKey = streamlitValue(row, "region_key", fallback: "")
            return CellDistributionClusterMetric(
                regionID: regionIDs[streamlitRegionKey(boundaryLabel: boundaryLabel, regionKey: regionKey)] ?? 1,
                regionName: boundaryLabel,
                regionKey: regionKey,
                clusterID: streamlitInt(row, "cluster_id"),
                clusterLabel: streamlitValue(row, "cluster_label", fallback: "Cluster"),
                occupiedTileCount: streamlitInt(row, "occupied_tile_count"),
                totalCellsInTiles: streamlitInt(row, "total_cells_in_tiles"),
                meanInsideFraction: streamlitDouble(row, "mean_inside_fraction")
            )
        }
        .sorted(by: clusterMetricSort)
    }

    private static func streamlitTileClassifications(rows: [[String: String]]) -> [CellDistributionTileClassification] {
        let regionIDs = streamlitRegionIDs(rows: rows)
        return rows.map { row in
            let boundaryLabel = streamlitValue(row, "boundary_label", fallback: "Selected boundary")
            let regionKey = streamlitValue(row, "region_key", fallback: "")
            let displayName = streamlitValue(
                row,
                "region",
                fallback: regionDisplayName(boundaryLabel: boundaryLabel, regionKey: regionKey)
            )
            return CellDistributionTileClassification(
                regionID: regionIDs[streamlitRegionKey(boundaryLabel: boundaryLabel, regionKey: regionKey)] ?? 1,
                regionName: boundaryLabel,
                regionKey: regionKey,
                regionDisplayName: displayName,
                tileRow: streamlitInt(row, "tile_row"),
                tileColumn: streamlitInt(row, "tile_col"),
                tileIndex: streamlitInt(row, "tile_index"),
                x0Px: streamlitInt(row, "x0_px"),
                x1Px: streamlitInt(row, "x1_px"),
                y0Px: streamlitInt(row, "y0_px"),
                y1Px: streamlitInt(row, "y1_px"),
                tileAreaPx: streamlitInt(row, "tile_area_px"),
                insidePx: streamlitInt(row, "inside_px"),
                insideFraction: streamlitDouble(row, "inside_fraction"),
                clusterID: streamlitInt(row, "cluster_id"),
                clusterKey: streamlitValue(row, "cluster_key", fallback: ""),
                clusterLabel: streamlitValue(row, "cluster_label", fallback: "Cluster"),
                cellCount: streamlitInt(row, "n_cells")
            )
        }
        .sorted {
            if $0.regionID != $1.regionID { return $0.regionID < $1.regionID }
            if $0.regionKey != $1.regionKey { return $0.regionKey < $1.regionKey }
            if $0.tileRow != $1.tileRow { return $0.tileRow < $1.tileRow }
            if $0.tileColumn != $1.tileColumn { return $0.tileColumn < $1.tileColumn }
            return $0.clusterID < $1.clusterID
        }
    }

    private static func streamlitTypeSummaries(clusterMetrics: [CellDistributionClusterMetric]) -> [CellDistributionTypeSummary] {
        Dictionary(grouping: clusterMetrics, by: \.clusterLabel)
            .map { label, rows in
                let total = rows.reduce(0) { $0 + $1.totalCellsInTiles }
                let presentRegions = Set(rows.filter { $0.totalCellsInTiles > 0 }.map { "\($0.regionID)|\($0.regionKey)" })
                return CellDistributionTypeSummary(
                    cellType: label,
                    colorHex: ColorPalette.color(at: abs(label.hashValue % 24)),
                    totalCount: total,
                    regionsPresent: presentRegions.count,
                    meanCountPerRegion: rows.isEmpty ? 0 : Double(total) / Double(rows.count),
                    maxRegionCount: rows.map(\.totalCellsInTiles).max() ?? 0
                )
            }
            .sorted {
                if $0.totalCount == $1.totalCount {
                    return $0.cellType.localizedStandardCompare($1.cellType) == .orderedAscending
                }
                return $0.totalCount > $1.totalCount
            }
    }

    private static func streamlitRegionIDs(rows: [[String: String]]) -> [String: Int] {
        var ids: [String: Int] = [:]
        for row in rows {
            let boundaryLabel = streamlitValue(row, "boundary_label", fallback: "Selected boundary")
            let regionKey = streamlitValue(row, "region_key", fallback: "")
            let key = streamlitRegionKey(boundaryLabel: boundaryLabel, regionKey: regionKey)
            if ids[key] == nil {
                ids[key] = ids.count + 1
            }
        }
        return ids
    }

    private static func streamlitRegionKey(boundaryLabel: String, regionKey: String) -> String {
        "\(boundaryLabel)\u{1f}\(regionKey)"
    }

    private static func streamlitValue(_ row: [String: String], _ key: String, fallback: String) -> String {
        let value = (row[key] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? fallback : value
    }

    private static func streamlitInt(_ row: [String: String], _ key: String) -> Int {
        Int((row[key] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
    }

    private static func streamlitDouble(_ row: [String: String], _ key: String) -> Double {
        Double((row[key] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
    }

    private static func parseCountsByType(_ text: String) -> [String: Int] {
        var counts: [String: Int] = [:]
        for pair in text.split(separator: ";") {
            let parts = pair.split(separator: "=", maxSplits: 1).map {
                String($0).trimmingCharacters(in: .whitespacesAndNewlines)
            }
            guard parts.count == 2, let count = Int(parts[1]) else { continue }
            counts[parts[0]] = count
        }
        return counts
    }

    private static func tableRows(fromCSVRows rows: [String]) -> [[String]] {
        rows.map(parseCSVRow)
    }

    private static func parseCSVRow(_ row: String) -> [String] {
        var fields: [String] = []
        var current = ""
        var isQuoted = false
        var index = row.startIndex
        while index < row.endIndex {
            let character = row[index]
            if character == "\"" {
                let nextIndex = row.index(after: index)
                if isQuoted, nextIndex < row.endIndex, row[nextIndex] == "\"" {
                    current.append("\"")
                    index = row.index(after: nextIndex)
                    continue
                }
                isQuoted.toggle()
            } else if character == ",", !isQuoted {
                fields.append(current)
                current = ""
            } else {
                current.append(character)
            }
            index = row.index(after: index)
        }
        fields.append(current)
        return fields
    }

    private static func safeName(_ text: String, fallback: String) -> String {
        let pattern = "[^0-9A-Za-z]+"
        let cleaned = text
            .replacingOccurrences(of: pattern, with: "_", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "_"))
        return cleaned.isEmpty ? fallback : cleaned
    }

    private static func bandToken(_ value: Double) -> String {
        let text = String(format: "%.4f", value)
            .replacingOccurrences(of: #"0+$"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"\.$"#, with: "", options: .regularExpression)
        return text.replacingOccurrences(of: ".", with: "p")
    }

    private static func shortStableHash(_ text: String) -> String {
        var hash: UInt64 = 0xcbf29ce484222325
        for byte in text.utf8 {
            hash ^= UInt64(byte)
            hash &*= 0x100000001b3
        }
        return String(format: "%016llx", hash).prefix(12).description
    }

    private static func clusterMetricSort(_ left: CellDistributionClusterMetric, _ right: CellDistributionClusterMetric) -> Bool {
        if left.regionName != right.regionName {
            return left.regionName.localizedStandardCompare(right.regionName) == .orderedAscending
        }
        if left.regionKey != right.regionKey { return left.regionKey < right.regionKey }
        if left.clusterID != right.clusterID { return left.clusterID < right.clusterID }
        return left.clusterLabel.localizedStandardCompare(right.clusterLabel) == .orderedAscending
    }

    private static func regionDisplayName(_ row: CellDistributionBandMetric) -> String {
        regionDisplayName(boundaryLabel: row.regionName, regionKey: row.regionKey)
    }

    private static func regionDisplayName(_ row: CellDistributionClusterMetric) -> String {
        regionDisplayName(boundaryLabel: row.regionName, regionKey: row.regionKey)
    }

    private static func regionDisplayName(boundaryLabel: String, regionKey: String) -> String {
        regionKey == "inside" ? "Inside \(boundaryLabel)" : "Outside \(boundaryLabel)"
    }

    private static func effectiveCPUWorkerCount(activeCPUCoreCount: Int, cpuAllocationPercent: Double) -> Int {
        let active = max(1, activeCPUCoreCount)
        let clamped = min(max(cpuAllocationPercent, 10), 100)
        let workers = Int((Double(active) * clamped / 100.0).rounded(.toNearestOrAwayFromZero))
        return min(active, max(1, workers))
    }

    private static func inferredCanvasSize(assignments: [CellTypeAssignment]) -> (width: Int, height: Int) {
        guard !assignments.isEmpty else { return (1, 1) }
        let maxX = assignments.map(\.centroidX).max() ?? 0
        let maxY = assignments.map(\.centroidY).max() ?? 0
        return (
            max(1, Int(ceil(maxX + 1))),
            max(1, Int(ceil(maxY + 1)))
        )
    }

    private static func inferredCanvasSize(regions: [RegionROI]) -> (width: Int, height: Int) {
        guard !regions.isEmpty else { return (1, 1) }
        let maxRegionX = regions.map { $0.xPx + $0.widthPx }.max() ?? 0
        let maxRegionY = regions.map { $0.yPx + $0.heightPx }.max() ?? 0
        let maxRunX = regions
            .compactMap(\.maskRuns)
            .flatMap { $0 }
            .map(\.xEnd)
            .max() ?? 0
        let maxRunY = regions
            .compactMap(\.maskRuns)
            .flatMap { $0 }
            .map { $0.y + 1 }
            .max() ?? 0
        return (
            max(1, Int(ceil(maxRegionX)), maxRunX),
            max(1, Int(ceil(maxRegionY)), maxRunY)
        )
    }

    private static func inferredCanvasSize(tiles: [NeighborhoodTile]) -> (width: Int, height: Int) {
        guard !tiles.isEmpty else { return (1, 1) }
        let maxX = tiles.map { $0.xPx + $0.widthPx }.max() ?? 0
        let maxY = tiles.map { $0.yPx + $0.heightPx }.max() ?? 0
        return (
            max(1, Int(ceil(maxX))),
            max(1, Int(ceil(maxY)))
        )
    }
}

private struct PipelineConfigPayload: Codable {
    var appName: String
    var inputMode: String
    var imageID: String
    var folder: String
    var saveDir: String
    var pixelSizeUm: [Double]
    var figureSizeUm: [Double]?
    var figureSizePx: [Int]?
    var channels: [PipelineChannelPayload]
    var overlayChannels: [String]
    var whiteChannel: String?
    var whiteWeight: Double
    var nucleusChannel: String?
    var nucleiRunMode: String?
    var nucleiParameters: NucleiParameters?
    var nucleiScanCombinationBudget: Int?
    var assignmentRunMode: String?
    var assignmentParameters: AssignmentParameters?
    var assignmentScanCombinationBudget: Int?
    var assignmentScreeningBandCount: Int?
    var assignmentScreeningSubsetMode: String?
    var cpuAllocationPercent: Double?
    var gpuAllocationPercent: Double?
}

private struct PipelineChannelPayload: Codable {
    var file: String
    var channel: String
    var colorHex: String
}

private struct CellTypePayload: Codable {
    var name: String
    var colorHex: String
    var mode: String
    var allPositiveMarkers: [String]
    var allNegativeMarkers: [String]
    var anyPositiveGroups: [[String]]
}

private struct CellTypeMaskIDPayload: Codable {
    var id: Int
    var name: String
}

private struct ResourceMetadataPayload: Codable {
    var cpuCoreCount: Int
    var activeCPUCoreCount: Int
    var gpuCount: Int
    var gpuNames: [String]
    var cpuAllocationPercent: Double
    var effectiveCPUWorkerCount: Int
    var gpuAllocationPercent: Double
    var cpuUsagePercentAtWrite: Double
    var gpuUsagePercentAtWrite: Double?
    var timestamp: String
}

private struct NucleiScanMetadataPayload: Codable {
    var strategy: String
    var plannedCombinationCount: Int
    var actualCombinationCount: Int
    var totalSearchSpace: Int
    var searchIntervalCount: Int
    var stageCounts: [String: Int]
    var fixedMinDiamUm: Double?
    var fixedMaxDiamUm: Double?
    var bestComboIndex: Int?
    var bestNucleiCount: Int?
    var estimatedSecondsAtStart: Double?
    var elapsedSeconds: Double
    var secondsPerCombination: Double?
    var cpuAllocationPercent: Double
    var effectiveCPUWorkerCount: Int
    var gpuAllocationPercent: Double
    var gpuCount: Int
    var timestamp: String
}

private struct StagedAnalysisManifestPayload: Codable {
    var sectionKey: String
    var sectionTitle: String
    var nativeEngineActive: Bool
    var status: String
    var message: String
    var parameters: [String: String]
    var cpuAllocationPercent: Double
    var effectiveCPUWorkerCount: Int
    var gpuAllocationPercent: Double
    var gpuCount: Int
    var timestamp: String
}

private struct NativeAnalysisManifestPayload: Codable {
    var sectionKey: String
    var sectionTitle: String
    var nativeEngineActive: Bool
    var status: String
    var message: String
    var resultCount: Int
    var outputFiles: [String]
    var timestamp: String
}

private struct RegionDisplayMetadataPayload: Codable {
    var workflow: String
    var title: String
    var requestedMasks: [String]
    var exportedMasks: [String]
    var selectedCellTypes: [String]
    var extra: [String: [String]]
    var timestamp: String

    enum CodingKeys: String, CodingKey {
        case workflow
        case title
        case requestedMasks = "requested_masks"
        case exportedMasks = "exported_masks"
        case selectedCellTypes = "selected_celltypes"
        case extra
        case timestamp
    }
}

private struct BoundaryMaskRegistryPayload: Codable {
    var entries: [BoundaryMaskRegistryEntryPayload]
}

private struct BoundaryMaskRegistryEntryPayload: Codable {
    var maskPath: String
    var displayName: String
    var source: String
    var groupName: String
    var maskKey: String

    enum CodingKeys: String, CodingKey {
        case maskPath = "mask_path"
        case displayName = "display_name"
        case source
        case groupName = "group_name"
        case maskKey = "mask_key"
    }
}

private struct RegionParamsCompatPayload: Codable {
    var workflow: String
    var selectedTypes: [String]
    var closeUm: Double
    var dilateUm: Double
    var minAreaUm2: Double
    var minCells: Int
    var contourDownsample: Int
    var lineWidth: Double
    var lineStyle: String
    var boundaryColor: String
    var useTypeColors: Bool

    enum CodingKeys: String, CodingKey {
        case workflow
        case selectedTypes = "selected_types"
        case closeUm = "close_um"
        case dilateUm = "dilate_um"
        case minAreaUm2 = "min_area_um2"
        case minCells = "min_cells"
        case contourDownsample = "contour_downsample"
        case lineWidth = "line_width"
        case lineStyle = "line_style"
        case boundaryColor = "boundary_color"
        case useTypeColors = "use_type_colors"
    }
}

private struct NeighborhoodParametersPayload: Codable {
    var gridSizeUm: Double
    var gridSizePx: Double
    var gridWidthPx: Double? = nil
    var gridHeightPx: Double? = nil
}

private struct NeighborhoodParamsCompatPayload: Codable {
    var gridSizeUm: Double
    var tileWidthPx: Int
    var tileHeightPx: Int
    var nTilesX: Int
    var nTilesY: Int
    var excludedCelltypes: [String]
    var displayClusterLabels: [String]
    var clusterColors: [String: String]

    enum CodingKeys: String, CodingKey {
        case gridSizeUm = "grid_size_um"
        case tileWidthPx = "tile_width_px"
        case tileHeightPx = "tile_height_px"
        case nTilesX = "n_tiles_x"
        case nTilesY = "n_tiles_y"
        case excludedCelltypes = "excluded_celltypes"
        case displayClusterLabels = "display_cluster_labels"
        case clusterColors = "cluster_colors"
    }
}

private struct RegionBandInputsCompatPayload: Codable {
    var imageID: String
    var boundaryLabel: String
    var boundaryMaskPath: String
    var pixelSizeUm: [Double]
    var bandWidthUm: Double
    var insideLabel: String
    var outsideLabel: String
    var overlayChannels: [String]
    var outputDir: String

    enum CodingKeys: String, CodingKey {
        case imageID = "image_id"
        case boundaryLabel = "boundary_label"
        case boundaryMaskPath = "boundary_mask_path"
        case pixelSizeUm = "pixel_size_um"
        case bandWidthUm = "band_width_um"
        case insideLabel = "inside_label"
        case outsideLabel = "outside_label"
        case overlayChannels = "overlay_channels"
        case outputDir = "output_dir"
    }
}
