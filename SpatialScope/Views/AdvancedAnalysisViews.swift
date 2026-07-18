import Foundation
import AppKit
import SwiftUI

struct NeighborhoodAnalysisView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                GroupBox("Neighborhood analysis") {
                    VStack(alignment: .leading, spacing: 14) {
                        ParameterSlider(title: "Neighborhood square size UM", value: $store.neighborhoodGridUm, range: 1...200, step: 1)
                        Button {
                            store.runNeighborhoodAnalysis()
                        } label: {
                            Label("Run Neighborhood Analysis", systemImage: "play.fill")
                        }
                            .spatialScopeProminentButtonStyle()
                            .disabled(store.isBusy)
                    }
                    .padding(6)
                }

                if let result = store.neighborhoodAnalysisResult {
                    GroupBox("Neighborhood map") {
                        VStack(alignment: .leading, spacing: 12) {
                            VStack(alignment: .leading, spacing: 10) {
                                ZoomableImageView(image: result.image, backgroundColor: .black)
                                    .frame(maxWidth: .infinity, minHeight: 560, maxHeight: 760)
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                                Text("\(result.clusterCounts.count) unique cluster types, \(result.occupiedTileCount) occupied squares, \(result.totalCells) cells")
                                    .font(.headline)
                            }

                            VStack(alignment: .leading, spacing: 8) {
                                Text("Number-to-cluster ID key")
                                    .font(.headline)
                                ZoomableImageView(image: result.clusterKeyImage, backgroundColor: .white)
                                    .frame(maxWidth: .infinity, minHeight: 260, maxHeight: 420)
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                            }

                            HStack {
                                Button {
                                    store.shuffleNeighborhoodColors()
                                } label: {
                                    Label("Shuffle Colors", systemImage: "shuffle")
                                }
                                Button {
                                    store.selectedSection = .region
                                } label: {
                                    Label("Save and Next", systemImage: "arrow.right.circle.fill")
                                }
                                .spatialScopeProminentButtonStyle()
                                Spacer()
                            }
                        }
                        .padding(6)
                    }

                    GroupBox("Neighborhood cluster statistics") {
                        VStack(alignment: .leading, spacing: 12) {
                            ZoomableImageView(image: result.statsImage, backgroundColor: .white)
                                .frame(maxWidth: .infinity, minHeight: 200, maxHeight: 280)
                                .clipShape(RoundedRectangle(cornerRadius: 8))

                            Table(result.clusterCounts) {
                                TableColumn("Number") { row in
                                    Text("\(row.clusterID)")
                                }
                                .width(72)
                                TableColumn("Cluster type", value: \.clusterLabel)
                                TableColumn("Tiles") { row in
                                    Text("\(row.tileCount)")
                                }
                                .width(80)
                                TableColumn("Cells") { row in
                                    Text("\(row.cellCount)")
                                }
                                .width(80)
                                TableColumn("Tile fraction") { row in
                                    Text(String(format: "%.3f", row.tileFraction))
                                }
                                .width(110)
                            }
                            .frame(minHeight: 240)
                        }
                        .padding(6)
                    }
                } else {
                    EmptyStateView(
                        systemImage: "square.grid.3x3",
                        title: "No neighborhood result yet",
                        message: "Run cell-type assignment first, then run neighborhood analysis."
                    )
                    .frame(minHeight: 320)
                }

                StatusBarView()
            }
            .padding(SpatialScopeDesign.contentPadding)
        }
    }
}

private enum ManualRegionDrawMode: String, CaseIterable, Identifiable {
    case polygon = "Polygon"
    case freeDraw = "Free draw"

    var id: String { rawValue }
}

struct RegionAnalysisView: View {
    @EnvironmentObject private var store: AppStore
    @State private var displayedRegionIDs: Set<Int> = []
    @State private var displayedCellTypes: Set<String> = []
    @State private var customizedRegionIDs: Set<Int> = []
    @State private var customizedCellTypes: Set<String> = []
    @State private var manualRegionIDs: Set<Int> = []
    @State private var manualCellTypes: Set<String> = []
    @State private var manualEditMode: RegionManualEditMode = .redraw
    @State private var manualTargetRegionID = 0
    @State private var manualDisplayName = "manual_drawn_ROI"
    @State private var manualPolygonGroups: [[CellBoundaryPoint]] = []
    @State private var manualPolygonClosed = false
    @State private var manualDrawMode: ManualRegionDrawMode = .polygon
    @State private var manualBoundaryParameters: RegionParameters = {
        var parameters = RegionParameters()
        parameters.closeUm = 2.0
        parameters.dilateUm = 0.0
        parameters.minAreaUm2 = 0.0
        parameters.minCells = 1
        parameters.contourDownsample = 1
        return parameters
    }()
    @State private var manualEditorResetID = 0
    @State private var manualEditorCloseRequestID = 0
    @State private var regionDisplayRenderKey = ""
    @State private var regionDisplayRenderedImage: NSImage?
    @State private var customizedDisplayRenderKey = ""
    @State private var customizedDisplayRenderedImage: NSImage?
    @State private var manualEditRenderKey = ""
    @State private var manualEditRenderedImage: NSImage?
    @State private var manualPreviewRenderKey = ""
    @State private var manualPreviewRenderedImage: NSImage?

