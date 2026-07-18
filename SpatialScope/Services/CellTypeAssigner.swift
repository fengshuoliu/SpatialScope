import AppKit
import Dispatch
import Foundation

enum CellTypeAssigner {
    private static let geometryCacheLock = NSLock()
    private static var nearestLabelDistanceCache: [String: (nearestLabels: [Int], distanceSquared: [Double])] = [:]
    private static let maxNativeVotingRadiusPx = 24

    private struct AssignmentSearchAxis {
        var keyPath: WritableKeyPath<AssignmentParameters, Double>
        var range: ClosedRange<Double>
    }

    private struct PixelOffset {
        var dy: Int
        var dx: Int
    }

    private struct AssignmentGeometry {
        var width: Int
        var height: Int
        var labels: [Int]
        var nearestLabels: [Int]
        var distanceSquared: [Double]
        var voronoiBand: [Bool]
        var expandedLabels: [Int]
        var bufferZone: [Bool]
        var ownerLabels: [Int]
        var voteOffsets: [PixelOffset]
        var candidateOffsets: [PixelOffset]
    }

    private struct AssignmentScreeningInput {
        var detections: [NucleiDetection]
        var matrices: [CSVMatrix]
        var labelMap: NucleiLabelMap?
        var pixelSize: (Double, Double)?
        var factor: Int
    }

    private struct MarkerProfile {
        var displayName: String
        var matrix: CSVMatrix?
        var integralImage: [Double]
        var normalizedValues: [Double]
        var positiveIndicesByMode: [String: [Int]]
        var positiveIntegralByMode: [String: [Double]]
        var positiveSignalIntegralByMode: [String: [Double]]
        var thresholdByMode: [String: Double]
        var generatedValuesByDetectionID: [Int: Double]
        var high: Double
        var positiveThreshold: Double
    }

    private struct MarkerEvidence {
        var positivePixels: Int
        var summedIntensity: Double
        var isPositive: Bool
    }

    private struct MarkerEvidenceSlice {
        var markerKey: String
        var valuesByDetectionID: [Int: MarkerEvidence]
    }

    private struct LabelAnchor {
        var id: Int
        var x: Double
        var y: Double
        var radius: Double
    }

    private struct SpatialLabelIndex {
        var anchors: [LabelAnchor]
        var cells: [Int64: [Int]]
        var cellSize: Double
        var searchCellRadius: Int
        var maxAnchorRadius: Double

        func nearestLabel(x: Double, y: Double, reachPx: Double) -> Int? {
            let cellX = Int(floor(x / cellSize))
            let cellY = Int(floor(y / cellSize))
            var bestID: Int?
            var bestBoundaryDistance = Double.infinity
            for yy in (cellY - searchCellRadius)...(cellY + searchCellRadius) {
                for xx in (cellX - searchCellRadius)...(cellX + searchCellRadius) {
                    guard let indices = cells[Self.key(x: xx, y: yy)] else { continue }
                    for index in indices {
                        let anchor = anchors[index]
                        let dx = x - anchor.x
                        let dy = y - anchor.y
                        let boundaryDistance = sqrt(dx * dx + dy * dy) - anchor.radius
                        guard boundaryDistance <= reachPx, boundaryDistance < bestBoundaryDistance else { continue }
                        bestBoundaryDistance = boundaryDistance
                        bestID = anchor.id
                    }
                }
            }
            return bestID
        }

        static func key(x: Int, y: Int) -> Int64 {
            (Int64(x) << 32) ^ (Int64(y) & 0x0000_0000_ffff_ffff)
        }
    }

    private struct ParsedCellType {
        var definition: CellTypeDefinition
        var positives: [String]
        var negatives: [String]
        var anyGroups: [[String]]
    }

    static func parameterSearchSpaceSize(fixVoronoi: Bool = false, fixBuffer: Bool = false) -> Int {
        Int(pow(5.0, Double(assignmentSearchAxes(fixVoronoi: fixVoronoi, fixBuffer: fixBuffer).count))) * 3 * 7 * 5 * 2
    }

    static func run(
        detections: [NucleiDetection],
        matrices: [CSVMatrix],
        channels: [ChannelConfig],
        cellTypes: [CellTypeDefinition],
        parameters: AssignmentParameters,
        pixelSize: (Double, Double)?,
        labelMap: NucleiLabelMap? = nil,
        cpuAllocationPercent: Double,
        cancellationToken: CancellationToken? = nil
    ) throws -> CellTypeAssignmentResult {
        try cancellationToken?.checkCancellation()
        guard !detections.isEmpty else {
            throw SpatialScopeError.message("Run final nuclei segmentation before cell-type assignment.")
        }
        guard let first = matrices.first else {
            throw SpatialScopeError.message("No channel matrices are loaded for cell-type assignment.")
        }
        for matrix in matrices where matrix.width != first.width || matrix.height != first.height {
            throw SpatialScopeError.message("\(matrix.fileName) has shape \(matrix.width)x\(matrix.height), expected \(first.width)x\(first.height).")
        }

        let workerCount = NucleiSegmenter.effectiveWorkerCount(cpuAllocationPercent: cpuAllocationPercent)
        let profileChannels = assignmentRelevantChannels(channels: channels, cellTypes: cellTypes)
        let profiles = markerProfiles(
            matrices: matrices,
            channels: profileChannels,
            detections: detections,
            parameters: parameters,
            workerCount: workerCount
        )
        guard !profiles.isEmpty else {
            throw SpatialScopeError.message("No marker channels are available for cell-type assignment.")
        }

        let parsedCellTypes = cellTypes.map(parseCellType)
        let scaleUmPerPx = sqrt(max(0.000_001, (pixelSize?.0 ?? 1.0) * (pixelSize?.1 ?? 1.0)))
        let voteRadiusPx = max(1, Int(round(max(0.5, parameters.rVoteUm + parameters.rBufferUm) / scaleUmPerPx)))
        let labelEvidence = labelMap.flatMap { map in
            markerEvidenceByDetection(
                detections: detections,
                profiles: profiles,
                labelMap: map,
                parameters: parameters,
                scaleUmPerPx: scaleUmPerPx,
                workerCount: workerCount
            )
        }

        let rawAssignments = try parallelAssignments(
            detections: detections,
            profiles: profiles,
            parsedCellTypes: parsedCellTypes,
            parameters: parameters,
            radiusPx: voteRadiusPx,
            workerCount: workerCount,
            labelEvidence: labelEvidence,
            cancellationToken: cancellationToken
        )
        try cancellationToken?.checkCancellation()
        let assignments = try assignmentsWithMarkerInformedBoundaries(
            rawAssignments,
            profiles: profiles,
            parameters: parameters,
            pixelSize: pixelSize,
            width: first.width,
            height: first.height,
            cancellationToken: cancellationToken
        )
        let cellTypeIDByName = Dictionary(uniqueKeysWithValues: cellTypes.enumerated().map { index, cellType in
            (cellType.name, UInt16(index + 1))
        })
        let cellTypeMask = makeCellTypeMask(
            assignments: assignments,
            cellTypeIDByName: cellTypeIDByName,
            width: first.width,
            height: first.height
        )
        let counts = makeCounts(assignments: assignments, cellTypes: cellTypes)
        let image = try renderAssignmentMap(
            assignments: assignments,
            labelMap: labelMap,
            profiles: profiles,
            parameters: parameters,
            scaleUmPerPx: scaleUmPerPx,
            pixelSize: pixelSize,
            width: first.width,
            height: first.height
        )
        let statsImage = renderCountsPlot(counts: counts)

        return CellTypeAssignmentResult(
            assignments: assignments,
            counts: counts,
            parameters: parameters,
            image: image,
            statsImage: statsImage,
            width: first.width,
            height: first.height,
            cellTypeMask: cellTypeMask,
            cellTypeIDByName: cellTypeIDByName
        )
    }

    static func runParameterScreening(
        detections: [NucleiDetection],
        matrices: [CSVMatrix],
        channels: [ChannelConfig],
        cellTypes: [CellTypeDefinition],
        baseParameters: AssignmentParameters,
        pixelSize: (Double, Double)?,
        labelMap: NucleiLabelMap? = nil,
        cpuAllocationPercent: Double,
        combinationBudget: Int,
        fixVoronoi: Bool = false,
        fixBuffer: Bool = false,
        screeningBandCount: Int = 6,
        screeningSubsetMode: AssignmentScreeningSubsetMode = .randomThree,
        screeningSelectedBands: [Int]? = nil,
        cancellationToken: CancellationToken? = nil
    ) throws -> [AssignmentScanRecord] {
        guard !detections.isEmpty else {
            throw SpatialScopeError.message("Run final nuclei segmentation before assignment screening.")
        }
        guard let first = matrices.first else {
            throw SpatialScopeError.message("No channel matrices are loaded for assignment screening.")
        }
        for matrix in matrices where matrix.width != first.width || matrix.height != first.height {
            throw SpatialScopeError.message("\(matrix.fileName) has shape \(matrix.width)x\(matrix.height), expected \(first.width)x\(first.height).")
        }

        let axes = assignmentSearchAxes(fixVoronoi: fixVoronoi, fixBuffer: fixBuffer)
        let planned = min(
            max(combinationBudget, 10),
            max(10, parameterSearchSpaceSize(fixVoronoi: fixVoronoi, fixBuffer: fixBuffer))
        )
        let screeningInput = makeAssignmentScreeningInput(
            detections: detections,
            matrices: matrices,
            labelMap: labelMap,
            pixelSize: pixelSize,
            bandCount: screeningBandCount,
            subsetMode: screeningSubsetMode,
            selectedBandsOverride: screeningSelectedBands
        )
        let workerCount = NucleiSegmenter.effectiveWorkerCount(cpuAllocationPercent: cpuAllocationPercent)
        let profileChannels = assignmentRelevantChannels(channels: channels, cellTypes: cellTypes)
        let profiles = markerProfiles(
            matrices: screeningInput.matrices,
            channels: profileChannels,
            detections: screeningInput.detections,
            parameters: baseParameters,
            workerCount: workerCount
        )
        let parsedCellTypes = cellTypes.map(parseCellType)
        let scaleUmPerPx = sqrt(max(0.000_001, (screeningInput.pixelSize?.0 ?? 1.0) * (screeningInput.pixelSize?.1 ?? 1.0)))
        let screeningDetections = deterministicScreeningSample(screeningInput.detections, maxCount: 1_000)
        let countScale = Double(detections.count) / Double(max(1, screeningDetections.count))

        let coarseCount = min(planned, max(8, Int(ceil(Double(planned) * 0.45))))
        let coarseRecords = try evaluateAssignmentRecords(
            localCount: coarseCount,
            comboOffset: 0,
            stage: "coarse",
            parameterProvider: { localIndex in
                assignmentParameters(
                    base: baseParameters,
                    axes: axes,
                    comboIndex: localIndex,
                    comboCount: coarseCount
                )
            },
            detections: screeningDetections,
            totalDetectionCount: detections.count,
            countScale: countScale,
            profiles: profiles,
            parsedCellTypes: parsedCellTypes,
            scaleUmPerPx: scaleUmPerPx,
            labelMap: screeningInput.labelMap,
            cpuAllocationPercent: cpuAllocationPercent,
            cancellationToken: cancellationToken
        )

        let refineCount = planned - coarseRecords.count
        guard refineCount > 0 else {
            return coarseRecords.sorted { $0.comboIndex < $1.comboIndex }
        }

        let seeds = coarseRecords
            .sorted(by: isBetterAssignmentRecord)
            .prefix(min(12, max(1, coarseRecords.count)))

        let refinedRecords = try evaluateAssignmentRecords(
            localCount: refineCount,
            comboOffset: coarseRecords.count,
            stage: "refine",
            parameterProvider: { localIndex in
                let seed = Array(seeds)[localIndex % max(1, seeds.count)]
                return refinedAssignmentParameters(
                    from: seed.parameters,
                    axes: axes,
                    localIndex: localIndex
                )
            },
            detections: screeningDetections,
            totalDetectionCount: detections.count,
            countScale: countScale,
            profiles: profiles,
            parsedCellTypes: parsedCellTypes,
            scaleUmPerPx: scaleUmPerPx,
            labelMap: screeningInput.labelMap,
            cpuAllocationPercent: cpuAllocationPercent,
            cancellationToken: cancellationToken
        )

        return (coarseRecords + refinedRecords).sorted { $0.comboIndex < $1.comboIndex }
    }

