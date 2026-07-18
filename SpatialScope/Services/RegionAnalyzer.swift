import AppKit
import Foundation

enum RegionAnalyzer {
    private static let excludedTypes: Set<String> = ["Unassigned", "Ambiguous"]

    static func run(
        assignments: [CellTypeAssignment],
        parameters: RegionParameters,
        pixelSize: (Double, Double)?,
        canvasWidth: Int,
        canvasHeight: Int,
        cellTypeMask: UInt16Raster? = nil,
        cellTypeIDByName: [String: UInt16] = [:]
    ) throws -> RegionAnalysisResult {
        guard !assignments.isEmpty else {
            throw SpatialScopeError.message("Run cell-type assignment before region analysis.")
        }

        let width = max(1, canvasWidth)
        let height = max(1, canvasHeight)
        let pixelAreaUm2 = max(0.000_001, (pixelSize?.0 ?? 1.0) * (pixelSize?.1 ?? 1.0))
        let scaleUmPerPx = sqrt(pixelAreaUm2)
        let closePx = max(0, Int(round(parameters.closeUm / scaleUmPerPx)))
        let dilatePx = max(0, Int(round(parameters.dilateUm / scaleUmPerPx)))
        let minAreaPx = max(0, Int(round(parameters.minAreaUm2 / pixelAreaUm2)))
        let selectedTypes = selectedRegionTypes(from: assignments, parameters: parameters)
        guard !selectedTypes.isEmpty else {
            throw SpatialScopeError.message("Select at least one assigned cell type for region analysis.")
        }

        var regions: [RegionROI] = []
        for typeName in selectedTypes {
            var mask = rasterMask(
                for: typeName,
                assignments: assignments,
                width: width,
                height: height,
                cellTypeMask: cellTypeMask,
                cellTypeIDByName: cellTypeIDByName
            )
            if closePx > 0 {
                mask = mask.closedDisk(radius: closePx)
            }
            mask = mask.filledHoles()
            if minAreaPx > 0 {
                mask = mask.removingSmallObjects(minSize: minAreaPx)
            }
            if dilatePx > 0 {
                mask = mask.dilatedDisk(radius: dilatePx)
            }
            let typeCentroids = assignments
                .filter { $0.assignedType == typeName }
                .map { ($0.centroidX, $0.centroidY) }
            mask = mask.keepingComponents(containingCentroids: typeCentroids, minimumHits: max(1, parameters.minCells))
            guard !mask.isEmpty,
                  let region = makeRegion(
                    id: regions.count + 1,
                    typeName: typeName,
                    mask: mask,
                    assignments: assignments,
                    pixelAreaUm2: pixelAreaUm2,
                    parameters: parameters
                  ) else {
                continue
            }
            regions.append(region)
        }

        regions.sort {
            if $0.sourceType == $1.sourceType { return $0.id < $1.id }
            return ($0.sourceType ?? $0.dominantType).localizedStandardCompare($1.sourceType ?? $1.dominantType) == .orderedAscending
        }
        for index in regions.indices {
            regions[index].id = index + 1
        }

        let dominantCounts = makeDominantCounts(regions: regions)
        let image = renderRegionMap(
            assignments: assignments,
            regions: regions,
            width: width,
            height: height,
            parameters: parameters,
            cellTypeMask: cellTypeMask,
            cellTypeIDByName: cellTypeIDByName
        )
        let statsImage = renderDominantCountsPlot(counts: dominantCounts)

        return RegionAnalysisResult(
            regions: regions,
            dominantCounts: dominantCounts,
            parameters: parameters,
            image: image,
            statsImage: statsImage,
            width: width,
            height: height
        )
    }

    static func applyManualEdit(
        to result: RegionAnalysisResult,
        assignments: [CellTypeAssignment],
        mode: RegionManualEditMode,
        targetRegionID: Int?,
        displayName: String,
        polygonPoints: [CellBoundaryPoint],
        pixelSize: (Double, Double)?,
        seedCellTypes: Set<String>? = nil,
        manualParameters: RegionParameters? = nil
    ) throws -> RegionAnalysisResult {
        try applyManualEdit(
            to: result,
            assignments: assignments,
            mode: mode,
            targetRegionID: targetRegionID,
            displayName: displayName,
            polygonGroups: [polygonPoints],
            pixelSize: pixelSize,
            seedCellTypes: seedCellTypes,
            manualParameters: manualParameters
        )
    }