    var body: some View {
        let availableRegionTypes = store.assignedCellTypeNames
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                GroupBox("ROI parameters") {
                    regionParameterGrid
                    .padding(6)
                }

                GroupBox("Computational ROI identification") {
                    VStack(alignment: .leading, spacing: 12) {
                        if availableRegionTypes.isEmpty {
                            Text("Run cell-type assignment to enable ROI cell-type selection.")
                                .foregroundStyle(.secondary)
                        } else {
                            Text("Cell types defining ROIs")
                                .font(.headline)
                            LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), alignment: .leading)], alignment: .leading, spacing: 8) {
                                ForEach(availableRegionTypes, id: \.self) { typeName in
                                    Toggle(typeName, isOn: Binding(
                                        get: {
                                            store.regionParameters.selectedTypes.isEmpty
                                                || store.regionParameters.selectedTypes.contains(typeName)
                                        },
                                        set: { isSelected in
                                            let all = Set(availableRegionTypes)
                                            var selected = store.regionParameters.selectedTypes.isEmpty
                                                ? all
                                                : Set(store.regionParameters.selectedTypes).intersection(all)
                                            if isSelected {
                                                selected.insert(typeName)
                                            } else if selected.count > 1 {
                                                selected.remove(typeName)
                                            }
                                            store.regionParameters.selectedTypes = selected == all
                                                ? []
                                                : selected.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
                                        }
                                    ))
                                    .toggleStyle(.checkbox)
                                }
                            }
                            Button {
                                store.regionParameters.selectedTypes = []
                            } label: {
                                Label("Select All Assigned Types", systemImage: "checklist")
                            }
                        }
                        HStack {
                            Button {
                                store.runRegionAnalysis()
                            } label: {
                                Label("Run ROI Identification + Counts", systemImage: "play.fill")
                            }
                                .spatialScopeProminentButtonStyle()
                                .disabled(store.isBusy || availableRegionTypes.isEmpty)
                            Spacer()
                        }
                    }
                    .padding(6)
                }

                if let result = store.regionAnalysisResult {
                    GroupBox("Region map") {
                        VStack(alignment: .leading, spacing: 10) {
                            regionDisplayControls(result: result)

                            if let image = regionDisplayRenderedImage {
                                ZoomableImageView(image: image, backgroundColor: .black, outerBackgroundColor: .black)
                                    .frame(maxWidth: .infinity, minHeight: 560, maxHeight: 760)
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                            } else {
                                RegionRenderPlaceholder(text: "Rendering ROI comparison...")
                                    .frame(maxWidth: .infinity, minHeight: 560, maxHeight: 760)
                            }
                            Text(regionDisplaySummary(result: result))
                                .font(.headline)
                        }
                        .padding(6)
                        .task(id: regionDisplayCacheKey(result: result)) {
                            await updateRegionDisplayImage(result: result)
                        }
                    }

                    GroupBox("Manual ROI adjustment") {
                        manualRegionAdjustmentControls(result: result)
                            .padding(6)
                    }

                    GroupBox("Customized display and save") {
                        customizedDisplayAndSaveControls(result: result)
                            .padding(6)
                    }

                    GroupBox("Region dominant counts") {
                        VStack(alignment: .leading, spacing: 12) {
                            ZoomableImageView(image: result.statsImage, backgroundColor: .white)
                                .frame(maxWidth: .infinity, minHeight: 200, maxHeight: 280)
                                .clipShape(RoundedRectangle(cornerRadius: 8))

                            Table(result.dominantCounts) {
                                TableColumn("Dominant type", value: \.name)
                                TableColumn("Regions") { row in
                                    Text("\(row.count)")
                                }
                                .width(80)
                            }
                            .frame(minHeight: 240)
                        }
                        .padding(6)
                    }

                    GroupBox("ROI table") {
                        Table(result.regions) {
                            TableColumn("ID") { region in
                                Text("\(region.id)")
                            }
                            .width(54)
                            TableColumn("Dominant", value: \.dominantType)
                            TableColumn("Cells") { region in
                                Text("\(region.cellCount)")
                            }
                            .width(70)
                            TableColumn("Area UM2") { region in
                                Text(region.areaUm2, format: .number.precision(.fractionLength(0)))
                            }
                            .width(110)
                        }
                        .frame(minHeight: 260)
                        .padding(6)
                    }
                } else {
                    EmptyStateView(
                        systemImage: "lasso",
                        title: "No region result yet",
                        message: "Run cell-type assignment first, then run ROI identification."
                    )
                    .frame(minHeight: 300)
                }

                StatusBarView()
            }
            .padding(SpatialScopeDesign.contentPadding)
        }
    }

    private var effectiveManualBoundaryParameters: RegionParameters {
        var parameters = manualBoundaryParameters
        parameters.lineWidth = store.regionParameters.lineWidth
        parameters.lineStyle = store.regionParameters.lineStyle
        parameters.boundaryColor = store.regionParameters.boundaryColor
        parameters.useTypeColors = store.regionParameters.useTypeColors
        return parameters
    }

    @ViewBuilder
    private var regionParameterGrid: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("These settings are shared by computational ROI identification and display exports.")
                .font(.caption)
                .foregroundStyle(.secondary)

            LazyVGrid(
                columns: [GridItem(.adaptive(minimum: 300, maximum: 380), spacing: 20, alignment: .top)],
                alignment: .leading,
                spacing: 18
            ) {
                RegionParameterField(
                    title: "Close (um)",
                    help: "Fills small gaps between neighboring same-type cells before ROI detection. Higher values merge nearby islands into broader regions."
                ) {
                    RegionInlineSlider(value: $store.regionParameters.closeUm, range: 0...80, step: 1, suffix: "um")
                }

                RegionParameterField(
                    title: "Dilate (um)",
                    help: "Expands the final ROI boundary outward from the selected cell type. Higher values place the contour farther from the cells."
                ) {
                    RegionInlineSlider(value: $store.regionParameters.dilateUm, range: 0...80, step: 1, suffix: "um")
                }

                RegionParameterField(
                    title: "Min area (um2)",
                    help: "Drops small ROI fragments after closing. Increase this to remove tiny regions; decrease it to keep smaller structures."
                ) {
                    TextField("20000", value: $store.regionParameters.minAreaUm2, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 150)
                }

                RegionParameterField(
                    title: "Min cells",
                    help: "Keeps only ROI components containing at least this many selected cells. Higher values suppress sparse components."
                ) {
                    Stepper("\(store.regionParameters.minCells)", value: $store.regionParameters.minCells, in: 1...10_000)
                        .font(.system(.body, design: .monospaced))
                        .frame(maxWidth: 170, alignment: .leading)
                }

                RegionParameterField(
                    title: "Contour downsample",
                    help: "Smooths and simplifies contour extraction before drawing. Higher values are faster and lighter, but less detailed."
                ) {
                    Picker("Contour downsample", selection: $store.regionParameters.contourDownsample) {
                        Text("1").tag(1)
                        Text("2").tag(2)
                        Text("4").tag(4)
                        Text("8").tag(8)
                    }
                    .labelsHidden()
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 240)
                }

                RegionParameterField(
                    title: "Boundary line width",
                    help: "Controls contour thickness in the displayed and exported figures."
                ) {
                    RegionInlineSlider(value: $store.regionParameters.lineWidth, range: 0.5...10, step: 0.5, suffix: "px")
                }

                RegionParameterField(
                    title: "Boundary line style",
                    help: "Controls whether the contour is solid, dashed, dash-dot, or dotted."
                ) {
                    Picker("Boundary line style", selection: $store.regionParameters.lineStyle) {
                        Text("Solid").tag("-")
                        Text("Dashed").tag("--")
                        Text("Dash-dot").tag("-.")
                        Text("Dotted").tag(":")
                    }
                    .labelsHidden()
                    .frame(maxWidth: 190)
                }

                RegionParameterField(
                    title: "Boundary color",
                    help: "Use a fixed boundary color, or enable cell-type-specific colors so each ROI contour matches its source cell type."
                ) {
                    VStack(alignment: .leading, spacing: 8) {
                        Toggle("Use each cell type color", isOn: $store.regionParameters.useTypeColors)
                            .toggleStyle(.checkbox)
                        ColorPicker(
                            "Fixed boundary color",
                            selection: colorBinding(
                                getHex: { store.regionParameters.boundaryColor },
                                setHex: { store.regionParameters.boundaryColor = $0 }
                            )
                        )
                        .disabled(store.regionParameters.useTypeColors)
                        .opacity(store.regionParameters.useTypeColors ? 0.55 : 1.0)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var manualBoundaryParameterGrid: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Manual boundary generation")
                .font(.headline)
            Text("The drawn areas select seed cells. These settings rebuild the adjusted ROI from those selected cell masks only.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 250), alignment: .top)], alignment: .leading, spacing: 14) {
                RegionParameterField(
                    title: "Manual close (um)",
                    help: "Bridges small gaps between the selected cells. Increase this only when one selected cell group should become a smoother continuous ROI."
                ) {
                    RegionInlineSlider(value: $manualBoundaryParameters.closeUm, range: 0...30, step: 1, suffix: "um")
                }

                RegionParameterField(
                    title: "Manual dilate (um)",
                    help: "Expands the adjusted ROI outward after it is rebuilt from selected cells. Keep it low for boundaries that stay close to the selected cells."
                ) {
                    RegionInlineSlider(value: $manualBoundaryParameters.dilateUm, range: 0...30, step: 1, suffix: "um")
                }

                RegionParameterField(
                    title: "Manual min area (um2)",
                    help: "Removes tiny fragments from the adjusted ROI. Set to 0 when drawing small local regions."
                ) {
                    TextField("0", value: $manualBoundaryParameters.minAreaUm2, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 150)
                }

                RegionParameterField(
                    title: "Manual min cells",
                    help: "Keeps connected adjusted ROI components that contain at least this many selected seed cells."
                ) {
                    Stepper("\(manualBoundaryParameters.minCells)", value: $manualBoundaryParameters.minCells, in: 1...10_000)
                        .font(.system(.body, design: .monospaced))
                }

                RegionParameterField(
                    title: "Manual contour detail",
                    help: "Controls boundary simplification for the adjusted preview and export. Lower values preserve more local detail."
                ) {
                    Picker("Manual contour detail", selection: $manualBoundaryParameters.contourDownsample) {
                        Text("1").tag(1)
                        Text("2").tag(2)
                        Text("4").tag(4)
                        Text("8").tag(8)
                    }
                    .labelsHidden()
                    .pickerStyle(.segmented)
                }
            }
        }
    }

    @ViewBuilder
    private func manualDisplayControls(result: RegionAnalysisResult) -> some View {
        let regions = result.regions.sorted { $0.id < $1.id }
        let cellTypes = availableDisplayCellTypes(result: result)

        HStack(alignment: .top, spacing: 18) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Boundaries visible while editing")
                        .font(.headline)
                    Spacer()
                    Button {
                        manualRegionIDs = []
                    } label: {
                        Label("Show All Boundaries", systemImage: "checklist")
                    }
                }

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 210), alignment: .leading)], alignment: .leading, spacing: 8) {
                    ForEach(regions) { region in
                        Toggle(regionDisplayTitle(region), isOn: manualRegionBinding(region.id, availableIDs: regions.map(\.id), result: result))
                            .toggleStyle(.checkbox)
                    }
                }
            }

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Cell type used while editing")
                        .font(.headline)
                    Spacer()
                    Button {
                        manualCellTypes = []
                    } label: {
                        Label("Use Target Cell Type", systemImage: "scope")
                    }
                }

                LazyVGrid(columns: [GridItem(.adaptive(minimum: 170), alignment: .leading)], alignment: .leading, spacing: 8) {
                    ForEach(cellTypes, id: \.self) { cellType in
                        Toggle(cellType, isOn: manualCellTypeBinding(cellType, availableTypes: cellTypes, result: result))
                            .toggleStyle(.checkbox)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func regionDisplayControls(result: RegionAnalysisResult) -> some View {
        let regions = result.regions.sorted { $0.id < $1.id }
        let cellTypes = availableDisplayCellTypes(result: result)

        VStack(alignment: .leading, spacing: 14) {
            if !regions.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("Computational ROIs to display")
                            .font(.headline)
                        Spacer()
                        Button {
                            displayedRegionIDs = []
                        } label: {
                            Label("Show All ROIs", systemImage: "checklist")
                        }
                    }

                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 210), alignment: .leading)], alignment: .leading, spacing: 8) {
                        ForEach(regions) { region in
                            Toggle(regionDisplayTitle(region), isOn: displayRegionBinding(region.id, availableIDs: regions.map(\.id)))
                                .toggleStyle(.checkbox)
                        }
                    }
                }
            }

            if !cellTypes.isEmpty {
                Divider()
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("Cell types to display")
                            .font(.headline)
                        Spacer()
                        Button {
                            displayedCellTypes = []
                        } label: {
                            Label("Show All Cell Types", systemImage: "checklist")
                        }
                    }

                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 170), alignment: .leading)], alignment: .leading, spacing: 8) {
                        ForEach(cellTypes, id: \.self) { cellType in
                            Toggle(cellType, isOn: displayCellTypeBinding(cellType, availableTypes: cellTypes))
                                .toggleStyle(.checkbox)
                        }
                    }
                }
            }
        }
        .padding(.bottom, 4)
    }

    @ViewBuilder
    private func customizedDisplayAndSaveControls(result: RegionAnalysisResult) -> some View {
        let regions = result.regions.sorted { $0.id < $1.id }
        let cellTypes = availableDisplayCellTypes(result: result)

        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 18) {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("Boundaries to include")
                            .font(.headline)
                        Spacer()
                        Button {
                            customizedRegionIDs = []
                        } label: {
                            Label("Use All Boundaries", systemImage: "checklist")
                        }
                    }

                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 210), alignment: .leading)], alignment: .leading, spacing: 8) {
                        ForEach(regions) { region in
                            Toggle(regionDisplayTitle(region), isOn: customizedRegionBinding(region.id, availableIDs: regions.map(\.id)))
                                .toggleStyle(.checkbox)
                        }
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("Cell types to show")
                            .font(.headline)
                        Spacer()
                        Button {
                            customizedCellTypes = []
                        } label: {
                            Label("Use All Cell Types", systemImage: "checklist")
                        }
                    }

                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 170), alignment: .leading)], alignment: .leading, spacing: 8) {
                        ForEach(cellTypes, id: \.self) { cellType in
                            Toggle(cellType, isOn: customizedCellTypeBinding(cellType, availableTypes: cellTypes))
                                .toggleStyle(.checkbox)
                        }
                    }
                }
            }

            if let image = customizedDisplayRenderedImage {
                ZoomableImageView(image: image, backgroundColor: .black, outerBackgroundColor: .black)
                    .frame(maxWidth: .infinity, minHeight: 360, maxHeight: 560)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            } else {
                RegionRenderPlaceholder(text: "Rendering customized comparison...")
                    .frame(maxWidth: .infinity, minHeight: 360, maxHeight: 560)
            }

            HStack {
                Button {
                    store.saveCustomizedRegionDisplay(
                        selectedRegionIDs: selectedCustomizedRegionIDs(result: result),
                        selectedCellTypes: selectedCustomizedCellTypes(availableTypes: cellTypes)
                    )
                } label: {
                    Label("Save Customized Display", systemImage: "square.and.arrow.down")
                }
                .spatialScopeProminentButtonStyle()
                .disabled(store.isBusy || regions.isEmpty || cellTypes.isEmpty)

                Text("Saves a customized export plus an original unmodified export.")
                    .foregroundStyle(.secondary)
                Spacer()
            }
        }
        .task(id: customizedDisplayCacheKey(result: result)) {
            await updateCustomizedDisplayImage(result: result)
        }
    }

    @ViewBuilder
    private func manualRegionAdjustmentControls(result: RegionAnalysisResult) -> some View {
        let regions = result.regions.sorted { $0.id < $1.id }
        let targetRegion = regions.first { $0.id == manualTargetRegionID }

        VStack(alignment: .leading, spacing: 14) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 16) {
                    manualAdjustmentModeControl(regions: regions)
                    manualBoundaryTypeControl(regions: regions)
                    manualBoundaryNameControl
                    manualDrawingModeControl
                }

                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .top, spacing: 16) {
                        manualAdjustmentModeControl(regions: regions)
                        manualBoundaryTypeControl(regions: regions)
                    }
                    HStack(alignment: .top, spacing: 16) {
                        manualBoundaryNameControl
                        manualDrawingModeControl
                    }
                }

                VStack(alignment: .leading, spacing: 12) {
                    manualAdjustmentModeControl(regions: regions)
                    manualBoundaryTypeControl(regions: regions)
                    manualBoundaryNameControl
                    manualDrawingModeControl
                }
            }

            Divider()
            manualDisplayControls(result: result)
            manualBoundaryParameterGrid

            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Draw/Edit ROI")
                        .font(.headline)
                    if let image = manualEditRenderedImage {
                        ManualRegionComparisonEditorView(
                            image: image,
                            originalWidth: result.width,
                            originalHeight: result.height,
                            hasTitle: true,
                            showsOverlayPreview: false,
                            drawMode: manualDrawMode,
                            resetID: manualEditorResetID,
                            closeRequestID: manualEditorCloseRequestID,
                            isClosed: $manualPolygonClosed,
                            polygons: $manualPolygonGroups
                        )
                        .frame(maxWidth: .infinity, minHeight: 860, maxHeight: 1100)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .stroke(Color.secondary.opacity(0.18), lineWidth: 1)
                        )
                    } else {
                        RegionRenderPlaceholder(text: "Rendering editable comparison...")
                            .frame(maxWidth: .infinity, minHeight: 860, maxHeight: 1100)
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("Adjusted Boundary Preview")
                        .font(.headline)
                    let previewImage = manualPreviewRenderedImage ?? (manualPolygonGroups.isEmpty ? manualEditRenderedImage : nil)
                    if let image = previewImage {
                        ZoomableImageView(image: image, backgroundColor: .black, outerBackgroundColor: .black)
                            .frame(maxWidth: .infinity, minHeight: 560, maxHeight: 780)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    } else {
                        RegionRenderPlaceholder(text: "Rendering adjusted preview...")
                            .frame(maxWidth: .infinity, minHeight: 560, maxHeight: 780)
                    }
                }
            }

            HStack {
                Button {
                    resetManualPolygon()
                } label: {
                    Label("Reset Drawing", systemImage: "arrow.counterclockwise")
                }
                .disabled(store.isBusy)

                Button {
                    closeManualPolygon()
                } label: {
                    Label("Close Current Area", systemImage: "checkmark.circle")
                }
                .disabled(store.isBusy)

                Button {
                    let seedCellTypes = selectedManualCellTypes(availableTypes: availableDisplayCellTypes(result: result), result: result)
                    store.saveManualRegionAdjustment(
                        mode: manualEditMode,
                        targetRegionID: manualTargetRegionID == 0 ? nil : manualTargetRegionID,
                        displayName: resolvedManualDisplayName(targetRegion: targetRegion),
                        polygonGroups: manualPolygonGroups,
                        seedCellTypes: seedCellTypes,
                        manualParameters: effectiveManualBoundaryParameters
                    )
                    resetManualPolygon()
                } label: {
                    Label("Save Adjusted ROI", systemImage: "square.and.arrow.down")
                }
                .spatialScopeProminentButtonStyle()
                .disabled(store.isBusy || manualPolygonGroups.isEmpty || (manualEditMode != .redraw && targetRegion == nil))

                Text(manualPolygonGroups.isEmpty ? "No closed area" : "\(manualPolygonGroups.count) closed area(s), \(manualPolygonGroups.reduce(0) { $0 + $1.count }) point(s)")
                    .foregroundStyle(.secondary)
                Spacer()
            }
        }
        .task(id: manualEditCacheKey(result: result)) {
            await updateManualEditImage(result: result)
        }
        .task(id: manualPreviewCacheKey(result: result)) {
            await updateManualPreviewImage(result: result)
        }
    }

    private func manualAdjustmentModeControl(regions: [RegionROI]) -> some View {
        Picker(selection: $manualEditMode) {
            ForEach(RegionManualEditMode.allCases) { mode in
                Text(mode.rawValue).tag(mode)
            }
        } label: {
            Text("Adjustment mode")
                .fixedSize(horizontal: true, vertical: false)
        }
        .frame(width: 250)
        .onChange(of: manualEditMode) { newMode in
            if newMode != .redraw, manualTargetRegionID == 0 {
                manualTargetRegionID = regions.first?.id ?? 0
            }
            manualCellTypes = []
            resetManualPolygon()
        }
    }

    private func manualBoundaryTypeControl(regions: [RegionROI]) -> some View {
        Picker(selection: $manualTargetRegionID) {
            if manualEditMode == .redraw {
                Text("Create new manual ROI / boundary").tag(0)
            }
            ForEach(regions) { region in
                Text(regionDisplayTitle(region)).tag(region.id)
            }
        } label: {
            Text("Boundary type to edit")
                .fixedSize(horizontal: true, vertical: false)
        }
        .frame(minWidth: 300)
        .onChange(of: manualTargetRegionID) { _ in
            manualCellTypes = []
            resetManualPolygon()
        }
    }

    private var manualBoundaryNameControl: some View {
        HStack(spacing: 8) {
            Text("New boundary name")
                .fixedSize(horizontal: true, vertical: false)
            TextField("Boundary name", text: $manualDisplayName)
                .textFieldStyle(.roundedBorder)
                .frame(width: 240)
        }
    }

    private var manualDrawingModeControl: some View {
        Picker(selection: $manualDrawMode) {
            ForEach(ManualRegionDrawMode.allCases) { mode in
                Text(mode.rawValue).tag(mode)
            }
        } label: {
            Text("Drawing mode")
                .fixedSize(horizontal: true, vertical: false)
        }
        .pickerStyle(.segmented)
        .frame(width: 220)
        .onChange(of: manualDrawMode) { _ in
            resetManualPolygon()
        }
    }

    private func regionDisplayCacheKey(result: RegionAnalysisResult) -> String {
        let regionIDs = selectedDisplayRegionIDs(result: result).sorted()
        let cellTypes = selectedDisplayCellTypes(availableTypes: availableDisplayCellTypes(result: result)).sorted()
        return [
            "display",
            result.id.uuidString,
            regionIDs.map(String.init).joined(separator: "."),
            cellTypes.joined(separator: "."),
            regionParametersCacheKey(result.parameters)
        ].joined(separator: "|")
    }

    private func customizedDisplayCacheKey(result: RegionAnalysisResult) -> String {
        let regionIDs = selectedCustomizedRegionIDs(result: result).sorted()
        let cellTypes = selectedCustomizedCellTypes(availableTypes: availableDisplayCellTypes(result: result)).sorted()
        return [
            "customized",
            result.id.uuidString,
            regionIDs.map(String.init).joined(separator: "."),
            cellTypes.joined(separator: "."),
            regionParametersCacheKey(result.parameters)
        ].joined(separator: "|")
    }

    private func manualEditCacheKey(result: RegionAnalysisResult) -> String {
        let regionIDs = selectedManualRegionIDs(result: result).sorted()
        let cellTypes = selectedManualCellTypes(availableTypes: availableDisplayCellTypes(result: result), result: result).sorted()
        return [
            "manual-edit",
            result.id.uuidString,
            regionIDs.map(String.init).joined(separator: "."),
            cellTypes.joined(separator: "."),
            regionParametersCacheKey(result.parameters)
        ].joined(separator: "|")
    }

    private func manualPreviewCacheKey(result: RegionAnalysisResult) -> String {
        let regionIDs = selectedManualRegionIDs(result: result).sorted()
        let cellTypes = selectedManualCellTypes(availableTypes: availableDisplayCellTypes(result: result), result: result).sorted()
        let pointKey: String
        if !manualPolygonGroups.isEmpty {
            pointKey = manualPolygonGroups
                .map { polygon in
                    polygon
                        .map { String(format: "%.1f,%.1f", $0.x, $0.y) }
                        .joined(separator: ";")
                }
                .joined(separator: "|")
        } else {
            pointKey = "editing"
        }
        return [
            "manual",
            result.id.uuidString,
            regionIDs.map(String.init).joined(separator: "."),
            cellTypes.joined(separator: "."),
            manualEditMode.rawValue,
            "\(manualTargetRegionID)",
            resolvedManualDisplayName(targetRegion: result.regions.first { $0.id == manualTargetRegionID }),
            manualPolygonGroups.isEmpty ? "open" : "closed",
            pointKey,
            regionParametersCacheKey(effectiveManualBoundaryParameters)
        ].joined(separator: "|")
    }

    private func regionParametersCacheKey(_ parameters: RegionParameters) -> String {
        [
            "\(parameters.closeUm)",
            "\(parameters.dilateUm)",
            "\(parameters.minAreaUm2)",
            "\(parameters.minCells)",
            "\(parameters.contourDownsample)",
            "\(parameters.lineWidth)",
            parameters.lineStyle,
            parameters.boundaryColor,
            "\(parameters.useTypeColors)"
        ].joined(separator: ",")
    }

    @MainActor
    private func updateRegionDisplayImage(result: RegionAnalysisResult) async {
        let key = regionDisplayCacheKey(result: result)
        guard regionDisplayRenderKey != key || regionDisplayRenderedImage == nil else { return }
        regionDisplayRenderKey = key
        regionDisplayRenderedImage = nil
        let allRegionIDs = Set(result.regions.map(\.id))
        let allCellTypes = Set(availableDisplayCellTypes(result: result))
        let useAllRegions = displayedRegionIDs.intersection(allRegionIDs).isEmpty
        let useAllCellTypes = displayedCellTypes.intersection(allCellTypes).isEmpty
        let selectedRegionIDs = useAllRegions ? allRegionIDs : displayedRegionIDs.intersection(allRegionIDs)
        let selectedCellTypes = useAllCellTypes ? allCellTypes : displayedCellTypes.intersection(allCellTypes)
        let assignmentResult = assignmentResultForRegionDisplay
        let assignments = (assignmentResult?.assignments ?? []).filter { selectedCellTypes.contains($0.assignedType) }
        let regions = result.regions.filter { region in
            selectedRegionIDs.contains(region.id)
                && regionMatchesSelectedCellTypes(region, selectedCellTypes: selectedCellTypes)
        }
        let overlay = currentOverlayImage()
        let pixelSize = store.pixelSize
        let cellTypeIDByName = filteredCellTypeIDByName(
            assignmentResult?.cellTypeIDByName ?? [:],
            selectedCellTypes: selectedCellTypes
        )
        let cellTypeMask = filteredCellTypeMask(
            assignmentResult?.cellTypeMask,
            cellTypeIDByName: cellTypeIDByName
        )
        let image = await Task.detached(priority: .userInitiated) {
            RegionAnalyzer.renderRegionComparisonMap(
                overlayImage: overlay,
                assignments: assignments,
                regions: regions,
                width: result.width,
                height: result.height,
                parameters: result.parameters,
                cellTypeMask: cellTypeMask,
                cellTypeIDByName: cellTypeIDByName,
                pixelSize: pixelSize,
                title: "Computed ROIs"
            )
        }.value
        guard !Task.isCancelled, regionDisplayRenderKey == key else { return }
        regionDisplayRenderedImage = image
    }

    @MainActor
    private func updateCustomizedDisplayImage(result: RegionAnalysisResult) async {
        let key = customizedDisplayCacheKey(result: result)
        guard customizedDisplayRenderKey != key || customizedDisplayRenderedImage == nil else { return }
        customizedDisplayRenderKey = key
        customizedDisplayRenderedImage = nil
        let selectedCellTypes = selectedCustomizedCellTypes(availableTypes: availableDisplayCellTypes(result: result))
        let regions = result.regions.filter { region in
            selectedCustomizedRegionIDs(result: result).contains(region.id)
                && regionMatchesSelectedCellTypes(region, selectedCellTypes: selectedCellTypes)
        }
        let assignmentResult = assignmentResultForRegionDisplay
        let assignments = (assignmentResult?.assignments ?? []).filter { selectedCellTypes.contains($0.assignedType) }
        let overlay = currentOverlayImage()
        let pixelSize = store.pixelSize
        let cellTypeIDByName = filteredCellTypeIDByName(
            assignmentResult?.cellTypeIDByName ?? [:],
            selectedCellTypes: selectedCellTypes
        )
        let cellTypeMask = filteredCellTypeMask(
            assignmentResult?.cellTypeMask,
            cellTypeIDByName: cellTypeIDByName
        )
        let image = await Task.detached(priority: .userInitiated) {
            RegionAnalyzer.renderRegionComparisonMap(
                overlayImage: overlay,
                assignments: assignments,
                regions: regions,
                width: result.width,
                height: result.height,
                parameters: result.parameters,
                cellTypeMask: cellTypeMask,
                cellTypeIDByName: cellTypeIDByName,
                pixelSize: pixelSize,
                title: "Customized display"
            )
        }.value
        guard !Task.isCancelled, customizedDisplayRenderKey == key else { return }
        customizedDisplayRenderedImage = image
    }

    @MainActor
    private func updateManualEditImage(result: RegionAnalysisResult) async {
        let key = manualEditCacheKey(result: result)
        guard manualEditRenderKey != key || manualEditRenderedImage == nil else { return }
        manualEditRenderKey = key
        manualEditRenderedImage = nil
        let selectedCellTypes = selectedManualCellTypes(availableTypes: availableDisplayCellTypes(result: result), result: result)
        let selectedRegionIDs = selectedManualRegionIDs(result: result)
        let regions = result.regions.filter { region in
            selectedRegionIDs.contains(region.id)
                && regionSourceMatchesSelectedCellTypes(region, selectedCellTypes: selectedCellTypes)
        }
        let assignmentResult = assignmentResultForRegionDisplay
        let assignments = (assignmentResult?.assignments ?? []).filter { selectedCellTypes.contains($0.assignedType) }
        let pixelSize = store.pixelSize
        let cellTypeIDByName = filteredCellTypeIDByName(
            assignmentResult?.cellTypeIDByName ?? [:],
            selectedCellTypes: selectedCellTypes
        )
        let cellTypeMask = filteredCellTypeMask(
            assignmentResult?.cellTypeMask,
            cellTypeIDByName: cellTypeIDByName
        )
        let image = await Task.detached(priority: .userInitiated) {
            RegionAnalyzer.renderRegionSinglePanelMap(
                assignments: assignments,
                regions: regions,
                width: result.width,
                height: result.height,
                parameters: result.parameters,
                cellTypeMask: cellTypeMask,
                cellTypeIDByName: cellTypeIDByName,
                pixelSize: pixelSize,
                title: "Manual ROI editing"
            )
        }.value
        guard !Task.isCancelled, manualEditRenderKey == key else { return }
        manualEditRenderedImage = image
    }

    @MainActor
    private func updateManualPreviewImage(result: RegionAnalysisResult) async {
        let key = manualPreviewCacheKey(result: result)
        guard manualPreviewRenderKey != key || manualPreviewRenderedImage == nil else { return }
        manualPreviewRenderKey = key
        guard !manualPolygonGroups.isEmpty else {
            manualPreviewRenderedImage = nil
            return
        }
        let assignmentResult = assignmentResultForRegionDisplay
        let allAssignments = assignmentResult?.assignments ?? []
        let selectedCellTypes = selectedManualCellTypes(availableTypes: availableDisplayCellTypes(result: result), result: result)
        let visibleAssignments = allAssignments.filter { selectedCellTypes.contains($0.assignedType) }
        let manualParameters = effectiveManualBoundaryParameters
        let mode = manualEditMode
        let targetRegionID = manualTargetRegionID == 0 ? nil : manualTargetRegionID
        let displayName = resolvedManualDisplayName(targetRegion: result.regions.first { $0.id == manualTargetRegionID })
        let polygonGroups = manualPolygonGroups
        let pixelSize = store.pixelSize
        let cellTypeIDByName = filteredCellTypeIDByName(
            assignmentResult?.cellTypeIDByName ?? [:],
            selectedCellTypes: selectedCellTypes
        )
        let cellTypeMask = filteredCellTypeMask(
            assignmentResult?.cellTypeMask,
            cellTypeIDByName: cellTypeIDByName
        )
        let baseRegionIDs = Set(result.regions.map(\.id))
        let image = await Task.detached(priority: .userInitiated) { () -> NSImage? in
            guard let adjusted = try? RegionAnalyzer.applyManualEdit(
                to: result,
                assignments: allAssignments,
                mode: mode,
                targetRegionID: targetRegionID,
                displayName: displayName,
                polygonGroups: polygonGroups,
                pixelSize: pixelSize,
                seedCellTypes: selectedCellTypes,
                manualParameters: manualParameters
            ) else {
                return nil
            }
            let previewRegions = adjusted.regions.filter { !baseRegionIDs.contains($0.id) }
            return RegionAnalyzer.renderRegionSinglePanelMap(
                assignments: visibleAssignments,
                regions: previewRegions,
                width: adjusted.width,
                height: adjusted.height,
                parameters: manualParameters,
                cellTypeMask: cellTypeMask,
                cellTypeIDByName: cellTypeIDByName,
                pixelSize: pixelSize,
                title: "Adjusted ROI preview"
            )
        }.value
        guard !Task.isCancelled, manualPreviewRenderKey == key else { return }
        manualPreviewRenderedImage = image
    }

    private func closeManualPolygon() {
        manualEditorCloseRequestID += 1
    }

    private func resetManualPolygon() {
        manualPolygonGroups = []
        manualPolygonClosed = false
        manualEditorResetID += 1
    }

    private func currentOverlayImage() -> NSImage? {
        store.overlayImage ?? OutputWriter.loadImage(outputFolder: store.outputFolder, section: "overlay", name: "overlay.png")
    }

    private var assignmentResultForRegionDisplay: CellTypeAssignmentResult? {
        store.cellTypeAssignmentResult ?? OutputWriter.loadCellTypeAssignmentResult(outputFolder: store.outputFolder)
    }

    private func filteredCellTypeIDByName(
        _ cellTypeIDByName: [String: UInt16],
        selectedCellTypes: Set<String>
    ) -> [String: UInt16] {
        guard !selectedCellTypes.isEmpty else { return cellTypeIDByName }
        return cellTypeIDByName.filter { selectedCellTypes.contains($0.key) }
    }

    private func filteredCellTypeMask(
        _ cellTypeMask: UInt16Raster?,
        cellTypeIDByName: [String: UInt16]
    ) -> UInt16Raster? {
        guard let cellTypeMask, !cellTypeIDByName.isEmpty else { return nil }
        let allowedIDs = Set(cellTypeIDByName.values)
        return UInt16Raster(
            width: cellTypeMask.width,
            height: cellTypeMask.height,
            values: cellTypeMask.values.map { allowedIDs.contains($0) ? $0 : 0 }
        )
    }

    private func resolvedManualDisplayName(targetRegion: RegionROI?) -> String {
        let trimmed = manualDisplayName.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            return trimmed
        }
        if let targetRegion {
            return "adjusted_\(targetRegion.sourceType ?? targetRegion.dominantType)"
        }
        return "manual_drawn_ROI"
    }

    private func regionDisplaySummary(result: RegionAnalysisResult) -> String {
        let shownRegions = selectedDisplayRegionIDs(result: result)
        let cellTypes = availableDisplayCellTypes(result: result)
        let shownTypes = selectedDisplayCellTypes(availableTypes: cellTypes)
        return "\(shownRegions.count) of \(result.regions.count) ROIs shown, \(shownTypes.count) of \(cellTypes.count) cell types shown, \(result.totalCells) cells counted"
    }

    private var assignmentSourceForRegionDisplay: [CellTypeAssignment] {
        assignmentResultForRegionDisplay?.assignments ?? []
    }

    private func availableDisplayCellTypes(result: RegionAnalysisResult) -> [String] {
        var names = Set(assignmentSourceForRegionDisplay.map(\.assignedType))
        for region in result.regions {
            names.formUnion(region.countsByType.keys)
        }
        names.remove("Unassigned")
        names.remove("Ambiguous")
        return Array(names).sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }

    private func selectedDisplayRegionIDs(result: RegionAnalysisResult) -> Set<Int> {
        let all = Set(result.regions.map(\.id))
        let selected = displayedRegionIDs.intersection(all)
        return selected.isEmpty ? all : selected
    }

    private func selectedDisplayCellTypes(availableTypes: [String]) -> Set<String> {
        let all = Set(availableTypes)
        let selected = displayedCellTypes.intersection(all)
        return selected.isEmpty ? all : selected
    }

    private func selectedCustomizedRegionIDs(result: RegionAnalysisResult) -> Set<Int> {
        let all = Set(result.regions.map(\.id))
        let selected = customizedRegionIDs.intersection(all)
        return selected.isEmpty ? all : selected
    }

    private func selectedCustomizedCellTypes(availableTypes: [String]) -> Set<String> {
        let all = Set(availableTypes)
        let selected = customizedCellTypes.intersection(all)
        return selected.isEmpty ? all : selected
    }

    private func selectedManualRegionIDs(result: RegionAnalysisResult) -> Set<Int> {
        let all = Set(result.regions.map(\.id))
        let selected = manualRegionIDs.intersection(all)
        return selected.isEmpty ? all : selected
    }

    private func selectedManualCellTypes(availableTypes: [String], result: RegionAnalysisResult) -> Set<String> {
        let all = Set(availableTypes)
        let selected = manualCellTypes.intersection(all)
        return selected.isEmpty ? defaultManualCellTypes(availableTypes: availableTypes, result: result) : selected
    }

    private func defaultManualCellTypes(availableTypes: [String], result: RegionAnalysisResult) -> Set<String> {
        let all = Set(availableTypes)
        if manualEditMode != .redraw,
           let target = result.regions.first(where: { $0.id == manualTargetRegionID }) {
            let preferred = preferredRegionCellType(target)
            if all.contains(preferred) {
                return [preferred]
            }
        }
        if let first = availableTypes.first {
            return [first]
        }
        return []
    }

    private func defaultManualRegionIDs(result: RegionAnalysisResult) -> Set<Int> {
        Set(result.regions.map(\.id))
    }

    private func displayRegionBinding(_ id: Int, availableIDs: [Int]) -> Binding<Bool> {
        Binding(
            get: {
                displayedRegionIDs.isEmpty || displayedRegionIDs.contains(id)
            },
            set: { isSelected in
                let all = Set(availableIDs)
                var selected = displayedRegionIDs.isEmpty ? all : displayedRegionIDs.intersection(all)
                if isSelected {
                    selected.insert(id)
                } else if selected.count > 1 {
                    selected.remove(id)
                }
                displayedRegionIDs = selected == all ? [] : selected
            }
        )
    }

    private func displayCellTypeBinding(_ cellType: String, availableTypes: [String]) -> Binding<Bool> {
        Binding(
            get: {
                displayedCellTypes.isEmpty || displayedCellTypes.contains(cellType)
            },
            set: { isSelected in
                let all = Set(availableTypes)
                var selected = displayedCellTypes.isEmpty ? all : displayedCellTypes.intersection(all)
                if isSelected {
                    selected.insert(cellType)
                } else if selected.count > 1 {
                    selected.remove(cellType)
                }
                displayedCellTypes = selected == all ? [] : selected
            }
        )
    }

    private func customizedRegionBinding(_ id: Int, availableIDs: [Int]) -> Binding<Bool> {
        Binding(
            get: {
                customizedRegionIDs.isEmpty || customizedRegionIDs.contains(id)
            },
            set: { isSelected in
                let all = Set(availableIDs)
                var selected = customizedRegionIDs.isEmpty ? all : customizedRegionIDs.intersection(all)
                if isSelected {
                    selected.insert(id)
                } else if selected.count > 1 {
                    selected.remove(id)
                }
                customizedRegionIDs = selected == all ? [] : selected
            }
        )
    }

    private func customizedCellTypeBinding(_ cellType: String, availableTypes: [String]) -> Binding<Bool> {
        Binding(
            get: {
                customizedCellTypes.isEmpty || customizedCellTypes.contains(cellType)
            },
            set: { isSelected in
                let all = Set(availableTypes)
                var selected = customizedCellTypes.isEmpty ? all : customizedCellTypes.intersection(all)
                if isSelected {
                    selected.insert(cellType)
                } else if selected.count > 1 {
                    selected.remove(cellType)
                }
                customizedCellTypes = selected == all ? [] : selected
            }
        )
    }

    private func manualRegionBinding(_ id: Int, availableIDs: [Int], result: RegionAnalysisResult) -> Binding<Bool> {
        Binding(
            get: {
                manualRegionIDs.isEmpty || manualRegionIDs.contains(id)
            },
            set: { isSelected in
                let all = Set(availableIDs)
                var selected = manualRegionIDs.isEmpty ? all : manualRegionIDs.intersection(all)
                if isSelected {
                    selected.insert(id)
                } else if selected.count > 1 {
                    selected.remove(id)
                }
                manualRegionIDs = selected == all ? [] : selected
            }
        )
    }

    private func manualCellTypeBinding(_ cellType: String, availableTypes: [String], result: RegionAnalysisResult) -> Binding<Bool> {
        Binding(
            get: {
                selectedManualCellTypes(availableTypes: availableTypes, result: result).contains(cellType)
            },
            set: { isSelected in
                let all = Set(availableTypes)
                var selected = manualCellTypes.intersection(all)
                if selected.isEmpty {
                    selected = selectedManualCellTypes(availableTypes: availableTypes, result: result)
                }
                if isSelected {
                    selected.insert(cellType)
                } else if selected.count > 1 {
                    selected.remove(cellType)
                }
                manualCellTypes = selected
            }
        )
    }

    private func regionDisplayTitle(_ region: RegionROI) -> String {
        let typeName = region.sourceType ?? region.dominantType
        return "#\(region.id) \(typeName) (\(region.cellCount) cells)"
    }

    private func preferredRegionCellType(_ region: RegionROI) -> String {
        for candidate in [region.originalSourceType, region.sourceType, region.dominantType] {
            let name = (candidate ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if !name.isEmpty, name != "Unassigned", name != "Ambiguous" {
                return name
            }
        }
        return region.sourceType ?? region.dominantType
    }

    private func regionMatchesSelectedCellTypes(_ region: RegionROI, selectedCellTypes: Set<String>) -> Bool {
        if selectedCellTypes.contains(preferredRegionCellType(region)) {
            return true
        }
        let countedTypes = Set(region.countsByType.filter { $0.value > 0 }.keys)
        return !countedTypes.isDisjoint(with: selectedCellTypes)
    }

    private func regionSourceMatchesSelectedCellTypes(_ region: RegionROI, selectedCellTypes: Set<String>) -> Bool {
        selectedCellTypes.contains(preferredRegionCellType(region))
    }
}

