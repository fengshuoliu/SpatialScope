import AppKit
import Darwin
import Foundation

enum DistanceAnalyzer {
    static func runNearestNeighborAnalysis(
        assignments: [CellTypeAssignment],
        targetType: String,
        queryTypes: [String],
        pixelSize: (Double, Double)?,
        canvasWidth: Int,
        canvasHeight: Int
    ) throws -> DistanceAnalysisResult {
        guard !assignments.isEmpty else {
            throw SpatialScopeError.message("Run cell-type assignment before distance analysis.")
        }
        let targetType = targetType.trimmingCharacters(in: .whitespacesAndNewlines)
        let queryTypes = orderedNonEmpty(queryTypes)
        guard !targetType.isEmpty else {
            throw SpatialScopeError.message("Select a target cell type.")
        }
        guard !queryTypes.isEmpty else {
            throw SpatialScopeError.message("Select at least one query cell type.")
        }

        let pixel = resolvedPixelSize(pixelSize)
        let distances = try queryTypes.flatMap { queryType in
            try nearestDistances(
                assignments: assignments,
                targetType: targetType,
                queryType: queryType,
                pixelSize: pixel
            )
        }
        let tTests = pairedTTests(nearestDistances: distances, orderedQueryTypes: queryTypes)
        let summaries = queryTypes.map { queryType in
            summary(
                metric: "\(targetType) to \(queryType)",
                values: distances
                    .filter { ($0.nearestType ?? "") == queryType }
                    .map(\.nearestDistanceUm)
            )
        }
        let image = renderNearestNeighborFigure(
            distances: distances,
            targetType: targetType,
            queryTypes: queryTypes,
            tTests: tTests
        )

        return DistanceAnalysisResult(
            nearestDistances: distances,
            boundaryDistances: [],
            nearestTTests: tTests,
            boundaryTTests: [],
            summaries: summaries,
            image: image,
            nearestHistogramImage: image,
            boundaryHistogramImage: NSImage(size: NSSize(width: 1, height: 1)),
            width: max(1, canvasWidth),
            height: max(1, canvasHeight),
            nearestTargetType: targetType,
            nearestQueryTypes: queryTypes
        )
    }

    static func runBoundaryDistanceAnalysis(
        assignments: [CellTypeAssignment],
        boundaryMaskURL: URL,
        boundaryID: Int,
        boundaryName: String,
        queryTypes: [String],
        regionFilter: DistanceBoundaryRegionFilter,
        pixelSize: (Double, Double)?,
        canvasWidth: Int,
        canvasHeight: Int
    ) throws -> DistanceAnalysisResult {
        let boundaryMask = try loadBoundaryMask(from: boundaryMaskURL)
        guard boundaryMask.width == max(1, canvasWidth),
              boundaryMask.height == max(1, canvasHeight) else {
            throw SpatialScopeError.message("Selected boundary mask does not match the current cell-type image size.")
        }
        return try runBoundaryDistanceAnalysis(
            assignments: assignments,
            boundaryMask: boundaryMask,
            boundaryID: boundaryID,
            boundaryName: boundaryName,
            queryTypes: queryTypes,
            regionFilter: regionFilter,
            pixelSize: pixelSize,
            canvasWidth: canvasWidth,
            canvasHeight: canvasHeight
        )
    }

