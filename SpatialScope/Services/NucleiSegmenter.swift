import AppKit
import Dispatch
import Foundation

enum NucleiSegmenter {
    static let advancedSearchIntervalCount = 5

    static var advancedSearchSpaceSize: Int {
        advancedSearchSpaceSize(fixMinDiameter: true, fixMaxDiameter: true)
    }

    static func advancedSearchSpaceSize(fixMinDiameter: Bool, fixMaxDiameter: Bool) -> Int {
        powInt(advancedSearchIntervalCount, advancedScanAxes(fixMinDiameter: fixMinDiameter, fixMaxDiameter: fixMaxDiameter).count)
    }

    private static let advancedScanMaxConcurrentEvaluations = 64

    private struct SearchAxis {
        var title: String
        var keyPath: WritableKeyPath<NucleiParameters, Double>
        var range: ClosedRange<Double>
    }

    private struct ScanCandidate {
        var comboIndex: Int
        var stage: String
        var params: NucleiParameters
    }

    private static func advancedScanAxes(fixMinDiameter: Bool, fixMaxDiameter: Bool) -> [SearchAxis] {
        var axes: [SearchAxis] = []
        if !fixMinDiameter {
            axes.append(SearchAxis(title: "MIN_DIAM_UM", keyPath: \.minDiamUm, range: 0...240))
        }
        if !fixMaxDiameter {
            axes.append(SearchAxis(title: "MAX_DIAM_UM", keyPath: \.maxDiamUm, range: 1...320))
        }
        axes.append(contentsOf: [
            SearchAxis(title: "LOCAL_OFFSET", keyPath: \.localOffset, range: -1...1),
            SearchAxis(title: "H_MAXIMA_UM", keyPath: \.hMaximaUm, range: 0...10),
            SearchAxis(title: "GAUSS_SIGMA_UM", keyPath: \.gaussSigmaUm, range: 0...10),
            SearchAxis(title: "TOPHAT_RADIUS_UM", keyPath: \.tophatRadiusUm, range: 0...40),
            SearchAxis(title: "LOCAL_WIN_UM", keyPath: \.localWinUm, range: 1...240),
            SearchAxis(title: "SEED_MIN_DIST_UM", keyPath: \.seedMinDistUm, range: 0...20),
            SearchAxis(title: "WATERSHED_COMPACTNESS", keyPath: \.watershedCompactness, range: 0...10),
            SearchAxis(title: "POST_RESPLIT_MULT", keyPath: \.postResplitMult, range: 0...10)
        ])
        return axes
    }

    static func runFinal(
        matrix: CSVMatrix,
        params: NucleiParameters,
        pixelSize: (Double, Double)?,
        cpuAllocationPercent: Double = 100,
        cancellationToken: CancellationToken? = nil
    ) throws -> NucleiSegmentationResult {
        try analyze(
            matrix: matrix,
            params: params,
            pixelSize: pixelSize,
            renderImage: true,
            cpuAllocationPercent: cpuAllocationPercent,
            cancellationToken: cancellationToken
        )
    }

