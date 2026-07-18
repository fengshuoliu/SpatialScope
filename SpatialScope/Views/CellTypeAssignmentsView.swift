import SwiftUI

struct CellTypeAssignmentsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var tab = "Marker Rules"

    var body: some View {
        VStack(spacing: 0) {
            Picker("", selection: $tab) {
                Text("Marker Rules").tag("Marker Rules")
                Text("Screening & Assignment").tag("Screening & Assignment")
            }
            .pickerStyle(.segmented)
            .frame(width: 430)
            .padding(.vertical, 16)
            .frame(maxWidth: .infinity)
            .spatialScopeGlassSurface(cornerRadius: 0, tint: Color.accentColor.opacity(0.025))

            Divider()

            ScrollView {
                if tab == "Marker Rules" {
                    defineCellTypes
                } else {
                    assignmentParameters
                }
            }
        }
    }

    private var defineCellTypes: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Button {
                    store.addCellType()
                } label: {
                    Label("Add Cell Type", systemImage: "plus")
                }
                Button {
                    store.saveCellTypes()
                } label: {
                    Label("Save Cell Types", systemImage: "square.and.arrow.down")
                }
                .spatialScopeProminentButtonStyle()
                Spacer()
            }

            GroupBox("Cell type definitions") {
                VStack(spacing: 10) {
                    HStack(spacing: 12) {
                        Text("Name").frame(width: 150, alignment: .leading)
                        Text("Color").frame(width: 70, alignment: .leading)
                        Text("All positive").frame(maxWidth: .infinity, alignment: .leading)
                        Text("All negative").frame(maxWidth: .infinity, alignment: .leading)
                        Text("Any-positive groups").frame(maxWidth: .infinity, alignment: .leading)
                        Text("").frame(width: 34)
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)

                    ForEach($store.cellTypes) { $cellType in
                        CellTypeDefinitionRow(cellType: $cellType, markerNames: store.cellTypeMarkerOptions) {
                            store.removeCellType(id: cellType.id)
                        }
                        Divider()
                    }
                }
                .padding(6)
            }

            StatusBarView()
        }
        .padding(SpatialScopeDesign.contentPadding)
    }

    private var assignmentParameters: some View {
        VStack(alignment: .leading, spacing: 18) {
            GroupBox("Assignment mode") {
                VStack(alignment: .leading, spacing: 12) {
                    Picker("Mode", selection: $store.assignmentRunMode) {
                        ForEach(AssignmentRunMode.allCases) { mode in
                            Text(mode.title).tag(mode)
                        }
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 260)

                    if store.assignmentRunMode == .screening {
                        ResourceAllocationControl(contextLabel: "Assignment parameter screening")

                        VStack(alignment: .leading, spacing: 12) {
                            ViewThatFits(in: .horizontal) {
                                HStack(spacing: 14) {
                                    assignmentSubsetControls
                                    Spacer()
                                }
                                VStack(alignment: .leading, spacing: 10) {
                                    assignmentSubsetControls
                                }
                            }

                            AssignmentScreeningSubsetDiagram(
                                bandCount: store.assignmentScreeningBandCount,
                                selectedBands: Set(store.assignmentScreeningSelectedBandIndices),
                                configuredWidth: store.xPx,
                                configuredHeight: store.yPx
                            )

                            ViewThatFits(in: .horizontal) {
                                HStack(spacing: 18) {
                                    assignmentFixedParameterToggles
                                    Spacer()
                                }
                                VStack(alignment: .leading, spacing: 8) {
                                    assignmentFixedParameterToggles
                                }
                            }
                            Text("The highlighted slices are the exact vertical image bands used by screening. Parameters that stay fixed are excluded from the search; remaining parameters are sampled broadly, then refined around combinations with fewer Unassigned and Ambiguous cells.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }

                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 12) {
                                assignmentCombinationControls
                                Button {
                                    store.runCellTypeAssignmentScreening()
                                } label: {
                                    Label("Run Advanced Screening", systemImage: "play.fill")
                                }
                                .spatialScopeProminentButtonStyle()
                                .disabled(store.isBusy)
                                Spacer()
                            }

                            VStack(alignment: .leading, spacing: 10) {
                                HStack(spacing: 12) {
                                    assignmentCombinationControls
                                }
                                Button {
                                    store.runCellTypeAssignmentScreening()
                                } label: {
                                    Label("Run Advanced Screening", systemImage: "play.fill")
                                }
                                .spatialScopeProminentButtonStyle()
                                .disabled(store.isBusy)
                            }
                        }

                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 190), alignment: .leading)], alignment: .leading, spacing: 8) {
                            Text("planned \(store.assignmentScanPlannedCombinationCount) / total \(store.assignmentScanTotalCombinationCount)")
                            Label("Estimated \(store.assignmentScanEstimatedTimeText)", systemImage: "clock")
                            Label("\(store.assignmentScanEffectiveWorkerCount) CPU worker(s)", systemImage: "cpu")
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)

                        if !store.assignmentScanResults.isEmpty {
                            StaticAssignmentScanPlotView(
                                records: store.assignmentScanResults,
                                selectedCombo: store.selectedAssignmentScanCombo
                            )
                            .frame(height: 220)

                            if let selected = selectedAssignmentRecord {
                                Text("Selected combo \(selected.comboIndex): \(selected.unresolvedCount) unresolved cells; \(assignmentParameterText(selected.parameters))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                            }

                            Table(store.assignmentScanResults.sorted { $0.comboIndex < $1.comboIndex }) {
                                TableColumn("#") { row in Text("\(row.comboIndex)") }
                                    .width(44)
                                TableColumn("Stage", value: \.stage)
                                    .width(70)
                                TableColumn("Unassigned") { row in Text("\(row.unassignedCount)") }
                                    .width(90)
                                TableColumn("Ambiguous") { row in Text("\(row.ambiguousCount)") }
                                    .width(90)
                                TableColumn("Assigned") { row in Text("\(row.assignedCount)") }
                                    .width(80)
                                TableColumn("Select") { row in
                                    Button("Select") {
                                        store.selectedAssignmentScanCombo = row.comboIndex
                                    }
                                }
                                .width(70)
                            }
                            .frame(minHeight: 220)
                        }
                    }
                }
                .padding(6)
            }

            GroupBox("Main assignment parameters") {
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 430, maximum: 620), spacing: 22, alignment: .top)],
                    alignment: .leading,
                    spacing: 14
                ) {
                    ParameterSlider(
                        title: "R_VORONOI_UM",
                        value: assignmentDoubleBinding(\.rVoronoiUm),
                        range: 0...300,
                        step: 1,
                        description: "Voronoi ownership radius around each nucleus. Increasing it allows marker signal farther from the nucleus to contribute; lowering it keeps assignment closer to the nuclear center and can reduce spillover between crowded cells."
                    )
                    ParameterSlider(
                        title: "R_BUFFER_UM",
                        value: assignmentDoubleBinding(\.rBufferUm),
                        range: 0...300,
                        step: 1,
                        description: "Extra buffer outside the nucleus used when sampling marker intensity. Increasing it captures membrane/cytoplasmic signal but may mix neighboring cells; lowering it is stricter and may miss broader cell boundaries."
                    )
                    ParameterSlider(
                        title: "R_VOTE_UM",
                        value: assignmentDoubleBinding(\.rVoteUm),
                        range: 0...300,
                        step: 1,
                        description: "Marker voting radius for deciding positive markers. Larger values stabilize noisy channels and include peripheral signal; smaller values make assignments more local and sensitive to nuclear-adjacent intensity."
                    )
                    ParameterSlider(
                        title: "TOPHAT_R_UM",
                        value: assignmentDoubleBinding(\.tophatRUm),
                        range: 0...150,
                        step: 1,
                        description: "Background correction radius for marker channels. Increasing it removes broad background and uneven staining more strongly; lowering it preserves diffuse signal but may increase false positives."
                    )
                    ParameterSlider(
                        title: "GAUSS_SIGMA_UM",
                        value: assignmentDoubleBinding(\.gaussSigmaUm),
                        range: 0...75,
                        step: 0.5,
                        description: "Smoothing applied before marker thresholding. Increasing it reduces speckle and isolated hot pixels but can blur small positive cells; lowering it keeps sharp signal and more noise."
                    )
                    VStack(alignment: .leading, spacing: 6) {
                        Picker("Threshold mode", selection: assignmentStringBinding(\.threshMode)) {
                            Text("Global Otsu").tag("global_otsu")
                            Text("Local").tag("local")
                            Text("Yen").tag("yen")
                        }
                        Text("Global Otsu is stable for even backgrounds, Local adapts to regional staining variation, and Yen is usually more permissive for bright sparse signal.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    AssignmentIntegerParameter(
                        title: "Minimum positive-object size (px)",
                        value: assignmentIntBinding(\.minPosObjectSizePx),
                        description: "Increasing this removes small noisy marker objects; lowering it allows small true positives but may add speckle-driven assignments."
                    )
                    AssignmentIntegerParameter(
                        title: "Minimum positive pixels",
                        value: assignmentIntBinding(\.minPosPix),
                        description: "Increasing this makes calls more conservative; lowering it increases sensitivity for weak or small marker-positive cells."
                    )
                }
                .padding(6)
            }
            .id("assignment-main-\(store.assignmentParameterPanelRevision)")

            GroupBox("Ambiguous-cell resolution") {
                VStack(spacing: 12) {
                    Toggle("Resolve ambiguous cells", isOn: assignmentBoolBinding(\.resolveAmbiguous))
                        .toggleStyle(.checkbox)
                    ParameterSlider(title: "Minimum winning probability", value: assignmentDoubleBinding(\.ambiguousMinProbability), range: 0...1, step: 0.01)
                    Text("Higher values require one cell type to dominate the marker evidence; lowering this accepts weaker winners and reduces Ambiguous calls.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: 720, alignment: .leading)
                    ParameterSlider(title: "Minimum probability gap", value: assignmentDoubleBinding(\.ambiguousMinGap), range: 0...1, step: 0.01)
                    Text("Minimum separation between the best and runner-up cell type. Increasing it makes mixed-marker cells more likely to remain Ambiguous; lowering it assigns borderline cells more aggressively.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: 720, alignment: .leading)
                }
                .padding(6)
            }
            .id("assignment-ambiguous-\(store.assignmentParameterPanelRevision)")

            finalAssignmentActionRow

            if let result = store.cellTypeAssignmentResult {
                assignmentResults(result)
            }

            StatusBarView()
        }
        .padding(SpatialScopeDesign.contentPadding)
    }

    private func assignmentResults(_ result: CellTypeAssignmentResult) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            GroupBox("Final assignment map") {
                VStack(alignment: .leading, spacing: 10) {
                    ZoomableImageView(image: result.image, backgroundColor: .black)
                        .frame(maxWidth: .infinity, minHeight: 420, maxHeight: 720)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    HStack {
                        Text("\(result.totalAssigned) assigned of \(result.assignments.count) nuclei")
                            .font(.headline)
                        Spacer()
                        Button {
                            store.selectedSection = .neighborhood
                        } label: {
                            Label("Save and Next", systemImage: "arrow.right.circle.fill")
                        }
                        .spatialScopeProminentButtonStyle()
                    }
                }
                .padding(6)
            }

            GroupBox("Cell type counts") {
                VStack(alignment: .leading, spacing: 12) {
                    ZoomableImageView(image: result.statsImage, backgroundColor: .white)
                        .frame(maxWidth: .infinity, minHeight: 200, maxHeight: 280)
                        .clipShape(RoundedRectangle(cornerRadius: 8))

                    Table(result.counts) {
                        TableColumn("Cell type", value: \.name)
                        TableColumn("Count") { row in
                            Text("\(row.count)")
                        }
                        .width(80)
                    }
                    .frame(minHeight: 240)
                }
                .padding(6)
            }
        }
    }

    @ViewBuilder
    private var assignmentSubsetControls: some View {
        Picker("Screening subset", selection: $store.assignmentScreeningSubsetMode) {
            ForEach(AssignmentScreeningSubsetMode.allCases) { mode in
                Text(mode.title).tag(mode)
            }
        }
        .pickerStyle(.segmented)
        .frame(width: 430)

        Stepper(
            "\(store.assignmentScreeningBandCount) vertical sections",
            value: $store.assignmentScreeningBandCount,
            in: 5...6
        )

        if store.assignmentScreeningSubsetMode == .randomThree {
            Button {
                store.reshuffleAssignmentScreeningBands()
            } label: {
                Label("Shuffle", systemImage: "shuffle")
            }
            .help("Choose a different set of three screening sections")
        }
    }

    @ViewBuilder
    private var assignmentFixedParameterToggles: some View {
        Toggle("Fix Voronoi radius at \(format(store.assignmentParameters.rVoronoiUm)) um", isOn: $store.assignmentScanFixVoronoi)
            .toggleStyle(.checkbox)
        Toggle("Fix buffer radius at \(format(store.assignmentParameters.rBufferUm)) um", isOn: $store.assignmentScanFixBuffer)
            .toggleStyle(.checkbox)
    }

    @ViewBuilder
    private var assignmentCombinationControls: some View {
        Text("Combinations to run")
            .frame(minWidth: 140, alignment: .leading)
        TextField("Combinations", value: $store.assignmentScanCombinationBudget, format: .number)
            .textFieldStyle(.roundedBorder)
            .frame(width: 110)
        Stepper("", value: $store.assignmentScanCombinationBudget, in: 10...store.assignmentScanTotalCombinationCount, step: 10)
            .labelsHidden()
    }

    private var finalAssignmentActionRow: some View {
        GroupBox("Final assignment") {
            VStack(alignment: .leading, spacing: 12) {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) {
                        finalAssignmentSummary
                        Spacer()
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        finalAssignmentSummary
                    }
                }

                HStack(spacing: 10) {
                    Spacer()
                    if let best = bestAssignmentRecord {
                        Button {
                            store.applyAssignmentScanRecord(selectedAssignmentRecord ?? best)
                        } label: {
                            Label("Apply Selected Combo", systemImage: "checkmark.circle")
                        }
                    }

                    Button {
                        store.runCellTypeAssignment()
                    } label: {
                        Label("Run Final Assignment", systemImage: "play.fill")
                    }
                    .spatialScopeProminentButtonStyle()
                    .disabled(store.isBusy)
                }
            }
            .padding(6)
        }
    }

    @ViewBuilder
    private var finalAssignmentSummary: some View {
        if let best = bestAssignmentRecord {
            Label("Suggested combo \(best.comboIndex): \(best.unresolvedCount) unresolved cells, \(best.assignedCount) assigned", systemImage: "star.fill")
                .foregroundStyle(.orange)
                .font(.caption.weight(.semibold))
        } else {
            Text("Run screening or adjust parameters manually before the final assignment. Results appear directly below after the final run.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var bestAssignmentRecord: AssignmentScanRecord? {
        store.assignmentScanResults.min {
            if $0.unresolvedCount != $1.unresolvedCount { return $0.unresolvedCount < $1.unresolvedCount }
            if $0.ambiguousCount != $1.ambiguousCount { return $0.ambiguousCount < $1.ambiguousCount }
            if $0.unassignedCount != $1.unassignedCount { return $0.unassignedCount < $1.unassignedCount }
            if $0.assignedCount != $1.assignedCount { return $0.assignedCount > $1.assignedCount }
            return $0.comboIndex < $1.comboIndex
        }
    }

    private var selectedAssignmentRecord: AssignmentScanRecord? {
        guard let selected = store.selectedAssignmentScanCombo else { return bestAssignmentRecord }
        return store.assignmentScanResults.first { $0.comboIndex == selected } ?? bestAssignmentRecord
    }

    private func assignmentParameterText(_ p: AssignmentParameters) -> String {
        "vor \(format(p.rVoronoiUm)), buffer \(format(p.rBufferUm)), vote \(format(p.rVoteUm)), top \(format(p.tophatRUm)), sigma \(format(p.gaussSigmaUm)), mode \(p.threshMode), minProb \(format(p.ambiguousMinProbability)), gap \(format(p.ambiguousMinGap))"
    }

    private func format(_ value: Double) -> String {
        String(format: "%.2f", value)
    }

    private func assignmentDoubleBinding(_ keyPath: WritableKeyPath<AssignmentParameters, Double>) -> Binding<Double> {
        Binding(
            get: { store.assignmentParameters[keyPath: keyPath] },
            set: { newValue in
                var parameters = store.assignmentParameters
                parameters[keyPath: keyPath] = newValue
                store.assignmentParameters = parameters
            }
        )
    }

    private func assignmentIntBinding(_ keyPath: WritableKeyPath<AssignmentParameters, Int>) -> Binding<Int> {
        Binding(
            get: { store.assignmentParameters[keyPath: keyPath] },
            set: { newValue in
                var parameters = store.assignmentParameters
                parameters[keyPath: keyPath] = newValue
                store.assignmentParameters = parameters
            }
        )
    }

    private func assignmentStringBinding(_ keyPath: WritableKeyPath<AssignmentParameters, String>) -> Binding<String> {
        Binding(
            get: { store.assignmentParameters[keyPath: keyPath] },
            set: { newValue in
                var parameters = store.assignmentParameters
                parameters[keyPath: keyPath] = newValue
                store.assignmentParameters = parameters
            }
        )
    }

    private func assignmentBoolBinding(_ keyPath: WritableKeyPath<AssignmentParameters, Bool>) -> Binding<Bool> {
        Binding(
            get: { store.assignmentParameters[keyPath: keyPath] },
            set: { newValue in
                var parameters = store.assignmentParameters
                parameters[keyPath: keyPath] = newValue
                store.assignmentParameters = parameters
            }
        )
    }
}

private struct AssignmentScreeningSubsetDiagram: View {
    var bandCount: Int
    var selectedBands: Set<Int>
    var configuredWidth: Int
    var configuredHeight: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("Screening area", systemImage: "rectangle.split.3x1")
                    .font(.caption.weight(.semibold))
                Spacer()
                Text(selectedSummary)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 3) {
                ForEach(0..<safeBandCount, id: \.self) { index in
                    Rectangle()
                        .fill(selectedBands.contains(index) ? Color.accentColor.opacity(0.78) : SpatialScopeDesign.subtleFill)
                        .overlay {
                            Text("\(index + 1)")
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(selectedBands.contains(index) ? Color.white : Color.secondary)
                        }
                        .accessibilityLabel("Section \(index + 1)")
                        .accessibilityValue(selectedBands.contains(index) ? "Selected" : "Not selected")
                }
            }
            .frame(width: compactDiagramSize.width, height: compactDiagramSize.height)
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .overlay {
                RoundedRectangle(cornerRadius: 6)
                    .stroke(SpatialScopeDesign.panelBorder, lineWidth: 1)
            }

            Text("The rectangle follows the configured image ratio. Highlighted vertical sections are joined and downsampled for the parameter scan.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: 360, alignment: .leading)
    }

    private var safeBandCount: Int {
        min(max(bandCount, 1), 12)
    }

    private var configuredAspectRatio: CGFloat {
        guard configuredWidth > 0, configuredHeight > 0 else { return 2.2 }
        return min(max(CGFloat(configuredWidth) / CGFloat(configuredHeight), 0.65), 3.5)
    }

    private var compactDiagramSize: CGSize {
        let maxSize = CGSize(width: 360, height: 120)
        if maxSize.width / maxSize.height > configuredAspectRatio {
            return CGSize(width: maxSize.height * configuredAspectRatio, height: maxSize.height)
        }
        return CGSize(width: maxSize.width, height: maxSize.width / configuredAspectRatio)
    }

    private var selectedSummary: String {
        let values = selectedBands
            .filter { $0 >= 0 && $0 < safeBandCount }
            .sorted()
            .map { String($0 + 1) }
            .joined(separator: ", ")
        return "Selected: \(values.isEmpty ? "none" : values)"
    }
}

