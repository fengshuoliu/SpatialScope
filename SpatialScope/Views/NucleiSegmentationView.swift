import SwiftUI

struct NucleiSegmentationView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                GroupBox("Nucleus channel") {
                    HStack {
                        Picker("Nucleus Channel", selection: $store.nucleusChannelID) {
                            ForEach(store.channels) { channel in
                                Text(channel.channelName).tag(Optional(channel.id))
                            }
                        }
                        .frame(maxWidth: 380)
                        Spacer()
                    }
                    .padding(6)
                }

                GroupBox("Mode") {
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 14) {
                            nucleiModePicker
                            nucleiModeDescription
                            Spacer()
                        }

                        VStack(alignment: .leading, spacing: 10) {
                            nucleiModePicker
                            nucleiModeDescription
                        }
                    }
                    .padding(6)
                }

                if store.nucleiRunMode == .advanced {
                    advancedScanPanel
                }

                GroupBox("Parameters for final run") {
                    VStack(alignment: .leading, spacing: 12) {
                        LazyVGrid(
                            columns: [GridItem(.adaptive(minimum: 430, maximum: 620), spacing: 22, alignment: .top)],
                            alignment: .leading,
                            spacing: 14
                        ) {
                            ParameterSlider(
                                title: "MIN_DIAM_UM",
                                value: $store.nucleiParameters.minDiamUm,
                                range: 0...240,
                                step: 0.5,
                                description: "Minimum nucleus diameter. Increasing it removes small noise but may drop true small nuclei. Check Fixed in advanced scan to keep this value locked."
                            )
                            ParameterSlider(
                                title: "MAX_DIAM_UM",
                                value: $store.nucleiParameters.maxDiamUm,
                                range: 1...320,
                                step: 1,
                                description: "Maximum nucleus diameter. Lower values reject large objects; higher values allow larger nuclei or clumps. Check Fixed in advanced scan to keep this value locked."
                            )
                            ParameterSlider(
                                title: "TOPHAT_RADIUS_UM",
                                value: $store.nucleiParameters.tophatRadiusUm,
                                range: 0...40,
                                step: 0.5,
                                description: "Background correction radius. Larger values remove slow-varying background more strongly; smaller values preserve raw intensity."
                            )
                            ParameterSlider(
                                title: "GAUSS_SIGMA_UM",
                                value: $store.nucleiParameters.gaussSigmaUm,
                                range: 0...10,
                                step: 0.1,
                                description: "Smoothing strength. Larger values reduce noise but blur borders and can merge adjacent nuclei."
                            )
                            ParameterSlider(
                                title: "LOCAL_WIN_UM",
                                value: $store.nucleiParameters.localWinUm,
                                range: 1...240,
                                step: 1,
                                description: "Local background window. Larger values behave more globally; smaller values adapt to local intensity changes."
                            )
                            ParameterSlider(
                                title: "LOCAL_OFFSET",
                                value: $store.nucleiParameters.localOffset,
                                range: -1...1,
                                step: 0.01,
                                description: "Threshold offset. Lower or more negative values usually detect more nuclei; higher values are stricter."
                            )
                            ParameterSlider(
                                title: "H_MAXIMA_UM",
                                value: $store.nucleiParameters.hMaximaUm,
                                range: 0...10,
                                step: 0.05,
                                description: "Peak prominence. Larger values are more conservative; smaller values keep weaker nuclei."
                            )
                            ParameterSlider(
                                title: "SEED_MIN_DIST_UM",
                                value: $store.nucleiParameters.seedMinDistUm,
                                range: 0...20,
                                step: 0.1,
                                description: "Minimum distance between candidate centers. Larger values reduce over-splitting; smaller values allow dense nuclei."
                            )
                            ParameterSlider(
                                title: "WATERSHED_COMPACTNESS",
                                value: $store.nucleiParameters.watershedCompactness,
                                range: 0...10,
                                step: 0.05,
                                description: "Watershed compactness. Larger values make regions more regular; smaller values follow intensity shape more closely."
                            )
                            ParameterSlider(
                                title: "POST_RESPLIT_MULT",
                                value: $store.nucleiParameters.postResplitMult,
                                range: 0...10,
                                step: 0.05,
                                description: "Post-processing split strength. Larger values split touching objects more aggressively; smaller values are more conservative."
                            )
                        }

                        Divider()

                        finalNucleiActionRow
                    }
                    .padding(6)
                }

                if let result = store.nucleiResult {
                    GroupBox("Final nuclei segmentation") {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("\(result.count) nuclei detected from \(result.channelName)")
                                .font(.headline)
                            ImagePreviewPane(title: "Nuclei segmentation", image: result.image, backgroundColor: .black)
                                .frame(minHeight: 680)
                            HStack {
                                Spacer()
                                Button {
                                    store.selectedSection = .cellTypes
                                } label: {
                                    Label("Save and Next", systemImage: "arrow.right.circle.fill")
                                }
                                .spatialScopeProminentButtonStyle()
                            }
                        }
                        .padding(6)
                    }
                }

                StatusBarView()
            }
            .padding(SpatialScopeDesign.contentPadding)
        }
    }

    private var nucleiModePicker: some View {
        Picker("Run mode", selection: $store.nucleiRunMode) {
            ForEach(NucleiRunMode.allCases) { mode in
                Text(mode.title).tag(mode)
            }
        }
        .pickerStyle(.segmented)
        .frame(width: 280)
    }

    private var nucleiModeDescription: some View {
        Text(store.nucleiRunMode == .manual
             ? "Manual mode uses the parameter controls exactly as shown below."
             : "Advanced Screening fixes selected diameters, samples broad ranges, then refines around the highest-count trends.")
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var advancedScanPanel: some View {
        GroupBox("Advanced screening") {
            VStack(alignment: .leading, spacing: 14) {
                Text("The scan samples broad intervals first, keeps high-count trends, then refines nearby combinations. Checked diameter parameters stay fixed; unchecked diameter parameters become part of the search.")
                    .foregroundStyle(.secondary)

                ResourceAllocationControl(contextLabel: "Advanced nuclei screening")

                VStack(alignment: .leading, spacing: 10) {
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 18) {
                            nucleiDiameterToggles
                            Spacer()
                        }
                        VStack(alignment: .leading, spacing: 8) {
                            nucleiDiameterToggles
                        }
                    }

                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 12) {
                            nucleiCombinationControls
                            Spacer()
                        }
                        VStack(alignment: .leading, spacing: 8) {
                            nucleiCombinationControls
                        }
                    }

                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 190), alignment: .leading)], alignment: .leading, spacing: 8) {
                        Label("Estimated \(store.nucleiScanEstimatedTimeText)", systemImage: "clock")
                        Label("\(store.nucleiScanEffectiveWorkerCount) CPU worker(s)", systemImage: "cpu")
                        Text("Model: \(store.nucleiScanSecondsPerCombination, format: .number.precision(.fractionLength(3))) sec/combo at \(store.nucleiScanBenchmarkCPUAllocationPercent, format: .number.precision(.fractionLength(0)))% CPU")
                            .foregroundStyle(.secondary)
                    }
                    .font(.caption)
                }

                HStack {
                    Button {
                        store.runNucleiAdvancedScan()
                    } label: {
                        Label("Run Advanced Screening", systemImage: "play.fill")
                    }
                    .spatialScopeProminentButtonStyle()
                    .disabled(store.isBusy || store.channels.isEmpty)

                    Spacer()
                }

                if !store.nucleiScanResults.isEmpty {
                    StaticNucleiScanPlotView(
                        records: store.nucleiScanResults,
                        selectedCombo: store.selectedNucleiScanCombo
                    )
                        .frame(height: 220)

                    HStack {
                        if let selected = selectedRecord {
                            Text("Selected combo \(selected.comboIndex): \(selected.count) nuclei; \(compactParameterText(selected.params))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        Spacer()
                    }

                    Table(store.nucleiScanResults.sorted { $0.comboIndex < $1.comboIndex }) {
                        TableColumn("#") { record in Text("\(record.comboIndex)") }
                            .width(44)
                        TableColumn("Stage", value: \.stage)
                            .width(70)
                        TableColumn("Nuclei") { record in Text("\(record.count)") }
                            .width(70)
                        TableColumn("Parameters") { record in
                            Text(compactParameterText(record.params))
                                .lineLimit(1)
                        }
                        TableColumn("Select") { record in
                            Button("Select") {
                                store.selectedNucleiScanCombo = record.comboIndex
                            }
                        }
                        .width(70)
                    }
                    .frame(minHeight: 240)
                }
            }
            .padding(6)
        }
    }

    @ViewBuilder
    private var nucleiDiameterToggles: some View {
        Toggle("Fix minimum diameter at \(store.nucleiParameters.minDiamUm, format: .number.precision(.fractionLength(1))) um", isOn: $store.nucleiScanFixMinDiameter)
            .toggleStyle(.checkbox)
        Toggle("Fix maximum diameter at \(store.nucleiParameters.maxDiamUm, format: .number.precision(.fractionLength(1))) um", isOn: $store.nucleiScanFixMaxDiameter)
            .toggleStyle(.checkbox)
    }

    @ViewBuilder
    private var nucleiCombinationControls: some View {
        Text("Combinations to run")
            .frame(minWidth: 140, alignment: .leading)
        TextField("Combinations", value: $store.nucleiScanCombinationBudget, format: .number)
            .textFieldStyle(.roundedBorder)
            .frame(width: 112)
        Stepper(
            "",
            value: $store.nucleiScanCombinationBudget,
            in: 10...store.nucleiScanTotalCombinationCount,
            step: 10
        )
        .labelsHidden()
        Text("planned \(store.nucleiScanPlannedCombinationCount) / total \(store.nucleiScanTotalCombinationCount)")
            .font(.caption)
            .foregroundStyle(.secondary)
    }

    private var finalNucleiActionRow: some View {
        VStack(alignment: .leading, spacing: 12) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) {
                    finalNucleiSummary
                    Spacer()
                }
                VStack(alignment: .leading, spacing: 7) {
                    finalNucleiSummary
                }
            }

            HStack(spacing: 10) {
                Spacer()
                if let selected = selectedRecord {
                    Button {
                        store.applyNucleiScanRecord(selected)
                    } label: {
                        Label("Apply Selected Combo", systemImage: "checkmark.circle")
                    }
                }

                Button {
                    store.runNucleiFinal()
                } label: {
                    Label("Run Final Nuclei Segmentation", systemImage: "play.fill")
                }
                .spatialScopeProminentButtonStyle()
                .disabled(store.isBusy || store.channels.isEmpty)
            }
        }
    }

    @ViewBuilder
    private var finalNucleiSummary: some View {
        Text("Final run saves black-background PNG 300dpi, AI, SVG, parameters JSON, and nuclei_summary.csv.")
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

        if let best = bestRecord {
            Label("Recommended combo \(best.comboIndex): \(best.count) nuclei", systemImage: "star.fill")
                .foregroundStyle(.red)
                .font(.caption.weight(.semibold))
        }
    }

    private var selectedRecord: NucleiScanRecord? {
        guard let selected = store.selectedNucleiScanCombo else { return bestRecord }
        return store.nucleiScanResults.first { $0.comboIndex == selected } ?? bestRecord
    }

    private var bestRecord: NucleiScanRecord? {
        store.nucleiScanResults.max {
            if $0.count == $1.count { return $0.comboIndex > $1.comboIndex }
            return $0.count < $1.count
        }
    }

    private func compactParameterText(_ p: NucleiParameters) -> String {
        "min \(format(p.minDiamUm)), max \(format(p.maxDiamUm)), top \(format(p.tophatRadiusUm)), sigma \(format(p.gaussSigmaUm)), win \(format(p.localWinUm)), offset \(format(p.localOffset)), h \(format(p.hMaximaUm)), seed \(format(p.seedMinDistUm)), compact \(format(p.watershedCompactness)), split \(format(p.postResplitMult))"
    }

    private func format(_ value: Double) -> String {
        String(format: "%.2f", value)
    }
}