    static func run(
        assignments: [CellTypeAssignment],
        regions: [RegionROI],
        pixelSize: (Double, Double)?,
        canvasWidth: Int,
        canvasHeight: Int,
        cpuAllocationPercent: Double
    ) throws -> DistanceAnalysisResult {
        let types = orderedNonEmpty(assignments.map(\.assignedType))
        guard let targetType = types.first else {
            throw SpatialScopeError.message("Run cell-type assignment before distance analysis.")
        }
        let nearest = try runNearestNeighborAnalysis(
            assignments: assignments,
            targetType: targetType,
            queryTypes: [targetType],
            pixelSize: pixelSize,
            canvasWidth: canvasWidth,
            canvasHeight: canvasHeight
        )
        guard let region = regions.sorted(by: { $0.id < $1.id }).first else {
            return nearest
        }
        let mask = RegionAnalyzer.mask(for: region, width: max(1, canvasWidth), height: max(1, canvasHeight))
        let boundary = try runBoundaryDistanceAnalysis(
            assignments: assignments,
            boundaryMask: mask,
            boundaryID: region.id,
            boundaryName: region.sourceType ?? region.dominantType,
            queryTypes: [targetType],
            regionFilter: .all,
            pixelSize: pixelSize,
            canvasWidth: canvasWidth,
            canvasHeight: canvasHeight
        )
        return DistanceAnalysisResult(
            nearestDistances: nearest.nearestDistances,
            boundaryDistances: boundary.boundaryDistances,
            nearestTTests: nearest.nearestTTests,
            boundaryTTests: boundary.boundaryTTests,
            summaries: nearest.summaries + boundary.summaries,
            image: nearest.image,
            nearestHistogramImage: nearest.nearestHistogramImage,
            boundaryHistogramImage: boundary.boundaryHistogramImage,
            width: max(1, canvasWidth),
            height: max(1, canvasHeight),
            nearestTargetType: nearest.nearestTargetType,
            nearestQueryTypes: nearest.nearestQueryTypes,
            boundaryName: boundary.boundaryName,
            boundaryQueryTypes: boundary.boundaryQueryTypes,
            boundaryFilter: boundary.boundaryFilter
        )
    }

    private static func runBoundaryDistanceAnalysis(
        assignments: [CellTypeAssignment],
        boundaryMask: RasterMask,
        boundaryID: Int,
        boundaryName: String,
        queryTypes: [String],
        regionFilter: DistanceBoundaryRegionFilter,
        pixelSize: (Double, Double)?,
        canvasWidth: Int,
        canvasHeight: Int
    ) throws -> DistanceAnalysisResult {
        guard !assignments.isEmpty else {
            throw SpatialScopeError.message("Run cell-type assignment before distance analysis.")
        }
        guard !boundaryMask.isEmpty else {
            throw SpatialScopeError.message("Selected boundary mask is empty.")
        }
        let queryTypes = orderedNonEmpty(queryTypes)
        guard !queryTypes.isEmpty else {
            throw SpatialScopeError.message("Select at least one query cell type.")
        }

        let pixel = resolvedPixelSize(pixelSize)
        let seed = boundaryMask.interfaceBoundary(includeImageEdges: true)
        guard !seed.isEmpty else {
            throw SpatialScopeError.message("Selected boundary mask does not contain a valid drawable edge.")
        }
        let distanceUm = boundaryMask.euclideanDistance(to: seed, xScale: pixel.x, yScale: pixel.y)
        let distancePx = boundaryMask.euclideanDistance(to: seed, xScale: 1.0, yScale: 1.0)
        let allowedTypes = Set(queryTypes)

        let rows: [BoundaryDistance] = assignments.compactMap { cell in
            guard allowedTypes.contains(cell.assignedType) else { return nil }
            let x = min(max(Int(round(cell.centroidX)), 0), boundaryMask.width - 1)
            let y = min(max(Int(round(cell.centroidY)), 0), boundaryMask.height - 1)
            let inside = boundaryMask[x, y]
            switch regionFilter {
            case .inside where !inside:
                return nil
            case .outside where inside:
                return nil
            default:
                break
            }
            let index = y * boundaryMask.width + x
            return BoundaryDistance(
                nucleusID: cell.nucleusID,
                assignedType: cell.assignedType,
                colorHex: cell.colorHex,
                centroidX: cell.centroidX,
                centroidY: cell.centroidY,
                regionID: boundaryID,
                boundaryName: boundaryName,
                insideRegion: inside,
                distanceToBoundaryPx: distancePx[index],
                distanceToBoundaryUm: distanceUm[index]
            )
        }
        guard !rows.isEmpty else {
            throw SpatialScopeError.message("No cells found after applying the selected boundary filter.")
        }

        let tTests = welchTTests(boundaryDistances: rows, orderedQueryTypes: queryTypes)
        let summaries = queryTypes.map { queryType in
            summary(
                metric: "\(queryType) to \(boundaryName)",
                values: rows
                    .filter { $0.assignedType == queryType }
                    .map(\.distanceToBoundaryUm)
            )
        }
        let image = renderBoundaryFigure(
            distances: rows,
            boundaryName: boundaryName,
            queryTypes: queryTypes,
            tTests: tTests
        )

        return DistanceAnalysisResult(
            nearestDistances: [],
            boundaryDistances: rows.sorted {
                if $0.assignedType == $1.assignedType {
                    return $0.nucleusID < $1.nucleusID
                }
                return $0.assignedType.localizedStandardCompare($1.assignedType) == .orderedAscending
            },
            nearestTTests: [],
            boundaryTTests: tTests,
            summaries: summaries,
            image: image,
            nearestHistogramImage: NSImage(size: NSSize(width: 1, height: 1)),
            boundaryHistogramImage: image,
            width: max(1, canvasWidth),
            height: max(1, canvasHeight),
            boundaryName: boundaryName,
            boundaryQueryTypes: queryTypes,
            boundaryFilter: regionFilter
        )
    }