private struct AssignmentIntegerParameter: View {
    var title: String
    @Binding var value: Int
    var description: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption.weight(.semibold))
            Stepper("\(value)", value: $value, in: 0...50_000)
                .font(.system(.body, design: .monospaced))
            Text(description)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct CellTypeDefinitionRow: View {
    @Binding var cellType: CellTypeDefinition
    var markerNames: [String]
    var remove: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            TextField("Name", text: $cellType.name)
                .textFieldStyle(.roundedBorder)
                .frame(width: 150)

            ColorPicker(
                "",
                selection: colorBinding(
                    getHex: { cellType.colorHex },
                    setHex: { cellType.colorHex = $0 }
                ),
                supportsOpacity: false
            )
            .labelsHidden()
            .frame(width: 70)

            MarkerSelectionMenu(title: "All positive", selection: $cellType.allPositiveMarkers, markerNames: markerNames)
            MarkerSelectionMenu(title: "All negative", selection: $cellType.allNegativeMarkers, markerNames: markerNames)
            MarkerSelectionMenu(title: "Any positive", selection: $cellType.anyPositiveGroups, markerNames: markerNames)

            Button(role: .destructive, action: remove) {
                Image(systemName: "xmark")
            }
            .buttonStyle(.borderless)
            .frame(width: 34)
            .help("Remove cell type")
        }
    }
}

