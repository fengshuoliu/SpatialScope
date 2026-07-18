import AppKit
import Combine
import Foundation
import OSLog

@MainActor
final class AppStore: ObservableObject {
    @Published var selectedSection: AnalysisSection = .inputs
    @Published var inputFolder: URL
    @Published var outputFolder: URL
    @Published var channels: [ChannelConfig] = []
    @Published var cellTypes: [CellTypeDefinition] = [
        CellTypeDefinition(name: "Tumor", colorHex: "#dc0000", allPositiveMarkers: "\(GeneratedMarkerNames.nuclearSegmentationSignal), GFP_tumor"),
        CellTypeDefinition(name: "CD8 T", colorHex: "#00ff00", allPositiveMarkers: "\(GeneratedMarkerNames.nuclearSegmentationSignal), CD8A"),
        CellTypeDefinition(name: "Macrophage", colorHex: "#0008e5", allPositiveMarkers: "\(GeneratedMarkerNames.nuclearSegmentationSignal), F4_80")
    ]

    @Published var xUm: Double = 0
    @Published var xPx: Int = 0
    @Published var yUm: Double = 0
    @Published var yPx: Int = 0
    @Published var whiteChannelID: UUID?
    @Published var whiteWeight: Double = 0

    @Published var nucleusChannelID: UUID?
    @Published var nucleiRunMode: NucleiRunMode = .manual
    @Published var nucleiParameters = NucleiParameters()
    @Published var nucleiScanResults: [NucleiScanRecord] = []
    @Published var selectedNucleiScanCombo: Int?
    @Published var nucleiScanFixMinDiameter: Bool = true {
        didSet { nucleiScanCombinationBudget = nucleiScanPlannedCombinationCount }
    }
    @Published var nucleiScanFixMaxDiameter: Bool = true {
        didSet { nucleiScanCombinationBudget = nucleiScanPlannedCombinationCount }
    }
    @Published var nucleiScanCombinationBudget: Int = 160 {
        didSet {
            let clamped = min(max(nucleiScanCombinationBudget, 10), nucleiScanTotalCombinationCount)
            if clamped != nucleiScanCombinationBudget {
                nucleiScanCombinationBudget = clamped
                return
            }
            defaults.set(clamped, forKey: nucleiScanCombinationBudgetKey)
        }
    }
    @Published private(set) var nucleiScanSecondsPerCombination: Double = 0.16
    @Published private(set) var nucleiScanBenchmarkCPUAllocationPercent: Double = 25
    @Published var nucleiResult: NucleiSegmentationResult?
    @Published var cellTypeAssignmentResult: CellTypeAssignmentResult?
    @Published var neighborhoodAnalysisResult: NeighborhoodAnalysisResult?
    @Published var regionAnalysisResult: RegionAnalysisResult?
    @Published var cellDistributionResult: CellDistributionAnalysisResult?
    @Published var distanceAnalysisResult: DistanceAnalysisResult?
    @Published var assignmentParameters = AssignmentParameters()
    @Published var assignmentRunMode: AssignmentRunMode = .manual
    @Published var assignmentScanFixVoronoi: Bool = false {
        didSet { assignmentScanCombinationBudget = assignmentScanPlannedCombinationCount }
    }
    @Published var assignmentScanFixBuffer: Bool = false {
        didSet { assignmentScanCombinationBudget = assignmentScanPlannedCombinationCount }
    }
    @Published var assignmentScreeningBandCount: Int = 6 {
        didSet {
            let clamped = min(max(assignmentScreeningBandCount, 5), 6)
            if clamped != assignmentScreeningBandCount {
                assignmentScreeningBandCount = clamped
                return
            }
            updateAssignmentScreeningBands(randomize: assignmentScreeningSubsetMode == .randomThree)
        }
    }
    @Published var assignmentScreeningSubsetMode: AssignmentScreeningSubsetMode = .randomThree {
        didSet {
            updateAssignmentScreeningBands(randomize: assignmentScreeningSubsetMode == .randomThree)
        }
    }
    @Published private(set) var assignmentScreeningSelectedBandIndices: [Int] = [0, 2, 4]
    @Published var assignmentScanCombinationBudget: Int = 20 {
        didSet {
            let clamped = min(max(assignmentScanCombinationBudget, 10), assignmentScanTotalCombinationCount)
            if clamped != assignmentScanCombinationBudget {
                assignmentScanCombinationBudget = clamped
            }
        }
    }
    @Published private(set) var assignmentScanSecondsPerCombination: Double = 12.0
    @Published var assignmentScanResults: [AssignmentScanRecord] = []
    @Published var selectedAssignmentScanCombo: Int?
    @Published var assignmentParameterPanelRevision: Int = 0
    @Published var regionParameters = RegionParameters()
    @Published var neighborhoodGridUm: Double = 20
    @Published var densityBandWidthUm: Double = 10
    @Published var selectedCellDistributionOutputMode: CellDistributionOutputMode = .regionMasks
    @Published var cellDistributionSelectedRegionIDs: Set<Int> = []
    @Published var cellDistributionSelectedCellTypes: Set<String> = []
    @Published var cellDistributionSelectedClusterIDs: Set<Int> = []
    @Published var cpuAllocationPercent: Double = 100 {
        didSet { defaults.set(cpuAllocationPercent, forKey: cpuAllocationKey) }
    }
    @Published var gpuAllocationPercent: Double = 0 {
        didSet { defaults.set(gpuAllocationPercent, forKey: gpuAllocationKey) }
    }
    @Published var resourceSnapshot: ResourceSnapshot

    @Published var overlayImage: NSImage?
    @Published var splitImage: NSImage?
    @Published var loadedMatrices: [CSVMatrix] = []
    @Published var outputFiles: [OutputFileInfo] = []
    @Published var statusMessage: String = ""
    @Published var isBusy = false
    @Published var runningSection: AnalysisSection?

    private let resourceMonitor: ResourceMonitor
    private var resourceCancellable: AnyCancellable?
    private var currentTask: Task<Void, Never>?
    private var currentCancellationToken: CancellationToken?
    private let logger = Logger(subsystem: "com.fengshuoliu.SpatialScope", category: "Pipeline")
    private let defaults = UserDefaults.standard
    private let inputFolderKey = "SpatialScope.inputFolder"
    private let outputFolderKey = "SpatialScope.outputFolder"
    private let cpuAllocationKey = "SpatialScope.cpuAllocationPercent"
    private let gpuAllocationKey = "SpatialScope.gpuAllocationPercent"
    private let maximumCPUDefaultMigrationKey = "SpatialScope4.maximumCPUDefaultApplied"
    private let nucleiScanCombinationBudgetKey = "SpatialScope.nucleiScanCombinationBudget"
    private let nucleiScanSecondsPerCombinationKey = "SpatialScope.nucleiScanSecondsPerCombination"
    private let nucleiScanBenchmarkCPUAllocationKey = "SpatialScope.nucleiScanBenchmarkCPUAllocation"
    private let automaticCPUAllocationPercent = 100.0
    private let automaticGPUAllocationPercent = 0.0

    init() {
        let monitor = ResourceMonitor()
        resourceMonitor = monitor
        resourceSnapshot = monitor.snapshot

        inputFolder = defaults.string(forKey: inputFolderKey)
            .map(URL.init(fileURLWithPath:))
            ?? Self.defaultWorkspaceFolder(named: "Input")
        outputFolder = defaults.string(forKey: outputFolderKey)
            .map(URL.init(fileURLWithPath:))
            ?? Self.defaultWorkspaceFolder(named: "Output")
        let savedCPU = defaults.double(forKey: cpuAllocationKey)
        if !defaults.bool(forKey: maximumCPUDefaultMigrationKey) {
            cpuAllocationPercent = 100
            defaults.set(true, forKey: maximumCPUDefaultMigrationKey)
        } else if savedCPU > 0 {
            cpuAllocationPercent = min(max(savedCPU, 10), 100)
        }
        gpuAllocationPercent = 0
        let savedBudget = defaults.integer(forKey: nucleiScanCombinationBudgetKey)
        if savedBudget > 0 {
            nucleiScanCombinationBudget = Self.clampedNucleiScanBudget(savedBudget)
        }
        let savedSecondsPerCombination = defaults.double(forKey: nucleiScanSecondsPerCombinationKey)
        if savedSecondsPerCombination > 0 {
            nucleiScanSecondsPerCombination = min(max(savedSecondsPerCombination, 0.02), 60)
        }
        let savedBenchmarkCPU = defaults.double(forKey: nucleiScanBenchmarkCPUAllocationKey)
        if savedBenchmarkCPU > 0 {
            nucleiScanBenchmarkCPUAllocationPercent = min(max(savedBenchmarkCPU, 10), 100)
        }

        resourceCancellable = monitor.$snapshot.sink { [weak self] snapshot in
            self?.resourceSnapshot = snapshot
        }
        monitor.start()

        autoImportPreviousRun()
        if channels.isEmpty {
            scanInputFolder()
        }
        refreshOutputs()
    }

    private static func defaultWorkspaceFolder(named name: String) -> URL {
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Documents", isDirectory: true)
        return documents
            .appendingPathComponent("SpatialScope", isDirectory: true)
            .appendingPathComponent(name, isDirectory: true)
    }

    var canGenerateOverlay: Bool {
        !channels.isEmpty && !isBusy
    }

    var pixelSize: (Double, Double)? {
        guard xUm > 0, yUm > 0, xPx > 0, yPx > 0 else { return nil }
        return (xUm / Double(xPx), yUm / Double(yPx))
    }

    var pixelSizeText: String {
        guard xUm > 0, yUm > 0, xPx > 0, yPx > 0 else { return "Figure resolution not set" }
        return "Figure resolution \(Self.compactNumber(xUm)) x \(Self.compactNumber(yUm)) um; \(xPx) x \(yPx) px"
    }