    private static func nearestDistances(
        assignments: [CellTypeAssignment],
        targetType: String,
        queryType: String,
        pixelSize: (x: Double, y: Double)
    ) throws -> [NearestNeighborDistance] {
        let targets = assignments.filter { $0.assignedType == targetType }
        let queries = assignments.filter { $0.assignedType == queryType }
        guard !targets.isEmpty else {
            throw SpatialScopeError.message("No cells found for target cell type \(targetType).")
        }
        guard !queries.isEmpty else {
            throw SpatialScopeError.message("No cells found for query cell type \(queryType).")
        }
        if targetType == queryType, queries.count < 2 {
            throw SpatialScopeError.message("Need at least 2 cells of type \(targetType) for self-type nearest neighbor.")
        }

        return targets.compactMap { target in
            var best: CellTypeAssignment?
            var bestDistanceUmSquared = Double.greatestFiniteMagnitude
            var bestDistancePxSquared = Double.greatestFiniteMagnitude
            for query in queries where query.nucleusID != target.nucleusID {
                let dxPx = query.centroidX - target.centroidX
                let dyPx = query.centroidY - target.centroidY
                let dxUm = dxPx * pixelSize.x
                let dyUm = dyPx * pixelSize.y
                let distanceUmSquared = dxUm * dxUm + dyUm * dyUm
                if distanceUmSquared < bestDistanceUmSquared {
                    bestDistanceUmSquared = distanceUmSquared
                    bestDistancePxSquared = dxPx * dxPx + dyPx * dyPx
                    best = query
                }
            }
            guard let best else { return nil }
            return NearestNeighborDistance(
                nucleusID: target.nucleusID,
                assignedType: target.assignedType,
                colorHex: target.colorHex,
                centroidX: target.centroidX,
                centroidY: target.centroidY,
                nearestNucleusID: best.nucleusID,
                nearestType: queryType,
                nearestDistancePx: sqrt(bestDistancePxSquared),
                nearestDistanceUm: sqrt(bestDistanceUmSquared)
            )
        }
        .sorted {
            if ($0.nearestType ?? "") == ($1.nearestType ?? "") {
                return $0.nucleusID < $1.nucleusID
            }
            return ($0.nearestType ?? "").localizedStandardCompare($1.nearestType ?? "") == .orderedAscending
        }
    }