    static func runAdvancedScan(
        matrix: CSVMatrix,
        baseParams: NucleiParameters,
        pixelSize: (Double, Double)?,
        cpuAllocationPercent: Double = 100,
        combinationBudget: Int = 160,
        fixMinDiameter: Bool = true,
        fixMaxDiameter: Bool = true,
        cancellationToken: CancellationToken? = nil
    ) throws -> [NucleiScanRecord] {
        let axes = advancedScanAxes(fixMinDiameter: fixMinDiameter, fixMaxDiameter: fixMaxDiameter)
        let searchSpaceSize = powInt(advancedSearchIntervalCount, axes.count)
        let plannedRecords = min(max(combinationBudget, 10), searchSpaceSize)
        let clampedCPU = min(max(cpuAllocationPercent, 10), 100)
        let maxDimension = Int(round(360.0 + (clampedCPU / 100.0) * 420.0))
        let sampled = downsample(matrix: matrix, pixelSize: pixelSize, maxDimension: maxDimension)
        let sampleMatrix = sampled.matrix
        let samplePixelSize = sampled.pixelSize
        var records: [NucleiScanRecord] = []
        var seen = Set<String>()
        var nextComboIndex = 1

        func normalizedDiameter(_ params: NucleiParameters) -> NucleiParameters {
            var fixed = params
            if fixMinDiameter {
                fixed.minDiamUm = baseParams.minDiamUm
            }
            if fixMaxDiameter {
                fixed.maxDiamUm = baseParams.maxDiamUm
            }
            fixed.maxDiamUm = max(fixed.minDiamUm, fixed.maxDiamUm)
            return fixed
        }

        func appendCandidate(
            _ params: NucleiParameters,
            stage: String,
            to candidates: inout [ScanCandidate],
            maxCandidateCount: Int
        ) {
            guard candidates.count < maxCandidateCount else { return }
            let candidateParams = normalizedDiameter(params)
            let key = parameterKey(candidateParams)
            guard seen.insert(key).inserted else { return }
            candidates.append(
                ScanCandidate(
                    comboIndex: nextComboIndex,
                    stage: stage,
                    params: candidateParams
                )
            )
            nextComboIndex += 1
        }

        let coarseValues = axes.map { axis in
            fullRangeValues(range: axis.range, count: advancedSearchIntervalCount)
        }
        let coarseTarget = plannedRecords <= 12
            ? max(1, plannedRecords / 2)
            : min(plannedRecords - 2, max(8, Int(ceil(Double(plannedRecords) * 0.42))))
        var coarseCandidates: [ScanCandidate] = []
        appendCandidate(baseParams, stage: "coarse-baseline", to: &coarseCandidates, maxCandidateCount: coarseTarget)

        let coarseSpace = max(1, productCount(coarseValues))
        func appendGlobalGridCandidates(
            stage: String,
            to candidates: inout [ScanCandidate],
            maxCandidateCount: Int,
            sampleOffset: Int = 0
        ) {
            var sampleIndex = 0
            let attemptLimit = min(coarseSpace, max(maxCandidateCount, maxCandidateCount * 8))
            while candidates.count < maxCandidateCount && sampleIndex < attemptLimit {
                let gridIndex = gridSampleIndex(
                    sampleIndex: sampleIndex + sampleOffset,
                    sampleCount: maxCandidateCount,
                    totalCount: coarseSpace
                )
                let candidate = parametersFromGrid(
                    base: baseParams,
                    axes: axes,
                    valuesByAxis: coarseValues,
                    gridIndex: gridIndex
                )
                appendCandidate(candidate, stage: stage, to: &candidates, maxCandidateCount: maxCandidateCount)
                sampleIndex += 1
            }
        }

        let coarseAttemptLimit = min(coarseSpace, max(coarseTarget, coarseTarget * 4))
        var coarseSample = 0
        while coarseCandidates.count < coarseTarget && coarseSample < coarseAttemptLimit {
            let gridIndex = gridSampleIndex(
                sampleIndex: coarseSample,
                sampleCount: coarseTarget,
                totalCount: coarseSpace
            )
            let candidate = parametersFromGrid(
                base: baseParams,
                axes: axes,
                valuesByAxis: coarseValues,
                gridIndex: gridIndex
            )
            appendCandidate(candidate, stage: "coarse", to: &coarseCandidates, maxCandidateCount: coarseTarget)
            coarseSample += 1
        }

        records.append(
            contentsOf: try evaluateScanCandidates(
                coarseCandidates,
                matrix: sampleMatrix,
                pixelSize: samplePixelSize,
                cpuAllocationPercent: cpuAllocationPercent,
                cancellationToken: cancellationToken
            )
        )

        var refinementLevel = 1
        while records.count < plannedRecords {
            try cancellationToken?.checkCancellation()
            let remaining = plannedRecords - records.count
            let ranked = records.sorted {
                if $0.count == $1.count { return $0.comboIndex < $1.comboIndex }
                return $0.count > $1.count
            }
            let seedCount = min(max(1, remaining / 18 + 1), min(6, ranked.count))
            let seeds = Array(ranked.prefix(seedCount))
            guard !seeds.isEmpty else { break }

            var refinementCandidates: [ScanCandidate] = []
            let perSeed = max(1, Int(ceil(Double(remaining) / Double(seeds.count))))
            let stage = "refine-\(refinementLevel)"

            for seed in seeds {
                let refinedValues = axes.map { axis in
                    refinedRangeValues(
                        center: seed.params[keyPath: axis.keyPath],
                        range: axis.range,
                        refinementLevel: refinementLevel
                    )
                }
                let refinedSpace = max(1, productCount(refinedValues))
                let attemptLimit = min(refinedSpace, max(perSeed, perSeed * 4))
                var sampleIndex = 0
                while sampleIndex < attemptLimit && refinementCandidates.count < remaining {
                    let gridIndex = gridSampleIndex(
                        sampleIndex: sampleIndex,
                        sampleCount: perSeed,
                        totalCount: refinedSpace
                    )
                    let candidate = parametersFromGrid(
                        base: seed.params,
                        axes: axes,
                        valuesByAxis: refinedValues,
                        gridIndex: gridIndex
                    )
                    appendCandidate(
                        candidate,
                        stage: stage,
                        to: &refinementCandidates,
                        maxCandidateCount: remaining
                    )
                    sampleIndex += 1
                }
            }

            guard !refinementCandidates.isEmpty else { break }
            records.append(
                contentsOf: try evaluateScanCandidates(
                    refinementCandidates,
                    matrix: sampleMatrix,
                    pixelSize: samplePixelSize,
                    cpuAllocationPercent: cpuAllocationPercent,
                    cancellationToken: cancellationToken
                )
            )
            refinementLevel += 1
        }

        if records.count < plannedRecords {
            let remaining = plannedRecords - records.count
            var fallbackCandidates: [ScanCandidate] = []
            appendGlobalGridCandidates(
                stage: "coarse-fill",
                to: &fallbackCandidates,
                maxCandidateCount: remaining,
                sampleOffset: nextComboIndex * 13
            )
            if !fallbackCandidates.isEmpty {
                records.append(
                    contentsOf: try evaluateScanCandidates(
                        fallbackCandidates,
                        matrix: sampleMatrix,
                        pixelSize: samplePixelSize,
                        cpuAllocationPercent: cpuAllocationPercent,
                        cancellationToken: cancellationToken
                    )
                )
            }
        }

        return records.sorted { $0.comboIndex < $1.comboIndex }
    }