    static func applyManualEdit(
        to result: RegionAnalysisResult,
        assignments: [CellTypeAssignment],
        mode: RegionManualEditMode,
        targetRegionID: Int?,
        displayName: String,
        polygonGroups: [[CellBoundaryPoint]],
        pixelSize: (Double, Double)?,
        seedCellTypes: Set<String>? = nil,
        manualParameters: RegionParameters? = nil
    ) throws -> RegionAnalysisResult {
        let width = max(1, result.width)
        let height = max(1, result.height)
        var activeParameters = manualParameters ?? defaultManualParameters(from: result.parameters)
        let cleanName = displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanName.isEmpty else {
            throw SpatialScopeError.message("Enter a name for the adjusted ROI.")
        }
        let validPolygonGroups = polygonGroups.filter { $0.count >= 3 }
        guard !validPolygonGroups.isEmpty else {
            throw SpatialScopeError.message("Close at least one selection area before saving an adjusted ROI.")
        }

        let targetRegion = targetRegionID.flatMap { id in result.regions.first { $0.id == id } }
        if mode != .redraw, targetRegion == nil {
            throw SpatialScopeError.message("Select an existing ROI before using include or exclude editing.")
        }

        let availableSeedTypes = Set(assignments.map(\.assignedType).filter { !excludedTypes.contains($0) })
        let requestedSeedTypes = seedCellTypes.flatMap { types -> Set<String>? in
            let selectedTypes = Set(types
                .filter { !excludedTypes.contains($0) && availableSeedTypes.contains($0) }
            )
            return selectedTypes.isEmpty ? nil : selectedTypes
        }
        var allowedSeedTypes = requestedSeedTypes
            ?? targetRegion.flatMap { preferredSeedType(for: $0, availableTypes: availableSeedTypes).map { Set([$0]) } }
        if let allowedSeedTypes, !allowedSeedTypes.isEmpty {
            activeParameters.selectedTypes = allowedSeedTypes.sorted {
                $0.localizedStandardCompare($1) == .orderedAscending
            }
        }
        var selectedCells = assignments.filter { assignment in
            isSeedCandidate(assignment, allowedSeedTypes: allowedSeedTypes)
                && isCellSelected(assignment, by: validPolygonGroups)
        }
        if allowedSeedTypes == nil,
           let inferredType = dominantSeedType(in: selectedCells) {
            allowedSeedTypes = [inferredType]
            activeParameters.selectedTypes = [inferredType]
            selectedCells = selectedCells.filter { $0.assignedType == inferredType }
        }
        if isManualROIDebugEnabled {
            let selectedBounds = assignmentBounds(selectedCells)
            print("Manual ROI selected cells: \(selectedCells.count), centroid bounds: \(selectedBounds)")
        }
        guard !selectedCells.isEmpty else {
            throw SpatialScopeError.message("The drawn selection areas did not select any assigned cells.")
        }
        let measuredAssignments = assignments.filter {
            isSeedCandidate($0, allowedSeedTypes: allowedSeedTypes)
        }

        let adjustedSeedCells: [CellTypeAssignment]
        switch mode {
        case .redraw:
            adjustedSeedCells = selectedCells
        case .include:
            let baseMask = RegionAnalyzer.mask(for: targetRegion!, width: width, height: height)
            let baseCells = assignments.filter { assignment in
                isSeedCandidate(assignment, allowedSeedTypes: allowedSeedTypes)
                    && baseMask.contains(x: assignment.centroidX, y: assignment.centroidY)
            }
            let selectedIDs = Set(selectedCells.map(\.nucleusID))
            let baseIDs = Set(baseCells.map(\.nucleusID))
            adjustedSeedCells = baseCells + selectedCells.filter { !baseIDs.contains($0.nucleusID) && selectedIDs.contains($0.nucleusID) }
        case .exclude:
            let baseMask = RegionAnalyzer.mask(for: targetRegion!, width: width, height: height)
            let excludedIDs = Set(selectedCells.map(\.nucleusID))
            adjustedSeedCells = assignments.filter { assignment in
                isSeedCandidate(assignment, allowedSeedTypes: allowedSeedTypes)
                    && baseMask.contains(x: assignment.centroidX, y: assignment.centroidY)
                    && !excludedIDs.contains(assignment.nucleusID)
            }
        }
        guard !adjustedSeedCells.isEmpty else {
            throw SpatialScopeError.message("The adjusted ROI has no seed cells after applying this edit.")
        }

        let adjustedMask = manualBoundaryMask(
            from: adjustedSeedCells,
            width: width,
            height: height,
            parameters: activeParameters,
            pixelSize: pixelSize
        )
        guard !adjustedMask.isEmpty else {
            throw SpatialScopeError.message("The adjusted ROI is empty after applying this edit.")
        }

        let pixelAreaUm2 = max(0.000_001, (pixelSize?.0 ?? 1.0) * (pixelSize?.1 ?? 1.0))
        guard var adjustedRegion = makeRegion(
            id: (result.regions.map(\.id).max() ?? 0) + 1,
            typeName: cleanName,
            mask: adjustedMask,
            assignments: measuredAssignments,
            pixelAreaUm2: pixelAreaUm2,
            parameters: activeParameters
        ) else {
            throw SpatialScopeError.message("The adjusted ROI could not be converted to a saved region.")
        }

        adjustedRegion.name = "\(cleanName) region"
        adjustedRegion.sourceType = cleanName
        adjustedRegion.dominantType = cleanName
        adjustedRegion.colorHex = selectedCells.first?.colorHex ?? targetRegion?.colorHex ?? activeParameters.boundaryColor
        adjustedRegion.manualEditMode = mode.rawValue
        adjustedRegion.originalRegionID = targetRegion?.id
        adjustedRegion.originalSourceType = allowedSeedTypes?
            .sorted { $0.localizedStandardCompare($1) == .orderedAscending }
            .first
            ?? targetRegion.flatMap { preferredSeedType(for: $0, availableTypes: availableSeedTypes) }

        var updatedRegions = result.regions
        updatedRegions.append(adjustedRegion)
        let updatedCounts = makeDominantCounts(regions: updatedRegions)
        return RegionAnalysisResult(
            regions: updatedRegions,
            dominantCounts: updatedCounts,
            parameters: activeParameters,
            image: renderRegionMap(
                assignments: assignments,
                regions: [adjustedRegion],
                width: width,
                height: height,
                parameters: activeParameters
            ),
            statsImage: renderDominantCountsPlot(counts: updatedCounts),
            width: width,
            height: height
        )
    }

    private static func selectedRegionTypes(from assignments: [CellTypeAssignment], parameters: RegionParameters) -> [String] {
        let available = Set(assignments.map(\.assignedType).filter { !excludedTypes.contains($0) })
        let requested = parameters.selectedTypes.filter { available.contains($0) }
        return requested.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }

    private static func rasterMask(
        for typeName: String,
        assignments: [CellTypeAssignment],
        width: Int,
        height: Int,
        cellTypeMask: UInt16Raster?,
        cellTypeIDByName: [String: UInt16]
    ) -> RasterMask {
        if let cellTypeMask,
           cellTypeMask.width == width,
           cellTypeMask.height == height,
           let typeID = cellTypeIDByName[typeName] {
            return cellTypeMask.mask(for: typeID)
        }
        var mask = RasterMask(width: width, height: height)
        for assignment in assignments where assignment.assignedType == typeName {
            if let points = assignment.cellBoundaryPoints, points.count >= 3 {
                mask.fillPolygon(points)
            } else {
                let radius = max(2.0, sqrt(Double(max(1, assignment.areaPx)) / Double.pi))
                mask.fillDisk(centerX: assignment.centroidX, centerY: assignment.centroidY, radius: radius)
            }
        }
        return mask
    }