    private static func pairedTTests(
        nearestDistances: [NearestNeighborDistance],
        orderedQueryTypes: [String]
    ) -> [DistanceTTest] {
        let pairs = comparisonPairs(count: orderedQueryTypes.count)
        guard !pairs.isEmpty else { return [] }
        let grouped = Dictionary(grouping: nearestDistances, by: \.nucleusID)
        var valuesByTarget: [Int: [String: Double]] = [:]
        for (target, rows) in grouped {
            var values: [String: Double] = [:]
            for row in rows {
                values[row.nearestType ?? ""] = row.nearestDistanceUm
            }
            valuesByTarget[target] = values
        }

        return pairs.map { pair in
            let ref = orderedQueryTypes[pair.0]
            let cmp = orderedQueryTypes[pair.1]
            let diffs = valuesByTarget.values.compactMap { values -> Double? in
                guard let x = values[ref], let y = values[cmp],
                      x.isFinite, y.isFinite else { return nil }
                return x - y
            }
            let stats = oneSampleT(values: diffs)
            return DistanceTTest(
                ref: ref,
                cmp: cmp,
                nPairs: diffs.count,
                t: stats.t,
                p: stats.p,
                test: "paired_ttest"
            )
        }
    }

    private static func welchTTests(
        boundaryDistances: [BoundaryDistance],
        orderedQueryTypes: [String]
    ) -> [DistanceTTest] {
        let pairs = comparisonPairs(count: orderedQueryTypes.count)
        guard !pairs.isEmpty else { return [] }
        let grouped = Dictionary(grouping: boundaryDistances, by: \.assignedType)
            .mapValues { $0.map(\.distanceToBoundaryUm).filter(\.isFinite) }
        return pairs.map { pair in
            let ref = orderedQueryTypes[pair.0]
            let cmp = orderedQueryTypes[pair.1]
            let stats = welchT(x: grouped[ref] ?? [], y: grouped[cmp] ?? [])
            return DistanceTTest(
                ref: ref,
                cmp: cmp,
                nRef: grouped[ref]?.count ?? 0,
                nCmp: grouped[cmp]?.count ?? 0,
                t: stats.t,
                p: stats.p,
                test: "welch_ttest"
            )
        }
    }

    private static func comparisonPairs(count: Int) -> [(Int, Int)] {
        if count == 2 { return [(0, 1)] }
        if count > 2 { return (1..<count).map { (0, $0) } }
        return []
    }

    private static func oneSampleT(values: [Double]) -> (t: Double, p: Double) {
        guard values.count >= 2 else { return (.nan, .nan) }
        let mean = values.reduce(0, +) / Double(values.count)
        let variance = sampleVariance(values: values, mean: mean)
        guard variance > 0 else { return (0, 1) }
        let t = mean / sqrt(variance / Double(values.count))
        let p = twoTailedPValue(t: t, degreesOfFreedom: Double(values.count - 1))
        return (t, p)
    }

    private static func welchT(x: [Double], y: [Double]) -> (t: Double, p: Double) {
        guard x.count >= 2, y.count >= 2 else { return (.nan, .nan) }
        let meanX = x.reduce(0, +) / Double(x.count)
        let meanY = y.reduce(0, +) / Double(y.count)
        let varX = sampleVariance(values: x, mean: meanX)
        let varY = sampleVariance(values: y, mean: meanY)
        let sx = varX / Double(x.count)
        let sy = varY / Double(y.count)
        let denom = sqrt(sx + sy)
        guard denom > 0 else { return (0, 1) }
        let t = (meanX - meanY) / denom
        let numerator = (sx + sy) * (sx + sy)
        let denominator = (sx * sx) / Double(x.count - 1) + (sy * sy) / Double(y.count - 1)
        let df = denominator > 0 ? numerator / denominator : Double(x.count + y.count - 2)
        return (t, twoTailedPValue(t: t, degreesOfFreedom: df))
    }

    private static func sampleVariance(values: [Double], mean: Double) -> Double {
        guard values.count > 1 else { return 0 }
        return values.reduce(0.0) { $0 + ($1 - mean) * ($1 - mean) } / Double(values.count - 1)
    }

    private static func twoTailedPValue(t: Double, degreesOfFreedom: Double) -> Double {
        guard t.isFinite, degreesOfFreedom > 0 else { return .nan }
        let tAbs = abs(t)
        let x = degreesOfFreedom / (degreesOfFreedom + tAbs * tAbs)
        return min(max(regularizedBeta(x, degreesOfFreedom / 2.0, 0.5), 0.0), 1.0)
    }