    static func effectiveWorkerCount(
        activeCPUCoreCount: Int = ProcessInfo.processInfo.activeProcessorCount,
        cpuAllocationPercent: Double
    ) -> Int {
        let activeCores = max(1, activeCPUCoreCount)
        let clamped = min(max(cpuAllocationPercent, 1), 100)
        let requested = Int((Double(activeCores) * clamped / 100.0).rounded(.toNearestOrAwayFromZero))
        return min(activeCores, max(1, requested))
    }

    static func estimateAdvancedScanSeconds(
        combinationBudget: Int,
        secondsPerCombination: Double,
        benchmarkCPUAllocationPercent: Double,
        cpuAllocationPercent: Double
    ) -> Double {
        let planned = Double(min(max(combinationBudget, 10), advancedSearchSpaceSize))
        let observedSeconds = max(0.02, secondsPerCombination)
        let benchmarkCPU = min(max(benchmarkCPUAllocationPercent, 10), 100)
        let currentCPU = min(max(cpuAllocationPercent, 10), 100)
        let cpuScale = sqrt(benchmarkCPU / currentCPU)
        return planned * observedSeconds * cpuScale
    }

    private static func evaluateScanCandidates(
        _ candidates: [ScanCandidate],
        matrix: CSVMatrix,
        pixelSize: (Double, Double)?,
        cpuAllocationPercent: Double,
        cancellationToken: CancellationToken?
    ) throws -> [NucleiScanRecord] {
        guard !candidates.isEmpty else { return [] }
        try cancellationToken?.checkCancellation()

        let searchWorkers = min(
            advancedScanMaxConcurrentEvaluations,
            min(candidates.count, workerCount(for: cpuAllocationPercent))
        )
        let perEvaluationCPU = max(1, min(100, cpuAllocationPercent / Double(max(1, searchWorkers))))

        guard searchWorkers > 1 else {
            return try candidates.map { candidate in
                try cancellationToken?.checkCancellation()
                let result = try analyze(
                    matrix: matrix,
                    params: candidate.params,
                    pixelSize: pixelSize,
                    renderImage: false,
                    cpuAllocationPercent: perEvaluationCPU,
                    cancellationToken: cancellationToken
                )
                return NucleiScanRecord(
                    comboIndex: candidate.comboIndex,
                    stage: candidate.stage,
                    count: result.count,
                    params: candidate.params
                )
            }
        }

        let lock = NSLock()
        var records: [NucleiScanRecord] = []
        var firstError: Error?

        DispatchQueue.concurrentPerform(iterations: searchWorkers) { workerIndex in
            let start = workerIndex * candidates.count / searchWorkers
            let end = (workerIndex + 1) * candidates.count / searchWorkers
            guard start < end else { return }

            var localRecords: [NucleiScanRecord] = []
            var localError: Error?
            for index in start..<end {
                do {
                    try cancellationToken?.checkCancellation()
                    let candidate = candidates[index]
                    let result = try analyze(
                        matrix: matrix,
                        params: candidate.params,
                        pixelSize: pixelSize,
                        renderImage: false,
                        cpuAllocationPercent: perEvaluationCPU,
                        cancellationToken: cancellationToken
                    )
                    localRecords.append(
                        NucleiScanRecord(
                            comboIndex: candidate.comboIndex,
                            stage: candidate.stage,
                            count: result.count,
                            params: candidate.params
                        )
                    )
                } catch {
                    localError = error
                    break
                }
            }

            lock.lock()
            records.append(contentsOf: localRecords)
            if firstError == nil {
                firstError = localError
            }
            lock.unlock()
        }

        if let firstError {
            throw firstError
        }

        return records.sorted { $0.comboIndex < $1.comboIndex }
    }