    private static func evaluateAssignmentRecords(
        localCount: Int,
        comboOffset: Int,
        stage: String,
        parameterProvider: @escaping (Int) -> AssignmentParameters,
        detections: [NucleiDetection],
        totalDetectionCount: Int,
        countScale: Double,
        profiles: [MarkerProfile],
        parsedCellTypes: [ParsedCellType],
        scaleUmPerPx: Double,
        labelMap: NucleiLabelMap?,
        cpuAllocationPercent: Double,
        cancellationToken: CancellationToken?
    ) throws -> [AssignmentScanRecord] {
        let workerCount = NucleiSegmenter.effectiveWorkerCount(cpuAllocationPercent: cpuAllocationPercent)
        let searchWorkers = min(max(1, workerCount), max(1, localCount))
        let workersPerEvaluation = max(1, workerCount / max(1, searchWorkers))
        let lock = NSLock()
        var records: [AssignmentScanRecord] = []
        var firstError: Error?

        DispatchQueue.concurrentPerform(iterations: searchWorkers) { workerIndex in
            let start = workerIndex * localCount / searchWorkers
            let end = (workerIndex + 1) * localCount / searchWorkers
            guard start < end else { return }
            var local: [AssignmentScanRecord] = []
            var localError: Error?

            for localIndex in start..<end {
                do {
                    try cancellationToken?.checkCancellation()
                    let parameters = parameterProvider(localIndex)
                    let voteRadiusPx = max(1, Int(round(max(0.5, parameters.rVoteUm + parameters.rBufferUm) / scaleUmPerPx)))
                    let labelEvidence = labelMap.flatMap { map in
                        markerEvidenceByDetection(
                            detections: detections,
                            profiles: profiles,
                            labelMap: map,
                            parameters: parameters,
                            scaleUmPerPx: scaleUmPerPx,
                            workerCount: workersPerEvaluation
                        )
                    }
                    let assignments = try parallelAssignments(
                        detections: detections,
                        profiles: profiles,
                        parsedCellTypes: parsedCellTypes,
                        parameters: parameters,
                        radiusPx: voteRadiusPx,
                        workerCount: workersPerEvaluation,
                        labelEvidence: labelEvidence,
                        cancellationToken: cancellationToken
                    )
                    let sampledCounts = assignments.reduce(into: (unassigned: 0, ambiguous: 0)) { counts, assignment in
                        if assignment.assignedType == "Unassigned" {
                            counts.unassigned += 1
                        } else if assignment.assignedType == "Ambiguous" {
                            counts.ambiguous += 1
                        }
                    }
                    let sampledUnassigned = sampledCounts.unassigned
                    let sampledAmbiguous = sampledCounts.ambiguous
                    let unassigned = Int(round(Double(sampledUnassigned) * countScale))
                    let ambiguous = Int(round(Double(sampledAmbiguous) * countScale))
                    let assigned = max(0, totalDetectionCount - unassigned - ambiguous)
                    local.append(
                        AssignmentScanRecord(
                            comboIndex: comboOffset + localIndex + 1,
                            stage: stage,
                            unassignedCount: unassigned,
                            ambiguousCount: ambiguous,
                            assignedCount: assigned,
                            parameters: parameters
                        )
                    )
                } catch {
                    localError = error
                    break
                }
            }

            lock.lock()
            records.append(contentsOf: local)
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

    private static func markerProfiles(
        matrices: [CSVMatrix],
        channels: [ChannelConfig],
        detections: [NucleiDetection],
        parameters: AssignmentParameters,
        workerCount: Int
    ) -> [MarkerProfile] {
        let matrixByFile = Dictionary(uniqueKeysWithValues: matrices.map { ($0.fileName, $0) })
        var profiles = parallelCompactMap(channels, workerCount: workerCount) { channel -> MarkerProfile? in
            guard let matrix = matrixByFile[channel.fileName] else { return nil }
            let normalized = normalizedValues(for: matrix)
            let thresholds = markerThresholds(values: normalized)
            var positiveIndicesByMode: [String: [Int]] = [:]
            var positiveIntegralByMode: [String: [Double]] = [:]
            var positiveSignalIntegralByMode: [String: [Double]] = [:]
            for mode in ["global_otsu", "local", "yen"] {
                let threshold = thresholds[mode] ?? thresholds["global_otsu"] ?? 0.5
                var positiveValues = Array(repeating: 0.0, count: normalized.count)
                var positiveSignals = Array(repeating: 0.0, count: normalized.count)
                var indices: [Int] = []
                indices.reserveCapacity(normalized.count / 8)
                for (index, value) in normalized.enumerated() where value > threshold {
                    positiveValues[index] = 1.0
                    positiveSignals[index] = value
                    indices.append(index)
                }
                positiveIndicesByMode[mode] = indices
                positiveIntegralByMode[mode] = summedAreaTable(
                    width: matrix.width,
                    height: matrix.height,
                    values: positiveValues
                )
                positiveSignalIntegralByMode[mode] = summedAreaTable(
                    width: matrix.width,
                    height: matrix.height,
                    values: positiveSignals
                )
            }
            let defaultThreshold = thresholds[parameters.threshMode] ?? thresholds["global_otsu"] ?? 0.5
            return MarkerProfile(
                displayName: channel.channelName,
                matrix: matrix,
                integralImage: summedAreaTable(matrix),
                normalizedValues: normalized,
                positiveIndicesByMode: positiveIndicesByMode,
                positiveIntegralByMode: positiveIntegralByMode,
                positiveSignalIntegralByMode: positiveSignalIntegralByMode,
                thresholdByMode: thresholds,
                generatedValuesByDetectionID: [:],
                high: 1.0,
                positiveThreshold: defaultThreshold
            )
        }
        let nuclearValues = Dictionary(uniqueKeysWithValues: detections.map { ($0.id, $0.meanIntensity) })
        if !nuclearValues.isEmpty {
            profiles.append(
                MarkerProfile(
                    displayName: GeneratedMarkerNames.nuclearSegmentationSignal,
                    matrix: nil,
                    integralImage: [],
                    normalizedValues: [],
                    positiveIndicesByMode: [:],
                    positiveIntegralByMode: [:],
                    positiveSignalIntegralByMode: [:],
                    thresholdByMode: [:],
                    generatedValuesByDetectionID: nuclearValues,
                    high: max(0.000_001, detections.map(\.meanIntensity).max() ?? 0),
                    positiveThreshold: 0.000_001
                )
            )
        }
        return profiles
    }

    private static func parallelCompactMap<Input, Output>(
        _ inputs: [Input],
        workerCount: Int,
        transform: (Input) -> Output?
    ) -> [Output] {
        let workers = min(max(1, workerCount), max(1, inputs.count))
        guard workers > 1 else {
            return inputs.compactMap(transform)
        }

        let lock = NSLock()
        var parts: [(Int, [Output])] = []
        parts.reserveCapacity(workers)
        DispatchQueue.concurrentPerform(iterations: workers) { workerIndex in
            let start = workerIndex * inputs.count / workers
            let end = (workerIndex + 1) * inputs.count / workers
            guard start < end else { return }
            var local: [Output] = []
            local.reserveCapacity(end - start)
            for index in start..<end {
                if let value = transform(inputs[index]) {
                    local.append(value)
                }
            }
            lock.lock()
            parts.append((workerIndex, local))
            lock.unlock()
        }
        return parts.sorted { $0.0 < $1.0 }.flatMap(\.1)
    }

    private static func markerEvidenceByDetection(
        detections: [NucleiDetection],
        profiles: [MarkerProfile],
        labelMap: NucleiLabelMap,
        parameters: AssignmentParameters,
        scaleUmPerPx: Double,
        workerCount: Int
    ) -> [Int: [String: MarkerEvidence]]? {
        guard labelMap.width > 0,
              labelMap.height > 0,
              labelMap.labels.count == labelMap.width * labelMap.height else {
            return nil
        }

        var evidenceByDetection: [Int: [String: MarkerEvidence]] = [:]
        evidenceByDetection.reserveCapacity(detections.count)
        let detectionIDs = Set(detections.map(\.id))
        for detection in detections {
            evidenceByDetection[detection.id] = [
                "nucleus": MarkerEvidence(
                    positivePixels: max(1, detection.areaPx),
                    summedIntensity: 1.0,
                    isPositive: detection.areaPx > 0
                )
            ]
        }

        let geometry = makeAssignmentGeometry(
            labelMap: labelMap,
            parameters: parameters,
            scaleUmPerPx: scaleUmPerPx
        )
        let matrixProfiles = profiles.filter { $0.matrix != nil }
        let slices = parallelCompactMap(matrixProfiles, workerCount: workerCount) { profile -> MarkerEvidenceSlice? in
            guard let matrix = profile.matrix,
                  matrix.width == labelMap.width,
                  matrix.height == labelMap.height,
                  profile.normalizedValues.count == labelMap.labels.count else {
                return nil
            }

            let markerKey = canonicalMarker(profile.displayName)
            guard markerKey != "nucleus" else { return nil }
            let markerPixels = parameterizedPositivePixels(
                profile: profile,
                parameters: parameters,
                scaleUmPerPx: scaleUmPerPx
            )
            var counts: [Int: Int] = [:]
            var sums: [Int: Double] = [:]
            counts.reserveCapacity(min(detections.count, markerPixels.indices.count))
            sums.reserveCapacity(min(detections.count, markerPixels.indices.count))

            for index in markerPixels.indices {
                guard markerPixelMayContributeToRequestedDetections(
                    index: index,
                    detectionIDs: detectionIDs,
                    geometry: geometry
                ) else { continue }
                let labelID = markerAssignmentLabel(
                    index: index,
                    processedValues: markerPixels.values,
                    geometry: geometry
                )
                guard labelID > 0, detectionIDs.contains(labelID) else { continue }
                counts[labelID, default: 0] += 1
                sums[labelID, default: 0] += markerPixels.values[index]
            }

            var valuesByDetectionID: [Int: MarkerEvidence] = [:]
            valuesByDetectionID.reserveCapacity(detections.count)
            for detection in detections {
                let count = counts[detection.id] ?? 0
                let sum = sums[detection.id] ?? 0
                let isPositive = parameters.minPosPix <= 0
                    ? count > 0
                    : count >= parameters.minPosPix
                valuesByDetectionID[detection.id] = MarkerEvidence(
                    positivePixels: count,
                    summedIntensity: sum,
                    isPositive: isPositive
                )
            }

            return MarkerEvidenceSlice(markerKey: markerKey, valuesByDetectionID: valuesByDetectionID)
        }

        for slice in slices {
            for (detectionID, evidence) in slice.valuesByDetectionID {
                evidenceByDetection[detectionID]?[slice.markerKey] = evidence
            }
        }

        return evidenceByDetection
    }

    private static func markerPixelMayContributeToRequestedDetections(
        index: Int,
        detectionIDs: Set<Int>,
        geometry: AssignmentGeometry
    ) -> Bool {
        let directLabel = geometry.labels[index]
        if directLabel > 0 {
            return detectionIDs.contains(directLabel)
        }
        let nearestLabel = geometry.nearestLabels[index]
        if nearestLabel > 0, detectionIDs.contains(nearestLabel) {
            return true
        }
        let ownerLabel = geometry.ownerLabels[index]
        if ownerLabel > 0, detectionIDs.contains(ownerLabel) {
            return true
        }
        let expandedLabel = geometry.expandedLabels[index]
        if expandedLabel > 0, detectionIDs.contains(expandedLabel) {
            return true
        }
        return false
    }

    private static func makeAssignmentGeometry(
        labelMap: NucleiLabelMap,
        parameters: AssignmentParameters,
        scaleUmPerPx: Double
    ) -> AssignmentGeometry {
        let width = labelMap.width
        let height = labelMap.height
        let labels = labelMap.labels
        let nearest = cachedNearestLabelDistanceMap(labelMap: labelMap)
        let rVoronoiPx = umToPositivePixelRadius(parameters.rVoronoiUm, scaleUmPerPx: scaleUmPerPx)
        let rBufferPx = umToPositivePixelRadius(parameters.rBufferUm, scaleUmPerPx: scaleUmPerPx)
        let rVotePx = umToPositivePixelRadius(parameters.rVoteUm, scaleUmPerPx: scaleUmPerPx)
        let rVoronoiSquared = Double(rVoronoiPx * rVoronoiPx)
        let rBufferSquared = Double(rBufferPx * rBufferPx)

        var voronoiBand = Array(repeating: false, count: labels.count)
        var ownerLabels = labels
        var expandedLabels = labels
        for index in labels.indices where labels[index] <= 0 {
            let nearestLabel = nearest.nearestLabels[index]
            guard nearestLabel > 0 else { continue }
            if nearest.distanceSquared[index] <= rVoronoiSquared {
                voronoiBand[index] = true
                ownerLabels[index] = nearestLabel
            }
            if nearest.distanceSquared[index] <= rBufferSquared {
                expandedLabels[index] = nearestLabel
            }
        }

        let boundaries = labelBoundaryMask(labels: expandedLabels, width: width, height: height)
        let boundaryDistanceSquared = distanceSquaredToMask(boundaries, width: width, height: height)
        let bufferRadius = max(1, rBufferPx / 2)
        let bufferRadiusSquared = Double(bufferRadius * bufferRadius)
        var bufferZone = Array(repeating: false, count: labels.count)
        for index in labels.indices where voronoiBand[index] && boundaryDistanceSquared[index] <= bufferRadiusSquared {
            bufferZone[index] = true
        }

        return AssignmentGeometry(
            width: width,
            height: height,
            labels: labels,
            nearestLabels: nearest.nearestLabels,
            distanceSquared: nearest.distanceSquared,
            voronoiBand: voronoiBand,
            expandedLabels: expandedLabels,
            bufferZone: bufferZone,
            ownerLabels: ownerLabels,
            voteOffsets: diskOffsets(radius: min(rVotePx, maxNativeVotingRadiusPx)),
            candidateOffsets: diskOffsets(radius: min(rBufferPx, maxNativeVotingRadiusPx))
        )
    }

    private static func markerAssignmentLabel(
        index: Int,
        processedValues: [Double],
        geometry: AssignmentGeometry
    ) -> Int {
        let directLabel = geometry.labels[index]
        if directLabel > 0 {
            return directLabel
        }
        guard geometry.voronoiBand[index] else {
            return 0
        }
        guard geometry.bufferZone[index] else {
            return geometry.nearestLabels[index]
        }

        let width = geometry.width
        let height = geometry.height
        let centerX = index % width
        let centerY = index / width
        var candidateLabels: [Int] = []
        var seenLabels = Set<Int>()
        for offset in geometry.candidateOffsets {
            let x = centerX + offset.dx
            let y = centerY + offset.dy
            guard x >= 0, x < width, y >= 0, y < height else { continue }
            let label = geometry.expandedLabels[y * width + x]
            guard label > 0, !seenLabels.contains(label) else { continue }
            seenLabels.insert(label)
            candidateLabels.append(label)
        }

        guard !candidateLabels.isEmpty else {
            return geometry.nearestLabels[index]
        }

        var bestLabel = 0
        var bestVote = -Double.infinity
        for label in candidateLabels {
            var vote = 0.0
            for offset in geometry.voteOffsets {
                let x = centerX + offset.dx
                let y = centerY + offset.dy
                guard x >= 0, x < width, y >= 0, y < height else { continue }
                let neighborIndex = y * width + x
                guard geometry.ownerLabels[neighborIndex] == label else { continue }
                vote += processedValues[neighborIndex]
            }
            if vote > bestVote {
                bestVote = vote
                bestLabel = label
            }
        }

        return bestLabel > 0 ? bestLabel : geometry.nearestLabels[index]
    }

    private static func umToPositivePixelRadius(_ valueUm: Double, scaleUmPerPx: Double) -> Int {
        max(1, Int(round(max(0, valueUm) / max(0.000_001, scaleUmPerPx))))
    }

    private static func cachedNearestLabelDistanceMap(
        labelMap: NucleiLabelMap
    ) -> (nearestLabels: [Int], distanceSquared: [Double]) {
        let key = labelMapCacheKey(labelMap)
        geometryCacheLock.lock()
        if let cached = nearestLabelDistanceCache[key] {
            geometryCacheLock.unlock()
            return cached
        }
        geometryCacheLock.unlock()

        let computed = nearestLabelDistanceMap(
            labels: labelMap.labels,
            width: labelMap.width,
            height: labelMap.height
        )

        geometryCacheLock.lock()
        if nearestLabelDistanceCache.count > 3 {
            nearestLabelDistanceCache.removeAll(keepingCapacity: true)
        }
        nearestLabelDistanceCache[key] = computed
        geometryCacheLock.unlock()
        return computed
    }

    private static func labelMapCacheKey(_ labelMap: NucleiLabelMap) -> String {
        var checksum = 0
        let stride = max(1, labelMap.labels.count / 4096)
        var index = 0
        while index < labelMap.labels.count {
            checksum = checksum &* 31 &+ labelMap.labels[index]
            index += stride
        }
        let maxLabel = labelMap.labels.max() ?? 0
        return "\(labelMap.width)x\(labelMap.height):\(labelMap.labels.count):\(maxLabel):\(checksum)"
    }

    private static func nearestLabelDistanceMap(
        labels: [Int],
        width: Int,
        height: Int
    ) -> (nearestLabels: [Int], distanceSquared: [Double]) {
        let count = width * height
        var nearestLabels = Array(repeating: 0, count: count)
        var sourceX = Array(repeating: -1, count: count)
        var sourceY = Array(repeating: -1, count: count)
        var distanceSquared = Array(repeating: Double.greatestFiniteMagnitude, count: count)

        for index in 0..<count where labels[index] > 0 {
            nearestLabels[index] = labels[index]
            sourceX[index] = index % width
            sourceY[index] = index / width
            distanceSquared[index] = 0
        }

        let forwardOffsets = [
            PixelOffset(dy: 0, dx: -1),
            PixelOffset(dy: -1, dx: 0),
            PixelOffset(dy: -1, dx: -1),
            PixelOffset(dy: -1, dx: 1)
        ]
        let reverseOffsets = [
            PixelOffset(dy: 0, dx: 1),
            PixelOffset(dy: 1, dx: 0),
            PixelOffset(dy: 1, dx: 1),
            PixelOffset(dy: 1, dx: -1)
        ]

        for _ in 0..<4 {
            for y in 0..<height {
                for x in 0..<width {
                    updateNearestSource(
                        x: x,
                        y: y,
                        offsets: forwardOffsets,
                        width: width,
                        height: height,
                        nearestLabels: &nearestLabels,
                        sourceX: &sourceX,
                        sourceY: &sourceY,
                        distanceSquared: &distanceSquared
                    )
                }
            }
            for y in stride(from: height - 1, through: 0, by: -1) {
                for x in stride(from: width - 1, through: 0, by: -1) {
                    updateNearestSource(
                        x: x,
                        y: y,
                        offsets: reverseOffsets,
                        width: width,
                        height: height,
                        nearestLabels: &nearestLabels,
                        sourceX: &sourceX,
                        sourceY: &sourceY,
                        distanceSquared: &distanceSquared
                    )
                }
            }
        }

        return (nearestLabels, distanceSquared)
    }

    private static func updateNearestSource(
        x: Int,
        y: Int,
        offsets: [PixelOffset],
        width: Int,
        height: Int,
        nearestLabels: inout [Int],
        sourceX: inout [Int],
        sourceY: inout [Int],
        distanceSquared: inout [Double]
    ) {
        let index = y * width + x
        for offset in offsets {
            let nx = x + offset.dx
            let ny = y + offset.dy
            guard nx >= 0, nx < width, ny >= 0, ny < height else { continue }
            let neighborIndex = ny * width + nx
            guard nearestLabels[neighborIndex] > 0,
                  sourceX[neighborIndex] >= 0,
                  sourceY[neighborIndex] >= 0 else { continue }
            let dx = x - sourceX[neighborIndex]
            let dy = y - sourceY[neighborIndex]
            let candidateDistance = Double(dx * dx + dy * dy)
            if candidateDistance < distanceSquared[index] {
                distanceSquared[index] = candidateDistance
                sourceX[index] = sourceX[neighborIndex]
                sourceY[index] = sourceY[neighborIndex]
                nearestLabels[index] = nearestLabels[neighborIndex]
            }
        }
    }

    private static func distanceSquaredToMask(_ mask: [Bool], width: Int, height: Int) -> [Double] {
        let labels = mask.map { $0 ? 1 : 0 }
        return nearestLabelDistanceMap(labels: labels, width: width, height: height).distanceSquared
    }

    private static func labelBoundaryMask(labels: [Int], width: Int, height: Int) -> [Bool] {
        var boundaries = Array(repeating: false, count: labels.count)
        for y in 0..<height {
            for x in 0..<width {
                let index = y * width + x
                let label = labels[index]
                guard label > 0 else { continue }
                for yy in max(0, y - 1)...min(height - 1, y + 1) {
                    for xx in max(0, x - 1)...min(width - 1, x + 1) {
                        guard xx != x || yy != y else { continue }
                        let neighborLabel = labels[yy * width + xx]
                        if neighborLabel != label {
                            boundaries[index] = true
                            break
                        }
                    }
                    if boundaries[index] {
                        break
                    }
                }
            }
        }
        return boundaries
    }

    private static func diskOffsets(radius: Int) -> [PixelOffset] {
        let safeRadius = max(0, radius)
        guard safeRadius > 0 else { return [PixelOffset(dy: 0, dx: 0)] }
        let squared = safeRadius * safeRadius
        var offsets: [PixelOffset] = []
        offsets.reserveCapacity(max(1, squared * 3))
        for dy in -safeRadius...safeRadius {
            for dx in -safeRadius...safeRadius where dx * dx + dy * dy <= squared {
                offsets.append(PixelOffset(dy: dy, dx: dx))
            }
        }
        return offsets
    }

    private static func makeSpatialLabelIndex(detections: [NucleiDetection], reachPx: Double) -> SpatialLabelIndex {
        let anchors = detections.map { detection in
            LabelAnchor(
                id: detection.id,
                x: detection.centroidX,
                y: detection.centroidY,
                radius: max(1.0, sqrt(Double(max(1, detection.areaPx)) / Double.pi))
            )
        }
        let maxRadius = anchors.map(\.radius).max() ?? 1.0
        let cellSize = max(8.0, min(96.0, max(16.0, reachPx + maxRadius)))
        let searchRadius = reachPx + maxRadius
        let searchCellRadius = max(1, Int(ceil(searchRadius / cellSize)) + 1)
        var cells: [Int64: [Int]] = [:]
        for (index, anchor) in anchors.enumerated() {
            let cellX = Int(floor(anchor.x / cellSize))
            let cellY = Int(floor(anchor.y / cellSize))
            cells[SpatialLabelIndex.key(x: cellX, y: cellY), default: []].append(index)
        }
        return SpatialLabelIndex(
            anchors: anchors,
            cells: cells,
            cellSize: cellSize,
            searchCellRadius: searchCellRadius,
            maxAnchorRadius: maxRadius
        )
    }

    private static func parameterizedPositivePixels(
        profile: MarkerProfile,
        parameters: AssignmentParameters,
        scaleUmPerPx: Double
    ) -> (values: [Double], indices: [Int]) {
        guard let matrix = profile.matrix else { return ([], []) }
        var values = profile.normalizedValues
        let tophatPx = Int(round(parameters.tophatRUm / max(0.000_001, scaleUmPerPx)))
        if tophatPx > 0 {
            let background = boxBlur(values, width: matrix.width, height: matrix.height, radius: min(tophatPx, 160))
            values = zip(values, background).map { max(0, $0 - $1) }
        }

        let sigmaPx = Int(round(parameters.gaussSigmaUm / max(0.000_001, scaleUmPerPx)))
        if sigmaPx > 0 {
            values = boxBlur(values, width: matrix.width, height: matrix.height, radius: min(max(1, sigmaPx), 32))
        }

        let threshold: Double
        switch parameters.threshMode {
        case "yen":
            threshold = yenThreshold(values: values)
        default:
            threshold = otsuThreshold(values: values)
        }

        var base: [Int] = []
        base.reserveCapacity(values.count / 8)
        for (index, value) in values.enumerated() where value > threshold {
            base.append(index)
        }
        let filtered = filterPositiveObjects(
            width: matrix.width,
            height: matrix.height,
            baseIndices: base,
            minObjectSize: parameters.minPosObjectSizePx
        )
        return (values, filtered)
    }

    private static func boxBlur(_ values: [Double], width: Int, height: Int, radius: Int) -> [Double] {
        guard radius > 0, values.count == width * height else { return values }
        let table = summedAreaTable(width: width, height: height, values: values)
        var output = Array(repeating: 0.0, count: values.count)
        for y in 0..<height {
            let minY = max(0, y - radius)
            let maxY = min(height - 1, y + radius)
            for x in 0..<width {
                let minX = max(0, x - radius)
                let maxX = min(width - 1, x + radius)
                let sum = sumRect(
                    table: table,
                    width: width,
                    minX: minX,
                    maxX: maxX,
                    minY: minY,
                    maxY: maxY
                )
                let count = max(1, (maxX - minX + 1) * (maxY - minY + 1))
                output[y * width + x] = sum / Double(count)
            }
        }
        return output
    }

    private static func filteredPositiveIndices(
        profile: MarkerProfile,
        mode: String,
        minObjectSize: Int
    ) -> [Int] {
        guard minObjectSize > 1,
              let matrix = profile.matrix,
              let baseIndices = profile.positiveIndicesByMode[mode] ?? profile.positiveIndicesByMode["global_otsu"],
              !baseIndices.isEmpty else {
            return profile.positiveIndicesByMode[mode] ?? profile.positiveIndicesByMode["global_otsu"] ?? []
        }
        return filterPositiveObjects(
            width: matrix.width,
            height: matrix.height,
            baseIndices: baseIndices,
            minObjectSize: minObjectSize
        )
    }

    private static func filterPositiveObjects(
        width: Int,
        height: Int,
        baseIndices: [Int],
        minObjectSize: Int
    ) -> [Int] {
        guard minObjectSize > 1, !baseIndices.isEmpty else { return baseIndices }
        var isPositive = Array(repeating: false, count: width * height)
        for index in baseIndices {
            isPositive[index] = true
        }

        var visited = Array(repeating: false, count: isPositive.count)
        var kept: [Int] = []
        kept.reserveCapacity(baseIndices.count)
        let neighbors = [(1, 0), (-1, 0), (0, 1), (0, -1)]

        for start in baseIndices where !visited[start] {
            visited[start] = true
            var stack = [start]
            var component: [Int] = []
            component.reserveCapacity(minObjectSize)

            while let index = stack.popLast() {
                component.append(index)
                let x = index % width
                let y = index / width
                for neighbor in neighbors {
                    let nx = x + neighbor.0
                    let ny = y + neighbor.1
                    guard nx >= 0, nx < width, ny >= 0, ny < height else { continue }
                    let next = ny * width + nx
                    guard isPositive[next], !visited[next] else { continue }
                    visited[next] = true
                    stack.append(next)
                }
            }

            if component.count >= minObjectSize {
                kept.append(contentsOf: component)
            }
        }

        return kept
    }

    private static func parallelAssignments(
        detections: [NucleiDetection],
        profiles: [MarkerProfile],
        parsedCellTypes: [ParsedCellType],
        parameters: AssignmentParameters,
        radiusPx: Int,
        workerCount: Int,
        labelEvidence: [Int: [String: MarkerEvidence]]?,
        cancellationToken: CancellationToken? = nil
    ) throws -> [CellTypeAssignment] {
        let workers = min(max(1, workerCount), max(1, detections.count))
        guard workers > 1 else {
            var output: [CellTypeAssignment] = []
            output.reserveCapacity(detections.count)
            for detection in detections {
                try cancellationToken?.checkCancellation()
                output.append(assign(
                    detection: detection,
                    profiles: profiles,
                    parsedCellTypes: parsedCellTypes,
                    parameters: parameters,
                    radiusPx: radiusPx,
                    labelEvidence: labelEvidence?[detection.id]
                ))
            }
            return output
        }

        let lock = NSLock()
        var parts: [(Int, [CellTypeAssignment])] = []
        var firstError: Error?
        parts.reserveCapacity(workers)
        DispatchQueue.concurrentPerform(iterations: workers) { workerIndex in
            let start = workerIndex * detections.count / workers
            let end = (workerIndex + 1) * detections.count / workers
            guard start < end else { return }
            var local: [CellTypeAssignment] = []
            var localError: Error?
            for detection in detections[start..<end] {
                do {
                    try cancellationToken?.checkCancellation()
                    local.append(assign(
                        detection: detection,
                        profiles: profiles,
                        parsedCellTypes: parsedCellTypes,
                        parameters: parameters,
                        radiusPx: radiusPx,
                        labelEvidence: labelEvidence?[detection.id]
                    ))
                } catch {
                    localError = error
                    break
                }
            }
            lock.lock()
            parts.append((workerIndex, local))
            if firstError == nil {
                firstError = localError
            }
            lock.unlock()
        }
        if let firstError {
            throw firstError
        }
        return parts.sorted { $0.0 < $1.0 }.flatMap(\.1)
    }

    private static func refinedAssignmentParameters(
        from seed: AssignmentParameters,
        axes: [AssignmentSearchAxis],
        localIndex: Int
    ) -> AssignmentParameters {
        var params = seed
        let axis = axes[localIndex % max(1, axes.count)]
        let direction = (localIndex / max(1, axes.count)) % 2 == 0 ? -1.0 : 1.0
        let scaleStep = Double(((localIndex / max(1, axes.count * 2)) % 3) + 1) / 3.0
        let span = axis.range.upperBound - axis.range.lowerBound
        let delta = span * 0.12 * scaleStep * direction
        let current = params[keyPath: axis.keyPath]
        params[keyPath: axis.keyPath] = min(max(current + delta, axis.range.lowerBound), axis.range.upperBound)

        let modes = ["global_otsu", "local", "yen"]
        params.threshMode = modes[localIndex % modes.count]
        params.resolveAmbiguous = localIndex % 6 != 5
        params.minPosObjectSizePx = max(0, min(50_000, seed.minPosObjectSizePx + ((localIndex % 5) - 2) * 35))
        params.minPosPix = max(0, min(50_000, seed.minPosPix + ((localIndex % 7) - 3) * 20))
        return params
    }

    private static func makeAssignmentScreeningInput(
        detections: [NucleiDetection],
        matrices: [CSVMatrix],
        labelMap: NucleiLabelMap?,
        pixelSize: (Double, Double)?,
        bandCount: Int,
        subsetMode: AssignmentScreeningSubsetMode,
        selectedBandsOverride: [Int]?
    ) -> AssignmentScreeningInput {
        guard let labelMap,
              labelMap.width > 0,
              labelMap.height > 0,
              labelMap.labels.count == labelMap.width * labelMap.height else {
            return AssignmentScreeningInput(
                detections: detections,
                matrices: matrices,
                labelMap: labelMap,
                pixelSize: pixelSize,
                factor: 1
            )
        }

        let safeBandCount = min(max(bandCount, 5), 6)
        let selectedBands = normalizedScreeningBandSelection(
            selectedBandsOverride,
            bandCount: safeBandCount
        ) ?? selectedAssignmentScreeningBands(
            bandCount: safeBandCount,
            mode: subsetMode
        )

        for factor in [4, 2, 1] {
            guard min(labelMap.width, labelMap.height) / factor >= 64 else { continue }
            guard let subset = verticalBandAssignmentScreeningInput(
                detections: detections,
                matrices: matrices,
                labelMap: labelMap,
                pixelSize: pixelSize,
                bandCount: safeBandCount,
                selectedBands: selectedBands,
                factor: factor
            ) else { continue }
            guard subset.detections.count >= min(50, detections.count) else { continue }
            return subset
        }

        return AssignmentScreeningInput(
            detections: detections,
            matrices: matrices,
            labelMap: labelMap,
            pixelSize: pixelSize,
            factor: 1
        )
    }

    private static func selectedAssignmentScreeningBands(
        bandCount: Int,
        mode: AssignmentScreeningSubsetMode
    ) -> [Int] {
        let allBands = Array(0..<max(1, bandCount))
        switch mode {
        case .randomThree:
            return Array(allBands.shuffled().prefix(min(3, allBands.count))).sorted()
        case .oddBands:
            let selected = allBands.filter { $0 % 2 == 0 }
            return selected.isEmpty ? [0] : selected
        case .evenBands:
            let selected = allBands.filter { $0 % 2 == 1 }
            return selected.isEmpty ? [0] : selected
        }
    }

    private static func normalizedScreeningBandSelection(_ values: [Int]?, bandCount: Int) -> [Int]? {
        guard let values else { return nil }
        let normalized = Array(Set(values.filter { $0 >= 0 && $0 < bandCount })).sorted()
        return normalized.isEmpty ? nil : normalized
    }

    private static func verticalBandAssignmentScreeningInput(
        detections: [NucleiDetection],
        matrices: [CSVMatrix],
        labelMap: NucleiLabelMap,
        pixelSize: (Double, Double)?,
        bandCount: Int,
        selectedBands: [Int],
        factor: Int
    ) -> AssignmentScreeningInput? {
        let bounds = verticalBandBounds(width: labelMap.width, bandCount: bandCount)
        let selectedBounds = selectedBands.compactMap { index -> (Int, Int)? in
            guard index >= 0, index < bounds.count else { return nil }
            return bounds[index]
        }
        guard !selectedBounds.isEmpty else { return nil }

        let safeFactor = max(1, factor)
        let outputHeight = (labelMap.height + safeFactor - 1) / safeFactor
        let bandOutputWidths = selectedBounds.map { bounds in
            max(0, (bounds.1 - bounds.0 + safeFactor - 1) / safeFactor)
        }
        let outputWidth = bandOutputWidths.reduce(0, +)
        guard outputWidth > 0, outputHeight > 0 else { return nil }

        var labels: [Int] = []
        labels.reserveCapacity(outputWidth * outputHeight)
        var labelStats: [Int: (sumX: Double, sumY: Double, count: Int)] = [:]

        var outputY = 0
        var sourceY = 0
        while sourceY < labelMap.height {
            var outputX = 0
            for bounds in selectedBounds {
                var sourceX = bounds.0
                while sourceX < bounds.1 {
                    let label = labelMap.labels[sourceY * labelMap.width + sourceX]
                    labels.append(label)
                    if label > 0 {
                        let existing = labelStats[label] ?? (0, 0, 0)
                        labelStats[label] = (
                            existing.sumX + Double(outputX),
                            existing.sumY + Double(outputY),
                            existing.count + 1
                        )
                    }
                    sourceX += safeFactor
                    outputX += 1
                }
            }
            sourceY += safeFactor
            outputY += 1
        }
        guard labels.count == outputWidth * outputHeight else { return nil }

        let meanIntensityByID = Dictionary(uniqueKeysWithValues: detections.map { ($0.id, $0.meanIntensity) })
        let subsetDetections = labelStats
            .compactMap { label, stats -> NucleiDetection? in
                guard let meanIntensity = meanIntensityByID[label], stats.count > 0 else { return nil }
                return NucleiDetection(
                    id: label,
                    centroidX: stats.sumX / Double(stats.count),
                    centroidY: stats.sumY / Double(stats.count),
                    areaPx: max(1, stats.count),
                    meanIntensity: meanIntensity
                )
            }
            .sorted { $0.id < $1.id }
        guard !subsetDetections.isEmpty else { return nil }

        let subsetMatrices = matrices.map { matrix in
            verticalBandMatrixSubset(
                matrix,
                selectedBounds: selectedBounds,
                outputWidth: outputWidth,
                outputHeight: outputHeight,
                factor: safeFactor
            )
        }
        let basePixelSize = pixelSize ?? (1.0, 1.0)
        let subsetPixelSize = (
            basePixelSize.0 * Double(safeFactor),
            basePixelSize.1 * Double(safeFactor)
        )
        return AssignmentScreeningInput(
            detections: subsetDetections,
            matrices: subsetMatrices,
            labelMap: NucleiLabelMap(width: outputWidth, height: outputHeight, labels: labels),
            pixelSize: subsetPixelSize,
            factor: safeFactor
        )
    }

    private static func verticalBandBounds(width: Int, bandCount: Int) -> [(Int, Int)] {
        let safeCount = max(1, bandCount)
        return (0..<safeCount).map { index in
            let start = Int(round(Double(index) * Double(width) / Double(safeCount)))
            let end = Int(round(Double(index + 1) * Double(width) / Double(safeCount)))
            return (max(0, min(width, start)), max(0, min(width, end)))
        }
    }

    private static func verticalBandMatrixSubset(
        _ matrix: CSVMatrix,
        selectedBounds: [(Int, Int)],
        outputWidth: Int,
        outputHeight: Int,
        factor: Int
    ) -> CSVMatrix {
        var values: [Double] = []
        values.reserveCapacity(outputWidth * outputHeight)
        var sourceY = 0
        while sourceY < matrix.height {
            for bounds in selectedBounds {
                var sourceX = bounds.0
                while sourceX < min(bounds.1, matrix.width) {
                    values.append(matrix[sourceX, sourceY])
                    sourceX += factor
                }
            }
            sourceY += factor
        }
        if values.count < outputWidth * outputHeight {
            values.append(contentsOf: Array(repeating: 0, count: outputWidth * outputHeight - values.count))
        } else if values.count > outputWidth * outputHeight {
            values.removeLast(values.count - outputWidth * outputHeight)
        }
        return CSVMatrix(
            channelName: matrix.channelName,
            fileName: matrix.fileName,
            width: outputWidth,
            height: outputHeight,
            values: values
        )
    }

    private static func deterministicScreeningSample(
        _ detections: [NucleiDetection],
        maxCount: Int
    ) -> [NucleiDetection] {
        guard detections.count > maxCount else { return detections }
        let step = Double(detections.count) / Double(maxCount)
        return (0..<maxCount).map { index in
            let sourceIndex = min(detections.count - 1, Int((Double(index) + 0.5) * step))
            return detections[sourceIndex]
        }
    }

    private static func assign(
        detection: NucleiDetection,
        profiles: [MarkerProfile],
        parsedCellTypes: [ParsedCellType],
        parameters: AssignmentParameters,
        radiusPx: Int,
        labelEvidence: [String: MarkerEvidence]?
    ) -> CellTypeAssignment {
        var markerMeans: [String: Double] = [:]
        var positiveMarkers = Set<String>()
        var canonicalToDisplay: [String: String] = [:]
        var evidenceByMarker: [String: MarkerEvidence] = [:]

        for profile in profiles {
            let displayName = profile.displayName
            let key = canonicalMarker(displayName)
            if let evidence = labelEvidence?[key] {
                markerMeans[displayName] = Double(evidence.positivePixels)
                canonicalToDisplay[key] = displayName
                evidenceByMarker[key] = evidence
                if evidence.isPositive {
                    positiveMarkers.insert(key)
                }
            } else if profile.matrix != nil {
                let stats = localPositiveStats(
                    profile: profile,
                    x: detection.centroidX,
                    y: detection.centroidY,
                    radiusPx: radiusPx,
                    thresholdMode: parameters.threshMode
                )
                let isPositive = parameters.minPosPix <= 0
                    ? stats.positivePixels > 0
                    : stats.positivePixels >= parameters.minPosPix
                markerMeans[displayName] = Double(stats.positivePixels)
                canonicalToDisplay[key] = displayName
                evidenceByMarker[key] = MarkerEvidence(
                    positivePixels: stats.positivePixels,
                    summedIntensity: stats.summedIntensity,
                    isPositive: isPositive
                )
                if isPositive {
                    positiveMarkers.insert(key)
                }
            } else {
                let mean = profile.generatedValuesByDetectionID[detection.id] ?? detection.meanIntensity
                markerMeans[displayName] = mean
                canonicalToDisplay[key] = displayName
                let isPositive = mean >= profile.positiveThreshold
                evidenceByMarker[key] = MarkerEvidence(
                    positivePixels: max(1, detection.areaPx),
                    summedIntensity: 1.0,
                    isPositive: isPositive
                )
                if isPositive {
                    positiveMarkers.insert(key)
                }
            }
        }

        var candidates: [(cellType: ParsedCellType, rawScore: Double, matched: [String], blocked: [String])] = []
        for cellType in parsedCellTypes {
            let positives = cellType.positives
            let negatives = cellType.negatives
            let groups = cellType.anyGroups
            let positiveMatches = positives.filter { positiveMarkers.contains($0) }
            let missingPositives = positives.count - positiveMatches.count
            let groupMatches = groups.filter { group in group.contains { positiveMarkers.contains($0) } }
            let missingGroups = groups.count - groupMatches.count
            let blocked = negatives.filter { positiveMarkers.contains($0) }
            let requiredCount = max(1, positives.count + groups.count)
            let rawScore = Double(positiveMatches.count + groupMatches.count) / Double(requiredCount) - Double(blocked.count)

            if missingPositives == 0, missingGroups == 0, blocked.isEmpty, rawScore > 0 {
                let matched = positiveMatches + groupMatches.flatMap { group in group.filter { positiveMarkers.contains($0) } }
                let probabilityScore = cellTypeProbabilityScore(
                    cellType,
                    evidenceByMarker: evidenceByMarker,
                    parameters: parameters
                )
                candidates.append((cellType, max(0.000_001, probabilityScore), Array(Set(matched)).sorted(), blocked))
            }
        }

        let sorted = candidates.sorted {
            if $0.rawScore == $1.rawScore {
                return $0.cellType.definition.name.localizedStandardCompare($1.cellType.definition.name) == .orderedAscending
            }
            return $0.rawScore > $1.rawScore
        }

        guard !sorted.isEmpty else {
            return CellTypeAssignment(
                nucleusID: detection.id,
                centroidX: detection.centroidX,
                centroidY: detection.centroidY,
                areaPx: detection.areaPx,
                assignedType: "Unassigned",
                colorHex: "#777777",
                score: 0,
                probability: 0,
                matchedPositiveMarkers: [],
                blockedNegativeMarkers: [],
                markerMeans: markerMeans
            )
        }

        let positiveScoreSum = max(0.000_001, sorted.reduce(0) { $0 + max(0.000_001, $1.rawScore) })
        let probabilities = sorted.map { candidate in
            (
                candidate: candidate,
                probability: max(0.000_001, candidate.rawScore) / positiveScoreSum
            )
        }
        let best = probabilities[0]
        let runnerUpProbability = probabilities.dropFirst().first?.probability ?? 0
        let gap = max(0, best.probability - runnerUpProbability)
        if sorted.count == 1 {
            return CellTypeAssignment(
                nucleusID: detection.id,
                centroidX: detection.centroidX,
                centroidY: detection.centroidY,
                areaPx: detection.areaPx,
                assignedType: best.candidate.cellType.definition.name,
                colorHex: best.candidate.cellType.definition.colorHex,
                score: best.candidate.rawScore,
                probability: 1,
                matchedPositiveMarkers: best.candidate.matched.compactMap { canonicalToDisplay[$0] ?? $0 },
                blockedNegativeMarkers: best.candidate.blocked.compactMap { canonicalToDisplay[$0] ?? $0 },
                markerMeans: markerMeans
            )
        }

        let shouldResolveAmbiguous = parameters.resolveAmbiguous
            && best.probability >= parameters.ambiguousMinProbability
            && gap >= parameters.ambiguousMinGap
        if !shouldResolveAmbiguous {
            return CellTypeAssignment(
                nucleusID: detection.id,
                centroidX: detection.centroidX,
                centroidY: detection.centroidY,
                areaPx: detection.areaPx,
                assignedType: "Ambiguous",
                colorHex: "#bbbbbb",
                score: best.candidate.rawScore,
                probability: best.probability,
                matchedPositiveMarkers: best.candidate.matched.compactMap { canonicalToDisplay[$0] ?? $0 },
                blockedNegativeMarkers: best.candidate.blocked.compactMap { canonicalToDisplay[$0] ?? $0 },
                markerMeans: markerMeans
            )
        }

        return CellTypeAssignment(
            nucleusID: detection.id,
            centroidX: detection.centroidX,
            centroidY: detection.centroidY,
            areaPx: detection.areaPx,
            assignedType: best.candidate.cellType.definition.name,
            colorHex: best.candidate.cellType.definition.colorHex,
            score: best.candidate.rawScore,
            probability: best.probability,
            matchedPositiveMarkers: best.candidate.matched.compactMap { canonicalToDisplay[$0] ?? $0 },
            blockedNegativeMarkers: best.candidate.blocked.compactMap { canonicalToDisplay[$0] ?? $0 },
            markerMeans: markerMeans
        )
    }

    private static func cellTypeProbabilityScore(
        _ cellType: ParsedCellType,
        evidenceByMarker: [String: MarkerEvidence],
        parameters: AssignmentParameters
    ) -> Double {
        var terms: [Double] = []
        for marker in cellType.positives {
            terms.append(markerProbability(marker, evidenceByMarker: evidenceByMarker, parameters: parameters))
        }
        for marker in cellType.negatives {
            terms.append(1.0 - markerProbability(marker, evidenceByMarker: evidenceByMarker, parameters: parameters))
        }
        for group in cellType.anyGroups where !group.isEmpty {
            let strongest = group
                .map { markerProbability($0, evidenceByMarker: evidenceByMarker, parameters: parameters) }
                .max() ?? 0
            terms.append(strongest)
        }
        guard !terms.isEmpty else { return 0.5 }
        let clipped = terms.map { min(max($0, 0.000_001), 1.0) }
        return exp(clipped.map(log).reduce(0, +) / Double(clipped.count))
    }

    private static func markerProbability(
        _ marker: String,
        evidenceByMarker: [String: MarkerEvidence],
        parameters: AssignmentParameters
    ) -> Double {
        let key = canonicalMarker(marker)
        if key == "nucleus" {
            return (evidenceByMarker[key]?.isPositive ?? false) ? 1.0 : 0.0
        }
        guard let evidence = evidenceByMarker[key] else { return 0 }
        let scalePix = max(1.0, Double(max(parameters.minPosPix, 1)))
        let pixProb = 1.0 - exp(-max(0.0, Double(evidence.positivePixels)) / scalePix)
        let intensityProb = 1.0 - exp(-max(0.0, evidence.summedIntensity) / max(1.0, scalePix / 2.0))
        return min(max((0.65 * pixProb) + (0.35 * intensityProb), 0), 1)
    }

    private static func localPositiveStats(
        profile: MarkerProfile,
        x: Double,
        y: Double,
        radiusPx: Int,
        thresholdMode: String
    ) -> (positivePixels: Int, summedIntensity: Double) {
        guard let matrix = profile.matrix else { return (0, 0) }
        let mode = profile.positiveIntegralByMode[thresholdMode] == nil ? "global_otsu" : thresholdMode
        guard let positiveIntegral = profile.positiveIntegralByMode[mode],
              let signalIntegral = profile.positiveSignalIntegralByMode[mode],
              positiveIntegral.count == (matrix.width + 1) * (matrix.height + 1),
              signalIntegral.count == (matrix.width + 1) * (matrix.height + 1) else {
            return (0, 0)
        }

        let centerX = min(max(Int(round(x)), 0), matrix.width - 1)
        let centerY = min(max(Int(round(y)), 0), matrix.height - 1)
        let minX = max(0, centerX - radiusPx)
        let maxX = min(matrix.width - 1, centerX + radiusPx)
        let minY = max(0, centerY - radiusPx)
        let maxY = min(matrix.height - 1, centerY + radiusPx)
        let positivePixels = Int(round(sumRect(
            table: positiveIntegral,
            width: matrix.width,
            minX: minX,
            maxX: maxX,
            minY: minY,
            maxY: maxY
        )))
        let summedIntensity = sumRect(
            table: signalIntegral,
            width: matrix.width,
            minX: minX,
            maxX: maxX,
            minY: minY,
            maxY: maxY
        )
        return (positivePixels, summedIntensity)
    }

    private static func localMean(profile: MarkerProfile, x: Double, y: Double, radiusPx: Int) -> Double {
        guard let matrix = profile.matrix else { return 0 }
        let centerX = min(max(Int(round(x)), 0), matrix.width - 1)
        let centerY = min(max(Int(round(y)), 0), matrix.height - 1)
        let minX = max(0, centerX - radiusPx)
        let maxX = min(matrix.width - 1, centerX + radiusPx)
        let minY = max(0, centerY - radiusPx)
        let maxY = min(matrix.height - 1, centerY + radiusPx)
        if profile.integralImage.count == (matrix.width + 1) * (matrix.height + 1) {
            let stride = matrix.width + 1
            let x0 = minX
            let y0 = minY
            let x1 = maxX + 1
            let y1 = maxY + 1
            let sum = profile.integralImage[y1 * stride + x1]
                - profile.integralImage[y0 * stride + x1]
                - profile.integralImage[y1 * stride + x0]
                + profile.integralImage[y0 * stride + x0]
            let count = max(1, (maxX - minX + 1) * (maxY - minY + 1))
            return sum / Double(count)
        }

        let radiusSquared = radiusPx * radiusPx
        var sum = 0.0
        var count = 0

        for yy in minY...maxY {
            for xx in minX...maxX {
                let dx = xx - centerX
                let dy = yy - centerY
                guard (dx * dx) + (dy * dy) <= radiusSquared else { continue }
                sum += matrix[xx, yy]
                count += 1
            }
        }

        return count > 0 ? sum / Double(count) : matrix[centerX, centerY]
    }

    private static func normalizedValues(for matrix: CSVMatrix) -> [Double] {
        let low = matrix.percentile(1)
        let high = matrix.percentile(99.8)
        let range = max(0.000_001, high - low)
        return matrix.values.map { value in
            guard value.isFinite else { return 0 }
            return min(max((value - low) / range, 0), 1)
        }
    }

    private static func markerThresholds(values: [Double]) -> [String: Double] {
        let otsu = otsuThreshold(values: values)
        let yen = yenThreshold(values: values)
        return [
            "global_otsu": otsu,
            "local": otsu,
            "yen": yen
        ]
    }

    private static func otsuThreshold(values: [Double], binCount: Int = 256) -> Double {
        let histogram = histogram(values: values, binCount: binCount)
        let total = histogram.reduce(0, +)
        guard total > 0 else { return 0.5 }

        var sumTotal = 0.0
        for index in 0..<binCount {
            sumTotal += Double(index) * Double(histogram[index])
        }

        var weightBackground = 0
        var sumBackground = 0.0
        var bestVariance = -Double.infinity
        var bestIndex = 0

        for index in 0..<binCount {
            weightBackground += histogram[index]
            guard weightBackground > 0 else { continue }
            let weightForeground = total - weightBackground
            guard weightForeground > 0 else { break }
            sumBackground += Double(index) * Double(histogram[index])
            let meanBackground = sumBackground / Double(weightBackground)
            let meanForeground = (sumTotal - sumBackground) / Double(weightForeground)
            let variance = Double(weightBackground) * Double(weightForeground) * pow(meanBackground - meanForeground, 2)
            if variance > bestVariance {
                bestVariance = variance
                bestIndex = index
            }
        }

        return min(max((Double(bestIndex) + 0.5) / Double(binCount), 0.02), 0.98)
    }

    private static func yenThreshold(values: [Double], binCount: Int = 256) -> Double {
        let counts = histogram(values: values, binCount: binCount)
        let total = max(1, counts.reduce(0, +))
        let pmf = counts.map { Double($0) / Double(total) }
        var p1 = Array(repeating: 0.0, count: binCount)
        var p1Squared = Array(repeating: 0.0, count: binCount)
        var p2Squared = Array(repeating: 0.0, count: binCount)

        for index in 0..<binCount {
            p1[index] = (index == 0 ? 0 : p1[index - 1]) + pmf[index]
            p1Squared[index] = (index == 0 ? 0 : p1Squared[index - 1]) + pmf[index] * pmf[index]
        }
        for index in stride(from: binCount - 1, through: 0, by: -1) {
            p2Squared[index] = (index == binCount - 1 ? 0 : p2Squared[index + 1]) + pmf[index] * pmf[index]
        }

        var bestIndex = Int(Double(binCount) * 0.92)
        var bestCriterion = -Double.infinity
        for index in 0..<(binCount - 1) {
            let left = max(p1Squared[index], 1e-12)
            let right = max(p2Squared[index + 1], 1e-12)
            let balance = max(p1[index] * (1.0 - p1[index]), 1e-12)
            let criterion = -log(left * right) + 2.0 * log(balance)
            if criterion > bestCriterion {
                bestCriterion = criterion
                bestIndex = index
            }
        }
        return min(max((Double(bestIndex) + 0.5) / Double(binCount), 0.02), 0.98)
    }

    private static func histogram(values: [Double], binCount: Int) -> [Int] {
        var counts = Array(repeating: 0, count: binCount)
        for value in values where value.isFinite {
            let clamped = min(max(value, 0), 1)
            let index = min(binCount - 1, max(0, Int(clamped * Double(binCount - 1))))
            counts[index] += 1
        }
        return counts
    }

    private static func summedAreaTable(_ matrix: CSVMatrix) -> [Double] {
        let stride = matrix.width + 1
        var table = Array(repeating: 0.0, count: stride * (matrix.height + 1))
        for y in 0..<matrix.height {
            var rowSum = 0.0
            for x in 0..<matrix.width {
                rowSum += matrix[x, y]
                table[(y + 1) * stride + (x + 1)] = table[y * stride + (x + 1)] + rowSum
            }
        }
        return table
    }

    private static func summedAreaTable(width: Int, height: Int, values: [Double]) -> [Double] {
        let stride = width + 1
        var table = Array(repeating: 0.0, count: stride * (height + 1))
        guard values.count == width * height else { return table }
        for y in 0..<height {
            var rowSum = 0.0
            for x in 0..<width {
                rowSum += values[(y * width) + x]
                table[(y + 1) * stride + (x + 1)] = table[y * stride + (x + 1)] + rowSum
            }
        }
        return table
    }

    private static func sumRect(
        table: [Double],
        width: Int,
        minX: Int,
        maxX: Int,
        minY: Int,
        maxY: Int
    ) -> Double {
        let stride = width + 1
        let x0 = minX
        let y0 = minY
        let x1 = maxX + 1
        let y1 = maxY + 1
        return table[y1 * stride + x1]
            - table[y0 * stride + x1]
            - table[y1 * stride + x0]
            + table[y0 * stride + x0]
    }

    private static func makeCounts(assignments: [CellTypeAssignment], cellTypes: [CellTypeDefinition]) -> [CellTypeCount] {
        var colorByType = Dictionary(uniqueKeysWithValues: cellTypes.map { ($0.name, $0.colorHex) })
        colorByType["Ambiguous"] = "#bbbbbb"
        colorByType["Unassigned"] = "#777777"
        let grouped = Dictionary(grouping: assignments, by: \.assignedType).mapValues(\.count)
        let orderedNames = cellTypes.map(\.name) + ["Ambiguous", "Unassigned"]
        return orderedNames.compactMap { name in
            guard let count = grouped[name], count > 0 else { return nil }
            return CellTypeCount(name: name, count: count, colorHex: colorByType[name] ?? "#777777")
        }
    }

    private static func renderAssignmentMap(
        assignments: [CellTypeAssignment],
        labelMap: NucleiLabelMap?,
        profiles: [MarkerProfile],
        parameters: AssignmentParameters,
        scaleUmPerPx: Double,
        pixelSize: (Double, Double)?,
        width: Int,
        height: Int
    ) throws -> NSImage {
        if let labelMap,
           labelMap.width == width,
           labelMap.height == height,
           labelMap.labels.count == width * height {
            return try renderLabelBasedAssignmentMap(
                assignments: assignments,
                labelMap: labelMap,
                profiles: profiles,
                parameters: parameters,
                scaleUmPerPx: scaleUmPerPx,
                width: width,
                height: height
            )
        }

        let image = NSImage(size: NSSize(width: width, height: height))
        image.lockFocus()
        NSColor.black.setFill()
        NSRect(x: 0, y: 0, width: width, height: height).fill()
        let scaleUmPerPx = sqrt(max(0.000_001, (pixelSize?.0 ?? 1.0) * (pixelSize?.1 ?? 1.0)))
        let boundaryExtensionPx = max(1.0, min(18.0, 1.5 / scaleUmPerPx))
        for assignment in assignments {
            let isUnresolved = assignment.assignedType == "Unassigned" || assignment.assignedType == "Ambiguous"
            guard !isUnresolved else { continue }
            let color = NSColor(hex: assignment.colorHex) ?? .systemGray
            let radius = max(2.0, sqrt(Double(max(1, assignment.areaPx)) / Double.pi))
            let center = NSPoint(x: assignment.centroidX, y: Double(height) - assignment.centroidY)
            let signalRegionPath = pathFromBoundaryPoints(assignment.cellBoundaryPoints, height: height)
                ?? maskLikePath(
                    center: center,
                    radius: radius + boundaryExtensionPx,
                    seed: assignment.nucleusID + 7_919
                )
            color.withAlphaComponent(0.90).setFill()
            signalRegionPath.fill()
        }

        image.unlockFocus()
        return image
    }

    private static func renderLabelBasedAssignmentMap(
        assignments: [CellTypeAssignment],
        labelMap: NucleiLabelMap,
        profiles: [MarkerProfile],
        parameters: AssignmentParameters,
        scaleUmPerPx: Double,
        width: Int,
        height: Int
    ) throws -> NSImage {
        var rgba = [UInt8](repeating: 0, count: width * height * 4)
        for pixel in 0..<(width * height) {
            rgba[pixel * 4 + 3] = 255
        }

        let resolvedAssignments = assignments.filter { assignment in
            assignment.assignedType != "Unassigned" && assignment.assignedType != "Ambiguous"
        }
        let colorByID: [Int: (UInt8, UInt8, UInt8)] = Dictionary(uniqueKeysWithValues: resolvedAssignments.map { assignment in
            let color = NSColor(hex: assignment.colorHex) ?? .systemGray
            let comps = color.rgbComponents01
            return (
                assignment.nucleusID,
                (UInt8(comps.0 * 255), UInt8(comps.1 * 255), UInt8(comps.2 * 255))
            )
        })

        for index in labelMap.labels.indices {
            let labelID = labelMap.labels[index]
            guard let color = colorByID[labelID] else { continue }
            setPixel(index, color: color, rgba: &rgba)
        }

        let supportKeysByLabel = Dictionary(uniqueKeysWithValues: resolvedAssignments.map { assignment in
            (
                assignment.nucleusID,
                Set(assignment.matchedPositiveMarkers.map(canonicalMarker).filter { $0 != "nucleus" })
            )
        })
        let neededMarkerKeys = Set(supportKeysByLabel.values.flatMap { $0 })
        if !neededMarkerKeys.isEmpty {
            let geometry = makeAssignmentGeometry(
                labelMap: labelMap,
                parameters: parameters,
                scaleUmPerPx: scaleUmPerPx
            )
            let paintRadius = max(0, min(3, Int(round((parameters.rBufferUm / max(0.000_001, scaleUmPerPx)) / 2.0))))

            for profile in profiles where neededMarkerKeys.contains(canonicalMarker(profile.displayName)) {
                guard let matrix = profile.matrix,
                      matrix.width == width,
                      matrix.height == height else { continue }
                let markerKey = canonicalMarker(profile.displayName)
                let markerPixels = parameterizedPositivePixels(
                    profile: profile,
                    parameters: parameters,
                    scaleUmPerPx: scaleUmPerPx
                )
                for index in markerPixels.indices {
                    let labelID = markerAssignmentLabel(
                        index: index,
                        processedValues: markerPixels.values,
                        geometry: geometry
                    )
                    guard labelID > 0,
                          supportKeysByLabel[labelID]?.contains(markerKey) == true,
                          let color = colorByID[labelID] else { continue }
                    paintPixelDisk(centerIndex: index, radius: paintRadius, width: width, height: height, color: color, rgba: &rgba)
                }
            }
        }

        return try ImageExportService.nsImage(width: width, height: height, rgba: rgba)
    }

    private static func setPixel(_ index: Int, color: (UInt8, UInt8, UInt8), rgba: inout [UInt8]) {
        let offset = index * 4
        guard offset + 3 < rgba.count else { return }
        rgba[offset] = color.0
        rgba[offset + 1] = color.1
        rgba[offset + 2] = color.2
        rgba[offset + 3] = 255
    }

    private static func paintPixelDisk(
        centerIndex: Int,
        radius: Int,
        width: Int,
        height: Int,
        color: (UInt8, UInt8, UInt8),
        rgba: inout [UInt8]
    ) {
        guard radius > 0 else {
            setPixel(centerIndex, color: color, rgba: &rgba)
            return
        }
        let centerX = centerIndex % width
        let centerY = centerIndex / width
        let radiusSquared = radius * radius
        for y in max(0, centerY - radius)...min(height - 1, centerY + radius) {
            for x in max(0, centerX - radius)...min(width - 1, centerX + radius) {
                let dx = x - centerX
                let dy = y - centerY
                guard dx * dx + dy * dy <= radiusSquared else { continue }
                setPixel(y * width + x, color: color, rgba: &rgba)
            }
        }
    }

    private static func assignmentsWithMarkerInformedBoundaries(
        _ assignments: [CellTypeAssignment],
        profiles: [MarkerProfile],
        parameters: AssignmentParameters,
        pixelSize: (Double, Double)?,
        width: Int,
        height: Int,
        cancellationToken: CancellationToken?
    ) throws -> [CellTypeAssignment] {
        try assignments.enumerated().map { index, assignment in
            if index % 256 == 0 {
                try cancellationToken?.checkCancellation()
            }
            var copy = assignment
            copy.cellBoundaryPoints = markerInformedBoundaryPoints(
                for: assignment,
                profiles: profiles,
                parameters: parameters,
                pixelSize: pixelSize,
                width: width,
                height: height
            )
            return copy
        }
    }

    private static func makeCellTypeMask(
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

    private static func markerInformedBoundaryPoints(
        for assignment: CellTypeAssignment,
        profiles: [MarkerProfile],
        parameters: AssignmentParameters,
        pixelSize: (Double, Double)?,
        width: Int,
        height: Int
    ) -> [CellBoundaryPoint] {
        let pointCount = 40
        let scaleUmPerPx = sqrt(max(0.000_001, (pixelSize?.0 ?? 1.0) * (pixelSize?.1 ?? 1.0)))
        let nucleusRadius = max(2.0, sqrt(Double(max(1, assignment.areaPx)) / Double.pi))
        let minimumExtension = max(1.0, min(14.0, 1.5 / scaleUmPerPx))
        let parameterReachPx = max(
            parameters.rBufferUm,
            parameters.rVoteUm,
            parameters.rVoronoiUm * 0.45
        ) / scaleUmPerPx
        let extraReach = min(140.0, max(minimumExtension + 2.0, parameterReachPx))
        let maxRadius = min(nucleusRadius + extraReach, max(nucleusRadius * 5.0, nucleusRadius + 20.0))
        let fallbackRadius = min(maxRadius, nucleusRadius + minimumExtension)
        let supportProfiles = markerSupportProfiles(for: assignment, profiles: profiles)
        guard !supportProfiles.isEmpty else {
            return circularBoundaryPoints(
                centerX: assignment.centroidX,
                centerY: assignment.centroidY,
                radius: fallbackRadius,
                pointCount: pointCount,
                width: width,
                height: height
            )
        }

        var radii: [Double] = []
        radii.reserveCapacity(pointCount)
        for index in 0..<pointCount {
            let angle = (Double(index) / Double(pointCount)) * Double.pi * 2.0
            let directionX = cos(angle)
            let directionY = sin(angle)
            var furthestSupportedRadius: Double?
            var strongestRadius = fallbackRadius
            var strongestScore = 0.0
            var r = nucleusRadius
            while r <= maxRadius {
                let x = assignment.centroidX + directionX * r
                let y = assignment.centroidY + directionY * r
                let score = markerSupportScore(x: x, y: y, profiles: supportProfiles)
                if score > strongestScore {
                    strongestScore = score
                    strongestRadius = r
                }
                if score >= 0.72 {
                    furthestSupportedRadius = r
                }
                r += 1.5
            }
            let markerRadius = furthestSupportedRadius.map { $0 + 1.0 }
                ?? (strongestScore >= 0.48 ? strongestRadius : fallbackRadius)
            radii.append(min(max(markerRadius, fallbackRadius), maxRadius))
        }

        for _ in 0..<3 {
            radii = radii.indices.map { index in
                let previous = radii[(index - 1 + radii.count) % radii.count]
                let current = radii[index]
                let next = radii[(index + 1) % radii.count]
                return min(maxRadius, max(fallbackRadius, (previous + current * 2.0 + next) / 4.0))
            }
        }

        return radii.enumerated().map { index, radius in
            let angle = (Double(index) / Double(pointCount)) * Double.pi * 2.0
            return CellBoundaryPoint(
                x: min(max(assignment.centroidX + cos(angle) * radius, 0), Double(max(0, width - 1))),
                y: min(max(assignment.centroidY + sin(angle) * radius, 0), Double(max(0, height - 1)))
            )
        }
    }

    private static func markerSupportProfiles(
        for assignment: CellTypeAssignment,
        profiles: [MarkerProfile]
    ) -> [MarkerProfile] {
        let matrixProfiles = profiles.filter { $0.matrix != nil && canonicalMarker($0.displayName) != "nucleus" }
        let matchedKeys = Set(assignment.matchedPositiveMarkers.map(canonicalMarker).filter { $0 != "nucleus" })
        let matchedProfiles = matrixProfiles.filter { matchedKeys.contains(canonicalMarker($0.displayName)) }
        if !matchedProfiles.isEmpty {
            return matchedProfiles
        }

        let ranked = matrixProfiles.sorted { lhs, rhs in
            let lhsMean = assignment.markerMeans[lhs.displayName] ?? 0
            let rhsMean = assignment.markerMeans[rhs.displayName] ?? 0
            return lhsMean / max(lhs.positiveThreshold, 0.000_001) > rhsMean / max(rhs.positiveThreshold, 0.000_001)
        }
        return ranked.prefix(2).filter { profile in
            let mean = assignment.markerMeans[profile.displayName] ?? 0
            return mean >= profile.positiveThreshold * 0.55
        }
    }

    private static func markerSupportScore(
        x: Double,
        y: Double,
        profiles: [MarkerProfile]
    ) -> Double {
        profiles.reduce(0.0) { best, profile in
            guard profile.matrix != nil else { return best }
            let local = localMaxNormalized(profile: profile, x: x, y: y, radiusPx: 1)
            let threshold = max(profile.positiveThreshold, 0.000_001)
            return max(best, local / threshold)
        }
    }

    private static func localMaxNormalized(profile: MarkerProfile, x: Double, y: Double, radiusPx: Int) -> Double {
        guard let matrix = profile.matrix,
              profile.normalizedValues.count == matrix.width * matrix.height else { return 0 }
        let centerX = min(max(Int(round(x)), 0), matrix.width - 1)
        let centerY = min(max(Int(round(y)), 0), matrix.height - 1)
        let minX = max(0, centerX - radiusPx)
        let maxX = min(matrix.width - 1, centerX + radiusPx)
        let minY = max(0, centerY - radiusPx)
        let maxY = min(matrix.height - 1, centerY + radiusPx)
        var value = profile.normalizedValues[(centerY * matrix.width) + centerX]
        for yy in minY...maxY {
            for xx in minX...maxX {
                value = max(value, profile.normalizedValues[(yy * matrix.width) + xx])
            }
        }
        return value
    }

    private static func localMax(matrix: CSVMatrix, x: Double, y: Double, radiusPx: Int) -> Double {
        let centerX = min(max(Int(round(x)), 0), matrix.width - 1)
        let centerY = min(max(Int(round(y)), 0), matrix.height - 1)
        let minX = max(0, centerX - radiusPx)
        let maxX = min(matrix.width - 1, centerX + radiusPx)
        let minY = max(0, centerY - radiusPx)
        let maxY = min(matrix.height - 1, centerY + radiusPx)
        var value = matrix[centerX, centerY]
        for yy in minY...maxY {
            for xx in minX...maxX {
                value = max(value, matrix[xx, yy])
            }
        }
        return value
    }

    private static func circularBoundaryPoints(
        centerX: Double,
        centerY: Double,
        radius: Double,
        pointCount: Int,
        width: Int,
        height: Int
    ) -> [CellBoundaryPoint] {
        (0..<pointCount).map { index in
            let angle = (Double(index) / Double(pointCount)) * Double.pi * 2.0
            return CellBoundaryPoint(
                x: min(max(centerX + cos(angle) * radius, 0), Double(max(0, width - 1))),
                y: min(max(centerY + sin(angle) * radius, 0), Double(max(0, height - 1)))
            )
        }
    }

    private static func pathFromBoundaryPoints(_ points: [CellBoundaryPoint]?, height: Int) -> NSBezierPath? {
        guard let points, points.count >= 3 else { return nil }
        let path = NSBezierPath()
        for (index, point) in points.enumerated() {
            let nsPoint = NSPoint(x: point.x, y: Double(height) - point.y)
            if index == 0 {
                path.move(to: nsPoint)
            } else {
                path.line(to: nsPoint)
            }
        }
        path.close()
        return path
    }

    private static func maskLikePath(center: NSPoint, radius: Double, seed: Int) -> NSBezierPath {
        let pointCount = 12
        let path = NSBezierPath()
        for index in 0..<pointCount {
            let angle = (Double(index) / Double(pointCount)) * Double.pi * 2.0
            let wobble = 0.78 + 0.28 * Double(((seed * 31 + index * 17) % 11)) / 10.0
            let r = radius * wobble
            let point = NSPoint(
                x: center.x + cos(angle) * r,
                y: center.y + sin(angle) * r
            )
            if index == 0 {
                path.move(to: point)
            } else {
                path.line(to: point)
            }
        }
        path.close()
        return path
    }

    private static func renderCountsPlot(counts: [CellTypeCount]) -> NSImage {
        let width = 760.0
        let height = 430.0
        let left = 104.0
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

        "Cell type counts".draw(
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
            let color = NSColor(hex: row.colorHex) ?? .systemBlue
            color.setFill()
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
                maxWidth: 112,
                font: NSFont.systemFont(ofSize: 18, weight: .regular)
            )
        }

        image.unlockFocus()
        return image
    }

    private static func parseCellType(_ definition: CellTypeDefinition) -> ParsedCellType {
        ParsedCellType(
            definition: definition,
            positives: parseMarkerList(definition.allPositiveMarkers).map(canonicalMarker),
            negatives: parseMarkerList(definition.allNegativeMarkers).map(canonicalMarker),
            anyGroups: definition.anyPositiveGroups
                .split(whereSeparator: \.isNewline)
                .map { parseMarkerList(String($0)).map(canonicalMarker) }
                .filter { !$0.isEmpty }
        )
    }

    private static func assignmentRelevantChannels(
        channels: [ChannelConfig],
        cellTypes: [CellTypeDefinition]
    ) -> [ChannelConfig] {
        let markerNames = cellTypes.flatMap { definition in
            parseMarkerList(definition.allPositiveMarkers)
                + parseMarkerList(definition.allNegativeMarkers)
                + definition.anyPositiveGroups
                    .split(whereSeparator: \.isNewline)
                    .flatMap { parseMarkerList(String($0)) }
        }
        let requiredKeys = Set(markerNames.map(canonicalMarker).filter { $0 != "nucleus" })
        guard !requiredKeys.isEmpty else { return [] }
        let filtered = channels.filter { requiredKeys.contains(canonicalMarker($0.channelName)) }
        return filtered.isEmpty ? channels : filtered
    }

    private static func assignmentSearchAxes(fixVoronoi: Bool, fixBuffer: Bool) -> [AssignmentSearchAxis] {
        var axes: [AssignmentSearchAxis] = []
        if !fixVoronoi {
            axes.append(AssignmentSearchAxis(keyPath: \.rVoronoiUm, range: 1...300))
        }
        if !fixBuffer {
            axes.append(AssignmentSearchAxis(keyPath: \.rBufferUm, range: 0...300))
        }
        axes.append(contentsOf: [
            AssignmentSearchAxis(keyPath: \.rVoteUm, range: 1...300),
            AssignmentSearchAxis(keyPath: \.tophatRUm, range: 0...150),
            AssignmentSearchAxis(keyPath: \.gaussSigmaUm, range: 0...75),
            AssignmentSearchAxis(keyPath: \.ambiguousMinProbability, range: 0.01...1),
            AssignmentSearchAxis(keyPath: \.ambiguousMinGap, range: 0...1)
        ])
        return axes
    }

    private static func assignmentParameters(
        base: AssignmentParameters,
        axes: [AssignmentSearchAxis],
        comboIndex: Int,
        comboCount: Int
    ) -> AssignmentParameters {
        var params = base
        if comboIndex == 0 {
            return params
        }

        let effectiveIndex = comboIndex - 1
        let coarseLevels = 5
        let coarseSpace = Int(pow(Double(coarseLevels), Double(axes.count)))
        let gridIndex: Int
        if effectiveIndex < comboCount / 2 {
            gridIndex = gridSampleIndex(sampleIndex: effectiveIndex, sampleCount: max(1, comboCount / 2), totalCount: coarseSpace)
        } else {
            let seed = effectiveIndex - comboCount / 2
            gridIndex = abs((seed * 104_729) + (seed * seed * 37)) % max(1, coarseSpace)
        }

        var index = gridIndex
        for axis in axes {
            let valueIndex = index % coarseLevels
            let span = axis.range.upperBound - axis.range.lowerBound
            params[keyPath: axis.keyPath] = axis.range.lowerBound + span * Double(valueIndex) / Double(coarseLevels - 1)
            index /= coarseLevels
        }

        let modes = ["global_otsu", "local", "yen"]
        params.threshMode = modes[effectiveIndex % modes.count]
        params.resolveAmbiguous = (effectiveIndex / modes.count) % 2 == 0
        params.minPosObjectSizePx = max(0, min(50_000, base.minPosObjectSizePx + ((effectiveIndex % 7) - 3) * 50))
        params.minPosPix = max(0, min(50_000, base.minPosPix + ((effectiveIndex % 5) - 2) * 35))
        return params
    }

    private static func isBetterAssignmentRecord(_ lhs: AssignmentScanRecord, _ rhs: AssignmentScanRecord) -> Bool {
        if lhs.unresolvedCount != rhs.unresolvedCount {
            return lhs.unresolvedCount < rhs.unresolvedCount
        }
        if lhs.ambiguousCount != rhs.ambiguousCount {
            return lhs.ambiguousCount < rhs.ambiguousCount
        }
        if lhs.unassignedCount != rhs.unassignedCount {
            return lhs.unassignedCount < rhs.unassignedCount
        }
        if lhs.assignedCount != rhs.assignedCount {
            return lhs.assignedCount > rhs.assignedCount
        }
        return lhs.comboIndex < rhs.comboIndex
    }

    private static func gridSampleIndex(sampleIndex: Int, sampleCount: Int, totalCount: Int) -> Int {
        guard totalCount > 1 else { return 0 }
        let safeSampleCount = max(1, min(sampleCount, totalCount))
        let stride = max(1, totalCount / safeSampleCount)
        let jitter = (sampleIndex * sampleIndex * 37) % stride
        return min(totalCount - 1, sampleIndex * stride + stride / 2 + jitter)
    }

    private static func parseMarkerList(_ text: String) -> [String] {
        text.split { char in
            char == "," || char == ";" || char.isNewline
        }
        .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
    }

    private static func canonicalMarker(_ text: String) -> String {
        let canonical = text.lowercased().filter { $0.isLetter || $0.isNumber }
        if canonical == "nuclearsegmentationsignal" {
            return "nucleus"
        }
        return canonical
    }
}