    private static func regularizedBeta(_ x: Double, _ a: Double, _ b: Double) -> Double {
        guard x > 0 else { return 0 }
        guard x < 1 else { return 1 }
        let logBeta = lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log1p(-x)
        let beta = exp(logBeta)
        if x < (a + 1.0) / (a + b + 2.0) {
            return beta * betaContinuedFraction(a, b, x) / a
        }
        return 1.0 - beta * betaContinuedFraction(b, a, 1.0 - x) / b
    }

    private static func betaContinuedFraction(_ a: Double, _ b: Double, _ x: Double) -> Double {
        let maxIterations = 200
        let epsilon = 3.0e-12
        let fpMin = 1.0e-30
        let qab = a + b
        let qap = a + 1.0
        let qam = a - 1.0
        var c = 1.0
        var d = 1.0 - qab * x / qap
        if abs(d) < fpMin { d = fpMin }
        d = 1.0 / d
        var h = d
        for m in 1...maxIterations {
            let m2 = 2 * m
            var aa = Double(m) * (b - Double(m)) * x / ((qam + Double(m2)) * (a + Double(m2)))
            d = 1.0 + aa * d
            if abs(d) < fpMin { d = fpMin }
            c = 1.0 + aa / c
            if abs(c) < fpMin { c = fpMin }
            d = 1.0 / d
            h *= d * c
            aa = -(a + Double(m)) * (qab + Double(m)) * x / ((a + Double(m2)) * (qap + Double(m2)))
            d = 1.0 + aa * d
            if abs(d) < fpMin { d = fpMin }
            c = 1.0 + aa / c
            if abs(c) < fpMin { c = fpMin }
            d = 1.0 / d
            let delta = d * c
            h *= delta
            if abs(delta - 1.0) < epsilon { break }
        }
        return h
    }

    private static func summary(metric: String, values: [Double]) -> DistanceSummary {
        let sorted = values.filter(\.isFinite).sorted()
        guard !sorted.isEmpty else {
            return DistanceSummary(metric: metric, count: 0, meanUm: 0, medianUm: 0, minUm: 0, maxUm: 0)
        }
        let mean = sorted.reduce(0, +) / Double(sorted.count)
        let median: Double
        if sorted.count.isMultiple(of: 2) {
            median = (sorted[sorted.count / 2 - 1] + sorted[sorted.count / 2]) / 2
        } else {
            median = sorted[sorted.count / 2]
        }
        return DistanceSummary(
            metric: metric,
            count: sorted.count,
            meanUm: mean,
            medianUm: median,
            minUm: sorted.first ?? 0,
            maxUm: sorted.last ?? 0
        )
    }

    private static func renderNearestNeighborFigure(
        distances: [NearestNeighborDistance],
        targetType: String,
        queryTypes: [String],
        tTests: [DistanceTTest]
    ) -> NSImage {
        let valuesByGroup = queryTypes.map { queryType in
            PlotGroup(
                name: queryType,
                values: distances
                    .filter { ($0.nearestType ?? "") == queryType }
                    .map(\.nearestDistanceUm)
            )
        }
        let pairedValues = Dictionary(grouping: distances, by: \.nucleusID)
            .mapValues { rows -> [String: Double] in
                var values: [String: Double] = [:]
                for row in rows {
                    guard let queryType = row.nearestType else { continue }
                    values[queryType] = row.nearestDistanceUm
                }
                return values
            }
        return renderBoxScatter(
            title: "Nearest distances to target: \(targetType)",
            yLabel: "Nearest distance (um)",
            groups: valuesByGroup,
            tTests: tTests,
            pairedValues: pairedValues,
            pairedOrder: queryTypes
        )
    }

    private static func renderBoundaryFigure(
        distances: [BoundaryDistance],
        boundaryName: String,
        queryTypes: [String],
        tTests: [DistanceTTest]
    ) -> NSImage {
        let valuesByGroup = queryTypes.map { queryType in
            PlotGroup(
                name: queryType,
                values: distances
                    .filter { $0.assignedType == queryType }
                    .map(\.distanceToBoundaryUm)
            )
        }
        return renderBoxScatter(
            title: "Distance to boundary: \(boundaryName)",
            yLabel: "Shortest distance to boundary (um)",
            groups: valuesByGroup,
            tTests: tTests,
            pairedValues: nil,
            pairedOrder: queryTypes
        )
    }