    private static func analyze(
        matrix: CSVMatrix,
        params: NucleiParameters,
        pixelSize: (Double, Double)?,
        renderImage: Bool,
        cpuAllocationPercent: Double,
        cancellationToken: CancellationToken? = nil
    ) throws -> NucleiSegmentationResult {
        guard matrix.width > 0, matrix.height > 0, matrix.values.count == matrix.width * matrix.height else {
            throw SpatialScopeError.message("Invalid matrix shape for nuclei segmentation.")
        }
        try cancellationToken?.checkCancellation()

        let workerCount = workerCount(for: cpuAllocationPercent)
        let normalized = normalize(
            matrix.values,
            high: max(0.000_001, matrix.percentile(99.8)),
            workerCount: workerCount
        )
        let scaleUmPerPx = sqrt(max(0.000_001, (pixelSize?.0 ?? 1.0) * (pixelSize?.1 ?? 1.0)))

        var work = normalized
        let tophatPx = max(0, Int(round(params.tophatRadiusUm / scaleUmPerPx)))
        if tophatPx > 0 {
            try cancellationToken?.checkCancellation()
            let background = boxBlur(
                work,
                width: matrix.width,
                height: matrix.height,
                radius: min(tophatPx, 30),
                workerCount: workerCount
            )
            work = subtractBackground(work, background: background, workerCount: workerCount)
        }

        let sigmaPx = max(0, Int(round(params.gaussSigmaUm / scaleUmPerPx)))
        if sigmaPx > 0 {
            try cancellationToken?.checkCancellation()
            work = boxBlur(
                work,
                width: matrix.width,
                height: matrix.height,
                radius: min(max(1, sigmaPx), 12),
                workerCount: workerCount
            )
        }

        let stats = meanStd(work)
        let thresholdFactor = 0.56
            + (params.localOffset * 2.0)
            + (params.hMaximaUm * 0.18)
            + (params.seedMinDistUm * 0.012)
            + (params.watershedCompactness * 0.018)
            - (params.postResplitMult * 0.010)
        let threshold = min(max(stats.mean + stats.std * thresholdFactor, 0.01), 0.98)

        let mask = thresholdMask(work, threshold: threshold, workerCount: workerCount)

        let minRadiusPx = max(0.5, params.minDiamUm / max(0.000_001, scaleUmPerPx) / 2.0)
        let maxRadiusPx = max(minRadiusPx, params.maxDiamUm / max(0.000_001, scaleUmPerPx) / 2.0)
        let minArea = max(1, Int(Double.pi * minRadiusPx * minRadiusPx * 0.35))
        let maxArea = max(minArea, Int(Double.pi * maxRadiusPx * maxRadiusPx * 1.75))

        var visited = [Bool](repeating: false, count: mask.count)
        var labels = renderImage ? [Int](repeating: 0, count: mask.count) : []
        var detections: [NucleiDetection] = []
        var labelID = 1
        let width = matrix.width
        let height = matrix.height
        let neighbors = [(1, 0), (-1, 0), (0, 1), (0, -1)]

        for start in mask.indices where mask[start] && !visited[start] {
            if start % 4_096 == 0 {
                try cancellationToken?.checkCancellation()
            }
            var stack = [start]
            visited[start] = true
            var pixels: [Int] = renderImage ? [] : []
            var area = 0
            var sumX = 0.0
            var sumY = 0.0
            var sumIntensity = 0.0

            while let index = stack.popLast() {
                area += 1
                if renderImage { pixels.append(index) }
                let x = index % width
                let y = index / width
                sumX += Double(x)
                sumY += Double(y)
                sumIntensity += normalized[index]

                for neighbor in neighbors {
                    let nx = x + neighbor.0
                    let ny = y + neighbor.1
                    guard nx >= 0, nx < width, ny >= 0, ny < height else { continue }
                    let next = ny * width + nx
                    if mask[next] && !visited[next] {
                        visited[next] = true
                        stack.append(next)
                    }
                }
            }

            guard area >= minArea, area <= maxArea else { continue }
            let detection = NucleiDetection(
                id: labelID,
                centroidX: sumX / Double(area),
                centroidY: sumY / Double(area),
                areaPx: area,
                meanIntensity: sumIntensity / Double(area)
            )
            detections.append(detection)
            if renderImage {
                for pixel in pixels {
                    labels[pixel] = labelID
                }
            }
            labelID += 1
        }

        let image: NSImage
        if renderImage {
            try cancellationToken?.checkCancellation()
            image = try renderSegmentationImage(
                normalized: normalized,
                labels: labels,
                width: width,
                height: height,
                workerCount: workerCount
            )
        } else {
            image = try ImageExportService.nsImage(width: 1, height: 1, rgba: [0, 0, 0, 0])
        }

        return NucleiSegmentationResult(
            count: detections.count,
            params: params,
            channelName: matrix.channelName,
            image: image,
            detections: detections,
            labelMap: renderImage
                ? NucleiLabelMap(width: width, height: height, labels: labels)
                : nil
        )
    }