private struct MarkerSelectionMenu: View {
    var title: String
    @Binding var selection: String
    var markerNames: [String]

    var body: some View {
        Menu {
            ForEach(markerNames, id: \.self) { markerName in
                Button {
                    toggle(markerName)
                } label: {
                    HStack {
                        if selectedMarkers.contains(markerName) {
                            Image(systemName: "checkmark")
                        }
                        Text(markerName)
                    }
                }
            }
            Divider()
            Button("Clear") {
                selection = ""
            }
        } label: {
            HStack {
                Text(selection.isEmpty ? title : selection)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Image(systemName: "chevron.down")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 8)
            .frame(minHeight: 28)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(Color.secondary.opacity(0.35))
            )
        }
        .frame(maxWidth: .infinity)
    }

    private var selectedMarkers: [String] {
        selection
            .split { $0 == "," || $0 == ";" || $0.isNewline }
            .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    private func toggle(_ marker: String) {
        var markers = selectedMarkers
        if markers.contains(marker) {
            markers.removeAll { $0 == marker }
        } else {
            markers.append(marker)
        }
        selection = markers.joined(separator: ", ")
    }
}

private struct StaticAssignmentScanPlotView: View {
    var records: [AssignmentScanRecord]
    var selectedCombo: Int?

    var body: some View {
        if let image = AssignmentScanPlotRenderer.render(records: records, selectedCombo: selectedCombo) {
            ZoomableImageView(image: image, backgroundColor: .textBackgroundColor)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        } else {
            EmptyStateView(systemImage: "chart.bar", title: "No screening plot", message: "Run assignment screening first.")
        }
    }
}