struct ParameterSlider: View {
    var title: String
    @Binding var value: Double
    var range: ClosedRange<Double>
    var step: Double
    var description: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 10) {
                Text(displayTitle)
                    .font(.caption.weight(.semibold))
                    .frame(width: 180, alignment: .leading)
                    .lineLimit(1)
                Slider(value: $value, in: range, step: step)
                    .frame(minWidth: 120, maxWidth: .infinity)
                Text(value, format: .number.precision(.fractionLength(2)))
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .frame(width: 62, alignment: .trailing)
            }
            if let description {
                Text(description)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var displayTitle: String {
        switch title {
        case "MIN_DIAM_UM": "Minimum diameter (um)"
        case "MAX_DIAM_UM": "Maximum diameter (um)"
        case "TOPHAT_RADIUS_UM": "Top-hat radius (um)"
        case "GAUSS_SIGMA_UM": "Gaussian sigma (um)"
        case "LOCAL_WIN_UM": "Local window (um)"
        case "LOCAL_OFFSET": "Local threshold offset"
        case "H_MAXIMA_UM": "H-maxima (um)"
        case "SEED_MIN_DIST_UM": "Minimum seed distance (um)"
        case "WATERSHED_COMPACTNESS": "Watershed compactness"
        case "POST_RESPLIT_MULT": "Post-resplit multiplier"
        default: title
        }
    }
}

private struct StaticNucleiScanPlotView: View {
    var records: [NucleiScanRecord]
    var selectedCombo: Int?

    var body: some View {
        if let image = NucleiScanPlotRenderer.render(records: records, selectedCombo: selectedCombo) {
            ZoomableImageView(image: image, backgroundColor: .textBackgroundColor)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        } else {
            EmptyStateView(systemImage: "chart.bar", title: "No scan plot", message: "Run an advanced parameter scan first.")
        }
    }
}