    private static func normalize(_ values: [Double], high: Double, workerCount: Int) -> [Double] {
        parallelOrderedChunks(count: values.count, workerCount: workerCount) { range in
            range.map { index in
                min(max(values[index] / high, 0), 1)
            }
        }
    }

    private static func subtractBackground(
        _ input: [Double],
        background: [Double],
        workerCount: Int
    ) -> [Double] {
        parallelOrderedChunks(count: input.count, workerCount: workerCount) { range in
            range.map { index in
                max(0, input[index] - background[index])
            }
        }
    }

    private static func thresholdMask(_ values: [Double], threshold: Double, workerCount: Int) -> [Bool] {
        parallelOrderedChunks(count: values.count, workerCount: workerCount) { range in
            range.map { index in
                values[index] >= threshold
            }
        }
    }

    private static func meanStd(_ values: [Double]) -> (mean: Double, std: Double) {
        guard !values.isEmpty else { return (0, 0) }
        let mean = values.reduce(0, +) / Double(values.count)
        let variance = values.reduce(0) { $0 + (($1 - mean) * ($1 - mean)) } / Double(values.count)
        return (mean, sqrt(max(variance, 0)))
    }

    private static func boxBlur(
        _ input: [Double],
        width: Int,
        height: Int,
        radius: Int,
        workerCount: Int
    ) -> [Double] {
        guard radius > 0 else { return input }
        var output = [Double](repeating: 0, count: input.count)

        let horizontalRows = parallelOrderedRowChunks(height: height, workerCount: workerCount) { rowRange in
            var local = [Double]()
            local.reserveCapacity(rowRange.count * width)
            for y in rowRange {
                var sum = 0.0
                var count = 0
                for x in 0..<width {
                    let addX = x + radius
                    if addX < width {
                        sum += input[y * width + addX]
                        count += 1
                    }
                    let removeX = x - radius - 1
                    if removeX >= 0 {
                        sum -= input[y * width + removeX]
                        count -= 1
                    }
                    if x == 0 {
                        for leading in 0..<min(radius, width) {
                            if leading != addX {
                                sum += input[y * width + leading]
                                count += 1
                            }
                        }
                    }
                    local.append(count > 0 ? sum / Double(count) : input[y * width + x])
                }
            }
            return local
        }
        let horizontal = horizontalRows.flatMap { $0 }

        for x in 0..<width {
            var sum = 0.0
            var count = 0
            for y in 0..<height {
                let addY = y + radius
                if addY < height {
                    sum += horizontal[addY * width + x]
                    count += 1
                }
                let removeY = y - radius - 1
                if removeY >= 0 {
                    sum -= horizontal[removeY * width + x]
                    count -= 1
                }
                if y == 0 {
                    for leading in 0..<min(radius, height) {
                        if leading != addY {
                            sum += horizontal[leading * width + x]
                            count += 1
                        }
                    }
                }
                output[y * width + x] = count > 0 ? sum / Double(count) : horizontal[y * width + x]
            }
        }

        return output
    }