private struct RegionParameterField<Content: View>: View {
    var title: String
    var help: String
    private let content: Content

    init(title: String, help: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.help = help
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.headline)
            content
            Text(help)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: 380, alignment: .leading)
    }
}

private struct RegionInlineSlider: View {
    @Binding var value: Double
    var range: ClosedRange<Double>
    var step: Double
    var suffix: String

    var body: some View {
        HStack(spacing: 10) {
            Slider(value: $value, in: range, step: step)
                .frame(minWidth: 120, maxWidth: 240)
            Text(valueText)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
                .frame(width: 64, alignment: .trailing)
        }
        .frame(maxWidth: 320, alignment: .leading)
    }

    private var valueText: String {
        if step < 1 {
            return String(format: "%.1f %@", value, suffix)
        }
        return String(format: "%.0f %@", value, suffix)
    }
}

private struct RegionRenderPlaceholder: View {
    var text: String

    var body: some View {
        ZStack {
            Color.black
            VStack(spacing: 10) {
                ProgressView()
                    .controlSize(.small)
                Text(text)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct ManualRegionComparisonEditorView: NSViewRepresentable {
    let image: NSImage
    let originalWidth: Int
    let originalHeight: Int
    let hasTitle: Bool
    let showsOverlayPreview: Bool
    let drawMode: ManualRegionDrawMode
    let resetID: Int
    let closeRequestID: Int
    @Binding var isClosed: Bool
    @Binding var polygons: [[CellBoundaryPoint]]

    func makeCoordinator() -> Coordinator {
        Coordinator(isClosed: $isClosed, polygons: $polygons)
    }

    func makeNSView(context: Context) -> EditorView {
        let view = EditorView()
        view.coordinator = context.coordinator
        view.toolTip = "Draw on the cell-type mask panel. Press Return or right-click to close the drawing."
        updateNSView(view, context: context)
        return view
    }

    func updateNSView(_ nsView: EditorView, context: Context) {
        nsView.coordinator = context.coordinator
        nsView.image = image
        nsView.originalWidth = originalWidth
        nsView.originalHeight = originalHeight
        nsView.hasTitle = hasTitle
        nsView.showsOverlayPreview = showsOverlayPreview
        nsView.drawMode = drawMode
        nsView.applyResetIfNeeded(resetID)
        nsView.applyCloseRequestIfNeeded(closeRequestID)
        nsView.needsDisplay = true
    }

    final class Coordinator {
        var isClosed: Binding<Bool>
        var polygons: Binding<[[CellBoundaryPoint]]>

        init(isClosed: Binding<Bool>, polygons: Binding<[[CellBoundaryPoint]]>) {
            self.isClosed = isClosed
            self.polygons = polygons
        }

        func commitClosed(polygons closedPolygons: [[CellBoundaryPoint]]) {
            let validPolygons = closedPolygons.filter { $0.count >= 3 }
            guard !validPolygons.isEmpty else { return }
            polygons.wrappedValue = validPolygons
            isClosed.wrappedValue = true
        }

        func clearCommittedDrawing() {
            guard !polygons.wrappedValue.isEmpty || isClosed.wrappedValue else { return }
            polygons.wrappedValue = []
            isClosed.wrappedValue = false
        }
    }

    final class EditorView: NSView {
        var image: NSImage?
        var originalWidth = 0
        var originalHeight = 0
        var hasTitle = true
        var showsOverlayPreview = false
        var drawMode: ManualRegionDrawMode = .polygon {
            didSet {
                if oldValue != drawMode {
                    clearLocalDrawing(commit: true)
                }
            }
        }
        var coordinator: Coordinator?
        private var closedPolygonGroups: [[CellBoundaryPoint]] = []
        private var localPolygonPoints: [CellBoundaryPoint] = []
        private var freeDrawStrokes: [[CellBoundaryPoint]] = []
        private var activeFreeDrawStroke: [CellBoundaryPoint] = []
        private var lastResetID = 0
        private var lastCloseRequestID = 0
        private var eventMonitor: Any?

        override var acceptsFirstResponder: Bool {
            true
        }

        override var isFlipped: Bool {
            true
        }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if window == nil {
                removeEventMonitor()
            } else {
                installEventMonitor()
            }
        }

        deinit {
            removeEventMonitor()
        }

        override func draw(_ dirtyRect: NSRect) {
            super.draw(dirtyRect)
            NSColor.black.setFill()
            bounds.fill()
            guard let image else { return }
            let fitted = fittedImageRect()
            NSGraphicsContext.current?.imageInterpolation = .high
            image.draw(
                in: fitted.rect,
                from: .zero,
                operation: .sourceOver,
                fraction: 1.0,
                respectFlipped: true,
                hints: nil
            )
            drawDrawing(rect: fitted.rect, scale: fitted.scale)
        }

        override func mouseDown(with event: NSEvent) {
            window?.makeFirstResponder(self)
            guard let point = originalPoint(from: convert(event.locationInWindow, from: nil), requiresHit: true) else {
                super.mouseDown(with: event)
                return
            }
            beginDrawing(at: point)
        }

        override func mouseDragged(with event: NSEvent) {
            guard drawMode == .freeDraw,
                  let point = originalPoint(from: convert(event.locationInWindow, from: nil), requiresHit: false) else {
                super.mouseDragged(with: event)
                return
            }
            continueFreeDraw(at: point)
        }

        override func mouseUp(with event: NSEvent) {
            guard drawMode == .freeDraw else {
                super.mouseUp(with: event)
                return
            }
            finishFreeDrawStroke()
        }

        override func rightMouseDown(with event: NSEvent) {
            window?.makeFirstResponder(self)
            guard hasEnoughLocalPointsToClose else {
                super.rightMouseDown(with: event)
                return
            }
            closeLocalDrawing()
            needsDisplay = true
        }

        override func keyDown(with event: NSEvent) {
            if isReturnKey(event), hasEnoughLocalPointsToClose {
                closeLocalDrawing()
                needsDisplay = true
            } else {
                super.keyDown(with: event)
            }
        }

        private func installEventMonitor() {
            guard eventMonitor == nil else { return }
            eventMonitor = NSEvent.addLocalMonitorForEvents(
                matching: [.leftMouseDown, .leftMouseDragged, .leftMouseUp, .rightMouseDown, .keyDown]
            ) { [weak self] event in
                self?.handleMonitoredEvent(event) ?? event
            }
        }

        private func removeEventMonitor() {
            if let eventMonitor {
                NSEvent.removeMonitor(eventMonitor)
                self.eventMonitor = nil
            }
        }

        private func handleMonitoredEvent(_ event: NSEvent) -> NSEvent? {
            guard let window,
                  let eventWindow = event.window,
                  eventWindow === window else {
                return event
            }

            switch event.type {
            case .leftMouseDown:
                let location = convert(event.locationInWindow, from: nil)
                guard let point = originalPoint(from: location, requiresHit: true) else { return event }
                window.makeFirstResponder(self)
                beginDrawing(at: point)
                return nil

            case .leftMouseDragged:
                guard drawMode == .freeDraw, !activeFreeDrawStroke.isEmpty else { return event }
                let location = convert(event.locationInWindow, from: nil)
                guard let point = originalPoint(from: location, requiresHit: false) else { return nil }
                continueFreeDraw(at: point)
                return nil

            case .leftMouseUp:
                guard drawMode == .freeDraw, !activeFreeDrawStroke.isEmpty else { return event }
                finishFreeDrawStroke()
                return nil

            case .rightMouseDown:
                guard hasEnoughLocalPointsToClose,
                      bounds.contains(convert(event.locationInWindow, from: nil)) else {
                    return event
                }
                window.makeFirstResponder(self)
                closeLocalDrawing()
                needsDisplay = true
                return nil

            case .keyDown:
                guard window.firstResponder === self,
                      isReturnKey(event),
                      hasEnoughLocalPointsToClose else {
                    return event
                }
                closeLocalDrawing()
                needsDisplay = true
                return nil

            default:
                return event
            }
        }

        private func beginDrawing(at point: CellBoundaryPoint) {
            switch drawMode {
            case .polygon:
                localPolygonPoints.append(point)
            case .freeDraw:
                activeFreeDrawStroke = [point]
            }
            needsDisplay = true
        }

        private func continueFreeDraw(at point: CellBoundaryPoint) {
            if shouldAppend(point, to: activeFreeDrawStroke) {
                activeFreeDrawStroke.append(point)
                needsDisplay = true
            }
        }

        private func finishFreeDrawStroke() {
            if !activeFreeDrawStroke.isEmpty {
                freeDrawStrokes.append(activeFreeDrawStroke)
                activeFreeDrawStroke = []
                needsDisplay = true
            }
        }

        func applyResetIfNeeded(_ resetID: Int) {
            guard resetID != lastResetID else { return }
            lastResetID = resetID
            clearLocalDrawing(commit: false)
            needsDisplay = true
        }

        func applyCloseRequestIfNeeded(_ closeRequestID: Int) {
            guard closeRequestID != lastCloseRequestID else { return }
            lastCloseRequestID = closeRequestID
            if hasEnoughLocalPointsToClose {
                closeLocalDrawing()
                needsDisplay = true
            }
        }

        private var headerHeight: CGFloat {
            hasTitle ? 62 : 18
        }

        private var footerHeight: CGFloat {
            18
        }

        private var previewGap: CGFloat {
            showsOverlayPreview ? 28 : 0
        }

        private var hasEnoughLocalPointsToClose: Bool {
            switch drawMode {
            case .polygon:
                return localPolygonPoints.count >= 3
            case .freeDraw:
                return allFreeDrawPoints.count >= 3
            }
        }

        private var allFreeDrawPoints: [CellBoundaryPoint] {
            freeDrawStrokes.flatMap { $0 } + activeFreeDrawStroke
        }

        private func fittedImageRect() -> (rect: CGRect, scale: CGFloat) {
            let panelWidth = CGFloat(max(1, originalWidth))
            let imageWidth = showsOverlayPreview ? panelWidth * 2 + previewGap : panelWidth
            let imageHeight = CGFloat(max(1, originalHeight)) + headerHeight + footerHeight
            let scale = min(
                max(1.0, bounds.width) / imageWidth,
                max(1.0, bounds.height) / imageHeight
            )
            let width = imageWidth * scale
            let height = imageHeight * scale
            let rect = CGRect(
                x: (bounds.width - width) / 2.0,
                y: (bounds.height - height) / 2.0,
                width: width,
                height: height
            )
            return (rect, scale)
        }

        private func editablePanelRect(in canvasRect: CGRect, scale: CGFloat) -> CGRect {
            CGRect(
                x: canvasRect.minX,
                y: canvasRect.minY + headerHeight * scale,
                width: CGFloat(max(1, originalWidth)) * scale,
                height: CGFloat(max(1, originalHeight)) * scale
            )
        }

        private func originalPoint(from location: CGPoint, requiresHit: Bool) -> CellBoundaryPoint? {
            let fitted = fittedImageRect()
            let panelRect = editablePanelRect(in: fitted.rect, scale: fitted.scale)
            let hitRect = panelRect.insetBy(dx: -16, dy: -16)
            guard fitted.scale > 0,
                  (!requiresHit || hitRect.contains(location)) else {
                return nil
            }
            let originalX = (location.x - panelRect.minX) / fitted.scale
            let originalY = (location.y - panelRect.minY) / fitted.scale
            return CellBoundaryPoint(
                x: min(max(Double(originalX), 0.0), Double(max(1, originalWidth) - 1)),
                y: min(max(Double(originalY), 0.0), Double(max(1, originalHeight) - 1))
            )
        }

        private func closeLocalDrawing() {
            let closedPoints: [CellBoundaryPoint]
            switch drawMode {
            case .polygon:
                closedPoints = localPolygonPoints
            case .freeDraw:
                closedPoints = convexHull(points: allFreeDrawPoints)
            }
            guard closedPoints.count >= 3 else { return }
            closedPolygonGroups.append(closedPoints)
            localPolygonPoints = []
            freeDrawStrokes = []
            activeFreeDrawStroke = []
            coordinator?.commitClosed(polygons: closedPolygonGroups)
        }

        private func clearLocalDrawing(commit: Bool) {
            closedPolygonGroups = []
            localPolygonPoints = []
            freeDrawStrokes = []
            activeFreeDrawStroke = []
            if commit {
                coordinator?.clearCommittedDrawing()
            }
        }

        private func shouldAppend(_ point: CellBoundaryPoint, to stroke: [CellBoundaryPoint]) -> Bool {
            guard let last = stroke.last else { return true }
            let dx = point.x - last.x
            let dy = point.y - last.y
            return dx * dx + dy * dy >= 4.0
        }

        private func drawDrawing(rect: CGRect, scale: CGFloat) {
            for polygon in closedPolygonGroups {
                drawPolygon(points: polygon, closed: true, showVertices: false, rect: rect, scale: scale)
            }
            switch drawMode {
            case .polygon:
                drawPolygon(points: localPolygonPoints, closed: false, showVertices: true, rect: rect, scale: scale)
            case .freeDraw:
                drawFreeDrawStrokes(rect: rect, scale: scale)
            }
        }

        private func drawFreeDrawStrokes(rect: CGRect, scale: CGFloat) {
            let strokes = freeDrawStrokes + (activeFreeDrawStroke.isEmpty ? [] : [activeFreeDrawStroke])
            guard !strokes.isEmpty else { return }
            for stroke in strokes where !stroke.isEmpty {
                let path = polygonPath(points: stroke, rect: rect, scale: scale, closed: false)
                path.lineWidth = 2.2
                path.lineCapStyle = .round
                path.lineJoinStyle = .round
                NSColor.white.withAlphaComponent(0.95).setStroke()
                path.stroke()

                let innerPath = polygonPath(points: stroke, rect: rect, scale: scale, closed: false)
                innerPath.lineWidth = 1.2
                innerPath.lineCapStyle = .round
                innerPath.lineJoinStyle = .round
                NSColor.controlAccentColor.withAlphaComponent(0.95).setStroke()
                innerPath.stroke()
            }
        }

        private func drawPolygon(
            points: [CellBoundaryPoint],
            closed: Bool,
            showVertices: Bool,
            rect: CGRect,
            scale: CGFloat
        ) {
            guard !points.isEmpty else { return }
            if closed, points.count >= 3 {
                NSColor.controlAccentColor.withAlphaComponent(0.20).setFill()
                polygonPath(points: points, rect: rect, scale: scale, closed: true)
                    .fill()
            }
            if points.count >= 2 {
                let outerPath = polygonPath(points: points, rect: rect, scale: scale, closed: closed)
                outerPath.lineWidth = 2.0
                outerPath.lineJoinStyle = .round
                NSColor.white.withAlphaComponent(0.95).setStroke()
                outerPath.stroke()

                let innerPath = polygonPath(points: points, rect: rect, scale: scale, closed: closed)
                innerPath.lineWidth = 1.0
                innerPath.lineJoinStyle = .round
                var dash: [CGFloat] = [6, 4]
                innerPath.setLineDash(&dash, count: dash.count, phase: 0)
                NSColor.controlAccentColor.withAlphaComponent(0.90).setStroke()
                innerPath.stroke()
            }
            if showVertices {
                for (index, point) in points.enumerated() {
                    drawVertex(index: index, point: point, rect: rect, scale: scale)
                }
            }
        }

        private func polygonPath(points: [CellBoundaryPoint], rect: CGRect, scale: CGFloat, closed: Bool) -> NSBezierPath {
            let path = NSBezierPath()
            guard let first = points.first else { return path }
            path.move(to: viewPoint(first, rect: rect, scale: scale))
            for point in points.dropFirst() {
                path.line(to: viewPoint(point, rect: rect, scale: scale))
            }
            if closed {
                path.close()
            }
            return path
        }

        private func drawVertex(index: Int, point: CellBoundaryPoint, rect: CGRect, scale: CGFloat) {
            let location = viewPoint(point, rect: rect, scale: scale)
            NSColor.white.setFill()
            NSBezierPath(ovalIn: NSRect(x: location.x - 6, y: location.y - 6, width: 12, height: 12)).fill()
            NSColor.controlAccentColor.setFill()
            NSBezierPath(ovalIn: NSRect(x: location.x - 3.5, y: location.y - 3.5, width: 7, height: 7)).fill()
            "\(index + 1)".draw(
                at: NSPoint(x: location.x - 4, y: location.y - 26),
                withAttributes: [
                    .font: NSFont.systemFont(ofSize: 9, weight: .bold),
                    .foregroundColor: NSColor.white
                ]
            )
        }

        private func viewPoint(_ point: CellBoundaryPoint, rect: CGRect, scale: CGFloat) -> CGPoint {
            let panelRect = editablePanelRect(in: rect, scale: scale)
            return CGPoint(
                x: panelRect.minX + CGFloat(point.x) * scale,
                y: panelRect.minY + CGFloat(point.y) * scale
            )
        }

        private func convexHull(points: [CellBoundaryPoint]) -> [CellBoundaryPoint] {
            let unique = Array(Set(points.map { QuantizedPoint($0) })).sorted { lhs, rhs in
                if lhs.x == rhs.x {
                    return lhs.y < rhs.y
                }
                return lhs.x < rhs.x
            }
            guard unique.count >= 3 else { return unique.map(\.point) }

            func cross(_ origin: QuantizedPoint, _ a: QuantizedPoint, _ b: QuantizedPoint) -> Int {
                (a.x - origin.x) * (b.y - origin.y) - (a.y - origin.y) * (b.x - origin.x)
            }

            var lower: [QuantizedPoint] = []
            for point in unique {
                while lower.count >= 2, cross(lower[lower.count - 2], lower[lower.count - 1], point) <= 0 {
                    lower.removeLast()
                }
                lower.append(point)
            }

            var upper: [QuantizedPoint] = []
            for point in unique.reversed() {
                while upper.count >= 2, cross(upper[upper.count - 2], upper[upper.count - 1], point) <= 0 {
                    upper.removeLast()
                }
                upper.append(point)
            }

            lower.removeLast()
            upper.removeLast()
            let hull = (lower + upper).map(\.point)
            return hull.count >= 3 ? hull : unique.map(\.point)
        }

        private struct QuantizedPoint: Hashable {
            let x: Int
            let y: Int
            let point: CellBoundaryPoint

            init(_ point: CellBoundaryPoint) {
                self.x = Int(point.x.rounded())
                self.y = Int(point.y.rounded())
                self.point = CellBoundaryPoint(x: Double(x), y: Double(y))
            }

            static func == (lhs: QuantizedPoint, rhs: QuantizedPoint) -> Bool {
                lhs.x == rhs.x && lhs.y == rhs.y
            }

            func hash(into hasher: inout Hasher) {
                hasher.combine(x)
                hasher.combine(y)
            }
        }

        private func isReturnKey(_ event: NSEvent) -> Bool {
            event.type == .keyDown
                && (event.keyCode == 36 || event.keyCode == 76 || event.charactersIgnoringModifiers == "\r")
        }
    }
}

private struct ManualRegionPolygonEditorView: View {
    let image: NSImage
    @Binding var points: [CellBoundaryPoint]

    var body: some View {
        GeometryReader { proxy in
            let imageSize = ImageExportService.pixelSize(for: image)
            let fitted = fittedImageRect(imageSize: imageSize, containerSize: proxy.size)

            ZStack(alignment: .topLeading) {
                Color.black
                Image(nsImage: image)
                    .resizable()
                    .frame(width: fitted.rect.width, height: fitted.rect.height)
                    .position(x: fitted.rect.midX, y: fitted.rect.midY)

                if points.count >= 3 {
                    polygonPath(points: points, rect: fitted.rect, scale: fitted.scale, closed: true)
                        .fill(Color.accentColor.opacity(0.24))
                }
                if points.count >= 2 {
                    polygonPath(points: points, rect: fitted.rect, scale: fitted.scale, closed: false)
                        .stroke(Color.white.opacity(0.95), style: StrokeStyle(lineWidth: 2.0, lineJoin: .round))
                    polygonPath(points: points, rect: fitted.rect, scale: fitted.scale, closed: false)
                        .stroke(Color.accentColor.opacity(0.90), style: StrokeStyle(lineWidth: 1.0, lineJoin: .round, dash: [6, 4]))
                }
                ForEach(Array(points.enumerated()), id: \.offset) { index, point in
                    let viewPoint = CGPoint(
                        x: fitted.rect.minX + CGFloat(point.x) * fitted.scale,
                        y: fitted.rect.minY + CGFloat(point.y) * fitted.scale
                    )
                    ZStack {
                        Circle()
                            .fill(Color.white)
                            .frame(width: 12, height: 12)
                        Circle()
                            .fill(Color.accentColor)
                            .frame(width: 7, height: 7)
                        Text("\(index + 1)")
                            .font(.system(size: 9, weight: .bold))
                            .foregroundStyle(.white)
                            .offset(x: 0, y: -16)
                    }
                    .position(viewPoint)
                }
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onEnded { value in
                        guard fitted.rect.contains(value.location) else { return }
                        let imageX = Double((value.location.x - fitted.rect.minX) / fitted.scale)
                        let imageY = Double((value.location.y - fitted.rect.minY) / fitted.scale)
                        points.append(
                            CellBoundaryPoint(
                                x: min(max(imageX, 0.0), Double(max(1, Int(imageSize.width)) - 1)),
                                y: min(max(imageY, 0.0), Double(max(1, Int(imageSize.height)) - 1))
                            )
                        )
                    }
            )
            .help("Click on the image to add polygon vertices.")
        }
    }

    private func fittedImageRect(imageSize: CGSize, containerSize: CGSize) -> (rect: CGRect, scale: CGFloat) {
        let imageWidth = max(1.0, imageSize.width)
        let imageHeight = max(1.0, imageSize.height)
        let scale = min(
            max(1.0, containerSize.width) / imageWidth,
            max(1.0, containerSize.height) / imageHeight
        )
        let width = imageWidth * scale
        let height = imageHeight * scale
        let rect = CGRect(
            x: (containerSize.width - width) / 2.0,
            y: (containerSize.height - height) / 2.0,
            width: width,
            height: height
        )
        return (rect, scale)
    }

    private func polygonPath(points: [CellBoundaryPoint], rect: CGRect, scale: CGFloat, closed: Bool) -> Path {
        var path = Path()
        guard let first = points.first else { return path }
        path.move(to: CGPoint(x: rect.minX + CGFloat(first.x) * scale, y: rect.minY + CGFloat(first.y) * scale))
        for point in points.dropFirst() {
            path.addLine(to: CGPoint(x: rect.minX + CGFloat(point.x) * scale, y: rect.minY + CGFloat(point.y) * scale))
        }
        if closed {
            path.closeSubpath()
        }
        return path
    }
}

struct CellDistributionView: View {
    @EnvironmentObject private var store: AppStore
    @State private var tab = "Region masks"

    var body: some View {
        VStack(spacing: 0) {
            Picker("", selection: $tab) {
                Text("Region masks").tag("Region masks")
                Text("Cell density").tag("Cell density")
                Text("Cell cluster distribution").tag("Cell cluster distribution")
            }
            .pickerStyle(.segmented)
            .frame(width: 560)
            .padding(.top, 16)

            Divider()
                .padding(.top, 16)

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    GroupBox(tab) {
                        VStack(alignment: .leading, spacing: 14) {
                            if tab == "Region masks" {
                                regionBoundaryPicker
                                ParameterSlider(title: "Band width (um)", value: $store.densityBandWidthUm, range: 0.5...100, step: 0.5)
                                Text("This tool builds distance bands on both sides of the selected Region analysis boundary, previews the band map, and automatically saves the base outputs.")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            } else if tab == "Cell density" {
                                if hasSavedRegionMaskOutput {
                                    Text("Uses the last generated Region masks output.")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    cellTypePicker
                                } else {
                                    Text("Generate Region masks first, then use that banded result for Cell density.")
                                        .foregroundStyle(.secondary)
                                }
                            } else if tab == "Cell cluster distribution" {
                                regionBoundaryPicker
                                clusterPicker
                                Text("Each occupied neighborhood tile is classified as inside or outside a saved Region-analysis mask by majority overlap. If a tile is exactly split 50/50 by the mask, the tile center pixel breaks the tie.")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }

                            Button {
                                syncSelectedCellDistributionMode()
                                let boundaryChoices = availableBoundaryChoices
                                if tab == "Region masks",
                                   !boundaryChoices.isEmpty,
                                   let id = singleBoundaryChoiceID(from: boundaryChoices) {
                                    store.cellDistributionSelectedRegionIDs = [id]
                                } else if tab == "Region masks", let id = singleBoundaryID(from: availableRegions) {
                                    store.cellDistributionSelectedRegionIDs = [id]
                                } else if tab == "Cell cluster distribution",
                                          store.cellDistributionSelectedRegionIDs.isEmpty,
                                          !boundaryChoices.isEmpty {
                                    store.cellDistributionSelectedRegionIDs = Set(defaultClusterBoundaryChoiceIDs(from: boundaryChoices))
                                } else if tab == "Cell cluster distribution", store.cellDistributionSelectedRegionIDs.isEmpty {
                                    store.cellDistributionSelectedRegionIDs = Set(defaultClusterBoundaryIDs(from: availableRegions))
                                }
                                store.runCellDistributionAnalysis(outputMode: cellDistributionOutputMode)
                            } label: {
                                Label(cellDistributionRunButtonTitle, systemImage: "play.fill")
                            }
                                .spatialScopeProminentButtonStyle()
                                .disabled(runButtonDisabled)
                            if let blocker = cellDistributionRunBlocker {
                                Text(blocker)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .padding(6)
                    }

                    if let result = store.cellDistributionResult {
                        if tab == "Region masks" {
                            GroupBox("Region band map") {
                                VStack(alignment: .leading, spacing: 12) {
                                    ZoomableImageView(image: result.image, backgroundColor: .white)
                                        .frame(maxWidth: .infinity, minHeight: 560, maxHeight: 760)
                                        .clipShape(RoundedRectangle(cornerRadius: 8))
                                    Text("\(result.regionSummaries.count) selected boundary region(s), \(result.totalCells) cells")
                                        .font(.headline)

                                    Text("Band summary")
                                        .font(.headline)
                                    Table(bandAreaRows(from: result)) {
                                        TableColumn("Boundary", value: \.regionName)
                                        TableColumn("Side", value: \.side)
                                            .width(80)
                                        TableColumn("Band") { row in
                                            Text("\(row.bandIndex)")
                                        }
                                        .width(64)
                                        TableColumn("Distance UM") { row in
                                            Text("\(row.distLoUm, format: .number.precision(.fractionLength(1)))-\(row.distHiUm, format: .number.precision(.fractionLength(1)))")
                                        }
                                        .width(120)
                                        TableColumn("Area UM2") { row in
                                            Text(row.areaUm2, format: .number.precision(.fractionLength(0)))
                                        }
                                        .width(110)
                                    }
                                    .frame(minHeight: 240)
                                }
                                .padding(6)
                            }
                        } else if tab == "Cell density", hasSavedDensityOutput {
                            GroupBox("Cell density by distance band") {
                                VStack(alignment: .leading, spacing: 12) {
                                    ZoomableImageView(image: result.densityImage, backgroundColor: .white)
                                        .frame(maxWidth: .infinity, minHeight: 220, maxHeight: 300)
                                        .clipShape(RoundedRectangle(cornerRadius: 8))

                                    Table(result.bandMetrics) {
                                        TableColumn("Region", value: \.regionName)
                                        TableColumn("Side", value: \.side)
                                            .width(80)
                                        TableColumn("Band") { row in
                                            Text("\(row.bandIndex)")
                                        }
                                        .width(64)
                                        TableColumn("Cell type", value: \.cellType)
                                        TableColumn("Cells") { row in
                                            Text("\(row.cellCount)")
                                        }
                                        .width(80)
                                        TableColumn("Density / mm2") { row in
                                            Text(row.densityCellsPerMm2, format: .number.precision(.fractionLength(1)))
                                        }
                                        .width(120)
                                    }
                                    .frame(minHeight: 240)

                                    Text("Region metrics")
                                        .font(.headline)
                                    Table(result.regionSummaries) {
                                        TableColumn("Region") { row in
                                            Text("\(row.regionID)")
                                        }
                                        .width(70)
                                        TableColumn("Type", value: \.dominantType)
                                        TableColumn("Cells") { row in
                                            Text("\(row.totalCells)")
                                        }
                                        .width(80)
                                    }
                                    .frame(minHeight: 240)
                                }
                                .padding(6)
                            }
                        } else if tab == "Cell density" {
                            EmptyStateView(
                                systemImage: "chart.xyaxis.line",
                                title: "No cell density result yet",
                                message: hasSavedRegionMaskOutput ? "Choose cell types, then generate cell density." : "Generate Region masks first."
                            )
                            .frame(minHeight: 320)
                        } else if hasSavedClusterOutput {
                            GroupBox("Cell cluster distribution") {
                                VStack(alignment: .leading, spacing: 12) {
                                    ZoomableImageView(image: result.clusterImage, backgroundColor: .white)
                                        .frame(maxWidth: .infinity, minHeight: 300, maxHeight: 520)
                                        .clipShape(RoundedRectangle(cornerRadius: 8))

                                    Text("Cluster-by-region metrics")
                                        .font(.headline)
                                    Table(result.clusterMetrics) {
                                        TableColumn("Boundary", value: \.regionName)
                                        TableColumn("Side", value: \.regionKey)
                                            .width(80)
                                        TableColumn("Cluster") { row in
                                            Text("#\(row.clusterID) \(row.clusterLabel)")
                                        }
                                        TableColumn("Tiles") { row in
                                            Text("\(row.occupiedTileCount)")
                                        }
                                        .width(80)
                                        TableColumn("Cells") { row in
                                            Text("\(row.totalCellsInTiles)")
                                        }
                                        .width(80)
                                        TableColumn("Mean inside fraction") { row in
                                            Text(row.meanInsideFraction, format: .number.precision(.fractionLength(3)))
                                        }
                                        .width(150)
                                    }
                                    .frame(minHeight: 240)

                                    Text("Tile classifications preview")
                                        .font(.headline)
                                    Table(Array(result.tileClassifications.prefix(200))) {
                                        TableColumn("Boundary", value: \.regionName)
                                        TableColumn("Side", value: \.regionKey)
                                            .width(80)
                                        TableColumn("Tile") { row in
                                            Text("\(row.tileRow), \(row.tileColumn)")
                                        }
                                        .width(90)
                                        TableColumn("Cluster") { row in
                                            Text("#\(row.clusterID)")
                                        }
                                        .width(72)
                                        TableColumn("Inside fraction") { row in
                                            Text(row.insideFraction, format: .number.precision(.fractionLength(3)))
                                        }
                                        .width(130)
                                    }
                                    .frame(minHeight: 220)
                                }
                                .padding(6)
                            }
                        } else {
                            EmptyStateView(
                                systemImage: "square.grid.3x3",
                                title: "No cluster distribution result yet",
                                message: "Choose Region-analysis boundaries and neighborhood clusters, then generate cell cluster distribution."
                            )
                            .frame(minHeight: 320)
                        }
                    } else {
                        EmptyStateView(
                            systemImage: "chart.xyaxis.line",
                            title: "No distribution result yet",
                            message: "Run region analysis first, then generate cell distribution outputs."
                        )
                        .frame(minHeight: 320)
                    }

                    StatusBarView()
                }
                .padding(SpatialScopeDesign.contentPadding)
            }
        }
        .onAppear {
            syncSelectedCellDistributionMode()
        }
        .onChange(of: tab) { _ in
            syncSelectedCellDistributionMode()
        }
    }

    private var availableRegions: [RegionROI] {
        let regions = store.regionAnalysisResult?.regions
            ?? OutputWriter.loadRegionAnalysisResult(outputFolder: store.outputFolder)?.regions
            ?? []
        return regions.sorted { $0.id < $1.id }
    }

    private var availableBoundaryChoices: [CellDistributionBoundaryChoice] {
        OutputWriter.loadCellDistributionBoundaryChoices(outputFolder: store.outputFolder)
            .sorted {
                if $0.id == $1.id {
                    return $0.label.localizedStandardCompare($1.label) == .orderedAscending
                }
                return $0.id < $1.id
            }
    }

    private var availableClusters: [NeighborhoodClusterCount] {
        let clusters = store.neighborhoodAnalysisResult?.clusterCounts
            ?? OutputWriter.loadNeighborhoodAnalysisResult(outputFolder: store.outputFolder)?.clusterCounts
            ?? []
        return clusters.sorted { $0.clusterID < $1.clusterID }
    }

    private var cellDistributionRunButtonTitle: String {
        switch tab {
        case "Region masks":
            return "Generate region masks"
        case "Cell density":
            return "Generate cell density"
        default:
            return "Generate cell cluster distribution"
        }
    }

    private var cellDistributionOutputMode: CellDistributionOutputMode {
        switch tab {
        case "Region masks":
            return .regionMasks
        case "Cell density":
            return .cellDensity
        default:
            return .cellClusterDistribution
        }
    }

    private var runButtonDisabled: Bool {
        if store.isBusy || cellDistributionRunBlocker != nil { return true }
        return false
    }

    private var cellDistributionRunBlocker: String? {
        if store.pixelSize == nil {
            return "Set figure resolution in Inputs before running Cell Distribution analysis."
        }
        if !hasCellTypeAssignmentOutput {
            return "Run cell-type assignment before Cell Distribution analysis."
        }
        switch tab {
        case "Region masks":
            return (availableBoundaryChoices.isEmpty && availableRegions.isEmpty)
                ? "Run region analysis to create at least one boundary mask."
                : nil
        case "Cell density":
            if !hasSavedRegionMaskOutput {
                return "Generate Region masks first, then use that banded result for Cell density."
            }
            return cellDensitySelectableCellTypes.isEmpty
                ? "Run cell-type assignment to enable cell-type selection."
                : nil
        default:
            if availableBoundaryChoices.isEmpty && availableRegions.isEmpty {
                return "Run region analysis to create at least one boundary mask."
            }
            return availableClusters.isEmpty
                ? "Run neighborhood analysis to enable cluster selection."
                : nil
        }
    }

    private var hasCellTypeAssignmentOutput: Bool {
        store.cellTypeAssignmentResult != nil
            || OutputWriter.loadCellTypeAssignmentResult(outputFolder: store.outputFolder) != nil
    }

    private func syncSelectedCellDistributionMode() {
        store.selectedCellDistributionOutputMode = cellDistributionOutputMode
    }

    private var hasSavedRegionMaskOutput: Bool {
        firstDistributionOutputFile(in: "01_region_masks", prefix: "region_bands__", suffix: "__arrays.npz") != nil
    }

    private var hasSavedDensityOutput: Bool {
        firstDistributionOutputFile(in: "02_cell_density", prefix: "cell_density__", suffix: "__plot.png") != nil
    }

    private var hasSavedClusterOutput: Bool {
        firstDistributionOutputFile(in: "03_cell_cluster_distribution", prefix: "cell_cluster_distribution__", suffix: "__heatmap.png") != nil
    }

    private func firstDistributionOutputFile(in subdirectory: String, prefix: String, suffix: String) -> URL? {
        let directory = store.outputFolder
            .appendingPathComponent("10_cell_distribution_analysis")
            .appendingPathComponent(subdirectory)
        guard let urls = try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        ) else {
            return nil
        }
        return urls
            .filter { url in
                let values = try? url.resourceValues(forKeys: [.isRegularFileKey])
                return values?.isRegularFile == true
                    && url.lastPathComponent.hasPrefix(prefix)
                    && url.lastPathComponent.hasSuffix(suffix)
            }
            .sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }
            .first
    }

    @ViewBuilder
    private var regionBoundaryPicker: some View {
        let choices = availableBoundaryChoices
        let regions = availableRegions
        if choices.isEmpty && regions.isEmpty {
            Text("Run region analysis to enable Region-analysis boundary selection.")
                .foregroundStyle(.secondary)
        } else if !choices.isEmpty, tab == "Region masks" || tab == "Cell density" {
            Picker("Boundary from Region analysis", selection: singleBoundaryChoiceSelectionBinding(choices)) {
                ForEach(choices) { choice in
                    Text(boundaryChoiceTitle(choice)).tag(choice.id)
                }
            }
            .pickerStyle(.menu)
        } else if !choices.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Region-analysis boundaries")
                    .font(.headline)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), alignment: .leading)], alignment: .leading, spacing: 8) {
                    ForEach(choices) { choice in
                        Toggle(boundaryChoiceTitle(choice), isOn: boundaryChoiceSelectionBinding(choice.id, availableIDs: choices.map(\.id)))
                            .toggleStyle(.checkbox)
                    }
                }
                Button {
                    store.cellDistributionSelectedRegionIDs = Set(choices.map(\.id))
                } label: {
                    Label("Use All Boundaries", systemImage: "checklist")
                }
            }
        } else if tab == "Region masks" || tab == "Cell density" {
            Picker("Boundary from Region analysis", selection: singleRegionSelectionBinding(regions)) {
                ForEach(regions) { region in
                    Text(regionSelectionTitle(region)).tag(region.id)
                }
            }
            .pickerStyle(.menu)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("Region-analysis boundaries")
                    .font(.headline)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 210), alignment: .leading)], alignment: .leading, spacing: 8) {
                    ForEach(regions) { region in
                        Toggle(regionSelectionTitle(region), isOn: regionSelectionBinding(region.id, availableIDs: regions.map(\.id)))
                            .toggleStyle(.checkbox)
                    }
                }
                Button {
                    store.cellDistributionSelectedRegionIDs = Set(regions.map(\.id))
                } label: {
                    Label("Use All Boundaries", systemImage: "checklist")
                }
            }
        }
    }

    private func singleBoundaryChoiceID(from choices: [CellDistributionBoundaryChoice]) -> Int? {
        let available = Set(choices.map(\.id))
        if let selected = store.cellDistributionSelectedRegionIDs.sorted().first,
           available.contains(selected) {
            return selected
        }
        return choices.first?.id
    }

    private func singleBoundaryChoiceSelectionBinding(_ choices: [CellDistributionBoundaryChoice]) -> Binding<Int> {
        Binding(
            get: {
                singleBoundaryChoiceID(from: choices) ?? 0
            },
            set: { id in
                store.cellDistributionSelectedRegionIDs = [id]
            }
        )
    }

    private func singleBoundaryID(from regions: [RegionROI]) -> Int? {
        let available = Set(regions.map(\.id))
        if let selected = store.cellDistributionSelectedRegionIDs.sorted().first,
           available.contains(selected) {
            return selected
        }
        return regions.first?.id
    }

    private func singleRegionSelectionBinding(_ regions: [RegionROI]) -> Binding<Int> {
        Binding(
            get: {
                singleBoundaryID(from: regions) ?? 0
            },
            set: { id in
                store.cellDistributionSelectedRegionIDs = [id]
            }
        )
    }

    private func defaultClusterBoundaryIDs(from regions: [RegionROI]) -> [Int] {
        Array(regions.prefix(min(3, regions.count)).map(\.id))
    }

    private func defaultClusterBoundaryChoiceIDs(from choices: [CellDistributionBoundaryChoice]) -> [Int] {
        Array(choices.prefix(min(3, choices.count)).map(\.id))
    }

    @ViewBuilder
    private var cellTypePicker: some View {
        let cellTypes = cellDensitySelectableCellTypes
        if cellTypes.isEmpty {
            Text("Run cell-type assignment to enable cell-type selection.")
                .foregroundStyle(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("Cell types for density")
                    .font(.headline)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 170), alignment: .leading)], alignment: .leading, spacing: 8) {
                    ForEach(cellTypes, id: \.self) { cellType in
                        Toggle(cellType, isOn: cellTypeSelectionBinding(cellType, availableTypes: cellTypes))
                            .toggleStyle(.checkbox)
                    }
                }
            }
        }
    }

    private var cellDensitySelectableCellTypes: [String] {
        let configured = configuredCellTypeNames()
        if !configured.isEmpty {
            return configured
        }
        return store.assignedCellTypeNames
    }

    private func configuredCellTypeNames() -> [String] {
        let savedConfig = OutputWriter.loadCellTypeConfig(outputFolder: store.outputFolder)
        let source = (savedConfig?.isEmpty == false ? savedConfig : store.cellTypes) ?? []
        var seen: Set<String> = []
        var names: [String] = []
        for cellType in source {
            let name = cellType.name.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty, !seen.contains(name) else { continue }
            seen.insert(name)
            names.append(name)
        }
        return names
    }

    @ViewBuilder
    private var clusterPicker: some View {
        let clusters = availableClusters
        if clusters.isEmpty {
            Text("Run neighborhood analysis to enable cluster selection.")
                .foregroundStyle(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text("Neighborhood cluster types")
                    .font(.headline)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), alignment: .leading)], alignment: .leading, spacing: 8) {
                    ForEach(clusters) { cluster in
                        Toggle(clusterSelectionTitle(cluster), isOn: clusterSelectionBinding(cluster.clusterID, availableIDs: clusters.map(\.clusterID)))
                            .toggleStyle(.checkbox)
                    }
                }
                Button {
                    store.cellDistributionSelectedClusterIDs = []
                } label: {
                    Label("Use All Clusters", systemImage: "checklist")
                }
            }
        }
    }

    private func regionSelectionTitle(_ region: RegionROI) -> String {
        let typeName = region.sourceType ?? region.dominantType
        return "#\(region.id) \(typeName)"
    }

    private func boundaryChoiceTitle(_ choice: CellDistributionBoundaryChoice) -> String {
        choice.label.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func clusterSelectionTitle(_ cluster: NeighborhoodClusterCount) -> String {
        "#\(cluster.clusterID) \(cluster.clusterLabel)"
    }

    private func regionSelectionBinding(_ id: Int, availableIDs: [Int]) -> Binding<Bool> {
        Binding(
            get: {
                store.cellDistributionSelectedRegionIDs.contains(id)
            },
            set: { isSelected in
                let all = Set(availableIDs)
                var selected = store.cellDistributionSelectedRegionIDs
                if isSelected {
                    selected.insert(id)
                } else if selected.count > 1 {
                    selected.remove(id)
                }
                store.cellDistributionSelectedRegionIDs = selected.intersection(all)
            }
        )
    }

    private func boundaryChoiceSelectionBinding(_ id: Int, availableIDs: [Int]) -> Binding<Bool> {
        Binding(
            get: {
                store.cellDistributionSelectedRegionIDs.contains(id)
            },
            set: { isSelected in
                let all = Set(availableIDs)
                var selected = store.cellDistributionSelectedRegionIDs
                if isSelected {
                    selected.insert(id)
                } else if selected.count > 1 {
                    selected.remove(id)
                }
                store.cellDistributionSelectedRegionIDs = selected.intersection(all)
            }
        )
    }

    private func cellTypeSelectionBinding(_ cellType: String, availableTypes: [String]) -> Binding<Bool> {
        Binding(
            get: {
                store.cellDistributionSelectedCellTypes.isEmpty
                    || store.cellDistributionSelectedCellTypes.contains(cellType)
            },
            set: { isSelected in
                let all = Set(availableTypes)
                var selected = store.cellDistributionSelectedCellTypes.isEmpty
                    ? all
                    : store.cellDistributionSelectedCellTypes.intersection(all)
                if isSelected {
                    selected.insert(cellType)
                } else if selected.count > 1 {
                    selected.remove(cellType)
                }
                store.cellDistributionSelectedCellTypes = selected == all ? [] : selected
            }
        )
    }

    private func clusterSelectionBinding(_ id: Int, availableIDs: [Int]) -> Binding<Bool> {
        Binding(
            get: {
                store.cellDistributionSelectedClusterIDs.isEmpty || store.cellDistributionSelectedClusterIDs.contains(id)
            },
            set: { isSelected in
                let all = Set(availableIDs)
                var selected = store.cellDistributionSelectedClusterIDs.isEmpty ? all : store.cellDistributionSelectedClusterIDs
                if isSelected {
                    selected.insert(id)
                } else if selected.count > 1 {
                    selected.remove(id)
                }
                store.cellDistributionSelectedClusterIDs = selected == all ? [] : selected
            }
        )
    }

    private func bandAreaRows(from result: CellDistributionAnalysisResult) -> [CellDistributionBandAreaRow] {
        let grouped = Dictionary(grouping: result.bandMetrics) { row in
            "\(row.regionID)|\(row.regionName)|\(row.side)|\(row.bandIndex)"
        }
        return grouped.compactMap { _, rows in
            guard let row = rows.first else { return nil }
            return CellDistributionBandAreaRow(
                regionID: row.regionID,
                regionName: row.regionName,
                side: row.side,
                bandIndex: row.bandIndex,
                distLoUm: row.distLoUm,
                distHiUm: row.distHiUm,
                areaPx: row.areaPx,
                areaUm2: row.areaUm2
            )
        }
        .sorted {
            if $0.regionID != $1.regionID { return $0.regionID < $1.regionID }
            if $0.side != $1.side { return $0.side < $1.side }
            return $0.bandIndex < $1.bandIndex
        }
    }
}