    private struct PlotGroup {
        var name: String
        var values: [Double]
    }

    private static func renderBoxScatter(
        title: String,
        yLabel: String,
        groups: [PlotGroup],
        tTests: [DistanceTTest],
        pairedValues: [Int: [String: Double]]?,
        pairedOrder: [String]
    ) -> NSImage {
        let groupCount = Double(max(1, groups.count))
        let width = max(620.0, 112.0 * Double(max(3, groups.count)))
        let height = 400.0
        let left = 108.0
        let right = 32.0
        let top = 56.0
        let bottom = 116.0
        let plotWidth = width - left - right
        let plotHeight = height - top - bottom
        let image = NSImage(size: NSSize(width: width, height: height))

        let allValues = groups.flatMap { $0.values.filter(\.isFinite) }
        let maxValue = max(1.0, allValues.max() ?? 1.0)
        let minValue = min(0.0, allValues.min() ?? 0.0)
        let span = max(1e-6, maxValue - minValue)
        let comparisonCount = tTests.filter { row in
            row.p.isFinite
                && groups.contains(where: { $0.name == row.ref })
                && groups.contains(where: { $0.name == row.cmp })
        }.count
        let extraTopFraction = comparisonCount > 0
            ? 0.22 + 0.13 * Double(comparisonCount - 1)
            : 0.12
        let extraTop = span * extraTopFraction
        let yMax = maxValue + extraTop
        let yMin = minValue

        func xPosition(_ index: Int) -> Double {
            left + (Double(index) + 0.5) * plotWidth / Double(max(1, groups.count))
        }

        func yPosition(_ value: Double) -> Double {
            let fraction = (value - yMin) / max(1e-9, yMax - yMin)
            return bottom + fraction * plotHeight
        }

        image.lockFocus()
        NSColor.white.setFill()
        NSRect(x: 0, y: 0, width: width, height: height).fill()

        let plotRect = NSRect(x: left, y: bottom, width: plotWidth, height: plotHeight)
        NSColor.white.setFill()
        plotRect.fill()
        NSColor.black.setStroke()
        let axes = NSBezierPath()
        axes.move(to: NSPoint(x: left, y: bottom))
        axes.line(to: NSPoint(x: left, y: bottom + plotHeight))
        axes.move(to: NSPoint(x: left, y: bottom))
        axes.line(to: NSPoint(x: left + plotWidth, y: bottom))
        axes.lineWidth = 1.2
        axes.stroke()

        drawText(title, rect: NSRect(x: left, y: height - 40, width: plotWidth, height: 28), size: 19, weight: .semibold, alignment: .center)
        drawVerticalYAxisTitle(
            yLabel,
            center: NSPoint(x: 20, y: bottom + plotHeight / 2),
            maxWidth: min(height - 56, plotHeight + 52),
            font: NSFont.systemFont(ofSize: 14, weight: .medium)
        )

        let tickCount = 5
        for tick in 0...tickCount {
            let value = yMin + (yMax - yMin) * Double(tick) / Double(tickCount)
            let y = yPosition(value)
            NSColor.black.withAlphaComponent(0.15).setStroke()
            let grid = NSBezierPath()
            grid.move(to: NSPoint(x: left, y: y))
            grid.line(to: NSPoint(x: left + plotWidth, y: y))
            grid.lineWidth = 0.8
            grid.stroke()
            drawText(String(format: "%.1f", value), rect: NSRect(x: 40, y: y - 9, width: left - 50, height: 18), size: 12, weight: .regular, alignment: .right)
        }

        if let pairedValues, groups.count >= 2 {
            NSColor.black.withAlphaComponent(0.18).setStroke()
            for values in pairedValues.values {
                let ordered = pairedOrder.compactMap { values[$0] }
                guard ordered.count >= 2 else { continue }
                let path = NSBezierPath()
                var started = false
                for (index, groupName) in pairedOrder.enumerated() {
                    guard let value = values[groupName], value.isFinite else { continue }
                    let point = NSPoint(x: xPosition(index), y: yPosition(value))
                    if started {
                        path.line(to: point)
                    } else {
                        path.move(to: point)
                        started = true
                    }
                }
                path.lineWidth = 0.6
                path.stroke()
            }
        }

        for (index, group) in groups.enumerated() {
            let values = group.values.filter(\.isFinite).sorted()
            let x = xPosition(index)
            let boxWidth = min(54.0, plotWidth / Double(max(1, groups.count)) * 0.34)
            let xLabelMaxWidth = min(112.0, max(88.0, plotWidth / groupCount * 0.92))
            let xLabelRise = xLabelMaxWidth / sqrt(2.0)
            StatPlotDrawing.drawRotatedXAxisLabel(
                group.name,
                anchor: NSPoint(x: x + 4.0, y: bottom - xLabelRise - 10.0),
                maxWidth: xLabelMaxWidth,
                font: NSFont.systemFont(ofSize: 13, weight: .medium)
            )
            guard !values.isEmpty else { continue }
            let q1 = quantile(values, 0.25)
            let q2 = quantile(values, 0.50)
            let q3 = quantile(values, 0.75)
            let minVal = values.first ?? q1
            let maxVal = values.last ?? q3

            NSColor.black.setStroke()
            let whisker = NSBezierPath()
            whisker.move(to: NSPoint(x: x, y: yPosition(minVal)))
            whisker.line(to: NSPoint(x: x, y: yPosition(maxVal)))
            whisker.move(to: NSPoint(x: x - boxWidth * 0.25, y: yPosition(minVal)))
            whisker.line(to: NSPoint(x: x + boxWidth * 0.25, y: yPosition(minVal)))
            whisker.move(to: NSPoint(x: x - boxWidth * 0.25, y: yPosition(maxVal)))
            whisker.line(to: NSPoint(x: x + boxWidth * 0.25, y: yPosition(maxVal)))
            whisker.lineWidth = 1.1
            whisker.stroke()

            let box = NSRect(
                x: x - boxWidth / 2,
                y: yPosition(q1),
                width: boxWidth,
                height: max(1.0, yPosition(q3) - yPosition(q1))
            )
            NSColor.white.setFill()
            box.fill()
            NSColor.black.setStroke()
            NSBezierPath(rect: box).stroke()
            let median = NSBezierPath()
            median.move(to: NSPoint(x: box.minX, y: yPosition(q2)))
            median.line(to: NSPoint(x: box.maxX, y: yPosition(q2)))
            median.lineWidth = 1.4
            median.stroke()

            let pointColor = ColorPalette.color(at: index + 2)
            (NSColor(hex: pointColor) ?? .systemBlue).withAlphaComponent(0.78).setFill()
            for (valueIndex, value) in values.enumerated() {
                let jitter = deterministicJitter(groupIndex: index, valueIndex: valueIndex) * boxWidth * 0.72
                let rect = NSRect(x: x + jitter - 2.6, y: yPosition(value) - 2.6, width: 5.2, height: 5.2)
                NSBezierPath(ovalIn: rect).fill()
            }
        }

        for (index, row) in tTests.enumerated() where row.p.isFinite {
            guard let x1Index = groups.firstIndex(where: { $0.name == row.ref }),
                  let x2Index = groups.firstIndex(where: { $0.name == row.cmp }) else {
                continue
            }
            let y = yPosition(maxValue + span * (0.06 + 0.12 * Double(index)))
            drawBracket(x1: xPosition(x1Index), x2: xPosition(x2Index), y: y, height: span / max(1e-9, yMax - yMin) * plotHeight * 0.025, text: formattedP(row.p))
        }

        image.unlockFocus()
        return image
    }