    private static func renderSegmentationImage(
        normalized: [Double],
        labels: [Int],
        width: Int,
        height: Int,
        workerCount: Int
    ) throws -> NSImage {
        var rgba = [UInt8](repeating: 0, count: width * height * 4)
        let palette = ColorPalette.segmentation
        _ = workerCount

        for index in normalized.indices {
            let gray = UInt8(min(max(normalized[index], 0), 1) * 160)
            let offset = index * 4
            rgba[offset] = gray
            rgba[offset + 1] = gray
            rgba[offset + 2] = gray
            rgba[offset + 3] = 255
        }

        for y in 0..<height {
            for x in 0..<width {
                let index = y * width + x
                let label = labels[index]
                guard label > 0 else { continue }
                let color = NSColor(hex: palette[(label - 1) % palette.count]) ?? .systemYellow
                let (r, g, b) = color.rgbComponents01
                let offset = index * 4
                rgba[offset] = UInt8(r * 255)
                rgba[offset + 1] = UInt8(g * 255)
                rgba[offset + 2] = UInt8(b * 255)
                rgba[offset + 3] = 255
            }
        }

        return try ImageExportService.nsImage(width: width, height: height, rgba: rgba)
    }

    private static func downsample(
        matrix: CSVMatrix,
        pixelSize: (Double, Double)?,
        maxDimension: Int
    ) -> (matrix: CSVMatrix, pixelSize: (Double, Double)?) {
        let factor = max(1, Int(ceil(Double(max(matrix.width, matrix.height)) / Double(maxDimension))))
        guard factor > 1 else { return (matrix, pixelSize) }

        let newWidth = max(1, matrix.width / factor)
        let newHeight = max(1, matrix.height / factor)
        var values = [Double](repeating: 0, count: newWidth * newHeight)

        for y in 0..<newHeight {
            for x in 0..<newWidth {
                values[y * newWidth + x] = matrix[min(matrix.width - 1, x * factor), min(matrix.height - 1, y * factor)]
            }
        }

        let sampled = CSVMatrix(
            channelName: matrix.channelName,
            fileName: matrix.fileName,
            width: newWidth,
            height: newHeight,
            values: values
        )
        let sampledPixelSize = pixelSize.map { ($0.0 * Double(factor), $0.1 * Double(factor)) }
        return (sampled, sampledPixelSize)
    }

    private static func fullRangeValues(range: ClosedRange<Double>, count: Int) -> [Double] {
        guard count > 1 else { return [roundedScanValue((range.lowerBound + range.upperBound) / 2.0)] }
        let span = range.upperBound - range.lowerBound
        return (0..<count).map { index in
            roundedScanValue(range.lowerBound + span * Double(index) / Double(count - 1))
        }
    }

    private static func refinedRangeValues(
        center: Double,
        range: ClosedRange<Double>,
        refinementLevel: Int
    ) -> [Double] {
        let span = max(0.000_001, range.upperBound - range.lowerBound)
        let radius = span / pow(2.0, Double(refinementLevel + 2))
        return smartValues(
            center: center,
            deltas: [-radius, 0, radius],
            range: range
        )
    }

    private static func parametersFromGrid(
        base: NucleiParameters,
        axes: [SearchAxis],
        valuesByAxis: [[Double]],
        gridIndex: Int
    ) -> NucleiParameters {
        var params = base
        var index = max(0, gridIndex)
        for axisIndex in axes.indices {
            let values = valuesByAxis[axisIndex]
            guard !values.isEmpty else { continue }
            let valueIndex = index % values.count
            params[keyPath: axes[axisIndex].keyPath] = values[valueIndex]
            index /= values.count
        }
        return params
    }

