import Foundation
import Metal

enum SmokeTestRunner {
    private static let smokePixelSize: (Double, Double) = (1.0, 1.0)

    private static func smokeInputFolder() -> URL {
        smokeFolder(environmentKey: "SPATIALSCOPE_SMOKE_INPUT", defaultName: "test_images_input_files")
    }

    private static func smokeOutputFolder() -> URL {
        smokeFolder(environmentKey: "SPATIALSCOPE_SMOKE_OUTPUT", defaultName: "test_images_output_files")
    }

    private static func smokeFolder(environmentKey: String, defaultName: String) -> URL {
        if let override = ProcessInfo.processInfo.environment[environmentKey]?
            .trimmingCharacters(in: .whitespacesAndNewlines),
           !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Documents/TEM_Spatial", isDirectory: true)
            .appendingPathComponent(defaultName, isDirectory: true)
    }

    static func refreshCellTypeMaskFromSavedAssignment() throws {
        let outputFolder = smokeOutputFolder()
        guard let result = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder) else {
            throw SpatialScopeError.message("Could not load saved cell-type assignment outputs.")
        }
        try OutputWriter.writeCellTypeAssignmentOutputs(result: result, outputFolder: outputFolder)
        print("Refreshed celltypes_mask_uint16.tiff/raw for \(result.assignments.count) saved cell assignments.")
    }

    static func runOverlaySmokeTest(cpuAllocationPercent: Double = 100) throws {
        let inputFolder = smokeInputFolder()
        let outputFolder = smokeOutputFolder()
        let csvURLs = try CSVImageLoader.discoverCSVFiles(in: inputFolder)
        let channels = csvURLs.enumerated().map { index, url in
            ChannelConfig(
                fileName: url.lastPathComponent,
                marker: url.deletingPathExtension().lastPathComponent,
                colorHex: ColorPalette.color(at: index),
                overlayEnabled: true
            )
        }
        let matrices = try CSVImageLoader.loadMatrices(inputFolder: inputFolder, channels: channels)
        let result = try OverlayRenderer.render(
            matrices: matrices,
            channels: channels,
            whiteChannelID: nil,
            whiteWeight: 0,
            pixelSizeXUm: nil,
            cpuAllocationPercent: cpuAllocationPercent
        )
        let resourceSnapshot = smokeResourceSnapshot()
        try OutputWriter.writeConfiguration(
            inputFolder: inputFolder,
            outputFolder: outputFolder,
            channels: channels,
            overlayChannels: channels,
            whiteChannel: nil,
            whiteWeight: 0,
            pixelSize: smokePixelSize,
            nucleusChannel: preferredNucleusChannel(in: channels),
            nucleiRunMode: .manual,
            nucleiParameters: NucleiParameters(),
            cpuAllocationPercent: 100,
            gpuAllocationPercent: 0
        )
        try OutputWriter.writeOverlayImages(
            result: result,
            outputFolder: outputFolder,
            pixelSizeXUm: nil
        )
        try OutputWriter.writeResourceMetadata(
            outputFolder: outputFolder,
            section: "overlay",
            cpuAllocationPercent: cpuAllocationPercent,
            gpuAllocationPercent: 0,
            snapshot: resourceSnapshot
        )
        print("Smoke overlay generated \(channels.count) channels into \(outputFolder.path). CPU allocation \(cpuAllocationPercent)%")
    }

    static func runNucleiSmokeTest(
        runScan: Bool,
        cpuAllocationPercent: Double = 100,
        combinationBudget: Int = 160
    ) throws {
        let inputFolder = smokeInputFolder()
        let outputFolder = smokeOutputFolder()
        let csvURLs = try CSVImageLoader.discoverCSVFiles(in: inputFolder)
        let channels = csvURLs.enumerated().map { index, url in
            ChannelConfig(
                fileName: url.lastPathComponent,
                marker: url.deletingPathExtension().lastPathComponent,
                colorHex: ColorPalette.color(at: index),
                overlayEnabled: true
            )
        }
        guard let nucleusURL = csvURLs.first(where: { $0.lastPathComponent.lowercased().contains("ir193") })
            ?? csvURLs.first(where: { $0.lastPathComponent.lowercased().contains("nuc") })
            ?? csvURLs.first else {
            throw SpatialScopeError.message("No CSV file available for nuclei smoke test.")
        }
        let channel = channels.first { $0.fileName == nucleusURL.lastPathComponent }
            ?? ChannelConfig(
                fileName: nucleusURL.lastPathComponent,
                marker: nucleusURL.deletingPathExtension().lastPathComponent,
                colorHex: "#ffffff",
                overlayEnabled: true
            )
        let matrix = try CSVImageLoader.loadMatrix(from: nucleusURL, channelName: channel.channelName)
        let params = NucleiParameters()
        let resourceSnapshot = smokeResourceSnapshot()
        let gpuAllocation = 0.0

        if runScan {
            let estimatedSeconds = NucleiSegmenter.estimateAdvancedScanSeconds(
                combinationBudget: combinationBudget,
                secondsPerCombination: 0.16,
                benchmarkCPUAllocationPercent: 25,
                cpuAllocationPercent: cpuAllocationPercent
            )
            let start = Date()
            let records = try NucleiSegmenter.runAdvancedScan(
                matrix: matrix,
                baseParams: params,
                pixelSize: smokePixelSize,
                cpuAllocationPercent: cpuAllocationPercent,
                combinationBudget: combinationBudget
            )
            let elapsed = Date().timeIntervalSince(start)
            try OutputWriter.writeNucleiScanResults(records, outputFolder: outputFolder)
            try OutputWriter.writeNucleiScanMetadata(
                records: records,
                outputFolder: outputFolder,
                plannedCombinationCount: combinationBudget,
                totalSearchSpace: NucleiSegmenter.advancedSearchSpaceSize,
                searchIntervalCount: NucleiSegmenter.advancedSearchIntervalCount,
                estimatedSecondsAtStart: estimatedSeconds,
                elapsedSeconds: elapsed,
                cpuAllocationPercent: cpuAllocationPercent,
                gpuAllocationPercent: gpuAllocation,
                snapshot: resourceSnapshot
            )
            try OutputWriter.writeResourceMetadata(
                outputFolder: outputFolder,
                section: "nuclei",
                cpuAllocationPercent: cpuAllocationPercent,
                gpuAllocationPercent: gpuAllocation,
                snapshot: resourceSnapshot
            )
            guard let best = records.max(by: { $0.count < $1.count }) else {
                throw SpatialScopeError.message("Nuclei scan produced no records.")
            }
            try OutputWriter.writeConfiguration(
                inputFolder: inputFolder,
                outputFolder: outputFolder,
                channels: channels,
                overlayChannels: channels,
                whiteChannel: nil,
                whiteWeight: 0,
                pixelSize: smokePixelSize,
                nucleusChannel: channel,
                nucleiRunMode: .advanced,
                nucleiParameters: best.params,
                nucleiScanCombinationBudget: combinationBudget,
                cpuAllocationPercent: cpuAllocationPercent,
                gpuAllocationPercent: gpuAllocation
            )
            print("Smoke nuclei scan generated \(records.count) combos. Best combo \(best.comboIndex): \(best.count) nuclei. CPU allocation \(cpuAllocationPercent)%. Budget \(combinationBudget).")
        } else {
            let result = try NucleiSegmenter.runFinal(
                matrix: matrix,
                params: params,
                pixelSize: smokePixelSize,
                cpuAllocationPercent: cpuAllocationPercent
            )
            try OutputWriter.writeNucleiOutputs(result: result, outputFolder: outputFolder)
            try OutputWriter.writeResourceMetadata(
                outputFolder: outputFolder,
                section: "nuclei",
                cpuAllocationPercent: cpuAllocationPercent,
                gpuAllocationPercent: 0,
                snapshot: smokeResourceSnapshot()
            )
            try OutputWriter.writeConfiguration(
                inputFolder: inputFolder,
                outputFolder: outputFolder,
                channels: channels,
                overlayChannels: channels,
                whiteChannel: nil,
                whiteWeight: 0,
                pixelSize: nil,
                nucleusChannel: channel,
                nucleiRunMode: .manual,
                nucleiParameters: result.params,
                nucleiScanCombinationBudget: combinationBudget,
                cpuAllocationPercent: cpuAllocationPercent,
                gpuAllocationPercent: gpuAllocation
            )
            print("Smoke nuclei final generated \(result.count) nuclei into \(outputFolder.path). CPU allocation \(cpuAllocationPercent)%")
        }
    }

    static func runStagedAnalysisSmokeTest(cpuAllocationPercent: Double = 100) throws {
        let outputFolder = smokeOutputFolder()
        let snapshot = smokeResourceSnapshot()
        let gpuAllocation = 0.0
        let sections: [(AnalysisSection, [String: String])] = []

        for (section, parameters) in sections {
            let message = "\(section.title) is staged; its native analysis engine is not active yet."
            try OutputWriter.writeStagedAnalysisManifest(
                outputFolder: outputFolder,
                sectionKey: section.outputSectionKey,
                sectionTitle: section.title,
                message: message,
                parameters: parameters,
                cpuAllocationPercent: cpuAllocationPercent,
                gpuAllocationPercent: gpuAllocation,
                snapshot: snapshot
            )
            try OutputWriter.writeResourceMetadata(
                outputFolder: outputFolder,
                section: section.outputSectionKey,
                cpuAllocationPercent: cpuAllocationPercent,
                gpuAllocationPercent: gpuAllocation,
                snapshot: snapshot
            )
        }

        print("Smoke staged manifests generated for \(sections.count) sections. CPU allocation \(cpuAllocationPercent)%")
    }

    static func runCellTypeAssignmentSmokeTest(cpuAllocationPercent: Double = 100) throws {
        let inputFolder = smokeInputFolder()
        let outputFolder = smokeOutputFolder()
        let csvURLs = try CSVImageLoader.discoverCSVFiles(in: inputFolder)
        let channels = csvURLs.enumerated().map { index, url in
            ChannelConfig(
                fileName: url.lastPathComponent,
                marker: url.deletingPathExtension().lastPathComponent,
                colorHex: ColorPalette.color(at: index),
                overlayEnabled: true
            )
        }
        var detections = OutputWriter.loadNucleiSummary(outputFolder: outputFolder)
        if detections.isEmpty {
            try runNucleiSmokeTest(runScan: false, cpuAllocationPercent: cpuAllocationPercent)
            detections = OutputWriter.loadNucleiSummary(outputFolder: outputFolder)
        }
        guard !detections.isEmpty else {
            throw SpatialScopeError.message("Cell-type smoke test could not find nuclei detections.")
        }

        let matrices = try CSVImageLoader.loadMatrices(inputFolder: inputFolder, channels: channels)
        let labelMap = OutputWriter.loadNucleiLabelMap(outputFolder: outputFolder)
        let cellTypes = [
            CellTypeDefinition(name: "Tumor", colorHex: "#dc0000", allPositiveMarkers: "GFP_tumor, RFP_tumor"),
            CellTypeDefinition(name: "CD8 T", colorHex: "#00ff00", allPositiveMarkers: "CD8A", allNegativeMarkers: "FOXP3"),
            CellTypeDefinition(name: "Treg", colorHex: "#ff00ff", allPositiveMarkers: "CD4, FOXP3"),
            CellTypeDefinition(name: "Macrophage", colorHex: "#0008e5", allPositiveMarkers: "F4_80"),
            CellTypeDefinition(name: "B cell", colorHex: "#00ffff", allPositiveMarkers: "B220")
        ]
        let parameters = bestAssignmentScreeningParameters(outputFolder: outputFolder) ?? AssignmentParameters()
        let result = try CellTypeAssigner.run(
            detections: detections,
            matrices: matrices,
            channels: channels,
            cellTypes: cellTypes,
            parameters: parameters,
            pixelSize: smokePixelSize,
            labelMap: labelMap,
            cpuAllocationPercent: cpuAllocationPercent
        )
        let snapshot = smokeResourceSnapshot()
        let gpuAllocation = 0.0
        try OutputWriter.writeCellTypeConfig(cellTypes, outputFolder: outputFolder)
        try OutputWriter.writeCellTypeAssignmentOutputs(result: result, outputFolder: outputFolder)
        try OutputWriter.writeResourceMetadata(
            outputFolder: outputFolder,
            section: "celltype_assignment",
            cpuAllocationPercent: cpuAllocationPercent,
            gpuAllocationPercent: gpuAllocation,
            snapshot: snapshot
        )
        try OutputWriter.writeConfiguration(
            inputFolder: inputFolder,
            outputFolder: outputFolder,
            channels: channels,
            overlayChannels: channels,
            whiteChannel: nil,
            whiteWeight: 0,
            pixelSize: smokePixelSize,
            nucleusChannel: preferredNucleusChannel(in: channels),
            nucleiRunMode: .manual,
            nucleiParameters: NucleiParameters(),
            nucleiScanCombinationBudget: 160,
            cpuAllocationPercent: cpuAllocationPercent,
            gpuAllocationPercent: gpuAllocation
        )
        print("Smoke cell-type assignment generated \(result.assignments.count) rows, \(result.totalAssigned) assigned. CPU allocation \(cpuAllocationPercent)%")
    }

    static func runCellTypeAssignmentScreeningSmokeTest(
        cpuAllocationPercent: Double = 100,
        combinationBudget: Int = 80
    ) throws {
        let inputFolder = smokeInputFolder()
        let outputFolder = smokeOutputFolder()
        let csvURLs = try CSVImageLoader.discoverCSVFiles(in: inputFolder)
        let channels = csvURLs.enumerated().map { index, url in
            ChannelConfig(
                fileName: url.lastPathComponent,
                marker: url.deletingPathExtension().lastPathComponent,
                colorHex: ColorPalette.color(at: index),
                overlayEnabled: true
            )
        }
        var detections = OutputWriter.loadNucleiSummary(outputFolder: outputFolder)
        if detections.isEmpty {
            try runNucleiSmokeTest(runScan: false, cpuAllocationPercent: cpuAllocationPercent)
            detections = OutputWriter.loadNucleiSummary(outputFolder: outputFolder)
        }
        guard !detections.isEmpty else {
            throw SpatialScopeError.message("Cell-type screening smoke test could not find nuclei detections.")
        }

        let matrices = try CSVImageLoader.loadMatrices(inputFolder: inputFolder, channels: channels)
        let labelMap = OutputWriter.loadNucleiLabelMap(outputFolder: outputFolder)
        let cellTypes = [
            CellTypeDefinition(name: "Tumor", colorHex: "#dc0000", allPositiveMarkers: "GFP_tumor, RFP_tumor"),
            CellTypeDefinition(name: "CD8 T", colorHex: "#00ff00", allPositiveMarkers: "CD8A", allNegativeMarkers: "FOXP3"),
            CellTypeDefinition(name: "Treg", colorHex: "#ff00ff", allPositiveMarkers: "CD4, FOXP3"),
            CellTypeDefinition(name: "Macrophage", colorHex: "#0008e5", allPositiveMarkers: "F4_80"),
            CellTypeDefinition(name: "B cell", colorHex: "#00ffff", allPositiveMarkers: "B220")
        ]
        let records = try CellTypeAssigner.runParameterScreening(
            detections: detections,
            matrices: matrices,
            channels: channels,
            cellTypes: cellTypes,
            baseParameters: AssignmentParameters(),
            pixelSize: nil,
            labelMap: labelMap,
            cpuAllocationPercent: cpuAllocationPercent,
            combinationBudget: combinationBudget,
            fixVoronoi: true,
            fixBuffer: true,
            screeningBandCount: 6,
            screeningSubsetMode: .randomThree,
            screeningSelectedBands: [0, 2, 4]
        )
        let assignmentDir = OutputWriter.sectionURL("celltype_assignment", outputFolder: outputFolder)
        try FileManager.default.createDirectory(at: assignmentDir, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(records).write(to: assignmentDir.appendingPathComponent("celltype_assignment_screening_results.json"))
        let snapshot = smokeResourceSnapshot()
        try OutputWriter.writeResourceMetadata(
            outputFolder: outputFolder,
            section: "celltype_assignment",
            cpuAllocationPercent: cpuAllocationPercent,
            gpuAllocationPercent: 0,
            snapshot: snapshot
        )
        let best = records.min {
            if $0.unresolvedCount == $1.unresolvedCount { return $0.assignedCount > $1.assignedCount }
            return $0.unresolvedCount < $1.unresolvedCount
        }
        print("Smoke cell-type screening generated \(records.count) combos. Best combo \(best?.comboIndex ?? 0): \(best?.unresolvedCount ?? 0) unresolved cells. CPU allocation \(cpuAllocationPercent)%.")
    }

    static func runNeighborhoodSmokeTest(cpuAllocationPercent: Double = 100) throws {
        let outputFolder = smokeOutputFolder()
        var assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        if assignmentResult == nil {
            try runCellTypeAssignmentSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        }
        guard let assignmentResult else {
            throw SpatialScopeError.message("Neighborhood smoke test could not load cell-type assignment output.")
        }

        let gridSizeUm = 20.0
        let result = try NeighborhoodAnalyzer.run(
            assignments: assignmentResult.assignments,
            gridSizeUm: gridSizeUm,
            pixelSize: nil,
            canvasWidth: assignmentResult.width,
            canvasHeight: assignmentResult.height
        )
        let snapshot = smokeResourceSnapshot()
        let gpuAllocation = 0.0
        try OutputWriter.writeNeighborhoodAnalysisOutputs(result: result, outputFolder: outputFolder)
        try OutputWriter.writeResourceMetadata(
            outputFolder: outputFolder,
            section: "neighborhood_analysis",
            cpuAllocationPercent: cpuAllocationPercent,
            gpuAllocationPercent: gpuAllocation,
            snapshot: snapshot
        )
        print("Smoke neighborhood analysis generated \(result.occupiedTileCount) occupied tiles from \(result.totalCells) cells. CPU allocation \(cpuAllocationPercent)%")
    }

    static func runRegionSmokeTest(cpuAllocationPercent: Double = 100) throws {
        let outputFolder = regionSmokeOutputFolder()
        var assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        if assignmentResult == nil {
            try runCellTypeAssignmentSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        }
        guard let assignmentResult else {
            throw SpatialScopeError.message("Region smoke test could not load cell-type assignment output.")
        }

        let assignedCellTypes = Set(assignmentResult.assignments
            .map(\.assignedType)
            .filter { $0 != "Unassigned" && $0 != "Ambiguous" })
            .sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        guard !assignedCellTypes.isEmpty else {
            throw SpatialScopeError.message("Region smoke test found no assigned cell type.")
        }
        var parameters = RegionParameters()
        parameters.selectedTypes = assignedCellTypes
        let result = try RegionAnalyzer.run(
            assignments: assignmentResult.assignments,
            parameters: parameters,
            pixelSize: nil,
            canvasWidth: assignmentResult.width,
            canvasHeight: assignmentResult.height,
            cellTypeMask: assignmentResult.cellTypeMask,
            cellTypeIDByName: assignmentResult.cellTypeIDByName
        )
        try validateRegionGeometry(result)
        let snapshot = smokeResourceSnapshot()
        let gpuAllocation = 0.0
        try OutputWriter.writeRegionAnalysisOutputs(
            result: result,
            outputFolder: outputFolder,
            assignments: assignmentResult.assignments,
            cellTypeMask: assignmentResult.cellTypeMask,
            cellTypeIDByName: assignmentResult.cellTypeIDByName
        )
        try OutputWriter.writeResourceMetadata(
            outputFolder: outputFolder,
            section: "region_analysis",
            cpuAllocationPercent: cpuAllocationPercent,
            gpuAllocationPercent: gpuAllocation,
            snapshot: snapshot
        )
        print("Smoke region analysis generated \(result.regions.count) ROIs from \(result.totalCells) counted cells. CPU allocation \(cpuAllocationPercent)%")
    }

    private static func validateRegionGeometry(_ result: RegionAnalysisResult) throws {
        guard !result.regions.isEmpty else {
            throw SpatialScopeError.message("Region regression check found no ROI geometry.")
        }

        let hasOrganicTwoDimensionalBoundary = result.regions.contains { region in
            guard region.widthPx > 2, region.heightPx > 2,
                  let runs = region.maskRuns, !runs.isEmpty else {
                return false
            }
            let distinctRows = Set(runs.map(\.y)).count
            let minX = runs.map(\.xStart).min() ?? 0
            let maxX = runs.map(\.xEnd).max() ?? 0
            guard distinctRows > 2, maxX - minX > 2 else { return false }

            let mask = RasterMask(width: result.width, height: result.height, runs: runs)
            var changesBetweenRows = 0
            var changesWithinRows = 0
            for y in 0..<mask.height {
                for x in 0..<mask.width {
                    if y > 0, mask[x, y] != mask[x, y - 1] {
                        changesBetweenRows += 1
                    }
                    if x > 0, mask[x, y] != mask[x - 1, y] {
                        changesWithinRows += 1
                    }
                }
            }
            let smallerTransitionCount = max(1, min(changesBetweenRows, changesWithinRows))
            let transitionAnisotropy = Double(max(changesBetweenRows, changesWithinRows))
                / Double(smallerTransitionCount)
            return transitionAnisotropy < 3.5
        }

        guard hasOrganicTwoDimensionalBoundary else {
            throw SpatialScopeError.message("Region regression check detected flattened or scanline-dominated ROI boundaries.")
        }
    }

    private static func regionSmokeOutputFolder() -> URL {
        let override = ProcessInfo.processInfo.environment["SPATIALSCOPE_REGION_SMOKE_OUTPUT"]?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let override, !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        return smokeOutputFolder()
    }

    static func runRegionCustomizedDisplaySmokeTest(cpuAllocationPercent: Double = 100) throws {
        let outputFolder = smokeOutputFolder()
        var assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        if assignmentResult == nil {
            try runCellTypeAssignmentSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        }
        guard let assignmentResult else {
            throw SpatialScopeError.message("Region customized display smoke test could not load cell-type assignment output.")
        }

        var regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        if regionResult == nil {
            try runRegionSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        }
        guard let regionResult else {
            throw SpatialScopeError.message("Region customized display smoke test could not load region analysis output.")
        }
        guard let firstRegionID = regionResult.regions.sorted(by: { $0.id < $1.id }).first?.id else {
            throw SpatialScopeError.message("Region customized display smoke test found no region boundaries.")
        }
        let firstCellType = assignmentResult.assignments
            .map(\.assignedType)
            .filter { $0 != "Unassigned" && $0 != "Ambiguous" }
            .sorted { $0.localizedStandardCompare($1) == .orderedAscending }
            .first
        guard let firstCellType else {
            throw SpatialScopeError.message("Region customized display smoke test found no assigned cell type.")
        }

        let summary = try OutputWriter.writeCustomizedRegionDisplayOutputs(
            result: regionResult,
            outputFolder: outputFolder,
            assignments: assignmentResult.assignments,
            selectedRegionIDs: [firstRegionID],
            selectedCellTypes: [firstCellType],
            cellTypeMask: assignmentResult.cellTypeMask,
            cellTypeIDByName: assignmentResult.cellTypeIDByName
        )
        print("Smoke customized region display wrote \(summary.customizedFiles.count) customized files and \(summary.originalFiles.count) original files. CPU allocation \(cpuAllocationPercent)%")
    }

    static func runRegionManualAdjustmentSmokeTest(cpuAllocationPercent: Double = 100) throws {
        let outputFolder = smokeOutputFolder()
        var assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        if assignmentResult == nil {
            try runCellTypeAssignmentSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        }
        guard let assignmentResult else {
            throw SpatialScopeError.message("Region manual adjustment smoke test could not load cell-type assignment output.")
        }

        var regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        if regionResult == nil {
            try runRegionSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        } else if let loadedRegion = regionResult,
                  loadedRegion.width != assignmentResult.width
                    || loadedRegion.height != assignmentResult.height
                    || loadedRegion.regions.contains(where: { $0.manualEditMode != nil }) {
            try runRegionSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        }
        guard let baseRegionResult = regionResult else {
            throw SpatialScopeError.message("Region manual adjustment smoke test could not load region analysis output.")
        }
        guard let targetRegion = baseRegionResult.regions.sorted(by: { $0.id < $1.id }).first else {
            throw SpatialScopeError.message("Region manual adjustment smoke test found no base ROI.")
        }
        let baseRegionIDs = Set(baseRegionResult.regions.map(\.id))
        let assignedCellTypes = Set(assignmentResult.assignments
            .map(\.assignedType)
            .filter { $0 != "Unassigned" && $0 != "Ambiguous" })

        let redrawPolygon = lassoPolygon(points: [
            (154.0, 466.0),
            (238.0, 470.0),
            (260.0, 512.0),
            (234.0, 552.0),
            (166.0, 548.0),
            (144.0, 508.0)
        ])
        let manualParameters = tightManualRegionParameters(from: baseRegionResult.parameters)
        var adjustedResult = try RegionAnalyzer.applyManualEdit(
            to: baseRegionResult,
            assignments: assignmentResult.assignments,
            mode: .redraw,
            targetRegionID: nil,
            displayName: "manual_drawn_smoke_ROI",
            polygonPoints: redrawPolygon,
            pixelSize: nil,
            seedCellTypes: assignedCellTypes,
            manualParameters: manualParameters
        )

        let includePolygon = rectanglePolygon(
            x: max(0.0, targetRegion.xPx - targetRegion.widthPx * 0.10),
            y: max(0.0, targetRegion.yPx - targetRegion.heightPx * 0.10),
            width: max(8.0, targetRegion.widthPx * 0.35),
            height: max(8.0, targetRegion.heightPx * 0.35)
        )
        adjustedResult = try RegionAnalyzer.applyManualEdit(
            to: adjustedResult,
            assignments: assignmentResult.assignments,
            mode: .include,
            targetRegionID: targetRegion.id,
            displayName: "adjusted_include_smoke_ROI",
            polygonPoints: includePolygon,
            pixelSize: nil,
            seedCellTypes: assignedCellTypes,
            manualParameters: manualParameters
        )

        let excludePolygon = rectanglePolygon(
            x: targetRegion.xPx + targetRegion.widthPx * 0.25,
            y: targetRegion.yPx + targetRegion.heightPx * 0.25,
            width: max(6.0, targetRegion.widthPx * 0.18),
            height: max(6.0, targetRegion.heightPx * 0.18)
        )
        adjustedResult = try RegionAnalyzer.applyManualEdit(
            to: adjustedResult,
            assignments: assignmentResult.assignments,
            mode: .exclude,
            targetRegionID: targetRegion.id,
            displayName: "adjusted_exclude_smoke_ROI",
            polygonPoints: excludePolygon,
            pixelSize: nil,
            seedCellTypes: assignedCellTypes,
            manualParameters: manualParameters
        )

        let snapshot = smokeResourceSnapshot()
        let gpuAllocation = 0.0
        let adjustedRenderRegions = adjustedResult.regions.filter { !baseRegionIDs.contains($0.id) }
        adjustedResult.image = RegionAnalyzer.renderRegionMap(
            assignments: assignmentResult.assignments,
            regions: adjustedResult.regions,
            width: adjustedResult.width,
            height: adjustedResult.height,
            parameters: adjustedResult.parameters,
            cellTypeMask: assignmentResult.cellTypeMask,
            cellTypeIDByName: assignmentResult.cellTypeIDByName
        )
        adjustedResult.statsImage = RegionAnalyzer.renderDominantCountsPlot(counts: adjustedResult.dominantCounts)
        try OutputWriter.writeRegionAnalysisOutputs(
            result: adjustedResult,
            outputFolder: outputFolder,
            assignments: assignmentResult.assignments,
            cellTypeMask: assignmentResult.cellTypeMask,
            cellTypeIDByName: assignmentResult.cellTypeIDByName
        )
        try OutputWriter.writeResourceMetadata(
            outputFolder: outputFolder,
            section: "region_analysis",
            cpuAllocationPercent: cpuAllocationPercent,
            gpuAllocationPercent: gpuAllocation,
            snapshot: snapshot
        )

        guard let loaded = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder) else {
            throw SpatialScopeError.message("Region manual adjustment smoke test could not reload adjusted region output.")
        }
        let loadedRegionIDs = Set(loaded.regions.map(\.id))
        guard baseRegionIDs.isSubset(of: loadedRegionIDs) else {
            throw SpatialScopeError.message("Manual ROI save dropped one or more pre-existing region boundaries.")
        }
        let sourceTypes = Set(loaded.regions.map { $0.sourceType ?? $0.dominantType })
        for expected in ["manual_drawn_smoke_ROI", "adjusted_include_smoke_ROI", "adjusted_exclude_smoke_ROI"] {
            guard sourceTypes.contains(expected) else {
                throw SpatialScopeError.message("Adjusted ROI \(expected) was not present after reload.")
            }
        }
        let registryURL = outputFolder
            .appendingPathComponent("07_region_analysis")
            .appendingPathComponent("boundary_mask_registry.json")
        let registryText = (try? String(contentsOf: registryURL, encoding: .utf8)) ?? ""
        guard registryText.contains("manual_region_adjustment") else {
            throw SpatialScopeError.message("Boundary mask registry did not record manual_region_adjustment entries.")
        }

        guard loaded.regions.count == adjustedResult.regions.count,
              loaded.regions.count > adjustedRenderRegions.count else {
            throw SpatialScopeError.message("Manual ROI export did not preserve base boundaries plus adjusted boundaries.")
        }

        print("Smoke manual region adjustment saved redraw/include/exclude ROIs; \(loaded.regions.count) total downstream boundaries preserved. CPU allocation \(cpuAllocationPercent)%")
    }

    static func runCellDistributionSmokeTest(cpuAllocationPercent: Double = 100) throws {
        let outputFolder = smokeOutputFolder()
        var assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        if assignmentResult == nil {
            try runCellTypeAssignmentSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        }
        guard let assignmentResult else {
            throw SpatialScopeError.message("Cell distribution smoke test could not load cell-type assignment output.")
        }

        var regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        if regionResult == nil {
            try runRegionSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        }
        guard let regionResult else {
            throw SpatialScopeError.message("Cell distribution smoke test could not load region analysis output.")
        }
        let smokeRegions = regionResult.regions.sorted(by: { $0.id < $1.id })
        guard let firstRegion = smokeRegions.first else {
            throw SpatialScopeError.message("Cell distribution smoke test found no region boundaries.")
        }
        try ensureSmokePixelSizeConfiguration(outputFolder: outputFolder, cpuAllocationPercent: cpuAllocationPercent)

        let boundaryLabel = firstRegion.sourceType ?? firstRegion.dominantType
        let bandWidthUm = 10.0
        try OutputWriter.runStreamlitCellDistributionExport(
            outputFolder: outputFolder,
            mode: .regionMasks,
            selectedBoundaryLabels: [boundaryLabel],
            selectedCellTypes: [],
            selectedClusterLabels: [],
            bandWidthUm: bandWidthUm
        )
        let latestBoundaryLabel: String
        if let secondRegion = smokeRegions.dropFirst().first {
            latestBoundaryLabel = secondRegion.sourceType ?? secondRegion.dominantType
            try OutputWriter.runStreamlitCellDistributionExport(
                outputFolder: outputFolder,
                mode: .regionMasks,
                selectedBoundaryLabels: [latestBoundaryLabel],
                selectedCellTypes: [],
                selectedClusterLabels: [],
                bandWidthUm: bandWidthUm
            )
        } else {
            latestBoundaryLabel = boundaryLabel
        }

        let assignedCellTypes = Set(assignmentResult.assignments
            .map(\.assignedType)
            .filter { $0 != "Unassigned" && $0 != "Ambiguous" })
            .sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        guard !assignedCellTypes.isEmpty else {
            throw SpatialScopeError.message("Cell distribution smoke test found no assigned cell type for density.")
        }
        try OutputWriter.runStreamlitCellDistributionExport(
            outputFolder: outputFolder,
            mode: .cellDensity,
            selectedBoundaryLabels: [],
            selectedCellTypes: [],
            selectedClusterLabels: [],
            bandWidthUm: bandWidthUm
        )
        guard let densityResult = OutputWriter.loadCellDistributionResult(outputFolder: outputFolder),
              !densityResult.bandMetrics.isEmpty else {
            throw SpatialScopeError.message("Cell distribution smoke test did not reload Streamlit density outputs.")
        }
        guard densityResult.regionSummaries.contains(where: { $0.dominantType.contains(latestBoundaryLabel) }),
              Set(densityResult.bandMetrics.map(\.cellType)).isSuperset(of: assignedCellTypes) else {
            throw SpatialScopeError.message("Cell distribution smoke test did not load the latest boundary with all default cell types.")
        }
        print("Smoke cell distribution generated \(densityResult.bandMetrics.count) density band rows from latest Streamlit region masks. CPU allocation \(cpuAllocationPercent)%")
    }

    static func runCellDistributionClusterSmokeTest(cpuAllocationPercent: Double = 100) throws {
        let outputFolder = smokeOutputFolder()
        var assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        if assignmentResult == nil {
            try runCellTypeAssignmentSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        }
        guard assignmentResult != nil else {
            throw SpatialScopeError.message("Cell distribution cluster smoke test could not load cell-type assignment output.")
        }

        var regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        if regionResult == nil {
            try runRegionSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        }
        guard let regionResult else {
            throw SpatialScopeError.message("Cell distribution cluster smoke test could not load region analysis output.")
        }

        var neighborhoodResult = OutputWriter.loadNeighborhoodAnalysisResult(outputFolder: outputFolder)
        if neighborhoodResult == nil {
            try runNeighborhoodSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            neighborhoodResult = OutputWriter.loadNeighborhoodAnalysisResult(outputFolder: outputFolder)
        }
        guard let neighborhoodResult else {
            throw SpatialScopeError.message("Cell distribution cluster smoke test could not load neighborhood analysis output.")
        }
        guard let firstRegion = regionResult.regions.sorted(by: { $0.id < $1.id }).first else {
            throw SpatialScopeError.message("Cell distribution cluster smoke test found no region boundaries.")
        }
        try ensureSmokePixelSizeConfiguration(outputFolder: outputFolder, cpuAllocationPercent: cpuAllocationPercent)
        let boundaryLabel = firstRegion.sourceType ?? firstRegion.dominantType
        let clusterLabels = neighborhoodResult.clusterCounts
            .sorted { $0.clusterID < $1.clusterID }
            .map(\.clusterLabel)
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        guard !clusterLabels.isEmpty else {
            throw SpatialScopeError.message("Cell distribution cluster smoke test found no neighborhood cluster labels.")
        }
        try OutputWriter.runStreamlitCellDistributionExport(
            outputFolder: outputFolder,
            mode: .cellClusterDistribution,
            selectedBoundaryLabels: [boundaryLabel],
            selectedCellTypes: [],
            selectedClusterLabels: clusterLabels,
            bandWidthUm: 10.0
        )
        guard let result = OutputWriter.loadCellDistributionResult(outputFolder: outputFolder),
              !result.clusterMetrics.isEmpty,
              !result.tileClassifications.isEmpty else {
            throw SpatialScopeError.message("Cell distribution cluster smoke test did not reload Streamlit cluster outputs.")
        }
        print("Smoke cell distribution cluster generated \(result.clusterMetrics.count) cluster-region rows and \(result.tileClassifications.count) tile rows. CPU allocation \(cpuAllocationPercent)%")
    }

    static func runCellDistributionLoadSmokeTest(cpuAllocationPercent: Double = 100) throws {
        let outputFolder = smokeOutputFolder()
        var result = OutputWriter.loadCellDistributionResult(outputFolder: outputFolder)
        if result == nil {
            try runCellDistributionSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            result = OutputWriter.loadCellDistributionResult(outputFolder: outputFolder)
        }
        guard var result else {
            throw SpatialScopeError.message("Cell distribution load smoke test could not reload Streamlit-style outputs.")
        }
        guard !result.bandMetrics.isEmpty else {
            throw SpatialScopeError.message("Cell distribution load smoke test reloaded no density band metrics.")
        }
        if result.clusterMetrics.isEmpty || result.tileClassifications.isEmpty {
            try runCellDistributionClusterSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            guard let reloaded = OutputWriter.loadCellDistributionResult(outputFolder: outputFolder) else {
                throw SpatialScopeError.message("Cell distribution load smoke test could not reload cluster distribution outputs.")
            }
            result = reloaded
        }
        guard !result.clusterMetrics.isEmpty, !result.tileClassifications.isEmpty else {
            throw SpatialScopeError.message("Cell distribution load smoke test reloaded no cluster metrics or tile rows.")
        }
        print("Smoke cell distribution reload read \(result.bandMetrics.count) band rows, \(result.clusterMetrics.count) cluster-region rows, and \(result.tileClassifications.count) tile rows.")
    }

    private static func rectanglePolygon(x: Double, y: Double, width: Double, height: Double) -> [CellBoundaryPoint] {
        [
            CellBoundaryPoint(x: x, y: y),
            CellBoundaryPoint(x: x + width, y: y),
            CellBoundaryPoint(x: x + width, y: y + height),
            CellBoundaryPoint(x: x, y: y + height)
        ]
    }

    private static func lassoPolygon(points: [(Double, Double)]) -> [CellBoundaryPoint] {
        points.map { CellBoundaryPoint(x: $0.0, y: $0.1) }
    }

    private static func tightManualRegionParameters(from base: RegionParameters) -> RegionParameters {
        var parameters = base
        parameters.closeUm = 0.0
        parameters.dilateUm = 0.0
        parameters.minAreaUm2 = 0.0
        parameters.minCells = 1
        parameters.contourDownsample = 1
        return parameters
    }

    static func runDistanceSmokeTest(cpuAllocationPercent: Double = 100) throws {
        let outputFolder = smokeOutputFolder()
        var assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        if assignmentResult == nil {
            try runCellTypeAssignmentSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        }
        guard let assignmentResult else {
            throw SpatialScopeError.message("Distance smoke test could not load cell-type assignment output.")
        }

        var regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        if regionResult == nil {
            try runRegionSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        }
        guard var resolvedRegionResult = regionResult else {
            throw SpatialScopeError.message("Distance smoke test could not load region analysis output.")
        }

        let cellTypes = Array(Set(assignmentResult.assignments.map(\.assignedType).filter {
            !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }))
        .sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        guard let targetType = cellTypes.first else {
            throw SpatialScopeError.message("Distance smoke test found no assigned cell types.")
        }
        let queryTypes = Array(cellTypes.prefix(min(2, cellTypes.count)))
        let nearest = try DistanceAnalyzer.runNearestNeighborAnalysis(
            assignments: assignmentResult.assignments,
            targetType: targetType,
            queryTypes: queryTypes,
            pixelSize: nil,
            canvasWidth: assignmentResult.width,
            canvasHeight: assignmentResult.height
        )

        var boundaryChoices = OutputWriter.loadCellDistributionBoundaryChoices(outputFolder: outputFolder)
        if boundaryChoices.isEmpty {
            try runRegionSmokeTest(cpuAllocationPercent: cpuAllocationPercent)
            if let reloadedRegionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder) {
                resolvedRegionResult = reloadedRegionResult
            }
            boundaryChoices = OutputWriter.loadCellDistributionBoundaryChoices(outputFolder: outputFolder)
        }
        guard let boundaryChoice = boundaryChoices.first else {
            throw SpatialScopeError.message("Distance smoke test found no saved boundary masks.")
        }
        let boundary = try DistanceAnalyzer.runBoundaryDistanceAnalysis(
            assignments: assignmentResult.assignments,
            boundaryMaskURL: URL(fileURLWithPath: boundaryChoice.maskPath),
            boundaryID: boundaryChoice.id,
            boundaryName: boundaryChoice.label,
            queryTypes: queryTypes,
            regionFilter: .all,
            pixelSize: nil,
            canvasWidth: assignmentResult.width,
            canvasHeight: assignmentResult.height
        )
        let result = DistanceAnalysisResult(
            nearestDistances: nearest.nearestDistances,
            boundaryDistances: boundary.boundaryDistances,
            nearestTTests: nearest.nearestTTests,
            boundaryTTests: boundary.boundaryTTests,
            summaries: nearest.summaries + boundary.summaries,
            image: nearest.image,
            nearestHistogramImage: nearest.nearestHistogramImage,
            boundaryHistogramImage: boundary.boundaryHistogramImage,
            width: assignmentResult.width,
            height: assignmentResult.height,
            nearestTargetType: nearest.nearestTargetType,
            nearestQueryTypes: nearest.nearestQueryTypes,
            boundaryName: boundary.boundaryName,
            boundaryQueryTypes: boundary.boundaryQueryTypes,
            boundaryFilter: boundary.boundaryFilter
        )
        let snapshot = smokeResourceSnapshot()
        let gpuAllocation = 0.0
        try OutputWriter.writeDistanceAnalysisOutputs(
            result: result,
            regions: resolvedRegionResult.regions,
            outputFolder: outputFolder
        )
        try OutputWriter.writeResourceMetadata(
            outputFolder: outputFolder,
            section: "distance_analysis",
            cpuAllocationPercent: cpuAllocationPercent,
            gpuAllocationPercent: gpuAllocation,
            snapshot: snapshot
        )
        guard let loaded = OutputWriter.loadDistanceAnalysisResult(outputFolder: outputFolder),
              !loaded.nearestDistances.isEmpty,
              !loaded.boundaryDistances.isEmpty else {
            throw SpatialScopeError.message("Distance smoke test could not reload both nearest-neighbor and boundary outputs.")
        }
        print("Smoke distance analysis generated \(result.nearestDistances.count) nearest-neighbor rows and \(result.boundaryDistances.count) boundary rows. CPU allocation \(cpuAllocationPercent)%")
    }

    private static func smokeGPUCount() -> Int {
        MTLCopyAllDevices().count
    }

    private static func ensureSmokePixelSizeConfiguration(outputFolder: URL, cpuAllocationPercent: Double) throws {
        if let loaded = OutputWriter.loadConfiguration(outputFolder: outputFolder),
           loaded.pixelSize != nil {
            return
        }

        let fallbackInputFolder = smokeInputFolder()
        let loaded = OutputWriter.loadConfiguration(outputFolder: outputFolder)
        let inputFolder = loaded?.inputFolder ?? fallbackInputFolder
        let channels: [ChannelConfig]
        if let loadedChannels = loaded?.channels, !loadedChannels.isEmpty {
            channels = loadedChannels
        } else {
            let csvURLs = try CSVImageLoader.discoverCSVFiles(in: inputFolder)
            channels = csvURLs.enumerated().map { index, url in
                ChannelConfig(
                    fileName: url.lastPathComponent,
                    marker: url.deletingPathExtension().lastPathComponent,
                    colorHex: ColorPalette.color(at: index),
                    overlayEnabled: true
                )
            }
        }
        let overlayChannels = channels.filter(\.overlayEnabled).isEmpty ? channels : channels.filter(\.overlayEnabled)
        let whiteChannel = loaded?.whiteChannelName.flatMap { name in
            channels.first { $0.channelName == name }
        }
        let nucleusChannel = loaded?.nucleusChannelName.flatMap { name in
            channels.first { $0.channelName == name }
        } ?? preferredNucleusChannel(in: channels)
        let gpuAllocation = 0.0

        try OutputWriter.writeConfiguration(
            inputFolder: inputFolder,
            outputFolder: outputFolder,
            channels: channels,
            overlayChannels: overlayChannels,
            whiteChannel: whiteChannel,
            whiteWeight: loaded?.whiteWeight ?? 0,
            pixelSize: smokePixelSize,
            imageID: loaded?.imageID,
            figureSizeUm: loaded?.figureSizeUm,
            figureSizePx: loaded?.figureSizePx,
            nucleusChannel: nucleusChannel,
            nucleiRunMode: loaded?.nucleiRunMode ?? .manual,
            nucleiParameters: loaded?.nucleiParameters,
            nucleiScanCombinationBudget: loaded?.nucleiScanCombinationBudget,
            assignmentRunMode: loaded?.assignmentRunMode,
            assignmentParameters: loaded?.assignmentParameters,
            assignmentScanCombinationBudget: loaded?.assignmentScanCombinationBudget,
            assignmentScreeningBandCount: loaded?.assignmentScreeningBandCount,
            assignmentScreeningSubsetMode: loaded?.assignmentScreeningSubsetMode,
            cpuAllocationPercent: loaded?.cpuAllocationPercent ?? cpuAllocationPercent,
            gpuAllocationPercent: loaded?.gpuAllocationPercent ?? gpuAllocation
        )
    }

    private static func smokeResourceSnapshot() -> ResourceSnapshot {
        let gpuNames = MTLCopyAllDevices().map(\.name)
        return ResourceSnapshot(
            cpuCoreCount: ProcessInfo.processInfo.processorCount,
            activeCPUCoreCount: ProcessInfo.processInfo.activeProcessorCount,
            gpuCount: gpuNames.count,
            gpuNames: gpuNames,
            cpuUsagePercent: 0,
            gpuUsagePercent: gpuNames.isEmpty ? 0 : smokeGPUUtilization()
        )
    }

    private static func preferredNucleusChannel(in channels: [ChannelConfig]) -> ChannelConfig? {
        let preferredTokens = ["dapi", "hoechst", "nuclei", "nucleus", "nuclear", "ir191", "ir193"]
        return channels.first { channel in
            let text = (channel.fileName + " " + channel.marker).lowercased()
            return preferredTokens.contains { text.contains($0) }
        } ?? channels.first
    }

    private static func bestAssignmentScreeningParameters(outputFolder: URL) -> AssignmentParameters? {
        let url = OutputWriter.sectionURL("celltype_assignment", outputFolder: outputFolder)
            .appendingPathComponent("celltype_assignment_screening_results.json")
        guard let data = try? Data(contentsOf: url),
              let records = try? JSONDecoder().decode([AssignmentScanRecord].self, from: data) else {
            return nil
        }
        return records.min {
            if $0.unresolvedCount == $1.unresolvedCount {
                return $0.assignedCount > $1.assignedCount
            }
            return $0.unresolvedCount < $1.unresolvedCount
        }?.parameters
    }

    private static func smokeGPUUtilization() -> Double? {
        for className in ["AGXAccelerator", "IOAccelerator"] {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/sbin/ioreg")
            process.arguments = ["-r", "-d", "1", "-c", className]
            let outputPipe = Pipe()
            process.standardOutput = outputPipe
            process.standardError = Pipe()

            do {
                try process.run()
                let data = outputPipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                guard process.terminationStatus == 0,
                      let text = String(data: data, encoding: .utf8),
                      let value = parseDeviceUtilizationPercent(from: text) else {
                    continue
                }
                return value
            } catch {
                continue
            }
        }
        return nil
    }

    private static func parseDeviceUtilizationPercent(from text: String) -> Double? {
        let pattern = #""Device Utilization %"\s*=\s*([0-9]+(?:\.[0-9]+)?)"#
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
              match.numberOfRanges > 1,
              let range = Range(match.range(at: 1), in: text),
              let value = Double(text[range]) else {
            return nil
        }
        return min(max(value, 0), 100)
    }
}