    private static func quantile(_ sorted: [Double], _ p: Double) -> Double {
        guard !sorted.isEmpty else { return 0 }
        let position = min(max(p, 0), 1) * Double(sorted.count - 1)
        let lower = Int(floor(position))
        let upper = Int(ceil(position))
        if lower == upper { return sorted[lower] }
        let fraction = position - Double(lower)
        return sorted[lower] * (1 - fraction) + sorted[upper] * fraction
    }

    private static func deterministicJitter(groupIndex: Int, valueIndex: Int) -> Double {
        let raw = (groupIndex + 1) * 1_103 + (valueIndex + 7) * 3_179
        let normalized = Double(raw % 10_000) / 10_000.0
        return normalized - 0.5
    }

    private static func drawBracket(x1: Double, x2: Double, y: Double, height: Double, text: String) {
        NSColor.black.setStroke()
        let path = NSBezierPath()
        path.move(to: NSPoint(x: x1, y: y))
        path.line(to: NSPoint(x: x1, y: y + height))
        path.line(to: NSPoint(x: x2, y: y + height))
        path.line(to: NSPoint(x: x2, y: y))
        path.lineWidth = 1.1
        path.stroke()
        drawText(text, rect: NSRect(x: min(x1, x2), y: y + height + 2, width: abs(x2 - x1), height: 18), size: 10, weight: .regular, alignment: .center)
    }