    private static func gridSampleIndex(
        sampleIndex: Int,
        sampleCount: Int,
        totalCount: Int
    ) -> Int {
        guard totalCount > 1 else { return 0 }
        let safeSampleCount = max(1, min(sampleCount, totalCount))
        if sampleIndex >= safeSampleCount {
            return abs((sampleIndex * 104_729) + (sampleIndex * sampleIndex * 37)) % totalCount
        }
        let stride = max(1, totalCount / safeSampleCount)
        let jitter = (sampleIndex * sampleIndex * 37) % stride
        return min(totalCount - 1, sampleIndex * stride + stride / 2 + jitter)
    }

    private static func productCount(_ valuesByAxis: [[Double]]) -> Int {
        valuesByAxis.reduce(1) { partial, values in
            partial * max(1, values.count)
        }
    }

    private static func powInt(_ base: Int, _ exponent: Int) -> Int {
        guard exponent > 0 else { return 1 }
        return (0..<exponent).reduce(1) { partial, _ in partial * base }
    }

    private static func roundedScanValue(_ value: Double) -> Double {
        (value * 1000).rounded() / 1000
    }

    private static func smartValues(center: Double, deltas: [Double], range: ClosedRange<Double>) -> [Double] {
        Array(
            Set(
                deltas.map { delta in
                    let value = min(max(center + delta, range.lowerBound), range.upperBound)
                    return roundedScanValue(value)
                }
            )
        )
        .sorted()
    }

    private static func parameterKey(_ params: NucleiParameters) -> String {
        [
            params.minDiamUm,
            params.maxDiamUm,
            params.tophatRadiusUm,
            params.gaussSigmaUm,
            params.localWinUm,
            params.localOffset,
            params.hMaximaUm,
            params.seedMinDistUm,
            params.watershedCompactness,
            params.postResplitMult
        ]
        .map { String(format: "%.4f", $0) }
        .joined(separator: "|")
    }

    private static func workerCount(for cpuAllocationPercent: Double) -> Int {
        effectiveWorkerCount(cpuAllocationPercent: cpuAllocationPercent)
    }

    private static func effectiveWorkerCount(count: Int, workerCount: Int) -> Int {
        guard count >= 2_048 else { return 1 }
        return min(max(1, workerCount), max(1, count))
    }

    private static func parallelOrderedChunks<Element>(
        count: Int,
        workerCount: Int,
        body: @escaping (Range<Int>) -> [Element]
    ) -> [Element] {
        guard count > 0 else { return [] }
        let workers = effectiveWorkerCount(count: count, workerCount: workerCount)
        guard workers > 1 else {
            return body(0..<count)
        }

        let lock = NSLock()
        var parts: [(Int, [Element])] = []
        parts.reserveCapacity(workers)
        DispatchQueue.concurrentPerform(iterations: workers) { workerIndex in
            let start = workerIndex * count / workers
            let end = (workerIndex + 1) * count / workers
            guard start < end else { return }
            let values = body(start..<end)
            lock.lock()
            parts.append((workerIndex, values))
            lock.unlock()
        }
        return parts.sorted { $0.0 < $1.0 }.flatMap(\.1)
    }

    private static func parallelOrderedRowChunks<Element>(
        height: Int,
        workerCount: Int,
        body: @escaping (Range<Int>) -> [Element]
    ) -> [[Element]] {
        guard height > 0 else { return [] }
        let workers = effectiveWorkerCount(count: height, workerCount: workerCount)
        guard workers > 1 else {
            return [body(0..<height)]
        }

        let lock = NSLock()
        var parts: [(Int, [Element])] = []
        parts.reserveCapacity(workers)
        DispatchQueue.concurrentPerform(iterations: workers) { workerIndex in
            let start = workerIndex * height / workers
            let end = (workerIndex + 1) * height / workers
            guard start < end else { return }
            let values = body(start..<end)
            lock.lock()
            parts.append((workerIndex, values))
            lock.unlock()
        }
        return parts.sorted { $0.0 < $1.0 }.map(\.1)
    }
}