    private static func isSeedCandidate(_ assignment: CellTypeAssignment, allowedSeedTypes: Set<String>?) -> Bool {
        guard !excludedTypes.contains(assignment.assignedType) else { return false }
        guard let allowedSeedTypes else { return true }
        return allowedSeedTypes.contains(assignment.assignedType)
    }

    private static func preferredSeedType(for region: RegionROI, availableTypes: Set<String>) -> String? {
        for candidate in [region.originalSourceType, region.sourceType, region.dominantType] {
            let name = (candidate ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if !name.isEmpty, !excludedTypes.contains(name), availableTypes.contains(name) {
                return name
            }
        }
        return nil
    }

    private static func dominantSeedType(in cells: [CellTypeAssignment]) -> String? {
        let counts = Dictionary(grouping: cells.filter { !excludedTypes.contains($0.assignedType) }, by: \.assignedType)
            .mapValues(\.count)
        return counts.sorted {
            if $0.value == $1.value {
                return $0.key.localizedStandardCompare($1.key) == .orderedAscending
            }
            return $0.value > $1.value
        }
        .first?.key
    }

    private static func defaultManualParameters(from base: RegionParameters) -> RegionParameters {
        var parameters = base
        parameters.closeUm = min(base.closeUm, 2.0)
        parameters.dilateUm = 0.0
        parameters.minAreaUm2 = 0.0
        parameters.minCells = 1
        parameters.contourDownsample = 1
        return parameters
    }

    private static var isManualROIDebugEnabled: Bool {
        ProcessInfo.processInfo.environment["SPATIALSCOPE_DEBUG_MANUAL_ROI"] == "1"
    }

    private static func assignmentBounds(_ assignments: [CellTypeAssignment]) -> String {
        guard !assignments.isEmpty else { return "empty" }
        let minX = assignments.map(\.centroidX).min() ?? 0
        let maxX = assignments.map(\.centroidX).max() ?? 0
        let minY = assignments.map(\.centroidY).min() ?? 0
        let maxY = assignments.map(\.centroidY).max() ?? 0
        return String(format: "x %.1f...%.1f, y %.1f...%.1f", minX, maxX, minY, maxY)
    }

    private static func isCellSelected(_ assignment: CellTypeAssignment, by polygons: [[CellBoundaryPoint]]) -> Bool {
        let centroid = CellBoundaryPoint(x: assignment.centroidX, y: assignment.centroidY)
        return polygons.contains(where: { contains(point: centroid, in: $0) })
    }

    private static func contains(point: CellBoundaryPoint, in polygon: [CellBoundaryPoint]) -> Bool {
        guard polygon.count >= 3 else { return false }
        var isInside = false
        var previous = polygon[polygon.count - 1]
        for current in polygon {
            if isPoint(point, onSegmentFrom: previous, to: current) {
                return true
            }
            let crosses = (current.y > point.y) != (previous.y > point.y)
            if crosses {
                let denominator = previous.y - current.y
                if abs(denominator) > 1e-9 {
                    let intersectionX = current.x + (point.y - current.y) * (previous.x - current.x) / denominator
                    if point.x <= intersectionX {
                        isInside.toggle()
                    }
                }
            }
            previous = current
        }
        return isInside
    }

    private static func isPoint(_ point: CellBoundaryPoint, onSegmentFrom a: CellBoundaryPoint, to b: CellBoundaryPoint) -> Bool {
        let cross = (point.y - a.y) * (b.x - a.x) - (point.x - a.x) * (b.y - a.y)
        guard abs(cross) <= 1e-6 else { return false }
        let minX = min(a.x, b.x) - 1e-6
        let maxX = max(a.x, b.x) + 1e-6
        let minY = min(a.y, b.y) - 1e-6
        let maxY = max(a.y, b.y) + 1e-6
        return point.x >= minX && point.x <= maxX && point.y >= minY && point.y <= maxY
    }

    private static func makeRegion(
        id: Int,
        typeName: String,
        mask: RasterMask,
        assignments: [CellTypeAssignment],
        pixelAreaUm2: Double,
        parameters: RegionParameters
    ) -> RegionROI? {
        guard let box = mask.boundingBox() else { return nil }
        let cells = assignments.filter { mask.contains(x: $0.centroidX, y: $0.centroidY) }
        let counts = Dictionary(grouping: cells, by: \.assignedType).mapValues(\.count)
        let assignedCellCount = cells.filter { !excludedTypes.contains($0.assignedType) }.count
        let areaPx = Double(mask.area)
        let areaUm2 = areaPx * pixelAreaUm2
        let color = parameters.useTypeColors
            ? (assignments.first { $0.assignedType == typeName }?.colorHex ?? parameters.boundaryColor)
            : parameters.boundaryColor

        return RegionROI(
            id: id,
            name: "\(typeName) region",
            sourceType: typeName,
            xPx: Double(box.x),
            yPx: Double(box.y),
            widthPx: Double(box.width),
            heightPx: Double(box.height),
            centroidX: Double(box.x) + Double(box.width) / 2.0,
            centroidY: Double(box.y) + Double(box.height) / 2.0,
            areaPx: areaPx,
            areaUm2: areaUm2,
            cellCount: cells.count,
            assignedCellCount: assignedCellCount,
            dominantType: typeName,
            colorHex: color,
            countsByType: counts,
            maskRuns: mask.toRuns()
        )
    }

    private static func makeDominantCounts(regions: [RegionROI]) -> [RegionTypeCount] {
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

    static func mask(for region: RegionROI, width: Int, height: Int) -> RasterMask {
        if let runs = region.maskRuns, !runs.isEmpty {
            return RasterMask(width: width, height: height, runs: runs)
        }
        var fallback = RasterMask(width: width, height: height)
        let x0 = max(0, Int(floor(region.xPx)))
        let y0 = max(0, Int(floor(region.yPx)))
        let x1 = min(width - 1, Int(ceil(region.xPx + region.widthPx)))
        let y1 = min(height - 1, Int(ceil(region.yPx + region.heightPx)))
        guard x0 <= x1, y0 <= y1 else { return fallback }
        for y in y0...y1 {
            for x in x0...x1 {
                fallback[x, y] = true
            }
        }
        return fallback
    }

    private static func manualBoundaryMask(
        from seedCells: [CellTypeAssignment],
        width: Int,
        height: Int,
        parameters: RegionParameters,
        pixelSize: (Double, Double)?
    ) -> RasterMask {
        let pixelAreaUm2 = max(0.000_001, (pixelSize?.0 ?? 1.0) * (pixelSize?.1 ?? 1.0))
        let scaleUmPerPx = sqrt(pixelAreaUm2)
        let closePx = max(0, Int(round(parameters.closeUm / scaleUmPerPx)))
        let dilatePx = max(0, Int(round(parameters.dilateUm / scaleUmPerPx)))
        let minAreaPx = max(0, Int(round(parameters.minAreaUm2 / pixelAreaUm2)))
        let seedCentroids = seedCells.map { ($0.centroidX, $0.centroidY) }

        var seedMask = RasterMask(width: width, height: height)
        for cell in seedCells {
            if let points = cell.cellBoundaryPoints, points.count >= 3 {
                seedMask.fillPolygon(points)
            } else {
                let radius = max(2.0, sqrt(Double(max(1, cell.areaPx)) / Double.pi))
                seedMask.fillDisk(centerX: cell.centroidX, centerY: cell.centroidY, radius: radius)
            }
        }
        if isManualROIDebugEnabled {
            print("Manual ROI seed mask bbox before morphology: \(String(describing: seedMask.boundingBox())), area: \(seedMask.area)")
        }

        let envelopeRadius = max(2, closePx + dilatePx + 2)
        let localEnvelope = rectangularEnvelope(around: seedMask, radius: envelopeRadius)
        var output = seedMask
        if closePx > 0 {
            output = output.closed(radius: closePx)
        }
        output = output.intersecting(localEnvelope)
        output = output.filledHoles().intersecting(localEnvelope)

        if minAreaPx > 0 {
            let filtered = output.removingSmallObjects(minSize: minAreaPx)
            if !filtered.isEmpty {
                output = filtered
            }
        }

        if dilatePx > 0 {
            output = output.dilated(radius: dilatePx).intersecting(localEnvelope)
        }

        let kept = output.keepingComponents(containingCentroids: seedCentroids, minimumHits: 1)
        if isManualROIDebugEnabled {
            print("Manual ROI kept mask bbox: \(String(describing: kept.boundingBox())), area: \(kept.area)")
        }
        return kept
    }

    private static func rectangularEnvelope(around mask: RasterMask, radius: Int) -> RasterMask {
        guard let box = mask.boundingBox() else {
            return RasterMask(width: mask.width, height: mask.height)
        }
        let x0 = max(0, box.x - radius)
        let x1 = min(mask.width - 1, box.x + box.width - 1 + radius)
        let y0 = max(0, box.y - radius)
        let y1 = min(mask.height - 1, box.y + box.height - 1 + radius)
        var envelope = RasterMask(width: mask.width, height: mask.height)
        guard x0 <= x1, y0 <= y1 else { return envelope }
        for y in y0...y1 {
            for x in x0...x1 {
                envelope[x, y] = true
            }
        }
        return envelope
    }

    static func renderRegionMap(
        assignments: [CellTypeAssignment],
        regions: [RegionROI],
        width: Int,
        height: Int,
        parameters: RegionParameters,
        cellTypeMask: UInt16Raster? = nil,
        cellTypeIDByName: [String: UInt16] = [:]
    ) -> NSImage {
        let colorByTypeName = assignmentColorByTypeName(assignments)
        let image = NSImage(size: NSSize(width: width, height: height))
        image.lockFocus()
        NSColor.black.setFill()
        NSRect(x: 0, y: 0, width: width, height: height).fill()

        drawCellTypeMask(
            assignments: assignments,
            cellTypeMask: cellTypeMask,
            cellTypeIDByName: cellTypeIDByName,
            width: width,
            height: height,
            xOffset: 0,
            yOffset: 0
        )
        drawSmoothRegionBoundaries(
            regions: regions,
            width: width,
            height: height,
            parameters: parameters,
            colorByTypeName: colorByTypeName,
            xOffset: 0,
            yOffset: 0
        )
        drawRegionTitle(regions: regions, width: width, height: height, xOffset: 0, yOffset: 0)
        drawRegionLegendLabels(
            regions: regions,
            width: width,
            height: height,
            parameters: parameters,
            colorByTypeName: colorByTypeName,
            xOffset: 0,
            yOffset: 0
        )

        image.unlockFocus()
        return image
    }

    static func renderRegionSinglePanelMap(
        assignments: [CellTypeAssignment],
        regions: [RegionROI],
        width: Int,
        height: Int,
        parameters: RegionParameters,
        cellTypeMask: UInt16Raster? = nil,
        cellTypeIDByName: [String: UInt16] = [:],
        pixelSize: (Double, Double)? = nil,
        title: String? = nil
    ) -> NSImage {
        let colorByTypeName = assignmentColorByTypeName(assignments)
        let panelWidth = Double(max(1, width))
        let panelHeight = Double(max(1, height))
        let headerHeight = title == nil ? 18.0 : 62.0
        let footerHeight = 18.0
        let canvasHeight = panelHeight + headerHeight + footerHeight
        let image = NSImage(size: NSSize(width: panelWidth, height: canvasHeight))

        image.lockFocus()
        NSColor.black.setFill()
        NSRect(x: 0, y: 0, width: panelWidth, height: canvasHeight).fill()

        if let title {
            title.draw(
                in: NSRect(x: 18, y: canvasHeight - 42, width: panelWidth - 36, height: 28),
                withAttributes: [
                    .font: NSFont.systemFont(ofSize: 22, weight: .semibold),
                    .foregroundColor: NSColor.white
                ]
            )
        }

        let panelY = footerHeight
        drawCellTypeMask(
            assignments: assignments,
            cellTypeMask: cellTypeMask,
            cellTypeIDByName: cellTypeIDByName,
            width: width,
            height: height,
            xOffset: 0,
            yOffset: panelY
        )
        drawSmoothRegionBoundaries(
            regions: regions,
            width: width,
            height: height,
            parameters: parameters,
            colorByTypeName: colorByTypeName,
            xOffset: 0,
            yOffset: panelY
        )
        drawScaleBar(pixelSize: pixelSize, width: width, height: height, xOffset: 0, yOffset: panelY)
        drawPanelLabel("Cell-type mask", x: max(8.0, panelWidth - 190.0), y: panelHeight + panelY - 34.0)
        drawRegionLegendLabels(
            regions: regions,
            width: width,
            height: height,
            parameters: parameters,
            colorByTypeName: colorByTypeName,
            xOffset: 0,
            yOffset: panelY
        )

        image.unlockFocus()
        return image
    }

    static func renderRegionComparisonMap(
        overlayImage: NSImage?,
        assignments: [CellTypeAssignment],
        regions: [RegionROI],
        width: Int,
        height: Int,
        parameters: RegionParameters,
        cellTypeMask: UInt16Raster? = nil,
        cellTypeIDByName: [String: UInt16] = [:],
        pixelSize: (Double, Double)? = nil,
        cellTypeMaskOnLeft: Bool = false,
        title: String? = nil
    ) -> NSImage {
        let colorByTypeName = assignmentColorByTypeName(assignments)
        let panelWidth = Double(max(1, width))
        let panelHeight = Double(max(1, height))
        let gap = 28.0
        let headerHeight = title == nil ? 18.0 : 62.0
        let footerHeight = 18.0
        let canvasWidth = panelWidth * 2.0 + gap
        let canvasHeight = panelHeight + headerHeight + footerHeight
        let image = NSImage(size: NSSize(width: canvasWidth, height: canvasHeight))

        image.lockFocus()
        NSColor.black.setFill()
        NSRect(x: 0, y: 0, width: canvasWidth, height: canvasHeight).fill()

        if let title {
            title.draw(
                in: NSRect(x: 18, y: canvasHeight - 42, width: canvasWidth - 36, height: 28),
                withAttributes: [
                    .font: NSFont.systemFont(ofSize: 22, weight: .semibold),
                    .foregroundColor: NSColor.white
                ]
            )
        }

        let panelY = footerHeight
        let leftX = 0.0
        let rightX = panelWidth + gap
        let leftRect = NSRect(x: leftX, y: panelY, width: panelWidth, height: panelHeight)
        let rightRect = NSRect(x: rightX, y: panelY, width: panelWidth, height: panelHeight)

        func drawOverlay(at rect: NSRect) {
            if let overlayImage {
                overlayImage.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1.0)
            } else {
                NSColor.black.setFill()
                rect.fill()
            }
        }

        func drawMask(at xOffset: Double) {
            drawCellTypeMask(
                assignments: assignments,
                cellTypeMask: cellTypeMask,
                cellTypeIDByName: cellTypeIDByName,
                width: width,
                height: height,
                xOffset: xOffset,
                yOffset: panelY
            )
        }

        if cellTypeMaskOnLeft {
            drawMask(at: leftX)
            drawOverlay(at: rightRect)
        } else {
            drawOverlay(at: leftRect)
            drawMask(at: rightX)
        }

        for xOffset in [leftX, rightX] {
            drawSmoothRegionBoundaries(
                regions: regions,
                width: width,
                height: height,
                parameters: parameters,
                colorByTypeName: colorByTypeName,
                xOffset: xOffset,
                yOffset: panelY
            )
            drawScaleBar(pixelSize: pixelSize, width: width, height: height, xOffset: xOffset, yOffset: panelY)
        }

        let leftLabel = cellTypeMaskOnLeft ? "Cell-type mask" : "Overlay preview"
        let rightLabel = cellTypeMaskOnLeft ? "Overlay preview" : "Cell-type mask"
        drawPanelLabel(leftLabel, x: panelWidth - 190, y: panelHeight + panelY - 34)
        drawPanelLabel(rightLabel, x: rightX + panelWidth - 190, y: panelHeight + panelY - 34)
        drawRegionLegendLabels(
            regions: regions,
            width: width,
            height: height,
            parameters: parameters,
            colorByTypeName: colorByTypeName,
            xOffset: leftX,
            yOffset: panelY
        )
        drawRegionLegendLabels(
            regions: regions,
            width: width,
            height: height,
            parameters: parameters,
            colorByTypeName: colorByTypeName,
            xOffset: rightX,
            yOffset: panelY
        )

        image.unlockFocus()
        return image
    }

    private static func drawCellTypeMask(
        assignments: [CellTypeAssignment],
        cellTypeMask: UInt16Raster?,
        cellTypeIDByName: [String: UInt16],
        width: Int,
        height: Int,
        xOffset: Double,
        yOffset: Double
    ) {
        NSColor.black.setFill()
        NSRect(x: xOffset, y: yOffset, width: Double(width), height: Double(height)).fill()

        if let cellTypeMask,
           cellTypeMask.width == width,
           cellTypeMask.height == height,
           !cellTypeIDByName.isEmpty {
            drawCellTypeRasterMask(
                cellTypeMask,
                assignments: assignments,
                cellTypeIDByName: cellTypeIDByName,
                xOffset: xOffset,
                yOffset: yOffset
            )
            return
        }

        for assignment in assignments where !excludedTypes.contains(assignment.assignedType) {
            let color = NSColor(hex: assignment.colorHex) ?? .systemGray
            let path = cellPath(for: assignment, height: height, xOffset: xOffset, yOffset: yOffset)
            color.withAlphaComponent(0.80).setFill()
            path.fill()
        }
    }

    private static func drawCellTypeRasterMask(
        _ cellTypeMask: UInt16Raster,
        assignments: [CellTypeAssignment],
        cellTypeIDByName: [String: UInt16],
        xOffset: Double,
        yOffset: Double
    ) {
        let selectedTypes = Set(assignments.map(\.assignedType).filter { !excludedTypes.contains($0) })
        let colorByTypeName = assignmentColorByTypeName(assignments)
        let selectedIDs = Set(cellTypeIDByName.compactMap { name, id in
            selectedTypes.contains(name) ? id : nil
        })
        let colorByID = Dictionary(uniqueKeysWithValues: cellTypeIDByName.compactMap { name, id -> (UInt16, NSColor)? in
            guard selectedIDs.contains(id),
                  let color = NSColor(hex: colorByTypeName[name] ?? "") else {
                return nil
            }
            return (id, color.withAlphaComponent(0.80))
        })
        guard !colorByID.isEmpty else { return }

        for y in 0..<cellTypeMask.height {
            var x = 0
            while x < cellTypeMask.width {
                let value = cellTypeMask[x, y]
                guard let color = colorByID[value] else {
                    x += 1
                    continue
                }
                let start = x
                x += 1
                while x < cellTypeMask.width, cellTypeMask[x, y] == value {
                    x += 1
                }
                color.setFill()
                NSRect(
                    x: xOffset + Double(start),
                    y: yOffset + Double(cellTypeMask.height - y - 1),
                    width: Double(x - start),
                    height: 1.0
                ).fill()
            }
        }
    }

    static func drawSmoothRegionBoundaries(
        regions: [RegionROI],
        width: Int,
        height: Int,
        parameters: RegionParameters,
        colorByTypeName: [String: String] = [:],
        xOffset: Double,
        yOffset: Double
    ) {
        for region in regions {
            let color = boundaryColor(for: region, parameters: parameters, colorByTypeName: colorByTypeName)
            let mask = mask(for: region, width: width, height: height)
            strokeMaskContour(
                mask: mask,
                color: color,
                lineWidth: max(0.5, parameters.lineWidth),
                lineStyle: parameters.lineStyle,
                contourDownsample: max(1, parameters.contourDownsample),
                xOffset: xOffset,
                yOffset: yOffset,
                outputWidth: width,
                outputHeight: height
            )
        }
    }

    private static func assignmentColorByTypeName(_ assignments: [CellTypeAssignment]) -> [String: String] {
        var colors: [String: String] = [:]
        for assignment in assignments where !excludedTypes.contains(assignment.assignedType) {
            colors[assignment.assignedType] = colors[assignment.assignedType] ?? assignment.colorHex
        }
        return colors
    }

    private static func boundaryColor(
        for region: RegionROI,
        parameters: RegionParameters,
        colorByTypeName: [String: String]
    ) -> NSColor {
        if parameters.useTypeColors {
            let typeName = region.sourceType ?? region.dominantType
            return NSColor(hex: colorByTypeName[typeName] ?? region.colorHex) ?? .systemGreen
        }
        return NSColor(hex: parameters.boundaryColor) ?? .systemGreen
    }

    private struct ContourPoint: Hashable {
        let x2: Int
        let y2: Int
    }

    private struct ContourSegment {
        let start: ContourPoint
        let end: ContourPoint
    }

    private struct ContourEdge: Hashable {
        let a: ContourPoint
        let b: ContourPoint

        init(_ p1: ContourPoint, _ p2: ContourPoint) {
            if RegionAnalyzer.contourPointComesBefore(p1, p2) {
                self.a = p1
                self.b = p2
            } else {
                self.a = p2
                self.b = p1
            }
        }
    }

    private static func strokeMaskContour(
        mask: RasterMask,
        color: NSColor,
        lineWidth: Double,
        lineStyle: String,
        contourDownsample: Int,
        xOffset: Double,
        yOffset: Double,
        outputWidth: Int,
        outputHeight: Int
    ) {
        let factor = max(1, contourDownsample)
        let contourMask = factor > 1 ? downsampledMask(mask, factor: factor) : mask
        let segments = contourSegments(for: contourMask)
        guard !segments.isEmpty else { return }
        let polylines = contourPolylines(from: segments)

        let path = NSBezierPath()
        path.lineWidth = CGFloat(lineWidth)
        path.lineCapStyle = .round
        path.lineJoinStyle = .round
        applyLineDash(style: lineStyle, lineWidth: CGFloat(lineWidth), to: path)

        let maxX = Double(outputWidth)
        let maxY = Double(outputHeight)
        func point(_ contourPoint: ContourPoint) -> NSPoint {
            let rawX = Double(contourPoint.x2) * 0.5 * Double(factor)
            let rawY = Double(contourPoint.y2) * 0.5 * Double(factor)
            let clampedX = min(max(rawX, 0.0), maxX)
            let clampedY = min(max(rawY, 0.0), maxY)
            return NSPoint(
                x: xOffset + clampedX,
                y: yOffset + maxY - clampedY
            )
        }

        for polyline in polylines {
            appendSmoothPolyline(polyline, to: path, point: point(_:))
        }
        color.setStroke()
        path.stroke()
    }

    private static func contourPointComesBefore(_ lhs: ContourPoint, _ rhs: ContourPoint) -> Bool {
        if lhs.y2 == rhs.y2 {
            return lhs.x2 <= rhs.x2
        }
        return lhs.y2 < rhs.y2
    }

    private static func contourPolylines(from segments: [ContourSegment]) -> [[ContourPoint]] {
        var adjacency: [ContourPoint: [ContourPoint]] = [:]
        for segment in segments {
            adjacency[segment.start, default: []].append(segment.end)
            adjacency[segment.end, default: []].append(segment.start)
        }

        var usedEdges = Set<ContourEdge>()
        var polylines: [[ContourPoint]] = []
        let starts = adjacency.keys.sorted { lhs, rhs in
            let lhsDegree = adjacency[lhs]?.count ?? 0
            let rhsDegree = adjacency[rhs]?.count ?? 0
            if lhsDegree != rhsDegree {
                return lhsDegree != 2 && rhsDegree == 2
            }
            return contourPointComesBefore(lhs, rhs)
        }

        for start in starts {
            while let firstNext = adjacency[start]?.first(where: { !usedEdges.contains(ContourEdge(start, $0)) }) {
                var path = [start]
                var previous: ContourPoint? = nil
                var current = start
                var next: ContourPoint? = firstNext

                while let destination = next {
                    let edge = ContourEdge(current, destination)
                    guard !usedEdges.contains(edge) else { break }
                    usedEdges.insert(edge)
                    path.append(destination)

                    previous = current
                    current = destination
                    if current == start {
                        break
                    }
                    next = adjacency[current]?.first(where: { candidate in
                        candidate != previous && !usedEdges.contains(ContourEdge(current, candidate))
                    }) ?? adjacency[current]?.first(where: { !usedEdges.contains(ContourEdge(current, $0)) })
                }

                if path.count > 1 {
                    polylines.append(path)
                }
            }
        }
        return polylines
    }

    private static func appendSmoothPolyline(
        _ polyline: [ContourPoint],
        to path: NSBezierPath,
        point: (ContourPoint) -> NSPoint
    ) {
        guard polyline.count >= 2 else { return }
        let points = polyline.map(point)
        path.move(to: points[0])
        guard points.count >= 4 else {
            for next in points.dropFirst() {
                path.line(to: next)
            }
            return
        }

        for index in 0..<(points.count - 1) {
            let p0 = points[max(0, index - 1)]
            let p1 = points[index]
            let p2 = points[index + 1]
            let p3 = points[min(points.count - 1, index + 2)]
            let cp1 = NSPoint(
                x: p1.x + (p2.x - p0.x) / 6.0,
                y: p1.y + (p2.y - p0.y) / 6.0
            )
            let cp2 = NSPoint(
                x: p2.x - (p3.x - p1.x) / 6.0,
                y: p2.y - (p3.y - p1.y) / 6.0
            )
            path.curve(to: p2, controlPoint1: cp1, controlPoint2: cp2)
        }
    }

    private static func downsampledMask(_ mask: RasterMask, factor: Int) -> RasterMask {
        let factor = max(1, factor)
        guard factor > 1 else { return mask }
        let outWidth = max(1, Int(ceil(Double(mask.width) / Double(factor))))
        let outHeight = max(1, Int(ceil(Double(mask.height) / Double(factor))))
        var out = RasterMask(width: outWidth, height: outHeight)
        for outY in 0..<outHeight {
            let y0 = outY * factor
            let y1 = min(mask.height, y0 + factor)
            for outX in 0..<outWidth {
                let x0 = outX * factor
                let x1 = min(mask.width, x0 + factor)
                var hasPixel = false
                for y in y0..<y1 {
                    for x in x0..<x1 where mask[x, y] {
                        hasPixel = true
                        break
                    }
                    if hasPixel { break }
                }
                out[outX, outY] = hasPixel
            }
        }
        return out
    }

    private static func contourSegments(for mask: RasterMask) -> [ContourSegment] {
        var segments: [ContourSegment] = []
        guard !mask.isEmpty else { return segments }

        func sample(_ x: Int, _ y: Int) -> Bool {
            mask[x, y]
        }

        for y in -1..<mask.height {
            for x in -1..<mask.width {
                let topLeft = sample(x, y)
                let topRight = sample(x + 1, y)
                let bottomRight = sample(x + 1, y + 1)
                let bottomLeft = sample(x, y + 1)
                let code = (topLeft ? 8 : 0) | (topRight ? 4 : 0) | (bottomRight ? 2 : 0) | (bottomLeft ? 1 : 0)
                guard code != 0, code != 15 else { continue }

                let top = ContourPoint(x2: 2 * x + 1, y2: 2 * y)
                let right = ContourPoint(x2: 2 * x + 2, y2: 2 * y + 1)
                let bottom = ContourPoint(x2: 2 * x + 1, y2: 2 * y + 2)
                let left = ContourPoint(x2: 2 * x, y2: 2 * y + 1)

                switch code {
                case 1:
                    segments.append(ContourSegment(start: left, end: bottom))
                case 2:
                    segments.append(ContourSegment(start: bottom, end: right))
                case 3:
                    segments.append(ContourSegment(start: left, end: right))
                case 4:
                    segments.append(ContourSegment(start: top, end: right))
                case 5:
                    segments.append(ContourSegment(start: top, end: left))
                    segments.append(ContourSegment(start: bottom, end: right))
                case 6:
                    segments.append(ContourSegment(start: top, end: bottom))
                case 7:
                    segments.append(ContourSegment(start: left, end: top))
                case 8:
                    segments.append(ContourSegment(start: left, end: top))
                case 9:
                    segments.append(ContourSegment(start: top, end: bottom))
                case 10:
                    segments.append(ContourSegment(start: top, end: right))
                    segments.append(ContourSegment(start: left, end: bottom))
                case 11:
                    segments.append(ContourSegment(start: top, end: right))
                case 12:
                    segments.append(ContourSegment(start: left, end: right))
                case 13:
                    segments.append(ContourSegment(start: bottom, end: right))
                case 14:
                    segments.append(ContourSegment(start: left, end: bottom))
                default:
                    break
                }
            }
        }
        return segments
    }

    private static func applyLineDash(style: String, lineWidth: CGFloat, to path: NSBezierPath) {
        let scale = max(1.0, lineWidth)
        let pattern: [CGFloat]
        switch style {
        case "--":
            pattern = [6.0 * scale, 4.0 * scale]
        case "-.":
            pattern = [7.0 * scale, 4.0 * scale, 1.8 * scale, 4.0 * scale]
        case ":":
            pattern = [1.4 * scale, 4.0 * scale]
        default:
            pattern = []
        }
        guard !pattern.isEmpty else { return }
        pattern.withUnsafeBufferPointer { buffer in
            path.setLineDash(buffer.baseAddress, count: buffer.count, phase: 0)
        }
    }

    private static func drawRegionTitle(regions: [RegionROI], width: Int, height: Int, xOffset: Double, yOffset: Double) {
        let labels = regions.map { $0.sourceType ?? $0.dominantType }
        guard !labels.isEmpty else { return }
        let title = "Boundaries: " + labels.joined(separator: ", ")
        title.draw(
            in: NSRect(x: xOffset + 18, y: yOffset + Double(height) - 38, width: Double(width) - 36, height: 28),
            withAttributes: [
                .font: NSFont.systemFont(ofSize: 22, weight: .bold),
                .foregroundColor: NSColor.white
            ]
        )
    }

    private static func drawRegionLegendLabels(
        regions: [RegionROI],
        width: Int,
        height: Int,
        parameters: RegionParameters,
        colorByTypeName: [String: String],
        xOffset: Double,
        yOffset: Double
    ) {
        guard !regions.isEmpty else { return }
        let maxLabels = min(12, regions.count)
        for (index, region) in regions.prefix(maxLabels).enumerated() {
            let text = region.sourceType ?? region.dominantType
            let color = boundaryColor(for: region, parameters: parameters, colorByTypeName: colorByTypeName)
            let textWidth = min(300.0, max(78.0, Double(text.count) * 9.0 + 18.0))
            let x = xOffset + Double(width) - textWidth - 16
            let y = yOffset + Double(height) - 74.0 - Double(index) * 30.0
            let rect = NSRect(x: x, y: y, width: textWidth, height: 24)
            NSColor.black.withAlphaComponent(0.34).setFill()
            NSBezierPath(roundedRect: rect, xRadius: 4, yRadius: 4).fill()
            text.draw(
                in: rect.insetBy(dx: 8, dy: 4),
                withAttributes: [
                    .font: NSFont.systemFont(ofSize: 13, weight: .bold),
                    .foregroundColor: color
                ]
            )
        }
    }

    private static func drawPanelLabel(_ text: String, x: Double, y: Double) {
        let rect = NSRect(x: x, y: y, width: 172, height: 24)
        NSColor.black.withAlphaComponent(0.36).setFill()
        NSBezierPath(roundedRect: rect, xRadius: 4, yRadius: 4).fill()
        text.draw(
            in: rect.insetBy(dx: 8, dy: 4),
            withAttributes: [
                .font: NSFont.systemFont(ofSize: 13, weight: .bold),
                .foregroundColor: NSColor.white
            ]
        )
    }

    private static func drawScaleBar(pixelSize: (Double, Double)?, width: Int, height: Int, xOffset: Double, yOffset: Double) {
        guard let pixelSize, pixelSize.0 > 0 else { return }
        let barUm = 20.0
        let barWidthPx = min(Double(width) * 0.25, max(18.0, barUm / pixelSize.0))
        let x = xOffset + Double(width) * 0.055
        let y = yOffset + Double(height) * 0.065
        let path = NSBezierPath()
        path.lineWidth = 4.0
        path.lineCapStyle = .butt
        path.move(to: NSPoint(x: x, y: y))
        path.line(to: NSPoint(x: x + barWidthPx, y: y))
        NSColor.white.setStroke()
        path.stroke()
        "20 um".draw(
            at: NSPoint(x: x, y: y + 8),
            withAttributes: [
                .font: NSFont.systemFont(ofSize: 13, weight: .semibold),
                .foregroundColor: NSColor.white
            ]
        )
    }

    private static func cellPath(
        for assignment: CellTypeAssignment,
        height: Int,
        xOffset: Double = 0,
        yOffset: Double = 0
    ) -> NSBezierPath {
        if let points = assignment.cellBoundaryPoints, points.count >= 3 {
            let path = NSBezierPath()
            path.move(to: NSPoint(x: xOffset + points[0].x, y: yOffset + Double(height) - points[0].y))
            for point in points.dropFirst() {
                path.line(to: NSPoint(x: xOffset + point.x, y: yOffset + Double(height) - point.y))
            }
            path.close()
            return path
        }
        let radius = max(1.8, min(8.0, sqrt(Double(max(1, assignment.areaPx)) / Double.pi)))
        let rect = NSRect(
            x: xOffset + assignment.centroidX - radius,
            y: yOffset + Double(height) - assignment.centroidY - radius,
            width: radius * 2.0,
            height: radius * 2.0
        )
        return NSBezierPath(ovalIn: rect)
    }

    static func renderDominantCountsPlot(counts: [RegionTypeCount]) -> NSImage {
        let width = 760.0
        let height = 430.0
        let left = 132.0
        let right = 32.0
        let top = 62.0
        let bottom = 126.0
        let plotWidth = width - left - right
        let plotHeight = height - top - bottom
        let maxCount = max(1, counts.map(\.count).max() ?? 1)
        let image = NSImage(size: NSSize(width: width, height: height))

        image.lockFocus()
        NSColor.white.setFill()
        NSRect(x: 0, y: 0, width: width, height: height).fill()
        NSColor(calibratedWhite: 0.12, alpha: 1).setStroke()
        NSBezierPath.strokeLine(from: NSPoint(x: left, y: bottom), to: NSPoint(x: left, y: height - top))
        NSBezierPath.strokeLine(from: NSPoint(x: left, y: bottom), to: NSPoint(x: width - right, y: bottom))
        "Cells inside computational ROIs".draw(
            at: NSPoint(x: left, y: height - 32),
            withAttributes: [
                .font: NSFont.systemFont(ofSize: 28, weight: .semibold),
                .foregroundColor: NSColor(calibratedWhite: 0.12, alpha: 1)
            ]
        )

        let step = plotWidth / Double(max(1, counts.count))
        let barWidth = min(72.0, step * 0.62)
        for (index, row) in counts.enumerated() {
            let barHeight = Double(row.count) / Double(maxCount) * plotHeight
            let x = left + Double(index) * step + (step - barWidth) / 2.0
            let y = bottom
            (NSColor(hex: row.colorHex) ?? .systemGreen).setFill()
            NSRect(x: x, y: y, width: barWidth, height: barHeight).fill()
            "\(row.count)".draw(
                at: NSPoint(x: x, y: y + barHeight + 7),
                withAttributes: [
                    .font: NSFont.systemFont(ofSize: 19, weight: .medium),
                    .foregroundColor: NSColor(calibratedWhite: 0.12, alpha: 1)
                ]
            )
            StatPlotDrawing.drawRotatedXAxisLabel(
                row.name,
                anchor: NSPoint(x: x + barWidth / 2.0 + 4.0, y: bottom - 14.0),
                maxWidth: 118,
                font: NSFont.systemFont(ofSize: 18, weight: .regular)
            )
        }

        image.unlockFocus()
        return image
    }
}