    var selectedOverlayChannels: [ChannelConfig] {
        let selected = channels.filter(\.overlayEnabled)
        return selected.isEmpty ? channels : selected
    }

    var assignedCellTypeNames: [String] {
        let assignments = (cellTypeAssignmentResult ?? OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder))?.assignments ?? []
        let names = Set(assignments.map(\.assignedType).filter { $0 != "Unassigned" && $0 != "Ambiguous" })
        return Array(names).sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }

    var cellTypeMarkerOptions: [String] {
        var names = [GeneratedMarkerNames.nuclearSegmentationSignal]
        for name in channels.map(\.channelName) {
            let canonical = Self.canonicalMarkerName(name)
            guard canonical != Self.canonicalMarkerName(GeneratedMarkerNames.nuclearSegmentationSignal),
                  !names.contains(where: { Self.canonicalMarkerName($0) == canonical }) else {
                continue
            }
            names.append(name)
        }
        return names
    }

    var whiteChannel: ChannelConfig? {
        channels.first { $0.id == whiteChannelID }
    }

    var hasGPU: Bool {
        resourceSnapshot.gpuCount > 0
    }

    var nucleiScanTotalCombinationCount: Int {
        NucleiSegmenter.advancedSearchSpaceSize(
            fixMinDiameter: nucleiScanFixMinDiameter,
            fixMaxDiameter: nucleiScanFixMaxDiameter
        )
    }

    var nucleiScanPlannedCombinationCount: Int {
        min(max(nucleiScanCombinationBudget, 10), nucleiScanTotalCombinationCount)
    }

    var nucleiScanEffectiveWorkerCount: Int {
        NucleiSegmenter.effectiveWorkerCount(
            activeCPUCoreCount: resourceSnapshot.activeCPUCoreCount,
            cpuAllocationPercent: cpuAllocationPercent
        )
    }

    var nucleiScanEstimatedSeconds: Double {
        NucleiSegmenter.estimateAdvancedScanSeconds(
            combinationBudget: nucleiScanPlannedCombinationCount,
            secondsPerCombination: nucleiScanSecondsPerCombination,
            benchmarkCPUAllocationPercent: nucleiScanBenchmarkCPUAllocationPercent,
            cpuAllocationPercent: cpuAllocationPercent
        )
    }

    var nucleiScanEstimatedTimeText: String {
        Self.durationText(seconds: nucleiScanEstimatedSeconds)
    }

    var assignmentScanTotalCombinationCount: Int {
        CellTypeAssigner.parameterSearchSpaceSize(
            fixVoronoi: assignmentScanFixVoronoi,
            fixBuffer: assignmentScanFixBuffer
        )
    }

    var assignmentScanPlannedCombinationCount: Int {
        min(max(assignmentScanCombinationBudget, 10), assignmentScanTotalCombinationCount)
    }

    var assignmentScanEffectiveWorkerCount: Int {
        NucleiSegmenter.effectiveWorkerCount(
            activeCPUCoreCount: resourceSnapshot.activeCPUCoreCount,
            cpuAllocationPercent: cpuAllocationPercent
        )
    }

    var assignmentScanEstimatedSeconds: Double {
        NucleiSegmenter.estimateAdvancedScanSeconds(
            combinationBudget: assignmentScanPlannedCombinationCount,
            secondsPerCombination: assignmentScanSecondsPerCombination,
            benchmarkCPUAllocationPercent: 25,
            cpuAllocationPercent: cpuAllocationPercent
        )
    }

    var assignmentScanEstimatedTimeText: String {
        Self.durationText(seconds: assignmentScanEstimatedSeconds)
    }

    var configuredCPUWorkerCount: Int {
        NucleiSegmenter.effectiveWorkerCount(
            activeCPUCoreCount: resourceSnapshot.activeCPUCoreCount,
            cpuAllocationPercent: cpuAllocationPercent
        )
    }

    func reshuffleAssignmentScreeningBands() {
        updateAssignmentScreeningBands(randomize: true)
    }

    func chooseInputFolder() {
        guard let url = FolderPanelService.chooseFolder(title: "Choose CSV input folder", initialURL: inputFolder) else { return }
        inputFolder = url
        persistFolders()
        logger.info("Selected input folder: \(url.path, privacy: .public)")
        scanInputFolder()
    }

    func chooseOutputFolder() {
        guard let url = FolderPanelService.chooseFolder(title: "Choose output folder", initialURL: outputFolder) else { return }
        outputFolder = url
        persistFolders()
        logger.info("Selected output folder: \(url.path, privacy: .public)")
        autoImportPreviousRun()
    }

    func revealOutputFolder() {
        FolderPanelService.reveal(outputFolder)
    }

    func scanInputFolder() {
        do {
            let urls = try CSVImageLoader.discoverCSVFiles(in: inputFolder)
            let existing = Dictionary(uniqueKeysWithValues: channels.map { ($0.fileName, $0) })
            channels = urls.enumerated().map { index, url in
                if var old = existing[url.lastPathComponent] {
                    old.fileName = url.lastPathComponent
                    return old
                }
                return ChannelConfig(
                    fileName: url.lastPathComponent,
                    marker: url.deletingPathExtension().lastPathComponent,
                    colorHex: ColorPalette.color(at: index),
                    overlayEnabled: true
                )
            }
            if nucleusChannelID == nil || !channels.contains(where: { $0.id == nucleusChannelID }) {
                nucleusChannelID = guessNuclearChannelID()
            }
            if whiteChannelID != nil && !channels.contains(where: { $0.id == whiteChannelID }) {
                whiteChannelID = nil
            }
            statusMessage = "Detected \(channels.count) CSV channel(s)."
            logger.info("Scanned input folder and found \(self.channels.count, privacy: .public) CSV channel(s)")
        } catch {
            channels = []
            statusMessage = error.localizedDescription
            logger.error("Input scan failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    func resetMarkerNamesFromFiles() {
        for index in channels.indices {
            channels[index].marker = URL(fileURLWithPath: channels[index].fileName)
                .deletingPathExtension()
                .lastPathComponent
        }
    }

    func reassignColors() {
        for index in channels.indices {
            channels[index].colorHex = ColorPalette.color(at: index + Int.random(in: 1...ColorPalette.commonFirst.count))
        }
    }

    func saveConfiguration() {
        do {
            try writeCurrentConfiguration()
            persistFolders()
            refreshOutputs()
            statusMessage = "Configuration saved."
            logger.info("Saved configuration")
        } catch {
            statusMessage = error.localizedDescription
            logger.error("Configuration save failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    func saveCellTypes() {
        do {
            try OutputWriter.writeCellTypeConfig(cellTypes, outputFolder: outputFolder)
            refreshOutputs()
            statusMessage = "Cell-type configuration saved."
            logger.info("Saved cell-type configuration")
        } catch {
            statusMessage = error.localizedDescription
            logger.error("Cell-type save failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    func generateOverlay() {
        guard let token = beginOperation(.overlay, status: "Loading CSV matrices and generating overlay...") else { return }
        let inputFolder = inputFolder
        let outputFolder = outputFolder
        let channels = channels
        let whiteChannelID = whiteChannelID
        let whiteWeight = whiteWeight
        let pixelSizeX = pixelSize?.0
        let snapshot = resourceSnapshot
        let automaticCPUAllocation = automaticCPUAllocationPercent
        let automaticGPUAllocation = automaticGPUAllocationPercent

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                await MainActor.run {
                    try? store.writeCurrentConfiguration()
                }
                let matrices = try CSVImageLoader.loadMatrices(inputFolder: inputFolder, channels: channels)
                try token.checkCancellation()
                let result = try OverlayRenderer.render(
                    matrices: matrices,
                    channels: channels,
                    whiteChannelID: whiteChannelID,
                    whiteWeight: whiteWeight,
                    pixelSizeXUm: pixelSizeX,
                    cpuAllocationPercent: automaticCPUAllocation
                )
                try token.checkCancellation()
                try OutputWriter.writeOverlayImages(
                    result: result,
                    outputFolder: outputFolder,
                    pixelSizeXUm: pixelSizeX
                )
                try OutputWriter.writeResourceMetadata(
                    outputFolder: outputFolder,
                    section: "overlay",
                    cpuAllocationPercent: automaticCPUAllocation,
                    gpuAllocationPercent: automaticGPUAllocation,
                    snapshot: snapshot
                )
                await MainActor.run {
                    store.loadedMatrices = result.matrices
                    store.overlayImage = result.overlayImage
                    store.splitImage = result.splitImage
                    store.persistFolders()
                    store.refreshOutputs()
                    store.selectedSection = .overlay
                    store.finishOperation(status: "Overlay and split-channel previews saved.")
                    store.logger.info("Generated overlay for \(channels.count, privacy: .public) channel(s)")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Overlay generation failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    func refreshOutputs() {
        outputFiles = OutputWriter.listOutputFiles(outputFolder: outputFolder)
    }

    func cancelCurrentOperation() {
        guard isBusy else { return }
        currentCancellationToken?.cancel()
        currentTask?.cancel()
        statusMessage = "Cancelling current operation..."
    }

    private func beginOperation(_ section: AnalysisSection, status: String) -> CancellationToken? {
        guard !isBusy else { return nil }
        let token = CancellationToken()
        currentCancellationToken = token
        runningSection = section
        isBusy = true
        statusMessage = status
        return token
    }

    private func finishOperation(status: String) {
        isBusy = false
        runningSection = nil
        currentTask = nil
        currentCancellationToken = nil
        statusMessage = status
    }

    private func finishOperationAfterCancellation() {
        finishOperation(status: "Operation cancelled.")
    }

    func runSection(_ section: AnalysisSection) {
        guard !isBusy else { return }
        selectedSection = section
        switch section {
        case .inputs:
            runQuickSection(.inputs, status: "Saving configuration...") { store in
                try store.writeCurrentConfiguration()
                store.persistFolders()
                store.refreshOutputs()
                store.logger.info("Saved configuration")
                return "Configuration saved."
            }
        case .overlay:
            generateOverlay()
        case .nuclei:
            if nucleiRunMode == .advanced {
                runNucleiAdvancedScan()
            } else {
                runNucleiFinal()
            }
        case .cellTypes:
            runCellTypeAssignment()
        case .outputs:
            runQuickSection(.outputs, status: "Refreshing outputs...") { store in
                store.refreshOutputs()
                return "Outputs refreshed."
            }
        case .neighborhood:
            runNeighborhoodAnalysis()
        case .region:
            runRegionAnalysis()
        case .cellDistribution:
            runCellDistributionAnalysis(outputMode: selectedCellDistributionOutputMode)
        case .distance:
            runDistanceAnalysis()
        }
    }

    private func runQuickSection(
        _ section: AnalysisSection,
        status: String,
        operation: @escaping @MainActor (AppStore) throws -> String
    ) {
        guard let token = beginOperation(section, status: status) else { return }
        currentTask = Task { @MainActor [store = self] in
            do {
                try token.checkCancellation()
                let finalStatus = try operation(store)
                try await Task.sleep(nanoseconds: 180_000_000)
                try token.checkCancellation()
                store.finishOperation(status: finalStatus)
            } catch is CancellationError {
                store.finishOperationAfterCancellation()
            } catch {
                store.finishOperation(status: error.localizedDescription)
                store.logger.error("Quick section operation failed: \(error.localizedDescription, privacy: .public)")
            }
        }
    }

    func runNucleiAdvancedScan() {
        guard let channel = selectedNucleusChannel() else {
            statusMessage = "Choose a nucleus channel first."
            return
        }
        let plannedCombinations = nucleiScanPlannedCombinationCount
        guard let token = beginOperation(.nuclei, status: "Running advanced nuclei parameter scan for \(plannedCombinations) combinations...") else { return }

        let inputFolder = inputFolder
        let outputFolder = outputFolder
        let params = nucleiParameters
        let pixelSize = pixelSize
        let cpuAllocation = cpuAllocationPercent
        let gpuAllocation = 0.0
        let snapshot = resourceSnapshot
        let estimatedSecondsAtStart = nucleiScanEstimatedSeconds
        let fixMinDiameter = nucleiScanFixMinDiameter
        let fixMaxDiameter = nucleiScanFixMaxDiameter
        let totalSearchSpace = nucleiScanTotalCombinationCount

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                let matrix = try CSVImageLoader.loadMatrix(
                    from: inputFolder.appendingPathComponent(channel.fileName),
                    channelName: channel.channelName
                )
                let start = Date()
                let records = try NucleiSegmenter.runAdvancedScan(
                    matrix: matrix,
                    baseParams: params,
                    pixelSize: pixelSize,
                    cpuAllocationPercent: cpuAllocation,
                    combinationBudget: plannedCombinations,
                    fixMinDiameter: fixMinDiameter,
                    fixMaxDiameter: fixMaxDiameter,
                    cancellationToken: token
                )
                let elapsed = Date().timeIntervalSince(start)
                try token.checkCancellation()
                try OutputWriter.writeNucleiScanResults(records, outputFolder: outputFolder)
                try OutputWriter.writeNucleiScanMetadata(
                    records: records,
                    outputFolder: outputFolder,
                    plannedCombinationCount: plannedCombinations,
                    totalSearchSpace: totalSearchSpace,
                    searchIntervalCount: NucleiSegmenter.advancedSearchIntervalCount,
                    estimatedSecondsAtStart: estimatedSecondsAtStart,
                    elapsedSeconds: elapsed,
                    cpuAllocationPercent: cpuAllocation,
                    gpuAllocationPercent: gpuAllocation,
                    snapshot: snapshot
                )
                try OutputWriter.writeResourceMetadata(
                    outputFolder: outputFolder,
                    section: "nuclei",
                    cpuAllocationPercent: cpuAllocation,
                    gpuAllocationPercent: gpuAllocation,
                    snapshot: snapshot
                )

                await MainActor.run {
                    if !records.isEmpty {
                        store.nucleiScanSecondsPerCombination = min(max(elapsed / Double(records.count), 0.02), 60)
                        store.nucleiScanBenchmarkCPUAllocationPercent = min(max(cpuAllocation, 10), 100)
                        store.defaults.set(store.nucleiScanSecondsPerCombination, forKey: store.nucleiScanSecondsPerCombinationKey)
                        store.defaults.set(store.nucleiScanBenchmarkCPUAllocationPercent, forKey: store.nucleiScanBenchmarkCPUAllocationKey)
                    }
                    store.nucleiScanResults = records
                    if let best = store.bestNucleiScanRecord(in: records) {
                        store.selectedNucleiScanCombo = best.comboIndex
                        store.nucleiParameters = best.params
                        store.finishOperation(status: "Advanced scan complete: \(records.count) combinations in \(Self.durationText(seconds: elapsed)). Recommended combo \(best.comboIndex): \(best.count) nuclei.")
                    } else {
                        store.finishOperation(status: "Advanced scan complete, but no successful combinations were found.")
                    }
                    try? store.writeCurrentConfiguration()
                    store.persistFolders()
                    store.refreshOutputs()
                    store.logger.info("Nuclei advanced scan generated \(records.count, privacy: .public) records")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Nuclei advanced scan failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    func runNucleiFinal() {
        guard let channel = selectedNucleusChannel() else {
            statusMessage = "Choose a nucleus channel first."
            return
        }
        guard let token = beginOperation(.nuclei, status: "Running final nuclei segmentation...") else { return }

        let inputFolder = inputFolder
        let outputFolder = outputFolder
        let params = nucleiParameters
        let pixelSize = pixelSize
        let cpuAllocation = cpuAllocationPercent
        let gpuAllocation = 0.0
        let snapshot = resourceSnapshot

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                let matrix = try CSVImageLoader.loadMatrix(
                    from: inputFolder.appendingPathComponent(channel.fileName),
                    channelName: channel.channelName
                )
                let result = try NucleiSegmenter.runFinal(
                    matrix: matrix,
                    params: params,
                    pixelSize: pixelSize,
                    cpuAllocationPercent: cpuAllocation,
                    cancellationToken: token
                )
                try token.checkCancellation()
                try OutputWriter.writeNucleiOutputs(result: result, outputFolder: outputFolder)
                try OutputWriter.writeResourceMetadata(
                    outputFolder: outputFolder,
                    section: "nuclei",
                    cpuAllocationPercent: cpuAllocation,
                    gpuAllocationPercent: gpuAllocation,
                    snapshot: snapshot
                )

                await MainActor.run {
                    store.nucleiResult = result
                    try? store.writeCurrentConfiguration()
                    store.persistFolders()
                    store.refreshOutputs()
                    store.finishOperation(status: "Final nuclei segmentation complete: \(result.count) nuclei.")
                    store.logger.info("Final nuclei segmentation found \(result.count, privacy: .public) nuclei")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Final nuclei segmentation failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    func applyNucleiScanRecord(_ record: NucleiScanRecord) {
        nucleiParameters = record.params
        selectedNucleiScanCombo = record.comboIndex
        statusMessage = "Applied combo \(record.comboIndex) with \(record.count) nuclei to the parameter panel. You can fine-tune before the final run."
    }

    func applyAssignmentScanRecord(_ record: AssignmentScanRecord) {
        objectWillChange.send()
        assignmentParameters = record.parameters
        selectedAssignmentScanCombo = record.comboIndex
        assignmentParameterPanelRevision += 1
        statusMessage = "Applied assignment combo \(record.comboIndex) to the parameter panel. You can fine-tune before the final run."
    }

    func runCellTypeAssignmentScreening() {
        let detections = nucleiResult?.detections.isEmpty == false
            ? nucleiResult?.detections ?? []
            : OutputWriter.loadNucleiSummary(outputFolder: outputFolder)
        guard !detections.isEmpty else {
            statusMessage = "Run final nuclei segmentation before assignment screening."
            return
        }
        let plannedCombinations = assignmentScanPlannedCombinationCount
        guard let token = beginOperation(.cellTypes, status: "Running cell-type assignment screening for \(plannedCombinations) combinations...") else { return }

        let inputFolder = inputFolder
        let outputFolder = outputFolder
        let channels = channels
        let cellTypes = cellTypes
        let parameters = assignmentParameters
        let fixVoronoi = assignmentScanFixVoronoi
        let fixBuffer = assignmentScanFixBuffer
        let screeningBandCount = assignmentScreeningBandCount
        let screeningSubsetMode = assignmentScreeningSubsetMode
        let screeningSelectedBands = assignmentScreeningSelectedBandIndices
        let pixelSize = pixelSize
        let labelMap = nucleiResult?.labelMap ?? OutputWriter.loadNucleiLabelMap(outputFolder: outputFolder)
        let cpuAllocation = cpuAllocationPercent
        let gpuAllocation = 0.0
        let snapshot = resourceSnapshot

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                let matrices = try CSVImageLoader.loadMatrices(inputFolder: inputFolder, channels: channels)
                let start = Date()
                let records = try CellTypeAssigner.runParameterScreening(
                    detections: detections,
                    matrices: matrices,
                    channels: channels,
                    cellTypes: cellTypes,
                    baseParameters: parameters,
                    pixelSize: pixelSize,
                    labelMap: labelMap,
                    cpuAllocationPercent: cpuAllocation,
                    combinationBudget: plannedCombinations,
                    fixVoronoi: fixVoronoi,
                    fixBuffer: fixBuffer,
                    screeningBandCount: screeningBandCount,
                    screeningSubsetMode: screeningSubsetMode,
                    screeningSelectedBands: screeningSelectedBands,
                    cancellationToken: token
                )
                let elapsed = Date().timeIntervalSince(start)
                try token.checkCancellation()
                let assignmentDir = OutputWriter.sectionURL("celltype_assignment", outputFolder: outputFolder)
                try FileManager.default.createDirectory(at: assignmentDir, withIntermediateDirectories: true)
                let encoder = JSONEncoder()
                encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                try encoder.encode(records).write(to: assignmentDir.appendingPathComponent("celltype_assignment_screening_results.json"))
                try OutputWriter.writeResourceMetadata(
                    outputFolder: outputFolder,
                    section: "celltype_assignment",
                    cpuAllocationPercent: cpuAllocation,
                    gpuAllocationPercent: gpuAllocation,
                    snapshot: snapshot
                )

                await MainActor.run {
                    if !records.isEmpty {
                        store.assignmentScanSecondsPerCombination = min(max(elapsed / Double(records.count), 0.01), 60)
                    }
                    store.assignmentScanResults = records
                    if let best = store.bestAssignmentScanRecord(in: records) {
                        store.selectedAssignmentScanCombo = best.comboIndex
                        store.assignmentParameters = best.parameters
                        store.assignmentParameterPanelRevision += 1
                        store.finishOperation(status: "Assignment screening complete: suggested combo \(best.comboIndex), \(best.unresolvedCount) unresolved cells.")
                    } else {
                        store.finishOperation(status: "Assignment screening complete, but no combinations were generated.")
                    }
                    store.refreshOutputs()
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Cell-type assignment screening failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    func runCellTypeAssignment() {
        let detections = nucleiResult?.detections.isEmpty == false
            ? nucleiResult?.detections ?? []
            : OutputWriter.loadNucleiSummary(outputFolder: outputFolder)
        guard !detections.isEmpty else {
            statusMessage = "Run final nuclei segmentation before cell-type assignment."
            return
        }
        guard let token = beginOperation(.cellTypes, status: "Running native cell-type assignment...") else { return }

        let inputFolder = inputFolder
        let outputFolder = outputFolder
        let channels = channels
        let cellTypes = cellTypes
        let parameters = assignmentParameters
        let pixelSize = pixelSize
        let labelMap = nucleiResult?.labelMap ?? OutputWriter.loadNucleiLabelMap(outputFolder: outputFolder)
        let cpuAllocation = cpuAllocationPercent
        let gpuAllocation = 0.0
        let snapshot = resourceSnapshot

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                let matrices = try CSVImageLoader.loadMatrices(inputFolder: inputFolder, channels: channels)
                let result = try CellTypeAssigner.run(
                    detections: detections,
                    matrices: matrices,
                    channels: channels,
                    cellTypes: cellTypes,
                    parameters: parameters,
                    pixelSize: pixelSize,
                    labelMap: labelMap,
                    cpuAllocationPercent: cpuAllocation,
                    cancellationToken: token
                )
                try token.checkCancellation()
                try OutputWriter.writeCellTypeConfig(cellTypes, outputFolder: outputFolder)
                try OutputWriter.writeCellTypeAssignmentOutputs(result: result, outputFolder: outputFolder)
                let persistedResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
                try OutputWriter.writeResourceMetadata(
                    outputFolder: outputFolder,
                    section: "celltype_assignment",
                    cpuAllocationPercent: cpuAllocation,
                    gpuAllocationPercent: gpuAllocation,
                    snapshot: snapshot
                )

                await MainActor.run {
                    store.cellTypeAssignmentResult = persistedResult ?? result
                    try? store.writeCurrentConfiguration()
                    store.persistFolders()
                    store.refreshOutputs()
                    store.finishOperation(status: "Cell-type assignment complete: \(result.totalAssigned) assigned of \(result.assignments.count) nuclei.")
                    store.logger.info("Cell-type assignment generated \(result.assignments.count, privacy: .public) rows")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Cell-type assignment failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    func runNeighborhoodAnalysis() {
        let assignmentResult = cellTypeAssignmentResult ?? OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        guard let assignmentResult else {
            statusMessage = "Run cell-type assignment before neighborhood analysis."
            return
        }
        guard let token = beginOperation(.neighborhood, status: "Running native neighborhood analysis...") else { return }
        let outputFolder = outputFolder
        let gridSizeUm = neighborhoodGridUm
        let pixelSize = pixelSize
        let snapshot = resourceSnapshot
        let automaticCPUAllocation = automaticCPUAllocationPercent
        let automaticGPUAllocation = automaticGPUAllocationPercent

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                await MainActor.run {
                    try? store.writeCurrentConfiguration()
                }
                let result = try NeighborhoodAnalyzer.run(
                    assignments: assignmentResult.assignments,
                    gridSizeUm: gridSizeUm,
                    pixelSize: pixelSize,
                    canvasWidth: assignmentResult.width,
                    canvasHeight: assignmentResult.height
                )
                try token.checkCancellation()
                try OutputWriter.writeNeighborhoodAnalysisOutputs(result: result, outputFolder: outputFolder)
                try OutputWriter.writeResourceMetadata(
                    outputFolder: outputFolder,
                    section: "neighborhood_analysis",
                    cpuAllocationPercent: automaticCPUAllocation,
                    gpuAllocationPercent: automaticGPUAllocation,
                    snapshot: snapshot
                )
                await MainActor.run {
                    store.neighborhoodAnalysisResult = result
                    store.persistFolders()
                    store.refreshOutputs()
                    store.finishOperation(status: "Neighborhood analysis complete: \(result.occupiedTileCount) occupied grid squares, \(result.totalCells) cells.")
                    store.logger.info("Neighborhood analysis generated \(result.occupiedTileCount, privacy: .public) occupied tiles")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Neighborhood analysis failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    func shuffleNeighborhoodColors() {
        guard var result = neighborhoodAnalysisResult else { return }
        let keys = result.clusterCounts.map(\.clusterKey)
        let offset = Int.random(in: 1...360)
        let colorByKey = Dictionary(uniqueKeysWithValues: keys.enumerated().map { index, key in
            (key, ColorPalette.clusterColor(at: index, offset: offset))
        })
        result.tiles = result.tiles.map { tile in
            var copy = tile
            copy.colorHex = colorByKey[tile.effectiveClusterKey] ?? tile.colorHex
            return copy
        }
        result.clusterCounts = result.clusterCounts.map { cluster in
            NeighborhoodClusterCount(
                clusterID: cluster.clusterID,
                clusterKey: cluster.clusterKey,
                clusterLabel: cluster.clusterLabel,
                tileCount: cluster.tileCount,
                cellCount: cluster.cellCount,
                tileFraction: cluster.tileFraction,
                colorHex: colorByKey[cluster.clusterKey] ?? cluster.colorHex
            )
        }
        result.image = NeighborhoodAnalyzer.renderNeighborhoodMap(
            tiles: result.tiles,
            clusterCounts: result.clusterCounts,
            gridSizeUm: result.gridSizeUm,
            width: result.width,
            height: result.height
        )
        result.clusterKeyImage = NeighborhoodAnalyzer.renderClusterKeyImage(counts: result.clusterCounts)
        result.statsImage = NeighborhoodAnalyzer.renderClusterCountsPlot(counts: result.clusterCounts)
        neighborhoodAnalysisResult = result
        do {
            try OutputWriter.writeNeighborhoodAnalysisOutputs(result: result, outputFolder: outputFolder)
            refreshOutputs()
            statusMessage = "Neighborhood colors shuffled and saved."
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    func runRegionAnalysis() {
        let assignmentResult = cellTypeAssignmentResult ?? OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        guard let assignmentResult else {
            statusMessage = "Run cell-type assignment before region analysis."
            return
        }
        let availableRegionTypes = Set(assignmentResult.assignments.map(\.assignedType))
            .filter { $0 != "Unassigned" && $0 != "Ambiguous" }
        guard !availableRegionTypes.isEmpty else {
            statusMessage = "Cell-type assignment did not produce any assigned cell types for region analysis."
            return
        }
        var parameters = regionParameters
        let selectedTypes = Set(parameters.selectedTypes).intersection(availableRegionTypes)
        parameters.selectedTypes = selectedTypes.isEmpty
            ? availableRegionTypes.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
            : selectedTypes.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        regionParameters = parameters
        guard let token = beginOperation(.region, status: "Running native region analysis...") else { return }
        let outputFolder = outputFolder
        let pixelSize = pixelSize
        let snapshot = resourceSnapshot
        let automaticCPUAllocation = automaticCPUAllocationPercent
        let automaticGPUAllocation = automaticGPUAllocationPercent
        let currentOverlayImage = overlayImage

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                await MainActor.run {
                    try? store.writeCurrentConfiguration()
                }
                let result = try RegionAnalyzer.run(
                    assignments: assignmentResult.assignments,
                    parameters: parameters,
                    pixelSize: pixelSize,
                    canvasWidth: assignmentResult.width,
                    canvasHeight: assignmentResult.height,
                    cellTypeMask: assignmentResult.cellTypeMask,
                    cellTypeIDByName: assignmentResult.cellTypeIDByName
                )
                try token.checkCancellation()
                try OutputWriter.writeRegionAnalysisOutputs(
                    result: result,
                    outputFolder: outputFolder,
                    assignments: assignmentResult.assignments,
                    cellTypeMask: assignmentResult.cellTypeMask,
                    cellTypeIDByName: assignmentResult.cellTypeIDByName,
                    overlayImage: currentOverlayImage
                )
                try OutputWriter.writeResourceMetadata(
                    outputFolder: outputFolder,
                    section: "region_analysis",
                    cpuAllocationPercent: automaticCPUAllocation,
                    gpuAllocationPercent: automaticGPUAllocation,
                    snapshot: snapshot
                )
                await MainActor.run {
                    store.regionAnalysisResult = result
                    store.persistFolders()
                    store.refreshOutputs()
                    store.finishOperation(status: "Region analysis complete: \(result.regions.count) ROIs, \(result.totalCells) cells counted.")
                    store.logger.info("Region analysis generated \(result.regions.count, privacy: .public) ROIs")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Region analysis failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    func saveCustomizedRegionDisplay(selectedRegionIDs: Set<Int>, selectedCellTypes: Set<String>) {
        let assignmentResult = cellTypeAssignmentResult ?? OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        let regionResult = regionAnalysisResult ?? OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        guard let assignmentResult else {
            statusMessage = "Run cell-type assignment before saving a customized region display."
            return
        }
        guard let regionResult else {
            statusMessage = "Run region analysis before saving a customized region display."
            return
        }
        guard let token = beginOperation(.region, status: "Saving customized region display...") else { return }
        let outputFolder = outputFolder
        let currentOverlayImage = overlayImage

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                await MainActor.run {
                    try? store.writeCurrentConfiguration()
                }
                let summary = try OutputWriter.writeCustomizedRegionDisplayOutputs(
                    result: regionResult,
                    outputFolder: outputFolder,
                    assignments: assignmentResult.assignments,
                    selectedRegionIDs: selectedRegionIDs,
                    selectedCellTypes: selectedCellTypes,
                    cellTypeMask: assignmentResult.cellTypeMask,
                    cellTypeIDByName: assignmentResult.cellTypeIDByName,
                    overlayImage: currentOverlayImage
                )
                try token.checkCancellation()
                await MainActor.run {
                    store.persistFolders()
                    store.refreshOutputs()
                    store.finishOperation(
                        status: "Customized region display saved to \(summary.customizedDirectory.lastPathComponent); original unmodified export saved to \(summary.originalDirectory.lastPathComponent)."
                    )
                    store.logger.info("Saved customized region display with \(summary.customizedFiles.count, privacy: .public) customized file(s)")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Customized region display save failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    func saveManualRegionAdjustment(
        mode: RegionManualEditMode,
        targetRegionID: Int?,
        displayName: String,
        polygonPoints: [CellBoundaryPoint],
        seedCellTypes: Set<String>? = nil,
        manualParameters: RegionParameters? = nil
    ) {
        saveManualRegionAdjustment(
            mode: mode,
            targetRegionID: targetRegionID,
            displayName: displayName,
            polygonGroups: [polygonPoints],
            seedCellTypes: seedCellTypes,
            manualParameters: manualParameters
        )
    }

    func saveManualRegionAdjustment(
        mode: RegionManualEditMode,
        targetRegionID: Int?,
        displayName: String,
        polygonGroups: [[CellBoundaryPoint]],
        seedCellTypes: Set<String>? = nil,
        manualParameters: RegionParameters? = nil
    ) {
        let assignmentResult = cellTypeAssignmentResult ?? OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        let regionResult = regionAnalysisResult ?? OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        guard let assignmentResult else {
            statusMessage = "Run cell-type assignment before saving an adjusted ROI."
            return
        }
        guard let regionResult else {
            statusMessage = "Run region analysis before saving an adjusted ROI."
            return
        }
        guard let token = beginOperation(.region, status: "Saving adjusted ROI...") else { return }
        let outputFolder = outputFolder
        let pixelSize = pixelSize
        let snapshot = resourceSnapshot
        let automaticCPUAllocation = automaticCPUAllocationPercent
        let automaticGPUAllocation = automaticGPUAllocationPercent
        let currentOverlayImage = overlayImage
        let baseRegionIDs = Set(regionResult.regions.map(\.id))

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                await MainActor.run {
                    try? store.writeCurrentConfiguration()
                }
                let adjustedResult = try RegionAnalyzer.applyManualEdit(
                    to: regionResult,
                    assignments: assignmentResult.assignments,
                    mode: mode,
                    targetRegionID: targetRegionID,
                    displayName: displayName,
                    polygonGroups: polygonGroups,
                    pixelSize: pixelSize,
                    seedCellTypes: seedCellTypes,
                    manualParameters: manualParameters
                )
                let adjustedRegionIDs = Set(adjustedResult.regions.map(\.id)).subtracting(baseRegionIDs)
                guard !adjustedRegionIDs.isEmpty else {
                    throw SpatialScopeError.message("No adjusted ROI was generated from the selected cells.")
                }
                var savedResult = adjustedResult
                if savedResult.parameters.selectedTypes.isEmpty {
                    let availableRegionTypes = Set(assignmentResult.assignments.map(\.assignedType))
                        .filter { $0 != "Unassigned" && $0 != "Ambiguous" }
                    savedResult.parameters.selectedTypes = availableRegionTypes.sorted {
                        $0.localizedStandardCompare($1) == .orderedAscending
                    }
                }
                savedResult.image = RegionAnalyzer.renderRegionMap(
                    assignments: assignmentResult.assignments,
                    regions: savedResult.regions,
                    width: savedResult.width,
                    height: savedResult.height,
                    parameters: savedResult.parameters,
                    cellTypeMask: assignmentResult.cellTypeMask,
                    cellTypeIDByName: assignmentResult.cellTypeIDByName
                )
                savedResult.statsImage = RegionAnalyzer.renderDominantCountsPlot(counts: savedResult.dominantCounts)
                try token.checkCancellation()
                try OutputWriter.writeRegionAnalysisOutputs(
                    result: savedResult,
                    outputFolder: outputFolder,
                    assignments: assignmentResult.assignments,
                    cellTypeMask: assignmentResult.cellTypeMask,
                    cellTypeIDByName: assignmentResult.cellTypeIDByName,
                    overlayImage: currentOverlayImage
                )
                try OutputWriter.writeResourceMetadata(
                    outputFolder: outputFolder,
                    section: "region_analysis",
                    cpuAllocationPercent: automaticCPUAllocation,
                    gpuAllocationPercent: automaticGPUAllocation,
                    snapshot: snapshot
                )
                try token.checkCancellation()
                let savedRegionResult = savedResult
                await MainActor.run {
                    store.regionAnalysisResult = savedRegionResult
                    store.cellDistributionResult = nil
                    store.persistFolders()
                    store.refreshOutputs()
                    store.finishOperation(status: "Adjusted ROI saved: \(displayName). Existing ROI boundaries were preserved and downstream boundary lists were refreshed.")
                    store.logger.info("Saved manual region adjustment with mode \(mode.rawValue, privacy: .public)")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Manual region adjustment failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    func runCellDistributionAnalysis(outputMode: CellDistributionOutputMode = .regionMasksAndDensity) {
        selectedCellDistributionOutputMode = outputMode
        let assignmentResult = cellTypeAssignmentResult ?? OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        let regionResult = regionAnalysisResult ?? OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        let neighborhoodResult = neighborhoodAnalysisResult ?? OutputWriter.loadNeighborhoodAnalysisResult(outputFolder: outputFolder)
        guard let assignmentResult else {
            statusMessage = "Run cell-type assignment before cell distribution analysis."
            return
        }
        if outputMode != .cellDensity, regionResult == nil {
            statusMessage = "Run region analysis before cell distribution analysis."
            return
        }
        if outputMode == .cellClusterDistribution, neighborhoodResult == nil {
            statusMessage = "Run neighborhood analysis before cell cluster distribution."
            return
        }
        guard pixelSize != nil else {
            statusMessage = "Set figure resolution in Inputs before running Cell Distribution analysis."
            return
        }
        guard let token = beginOperation(.cellDistribution, status: "Running cell distribution analysis...") else { return }
        let outputFolder = outputFolder
        let bandWidthUm = densityBandWidthUm
        let boundaryChoices = OutputWriter.loadCellDistributionBoundaryChoices(outputFolder: outputFolder)
        let selectedBoundaryChoices = resolvedCellDistributionBoundaryChoices(from: boundaryChoices, mode: outputMode)
        let selectedBoundaryLabels = selectedBoundaryChoices.isEmpty
            ? (regionResult.map { resolvedCellDistributionBoundaryLabels(from: $0.regions, mode: outputMode) } ?? [])
            : selectedBoundaryChoices.map(\.label)
        let selectedBoundaryMaskPaths = selectedBoundaryChoices.map(\.maskPath)
        let selectedCellTypes = resolvedCellDistributionCellTypes(assignments: assignmentResult.assignments)
        let selectedClusterLabels = resolvedCellDistributionClusterLabels(from: neighborhoodResult?.clusterCounts ?? [])

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                await MainActor.run {
                    try? store.writeCurrentConfiguration()
                }
                try token.checkCancellation()
                try OutputWriter.runStreamlitCellDistributionExport(
                    outputFolder: outputFolder,
                    mode: outputMode,
                    selectedBoundaryLabels: selectedBoundaryLabels,
                    selectedCellTypes: selectedCellTypes,
                    selectedClusterLabels: selectedClusterLabels,
                    bandWidthUm: bandWidthUm,
                    selectedBoundaryMaskPaths: selectedBoundaryMaskPaths
                )
                guard let displayResult = OutputWriter.loadCellDistributionResult(outputFolder: outputFolder) else {
                    throw SpatialScopeError.message("Cell Distribution export finished but no readable result was found.")
                }
                await MainActor.run {
                    store.cellDistributionResult = displayResult
                    store.persistFolders()
                    store.refreshOutputs()
                    store.finishOperation(status: "Cell distribution complete: \(displayResult.regionSummaries.count) regions, \(displayResult.totalCells) cells.")
                    store.logger.info("Cell distribution generated \(displayResult.regionSummaries.count, privacy: .public) region summaries")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Cell distribution failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    private func resolvedCellDistributionRegionIDs(from regions: [RegionROI]) -> Set<Int> {
        let available = Set(regions.map(\.id))
        let explicit = cellDistributionSelectedRegionIDs.intersection(available)
        if !explicit.isEmpty {
            return explicit
        }
        if let first = regions.sorted(by: { $0.id < $1.id }).first?.id {
            return [first]
        }
        return []
    }

    private func resolvedCellDistributionBoundaryLabels(from regions: [RegionROI], mode: CellDistributionOutputMode) -> [String] {
        let sortedRegions = regions.sorted { $0.id < $1.id }
        let selectedIDs: Set<Int>
        if mode == .cellClusterDistribution {
            let available = Set(sortedRegions.map(\.id))
            let explicit = cellDistributionSelectedRegionIDs.intersection(available)
            selectedIDs = explicit.isEmpty
                ? Set(sortedRegions.prefix(min(3, sortedRegions.count)).map(\.id))
                : explicit
        } else {
            selectedIDs = resolvedCellDistributionRegionIDs(from: sortedRegions)
        }
        return sortedRegions
            .filter { selectedIDs.contains($0.id) }
            .compactMap { region in
                let label = (region.sourceType ?? region.dominantType).trimmingCharacters(in: .whitespacesAndNewlines)
                return label.isEmpty ? nil : label
            }
    }

    private func resolvedCellDistributionBoundaryChoices(
        from choices: [CellDistributionBoundaryChoice],
        mode: CellDistributionOutputMode
    ) -> [CellDistributionBoundaryChoice] {
        let sortedChoices = choices.sorted {
            if $0.id == $1.id {
                return $0.label.localizedStandardCompare($1.label) == .orderedAscending
            }
            return $0.id < $1.id
        }
        guard !sortedChoices.isEmpty else { return [] }
        let available = Set(sortedChoices.map(\.id))
        let explicit = cellDistributionSelectedRegionIDs.intersection(available)
        let selectedIDs: Set<Int>
        if explicit.isEmpty {
            if mode == .cellClusterDistribution {
                selectedIDs = Set(sortedChoices.prefix(min(3, sortedChoices.count)).map(\.id))
            } else if let firstID = sortedChoices.first?.id {
                selectedIDs = [firstID]
            } else {
                selectedIDs = []
            }
        } else {
            selectedIDs = explicit
        }
        return sortedChoices.filter { selectedIDs.contains($0.id) }
    }

    private func resolvedCellDistributionCellTypes(assignments: [CellTypeAssignment]) -> [String] {
        let selected = cellDistributionSelectedCellTypes
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        if !selected.isEmpty {
            return selected
        }
        let configured = (OutputWriter.loadCellTypeConfig(outputFolder: outputFolder) ?? cellTypes)
            .map { $0.name.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        if !configured.isEmpty {
            return configured
        }
        let assigned = assignments
            .map(\.assignedType)
            .filter { $0 != "Unassigned" && $0 != "Ambiguous" && !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            .sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        return assigned
    }

    private func resolvedCellDistributionClusterLabels(from clusters: [NeighborhoodClusterCount]) -> [String] {
        let sortedClusters = clusters.sorted { $0.clusterID < $1.clusterID }
        let available = Set(sortedClusters.map(\.clusterID))
        let selectedIDs = cellDistributionSelectedClusterIDs.intersection(available)
        return sortedClusters
            .filter { selectedIDs.isEmpty || selectedIDs.contains($0.clusterID) }
            .map(\.clusterLabel)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    func runDistanceAnalysis() {
        let assignmentResult = cellTypeAssignmentResult ?? OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        let regionResult = regionAnalysisResult ?? OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)
        guard let assignmentResult else {
            statusMessage = "Run cell-type assignment before distance analysis."
            return
        }
        guard let regionResult else {
            statusMessage = "Run region analysis before distance analysis."
            return
        }
        guard let token = beginOperation(.distance, status: "Running native distance analysis...") else { return }
        let outputFolder = outputFolder
        let pixelSize = pixelSize
        let snapshot = resourceSnapshot
        let automaticCPUAllocation = automaticCPUAllocationPercent
        let automaticGPUAllocation = automaticGPUAllocationPercent

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                await MainActor.run {
                    try? store.writeCurrentConfiguration()
                }
                let result = try DistanceAnalyzer.run(
                    assignments: assignmentResult.assignments,
                    regions: regionResult.regions,
                    pixelSize: pixelSize,
                    canvasWidth: assignmentResult.width,
                    canvasHeight: assignmentResult.height,
                    cpuAllocationPercent: automaticCPUAllocation
                )
                try token.checkCancellation()
                try OutputWriter.writeDistanceAnalysisOutputs(
                    result: result,
                    regions: regionResult.regions,
                    outputFolder: outputFolder
                )
                try OutputWriter.writeResourceMetadata(
                    outputFolder: outputFolder,
                    section: "distance_analysis",
                    cpuAllocationPercent: automaticCPUAllocation,
                    gpuAllocationPercent: automaticGPUAllocation,
                    snapshot: snapshot
                )
                await MainActor.run {
                    store.distanceAnalysisResult = result
                    store.persistFolders()
                    store.refreshOutputs()
                    store.finishOperation(status: "Distance analysis complete: \(result.nearestDistances.count) nearest-neighbor rows, \(result.boundaryDistances.count) boundary rows.")
                    store.logger.info("Distance analysis generated \(result.nearestDistances.count, privacy: .public) nearest-neighbor rows")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Distance analysis failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    func runNearestNeighborDistanceAnalysis(targetType: String, queryTypes: [String]) {
        let assignmentResult = cellTypeAssignmentResult ?? OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        guard let assignmentResult else {
            statusMessage = "Run cell-type assignment before nearest-neighbor distance analysis."
            return
        }
        let targetType = targetType.trimmingCharacters(in: .whitespacesAndNewlines)
        let queryTypes = queryTypes
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard !targetType.isEmpty, !queryTypes.isEmpty else {
            statusMessage = "Select a target cell type and at least one query cell type."
            return
        }
        guard let token = beginOperation(.distance, status: "Computing nearest-neighbor distances...") else { return }
        let outputFolder = outputFolder
        let pixelSize = pixelSize
        let snapshot = resourceSnapshot
        let automaticCPUAllocation = automaticCPUAllocationPercent
        let automaticGPUAllocation = automaticGPUAllocationPercent
        let priorResult = distanceAnalysisResult ?? OutputWriter.loadDistanceAnalysisResult(outputFolder: outputFolder)
        let regionResult = regionAnalysisResult ?? OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                await MainActor.run {
                    try? store.writeCurrentConfiguration()
                }
                let nearest = try DistanceAnalyzer.runNearestNeighborAnalysis(
                    assignments: assignmentResult.assignments,
                    targetType: targetType,
                    queryTypes: queryTypes,
                    pixelSize: pixelSize,
                    canvasWidth: assignmentResult.width,
                    canvasHeight: assignmentResult.height
                )
                try token.checkCancellation()
                let result = Self.distanceResultByReplacingNearest(in: priorResult, with: nearest)
                try OutputWriter.writeDistanceAnalysisOutputs(
                    result: result,
                    regions: regionResult?.regions ?? [],
                    outputFolder: outputFolder
                )
                try OutputWriter.writeResourceMetadata(
                    outputFolder: outputFolder,
                    section: "distance_analysis",
                    cpuAllocationPercent: automaticCPUAllocation,
                    gpuAllocationPercent: automaticGPUAllocation,
                    snapshot: snapshot
                )
                await MainActor.run {
                    store.distanceAnalysisResult = result
                    store.persistFolders()
                    store.refreshOutputs()
                    store.finishOperation(status: "Nearest-neighbor distance analysis complete: \(nearest.nearestDistances.count) rows.")
                    store.logger.info("Nearest-neighbor distance analysis generated \(nearest.nearestDistances.count, privacy: .public) rows")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Nearest-neighbor distance analysis failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    func runBoundaryDistanceAnalysis(
        boundaryChoice: CellDistributionBoundaryChoice,
        queryTypes: [String],
        regionFilter: DistanceBoundaryRegionFilter
    ) {
        let assignmentResult = cellTypeAssignmentResult ?? OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder)
        guard let assignmentResult else {
            statusMessage = "Run cell-type assignment before cell-to-boundary distance analysis."
            return
        }
        let queryTypes = queryTypes
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard !queryTypes.isEmpty else {
            statusMessage = "Select at least one query cell type."
            return
        }
        let boundaryURL = URL(fileURLWithPath: boundaryChoice.maskPath)
        guard FileManager.default.fileExists(atPath: boundaryURL.path) else {
            statusMessage = "Selected boundary mask file was not found."
            return
        }
        guard let token = beginOperation(.distance, status: "Computing cell-to-boundary distances...") else { return }
        let outputFolder = outputFolder
        let pixelSize = pixelSize
        let snapshot = resourceSnapshot
        let automaticCPUAllocation = automaticCPUAllocationPercent
        let automaticGPUAllocation = automaticGPUAllocationPercent
        let priorResult = distanceAnalysisResult ?? OutputWriter.loadDistanceAnalysisResult(outputFolder: outputFolder)
        let regionResult = regionAnalysisResult ?? OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder)

        currentTask = Task.detached(priority: .userInitiated) { [store = self] in
            do {
                await MainActor.run {
                    try? store.writeCurrentConfiguration()
                }
                let boundary = try DistanceAnalyzer.runBoundaryDistanceAnalysis(
                    assignments: assignmentResult.assignments,
                    boundaryMaskURL: boundaryURL,
                    boundaryID: boundaryChoice.id,
                    boundaryName: boundaryChoice.label,
                    queryTypes: queryTypes,
                    regionFilter: regionFilter,
                    pixelSize: pixelSize,
                    canvasWidth: assignmentResult.width,
                    canvasHeight: assignmentResult.height
                )
                try token.checkCancellation()
                let result = Self.distanceResultByReplacingBoundary(in: priorResult, with: boundary)
                try OutputWriter.writeDistanceAnalysisOutputs(
                    result: result,
                    regions: regionResult?.regions ?? [],
                    outputFolder: outputFolder
                )
                try OutputWriter.writeResourceMetadata(
                    outputFolder: outputFolder,
                    section: "distance_analysis",
                    cpuAllocationPercent: automaticCPUAllocation,
                    gpuAllocationPercent: automaticGPUAllocation,
                    snapshot: snapshot
                )
                await MainActor.run {
                    store.distanceAnalysisResult = result
                    store.persistFolders()
                    store.refreshOutputs()
                    store.finishOperation(status: "Cell-to-boundary distance analysis complete: \(boundary.boundaryDistances.count) rows.")
                    store.logger.info("Cell-to-boundary distance analysis generated \(boundary.boundaryDistances.count, privacy: .public) rows")
                }
            } catch is CancellationError {
                await MainActor.run {
                    store.finishOperationAfterCancellation()
                }
            } catch {
                await MainActor.run {
                    store.finishOperation(status: error.localizedDescription)
                    store.logger.error("Cell-to-boundary distance analysis failed: \(error.localizedDescription, privacy: .public)")
                }
            }
        }
    }

    nonisolated private static func distanceResultByReplacingNearest(
        in prior: DistanceAnalysisResult?,
        with nearest: DistanceAnalysisResult
    ) -> DistanceAnalysisResult {
        guard let prior else { return nearest }
        return DistanceAnalysisResult(
            nearestDistances: nearest.nearestDistances,
            boundaryDistances: prior.boundaryDistances,
            nearestTTests: nearest.nearestTTests,
            boundaryTTests: prior.boundaryTTests,
            summaries: nearest.summaries + prior.summaries.filter { $0.metric.contains(" to ") && !$0.metric.hasPrefix(nearest.nearestTargetType ?? "") },
            image: nearest.image,
            nearestHistogramImage: nearest.nearestHistogramImage,
            boundaryHistogramImage: prior.boundaryHistogramImage,
            width: nearest.width,
            height: nearest.height,
            nearestTargetType: nearest.nearestTargetType,
            nearestQueryTypes: nearest.nearestQueryTypes,
            boundaryName: prior.boundaryName,
            boundaryQueryTypes: prior.boundaryQueryTypes,
            boundaryFilter: prior.boundaryFilter
        )
    }

    nonisolated private static func distanceResultByReplacingBoundary(
        in prior: DistanceAnalysisResult?,
        with boundary: DistanceAnalysisResult
    ) -> DistanceAnalysisResult {
        guard let prior else { return boundary }
        return DistanceAnalysisResult(
            nearestDistances: prior.nearestDistances,
            boundaryDistances: boundary.boundaryDistances,
            nearestTTests: prior.nearestTTests,
            boundaryTTests: boundary.boundaryTTests,
            summaries: prior.summaries.filter { !$0.metric.contains(" to \(boundary.boundaryName ?? "")") } + boundary.summaries,
            image: boundary.image,
            nearestHistogramImage: prior.nearestHistogramImage,
            boundaryHistogramImage: boundary.boundaryHistogramImage,
            width: boundary.width,
            height: boundary.height,
            nearestTargetType: prior.nearestTargetType,
            nearestQueryTypes: prior.nearestQueryTypes,
            boundaryName: boundary.boundaryName,
            boundaryQueryTypes: boundary.boundaryQueryTypes,
            boundaryFilter: boundary.boundaryFilter
        )
    }

    func runStagedSection(_ section: AnalysisSection) {
        guard !isBusy else { return }
        isBusy = true
        defer { isBusy = false }

        do {
            let message = "\(section.title) is staged; its native analysis engine is not active yet."
            try writeCurrentConfiguration()
            try OutputWriter.writeStagedAnalysisManifest(
                outputFolder: outputFolder,
                sectionKey: section.outputSectionKey,
                sectionTitle: section.title,
                message: message,
                parameters: stagedParameters(for: section),
                cpuAllocationPercent: automaticCPUAllocationPercent,
                gpuAllocationPercent: automaticGPUAllocationPercent,
                snapshot: resourceSnapshot
            )
            try OutputWriter.writeResourceMetadata(
                outputFolder: outputFolder,
                section: section.outputSectionKey,
                cpuAllocationPercent: automaticCPUAllocationPercent,
                gpuAllocationPercent: automaticGPUAllocationPercent,
                snapshot: resourceSnapshot
            )
            persistFolders()
            refreshOutputs()
            statusMessage = "\(message) Manifest and resource metadata saved."
            logger.info("Saved staged analysis manifest for \(section.title, privacy: .public)")
        } catch {
            statusMessage = error.localizedDescription
            logger.error("Staged analysis manifest failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    func addCellType() {
        let index = cellTypes.count
        cellTypes.append(
            CellTypeDefinition(
                name: "Cell type \(index + 1)",
                colorHex: ColorPalette.color(at: index + 7),
                allPositiveMarkers: GeneratedMarkerNames.nuclearSegmentationSignal
            )
        )
    }

    private static func withDefaultNucleusMarker(_ cellTypes: [CellTypeDefinition]) -> [CellTypeDefinition] {
        cellTypes.map { definition in
            var copy = definition
            var markers = markerList(copy.allPositiveMarkers).map { marker in
                marker == GeneratedMarkerNames.legacyNuclearSegmentationSignal
                    ? GeneratedMarkerNames.nuclearSegmentationSignal
                    : marker
            }
            if !markers.contains(GeneratedMarkerNames.nuclearSegmentationSignal) {
                markers.insert(GeneratedMarkerNames.nuclearSegmentationSignal, at: 0)
            }
            copy.allPositiveMarkers = markers.joined(separator: ", ")
            return copy
        }
    }

    private static func markerList(_ text: String) -> [String] {
        text.split { char in
            char == "," || char == ";" || char.isNewline
        }
        .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
    }

    private static func canonicalMarkerName(_ text: String) -> String {
        let canonical = text.lowercased().filter { $0.isLetter || $0.isNumber }
        if canonical == "nuclearsegmentationsignal" {
            return "nucleus"
        }
        return canonical
    }

    func removeCellType(id: UUID) {
        cellTypes.removeAll { $0.id == id }
    }

    private func guessNuclearChannelID() -> UUID? {
        let preferredTokens = ["dapi", "hoechst", "nuclei", "nucleus", "nuclear", "ir191", "ir193"]
        return channels.first { channel in
            let text = (channel.fileName + " " + channel.marker).lowercased()
            return preferredTokens.contains { text.contains($0) }
        }?.id ?? channels.first?.id
    }

    private func selectedNucleusChannel() -> ChannelConfig? {
        channels.first { $0.id == nucleusChannelID } ?? channels.first
    }

    private func stagedParameters(for section: AnalysisSection) -> [String: String] {
        switch section {
        case .neighborhood:
            return [
                "neighborhoodGridUm": String(format: "%.3f", neighborhoodGridUm)
            ]
        case .region:
            return [
                "closeUm": String(format: "%.3f", regionParameters.closeUm),
                "dilateUm": String(format: "%.3f", regionParameters.dilateUm),
                "minAreaUm2": String(format: "%.3f", regionParameters.minAreaUm2),
                "minCells": "\(regionParameters.minCells)",
                "contourDownsample": "\(regionParameters.contourDownsample)",
                "lineWidth": String(format: "%.3f", regionParameters.lineWidth),
                "lineStyle": regionParameters.lineStyle,
                "boundaryColor": regionParameters.boundaryColor,
                "selectedTypes": regionParameters.selectedTypes.joined(separator: "; "),
                "useTypeColors": "\(regionParameters.useTypeColors)"
            ]
        case .cellDistribution:
            return [
                "densityBandWidthUm": String(format: "%.3f", densityBandWidthUm)
            ]
        case .distance:
            return [
                "nearestNeighborDistances": "staged",
                "cellToBoundaryDistances": "staged"
            ]
        default:
            return [:]
        }
    }

    private func bestNucleiScanRecord(in records: [NucleiScanRecord]) -> NucleiScanRecord? {
        records.max {
            if $0.count == $1.count { return $0.comboIndex > $1.comboIndex }
            return $0.count < $1.count
        }
    }

    private func bestAssignmentScanRecord(in records: [AssignmentScanRecord]) -> AssignmentScanRecord? {
        records.min {
            if $0.unresolvedCount != $1.unresolvedCount { return $0.unresolvedCount < $1.unresolvedCount }
            if $0.ambiguousCount != $1.ambiguousCount { return $0.ambiguousCount < $1.ambiguousCount }
            if $0.unassignedCount != $1.unassignedCount { return $0.unassignedCount < $1.unassignedCount }
            if $0.assignedCount != $1.assignedCount { return $0.assignedCount > $1.assignedCount }
            return $0.comboIndex < $1.comboIndex
        }
    }

    private func updateAssignmentScreeningBands(randomize: Bool) {
        let bandCount = min(max(assignmentScreeningBandCount, 1), 12)
        let allBands = Array(0..<bandCount)
        switch assignmentScreeningSubsetMode {
        case .randomThree:
            let current = assignmentScreeningSelectedBandIndices
                .filter { allBands.contains($0) }
            if randomize || current.count != min(3, bandCount) {
                assignmentScreeningSelectedBandIndices = Array(allBands.shuffled().prefix(min(3, bandCount))).sorted()
            } else {
                assignmentScreeningSelectedBandIndices = current.sorted()
            }
        case .oddBands:
            assignmentScreeningSelectedBandIndices = allBands.filter { $0.isMultiple(of: 2) }
        case .evenBands:
            assignmentScreeningSelectedBandIndices = allBands.filter { !$0.isMultiple(of: 2) }
        }
    }

    private func persistFolders() {
        defaults.set(inputFolder.path, forKey: inputFolderKey)
        defaults.set(outputFolder.path, forKey: outputFolderKey)
    }

    private func writeCurrentConfiguration() throws {
        try OutputWriter.writeConfiguration(
            inputFolder: inputFolder,
            outputFolder: outputFolder,
            channels: channels,
            overlayChannels: selectedOverlayChannels,
            whiteChannel: whiteChannel,
            whiteWeight: whiteWeight,
            pixelSize: pixelSize,
            figureSizeUm: xUm > 0 && yUm > 0 ? (xUm, yUm) : nil,
            figureSizePx: xPx > 0 && yPx > 0 ? (xPx, yPx) : nil,
            nucleusChannel: selectedNucleusChannel(),
            nucleiRunMode: nucleiRunMode,
            nucleiParameters: nucleiParameters,
            nucleiScanCombinationBudget: nucleiScanPlannedCombinationCount,
            assignmentRunMode: assignmentRunMode,
            assignmentParameters: assignmentParameters,
            assignmentScanCombinationBudget: assignmentScanPlannedCombinationCount,
            assignmentScreeningBandCount: assignmentScreeningBandCount,
            assignmentScreeningSubsetMode: assignmentScreeningSubsetMode,
            cpuAllocationPercent: cpuAllocationPercent,
            gpuAllocationPercent: 0
        )
    }

    private func autoImportPreviousRun() {
        var importedPreviousRun = false

        if let loaded = OutputWriter.loadConfiguration(outputFolder: outputFolder) {
            importedPreviousRun = true
            inputFolder = loaded.inputFolder
            outputFolder = loaded.outputFolder
            channels = loaded.channels
            whiteWeight = loaded.whiteWeight
            if let runMode = loaded.nucleiRunMode {
                nucleiRunMode = runMode
            }
            if let importedParams = loaded.nucleiParameters {
                nucleiParameters = importedParams
            }
            if let scanBudget = loaded.nucleiScanCombinationBudget {
                nucleiScanCombinationBudget = Self.clampedNucleiScanBudget(scanBudget)
            }
            if let cpu = loaded.cpuAllocationPercent {
                cpuAllocationPercent = min(max(cpu, 10), 100)
            }
            gpuAllocationPercent = 0
            if let figureSizeUm = loaded.figureSizeUm, let figureSizePx = loaded.figureSizePx {
                xUm = figureSizeUm.0
                yUm = figureSizeUm.1
                xPx = figureSizePx.0
                yPx = figureSizePx.1
            } else if let pixelSize = loaded.pixelSize {
                xUm = pixelSize.0
                xPx = 1
                yUm = pixelSize.1
                yPx = 1
            }
            if let loadedAssignmentRunMode = loaded.assignmentRunMode {
                assignmentRunMode = loadedAssignmentRunMode
            }
            if let loadedAssignmentParameters = loaded.assignmentParameters {
                assignmentParameters = loadedAssignmentParameters
                assignmentParameterPanelRevision += 1
            }
            if let loadedAssignmentBudget = loaded.assignmentScanCombinationBudget {
                assignmentScanCombinationBudget = min(max(loadedAssignmentBudget, 10), assignmentScanTotalCombinationCount)
            }
            if let loadedBandCount = loaded.assignmentScreeningBandCount {
                assignmentScreeningBandCount = min(max(loadedBandCount, 5), 6)
            }
            if let loadedSubsetMode = loaded.assignmentScreeningSubsetMode {
                assignmentScreeningSubsetMode = loadedSubsetMode
            }
            if let whiteName = loaded.whiteChannelName {
                whiteChannelID = channels.first { $0.channelName == whiteName }?.id
            }
            if let nucleusName = loaded.nucleusChannelName {
                nucleusChannelID = channels.first { $0.channelName == nucleusName }?.id
            }
            if nucleusChannelID == nil {
                nucleusChannelID = guessNuclearChannelID()
            }
        }

        overlayImage = OutputWriter.loadImage(outputFolder: outputFolder, section: "overlay", name: "overlay.png")
        if overlayImage != nil {
            importedPreviousRun = true
        }
        splitImage = OutputWriter.loadImage(outputFolder: outputFolder, section: "overlay", name: "split_channels.png")
        if splitImage != nil {
            importedPreviousRun = true
        }
        if let loadedCellTypes = OutputWriter.loadCellTypeConfig(outputFolder: outputFolder), !loadedCellTypes.isEmpty {
            cellTypes = Self.withDefaultNucleusMarker(loadedCellTypes)
            importedPreviousRun = true
        }
        if let nucleiImage = OutputWriter.loadImage(outputFolder: outputFolder, section: "nuclei", name: "nuclei_segmentation.png") {
            importedPreviousRun = true
            let paramsURL = OutputWriter.sectionURL("nuclei", outputFolder: outputFolder)
                .appendingPathComponent("nuclei_segmentation_parameters.json")
            let params = (try? Data(contentsOf: paramsURL)).flatMap { try? JSONDecoder().decode(NucleiParameters.self, from: $0) } ?? nucleiParameters
            nucleiParameters = params
            let detections = OutputWriter.loadNucleiSummary(outputFolder: outputFolder)
            nucleiResult = NucleiSegmentationResult(
                count: detections.count,
                params: params,
                channelName: channels.first { $0.id == nucleusChannelID }?.channelName ?? "nucleus",
                image: nucleiImage,
                detections: detections,
                labelMap: OutputWriter.loadNucleiLabelMap(outputFolder: outputFolder)
            )
        }

        let scanURL = OutputWriter.sectionURL("nuclei", outputFolder: outputFolder)
            .appendingPathComponent("nuclei_parameter_scan_results.json")
        if let data = try? Data(contentsOf: scanURL),
           let records = try? JSONDecoder().decode([NucleiScanRecord].self, from: data) {
            nucleiScanResults = records
            selectedNucleiScanCombo = bestNucleiScanRecord(in: records)?.comboIndex
            importedPreviousRun = true
        }

        if let assignmentResult = OutputWriter.loadCellTypeAssignmentResult(outputFolder: outputFolder) {
            cellTypeAssignmentResult = assignmentResult
            assignmentParameters = assignmentResult.parameters
            importedPreviousRun = true
        }

        if let neighborhoodResult = OutputWriter.loadNeighborhoodAnalysisResult(outputFolder: outputFolder) {
            neighborhoodAnalysisResult = neighborhoodResult
            neighborhoodGridUm = neighborhoodResult.gridSizeUm
            importedPreviousRun = true
        }

        if let regionResult = OutputWriter.loadRegionAnalysisResult(outputFolder: outputFolder) {
            regionAnalysisResult = regionResult
            regionParameters = regionResult.parameters
            importedPreviousRun = true
        }

        if let distributionResult = OutputWriter.loadCellDistributionResult(outputFolder: outputFolder) {
            cellDistributionResult = distributionResult
            densityBandWidthUm = distributionResult.bandWidthUm
            importedPreviousRun = true
        }

        if let distanceResult = OutputWriter.loadDistanceAnalysisResult(outputFolder: outputFolder) {
            distanceAnalysisResult = distanceResult
            importedPreviousRun = true
        }

        persistFolders()
        refreshOutputs()
        statusMessage = importedPreviousRun || !outputFiles.isEmpty ? "Imported previous output folder." : "Ready"
    }

    private static func clampedNucleiScanBudget(_ value: Int) -> Int {
        min(max(value, 10), NucleiSegmenter.advancedSearchSpaceSize)
    }

    private static func durationText(seconds: Double) -> String {
        let safeSeconds = max(0, seconds)
        if safeSeconds < 90 {
            return "\(Int(ceil(safeSeconds))) sec"
        }
        if safeSeconds < 3600 {
            let minutes = Int(safeSeconds / 60)
            let seconds = Int(safeSeconds.rounded()) % 60
            return "\(minutes) min \(seconds) sec"
        }
        let hours = Int(safeSeconds / 3600)
        let minutes = Int((safeSeconds.truncatingRemainder(dividingBy: 3600)) / 60)
        return "\(hours) hr \(minutes) min"
    }

    private static func compactNumber(_ value: Double) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.minimumFractionDigits = 0
        formatter.maximumFractionDigits = 4
        formatter.usesGroupingSeparator = false
        return formatter.string(from: NSNumber(value: value)) ?? "\(value)"
    }

    private static func renderNeighborhoodTiles(_ tiles: [NeighborhoodTile], width: Int, height: Int) -> NSImage {
        let image = NSImage(size: NSSize(width: max(1, width), height: max(1, height)))
        image.lockFocus()
        NSColor.black.setFill()
        NSRect(x: 0, y: 0, width: width, height: height).fill()
        for tile in tiles where isDisplayableNeighborhoodTile(tile) {
            let color = NSColor(hex: tile.colorHex) ?? .systemGray
            color.setFill()
            NSRect(
                x: tile.xPx,
                y: Double(height) - tile.yPx - tile.heightPx,
                width: tile.widthPx,
                height: tile.heightPx
            ).fill()
        }
        image.unlockFocus()
        return image
    }

    private static func isDisplayableNeighborhoodTile(_ tile: NeighborhoodTile) -> Bool {
        tile.assignedCells > 0
            && tile.dominantType != "Unassigned"
            && tile.dominantType != "Ambiguous"
    }
}