private struct CellDistributionBandAreaRow: Identifiable {
    var id: String { "\(regionID)-\(side)-\(bandIndex)" }
    var regionID: Int
    var regionName: String
    var side: String
    var bandIndex: Int
    var distLoUm: Double
    var distHiUm: Double
    var areaPx: Int
    var areaUm2: Double
}

struct DistanceAnalysisView: View {
    @EnvironmentObject private var store: AppStore
    @State private var tab = "Nearest-neighbor distances"
    @State private var nearestTargetType = ""
    @State private var nearestQueryTypes: Set<String> = []
    @State private var boundaryChoiceID = 0
    @State private var boundaryQueryTypes: Set<String> = []
    @State private var boundaryFilter: DistanceBoundaryRegionFilter = .all

    var body: some View {
        VStack(spacing: 0) {
            Picker("", selection: $tab) {
                Text("Nearest-neighbor distances").tag("Nearest-neighbor distances")
                Text("Cell-to-boundary distances").tag("Cell-to-boundary distances")
            }
            .pickerStyle(.segmented)
            .frame(width: 470)
            .padding(.top, 16)

            Divider()
                .padding(.top, 16)

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    GroupBox("Distance analysis") {
                        VStack(alignment: .leading, spacing: 12) {
                            if tab == "Nearest-neighbor distances" {
                                nearestNeighborControls
                            } else {
                                boundaryDistanceControls
                            }
                        }
                        .padding(6)
                    }

                    if let result = store.distanceAnalysisResult {
                        GroupBox(tab) {
                            if tab == "Nearest-neighbor distances" {
                                nearestNeighborResult(result)
                            } else {
                                boundaryDistanceResult(result)
                            }
                        }

                        if !result.summaries.isEmpty {
                            GroupBox("Distance summary") {
                                distanceSummaryTable(result.summaries)
                                    .frame(minHeight: 130)
                                    .padding(6)
                            }
                        }
                    } else {
                        EmptyStateView(
                            systemImage: "ruler",
                            title: "No distance result yet",
                            message: "Run cell-type assignment first. Boundary distances also need a saved Region-analysis boundary mask."
                        )
                        .frame(minHeight: 320)
                    }

                    StatusBarView()
                }
                .padding(SpatialScopeDesign.contentPadding)
            }
        }
    }

    @ViewBuilder
    private var nearestNeighborControls: some View {
        let cellTypes = distanceCellTypes
        if cellTypes.isEmpty {
            Text("Run cell-type assignment to enable nearest-neighbor distance analysis.")
                .foregroundStyle(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top, spacing: 18) {
                    Picker("Target cell type", selection: nearestTargetBinding(cellTypes: cellTypes)) {
                        ForEach(cellTypes, id: \.self) { cellType in
                            Text(cellType).tag(cellType)
                        }
                    }
                    .pickerStyle(.menu)
                    .frame(minWidth: 240)

                    VStack(alignment: .leading, spacing: 8) {
                        Text("Query cell types")
                            .font(.headline)
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), alignment: .leading)], alignment: .leading, spacing: 8) {
                            ForEach(cellTypes, id: \.self) { cellType in
                                Toggle(cellType, isOn: nearestQueryBinding(cellType, availableTypes: cellTypes))
                                    .toggleStyle(.checkbox)
                            }
                        }
                    }
                }

                HStack {
                    Button {
                        store.runNearestNeighborDistanceAnalysis(
                            targetType: resolvedNearestTarget(cellTypes: cellTypes),
                            queryTypes: resolvedNearestQueryTypes(cellTypes: cellTypes)
                        )
                    } label: {
                        Label("Compute nearest-neighbor distances", systemImage: "play.fill")
                    }
                    .spatialScopeProminentButtonStyle()
                    .disabled(store.isBusy || resolvedNearestTarget(cellTypes: cellTypes).isEmpty || resolvedNearestQueryTypes(cellTypes: cellTypes).isEmpty)

                    Text("\(resolvedNearestQueryTypes(cellTypes: cellTypes).count) query type(s) selected")
                        .foregroundStyle(.secondary)
                    Spacer()
                }
            }
        }
    }

    @ViewBuilder
    private var boundaryDistanceControls: some View {
        let cellTypes = distanceCellTypes
        let choices = distanceBoundaryChoices
        if cellTypes.isEmpty {
            Text("Run cell-type assignment to enable cell-to-boundary distance analysis.")
                .foregroundStyle(.secondary)
        } else if choices.isEmpty {
            Text("No boundary masks were found yet. Save at least one computational ROI or adjusted ROI in Region analysis first.")
                .foregroundStyle(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top, spacing: 18) {
                    Picker("Boundary / ROI", selection: boundaryChoiceBinding(choices: choices)) {
                        ForEach(choices) { choice in
                            Text(boundaryChoiceTitle(choice)).tag(choice.id)
                        }
                    }
                    .pickerStyle(.menu)
                    .frame(minWidth: 280)

                    Picker("Filter", selection: $boundaryFilter) {
                        ForEach(DistanceBoundaryRegionFilter.allCases) { filter in
                            Text(filter.title).tag(filter)
                        }
                    }
                    .pickerStyle(.menu)
                    .frame(minWidth: 220)

                    VStack(alignment: .leading, spacing: 8) {
                        Text("Query cell types")
                            .font(.headline)
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), alignment: .leading)], alignment: .leading, spacing: 8) {
                            ForEach(cellTypes, id: \.self) { cellType in
                                Toggle(cellType, isOn: boundaryQueryBinding(cellType, availableTypes: cellTypes))
                                    .toggleStyle(.checkbox)
                            }
                        }
                    }
                }

                HStack {
                    Button {
                        if let choice = resolvedBoundaryChoice(choices: choices) {
                            store.runBoundaryDistanceAnalysis(
                                boundaryChoice: choice,
                                queryTypes: resolvedBoundaryQueryTypes(cellTypes: cellTypes),
                                regionFilter: boundaryFilter
                            )
                        }
                    } label: {
                        Label("Compute boundary distances", systemImage: "play.fill")
                    }
                    .spatialScopeProminentButtonStyle()
                    .disabled(store.isBusy || resolvedBoundaryChoice(choices: choices) == nil || resolvedBoundaryQueryTypes(cellTypes: cellTypes).isEmpty)

                    if let choice = resolvedBoundaryChoice(choices: choices) {
                        Text("\(boundaryChoiceTitle(choice)); \(resolvedBoundaryQueryTypes(cellTypes: cellTypes).count) query type(s)")
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
            }
        }
    }

    @ViewBuilder
    private func nearestNeighborResult(_ result: DistanceAnalysisResult) -> some View {
        if result.nearestDistances.isEmpty {
            EmptyStateView(
                systemImage: "point.3.connected.trianglepath.dotted",
                title: "No nearest-neighbor result yet",
                message: "Choose target and query cell types, then compute nearest-neighbor distances."
            )
            .frame(minHeight: 320)
        } else {
            VStack(alignment: .leading, spacing: 12) {
            ZoomableImageView(image: result.nearestHistogramImage, backgroundColor: .white)
                .id("\(result.id.uuidString)-nearest-boxplot")
                .frame(maxWidth: .infinity, minHeight: 330, maxHeight: 520)
                .clipShape(RoundedRectangle(cornerRadius: 8))

                Text("Distances preview")
                    .font(.headline)
                Table(Array(result.nearestDistances.prefix(200))) {
                    TableColumn("Target type", value: \.assignedType)
                    TableColumn("Target cell") { row in
                        Text("\(row.nucleusID)")
                    }
                    .width(90)
                    TableColumn("Query type") { row in
                        Text(row.nearestType ?? "-")
                    }
                    TableColumn("Query cell") { row in
                        Text(row.nearestNucleusID.map(String.init) ?? "-")
                    }
                    .width(90)
                    TableColumn("Distance UM") { row in
                        Text(row.nearestDistanceUm, format: .number.precision(.fractionLength(2)))
                    }
                    .width(120)
                }
                .frame(minHeight: 260)

                if !result.nearestTTests.isEmpty {
                    Text("Paired t-tests")
                        .font(.headline)
                    distanceTTestTable(result.nearestTTests)
                        .frame(minHeight: 130)
                }
            }
            .padding(6)
        }
    }

    @ViewBuilder
    private func boundaryDistanceResult(_ result: DistanceAnalysisResult) -> some View {
        if result.boundaryDistances.isEmpty {
            EmptyStateView(
                systemImage: "ruler",
                title: "No boundary distance result yet",
                message: "Choose a Region-analysis boundary, query cell types, and a filter, then compute boundary distances."
            )
            .frame(minHeight: 320)
        } else {
            VStack(alignment: .leading, spacing: 12) {
            ZoomableImageView(image: result.boundaryHistogramImage, backgroundColor: .white)
                .id("\(result.id.uuidString)-boundary-boxplot")
                .frame(maxWidth: .infinity, minHeight: 330, maxHeight: 520)
                .clipShape(RoundedRectangle(cornerRadius: 8))

                Text("Distances preview")
                    .font(.headline)
                Table(Array(result.boundaryDistances.prefix(200))) {
                    TableColumn("Boundary") { row in
                        Text(row.boundaryName ?? "-")
                    }
                    TableColumn("Query type", value: \.assignedType)
                    TableColumn("Query cell") { row in
                        Text("\(row.nucleusID)")
                    }
                    .width(90)
                    TableColumn("Inside") { row in
                        Text(row.insideRegion.map { $0 ? "Yes" : "No" } ?? "-")
                    }
                    .width(75)
                    TableColumn("Distance UM") { row in
                        Text(row.distanceToBoundaryUm, format: .number.precision(.fractionLength(2)))
                    }
                    .width(120)
                }
                .frame(minHeight: 260)

                if !result.boundaryTTests.isEmpty {
                    Text("P-value statistics")
                        .font(.headline)
                    distanceTTestTable(result.boundaryTTests)
                        .frame(minHeight: 130)
                }
            }
            .padding(6)
        }
    }

    private func distanceSummaryTable(_ summaries: [DistanceSummary]) -> some View {
        Table(summaries) {
            TableColumn("Metric", value: \.metric)
            TableColumn("Count") { row in
                Text("\(row.count)")
            }
            .width(80)
            TableColumn("Mean UM") { row in
                Text(row.meanUm, format: .number.precision(.fractionLength(2)))
            }
            .width(100)
            TableColumn("Median UM") { row in
                Text(row.medianUm, format: .number.precision(.fractionLength(2)))
            }
            .width(110)
            TableColumn("Max UM") { row in
                Text(row.maxUm, format: .number.precision(.fractionLength(2)))
            }
            .width(90)
        }
    }

    private func distanceTTestTable(_ rows: [DistanceTTest]) -> some View {
        Table(rows) {
            TableColumn("Test", value: \.test)
            TableColumn("Reference", value: \.ref)
            TableColumn("Comparison", value: \.cmp)
            TableColumn("N") { row in
                if let nPairs = row.nPairs {
                    Text("\(nPairs)")
                } else {
                    Text("\(row.nRef ?? 0) / \(row.nCmp ?? 0)")
                }
            }
            .width(90)
            TableColumn("t") { row in
                Text(row.t, format: .number.precision(.fractionLength(3)))
            }
            .width(80)
            TableColumn("p") { row in
                Text(row.p, format: .number.precision(.significantDigits(3)))
            }
            .width(90)
        }
    }

    private var distanceCellTypes: [String] {
        let assignments = (store.cellTypeAssignmentResult ?? OutputWriter.loadCellTypeAssignmentResult(outputFolder: store.outputFolder))?.assignments ?? []
        return Array(Set(assignments.map(\.assignedType).filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }))
            .sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }

    private var distanceBoundaryChoices: [CellDistributionBoundaryChoice] {
        OutputWriter.loadCellDistributionBoundaryChoices(outputFolder: store.outputFolder)
            .sorted {
                if $0.id == $1.id {
                    return $0.label.localizedStandardCompare($1.label) == .orderedAscending
                }
                return $0.id < $1.id
            }
    }

    private func nearestTargetBinding(cellTypes: [String]) -> Binding<String> {
        Binding(
            get: { resolvedNearestTarget(cellTypes: cellTypes) },
            set: { nearestTargetType = $0 }
        )
    }

    private func resolvedNearestTarget(cellTypes: [String]) -> String {
        if cellTypes.contains(nearestTargetType) {
            return nearestTargetType
        }
        return cellTypes.first ?? ""
    }

    private func resolvedNearestQueryTypes(cellTypes: [String]) -> [String] {
        let selected = nearestQueryTypes.intersection(Set(cellTypes))
        let resolved = selected.isEmpty ? Set(cellTypes.prefix(1)) : selected
        return cellTypes.filter { resolved.contains($0) }
    }

    private func resolvedBoundaryQueryTypes(cellTypes: [String]) -> [String] {
        let selected = boundaryQueryTypes.intersection(Set(cellTypes))
        let resolved = selected.isEmpty ? Set(cellTypes.prefix(1)) : selected
        return cellTypes.filter { resolved.contains($0) }
    }

    private func nearestQueryBinding(_ cellType: String, availableTypes: [String]) -> Binding<Bool> {
        Binding(
            get: { resolvedNearestQueryTypes(cellTypes: availableTypes).contains(cellType) },
            set: { isSelected in
                var selected = Set(resolvedNearestQueryTypes(cellTypes: availableTypes))
                if isSelected {
                    selected.insert(cellType)
                } else if selected.count > 1 {
                    selected.remove(cellType)
                }
                nearestQueryTypes = selected
            }
        )
    }

    private func boundaryQueryBinding(_ cellType: String, availableTypes: [String]) -> Binding<Bool> {
        Binding(
            get: { resolvedBoundaryQueryTypes(cellTypes: availableTypes).contains(cellType) },
            set: { isSelected in
                var selected = Set(resolvedBoundaryQueryTypes(cellTypes: availableTypes))
                if isSelected {
                    selected.insert(cellType)
                } else if selected.count > 1 {
                    selected.remove(cellType)
                }
                boundaryQueryTypes = selected
            }
        )
    }

    private func boundaryChoiceBinding(choices: [CellDistributionBoundaryChoice]) -> Binding<Int> {
        Binding(
            get: { resolvedBoundaryChoice(choices: choices)?.id ?? 0 },
            set: { boundaryChoiceID = $0 }
        )
    }

    private func resolvedBoundaryChoice(choices: [CellDistributionBoundaryChoice]) -> CellDistributionBoundaryChoice? {
        if let selected = choices.first(where: { $0.id == boundaryChoiceID }) {
            return selected
        }
        return choices.first
    }

    private func boundaryChoiceTitle(_ choice: CellDistributionBoundaryChoice) -> String {
        choice.label.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