    private static func drawText(_ text: String, rect: NSRect, size: CGFloat, weight: NSFont.Weight, alignment: NSTextAlignment) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = alignment
        (text as NSString).draw(
            in: rect,
            withAttributes: [
                .font: NSFont.systemFont(ofSize: size, weight: weight),
                .foregroundColor: NSColor.black,
                .paragraphStyle: paragraph
            ]
        )
    }

    private static func drawVerticalYAxisTitle(
        _ text: String,
        center: NSPoint,
        maxWidth: CGFloat,
        font: NSFont
    ) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        paragraph.lineBreakMode = .byTruncatingTail

        NSGraphicsContext.saveGraphicsState()
        let transform = NSAffineTransform()
        transform.translateX(by: center.x, yBy: center.y)
        transform.rotate(byDegrees: 90)
        transform.concat()

        (text as NSString).draw(
            in: NSRect(
                x: -maxWidth / 2,
                y: -font.pointSize * 0.7,
                width: maxWidth,
                height: font.pointSize * 1.4
            ),
            withAttributes: [
                .font: font,
                .foregroundColor: NSColor.black,
                .paragraphStyle: paragraph
            ]
        )

        NSGraphicsContext.restoreGraphicsState()
    }

    private static func formattedP(_ p: Double) -> String {
        guard p.isFinite else { return "p=NA" }
        if p < 1e-4 { return String(format: "p=%.1e", p) }
        return String(format: "p=%.4f", p)
    }

    private static func orderedNonEmpty(_ values: [String]) -> [String] {
        var seen: Set<String> = []
        var output: [String] = []
        for value in values {
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty, !seen.contains(trimmed) else { continue }
            seen.insert(trimmed)
            output.append(trimmed)
        }
        return output
    }

    private static func resolvedPixelSize(_ pixelSize: (Double, Double)?) -> (x: Double, y: Double) {
        let x = pixelSize?.0 ?? 1.0
        let y = pixelSize?.1 ?? x
        return (max(1e-9, x), max(1e-9, y))
    }

    private static func loadBoundaryMask(from url: URL) throws -> RasterMask {
        guard let image = NSImage(contentsOf: url),
              let tiff = image.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff) else {
            throw SpatialScopeError.message("Could not read selected boundary mask.")
        }
        let width = max(1, rep.pixelsWide)
        let height = max(1, rep.pixelsHigh)
        var pixels = Array(repeating: false, count: width * height)
        for y in 0..<height {
            for x in 0..<width {
                guard let color = rep.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) else { continue }
                pixels[y * width + x] = color.alphaComponent > 0.001
                    && max(color.redComponent, max(color.greenComponent, color.blueComponent)) > 0.001
            }
        }
        let mask = RasterMask(width: width, height: height, pixels: pixels)
        guard !mask.isEmpty else {
            throw SpatialScopeError.message("Selected boundary mask is empty.")
        }
        return mask
    }
}
