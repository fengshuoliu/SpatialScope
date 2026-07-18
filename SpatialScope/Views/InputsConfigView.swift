import SwiftUI

struct InputsConfigView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: SpatialScopeDesign.sectionSpacing) {
                GroupBox("Data locations") {
                    VStack(spacing: 12) {
                        FolderRow(
                            title: "Input folder",
                            url: store.inputFolder,
                            systemImage: "folder",
                            action: store.chooseInputFolder
                        )
                        Divider()
                        FolderRow(
                            title: "Output folder",
                            url: store.outputFolder,
                            systemImage: "tray.full",
                            action: store.chooseOutputFolder
                        )
                    }
                    .padding(6)
                }

                HStack {
                    Button {
                        store.scanInputFolder()
                    } label: {
                        Label("Rescan CSV Files", systemImage: "arrow.clockwise")
                    }
                    Button {
                        store.resetMarkerNamesFromFiles()
                    } label: {
                        Label("Reset Marker Names", systemImage: "textformat")
                    }
                    Button {
                        store.reassignColors()
                    } label: {
                        Label("Reassign Colors", systemImage: "paintpalette")
                    }
                    Spacer()
                }
                .controlSize(.regular)

                GroupBox("Channel registry") {
                    VStack(alignment: .leading, spacing: 10) {
                        if store.channels.isEmpty {
                            EmptyStateView(
                                systemImage: "doc.text.magnifyingglass",
                                title: "No CSV files found",
                                message: "Choose an input folder containing ImageJ-exported CSV text images."
                            )
                            .frame(minHeight: 220)
                        } else {
                            ChannelHeaderRow()
                            ForEach($store.channels) { $channel in
                                ChannelConfigRow(channel: $channel)
                                Divider()
                            }
                        }
                    }
                    .padding(6)
                }

                HStack(alignment: .top, spacing: SpatialScopeDesign.sectionSpacing) {
                    GroupBox("Spatial calibration") {
                        VStack(alignment: .leading, spacing: 12) {
                            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                                GridRow {
                                    Text("X axis")
                                        .font(.caption.weight(.semibold))
                                        .foregroundStyle(.secondary)
                                    Text("um")
                                        .lineLimit(1)
                                        .fixedSize(horizontal: true, vertical: false)
                                    TextField("0", value: $store.xUm, format: .number)
                                        .textFieldStyle(.roundedBorder)
                                        .frame(width: 86)
                                    Text("px")
                                        .lineLimit(1)
                                        .fixedSize(horizontal: true, vertical: false)
                                    TextField("0", value: $store.xPx, format: .number)
                                        .textFieldStyle(.roundedBorder)
                                        .frame(width: 78)
                                }

                                GridRow {
                                    Text("Y axis")
                                        .font(.caption.weight(.semibold))
                                        .foregroundStyle(.secondary)
                                    Text("um")
                                        .lineLimit(1)
                                        .fixedSize(horizontal: true, vertical: false)
                                    TextField("0", value: $store.yUm, format: .number)
                                        .textFieldStyle(.roundedBorder)
                                        .frame(width: 86)
                                    Text("px")
                                        .lineLimit(1)
                                        .fixedSize(horizontal: true, vertical: false)
                                    TextField("0", value: $store.yPx, format: .number)
                                        .textFieldStyle(.roundedBorder)
                                        .frame(width: 78)
                                }
                            }
                            Label(store.pixelSizeText, systemImage: "ruler")
                                .foregroundStyle(.secondary)
                        }
                        .padding(6)
                    }
                    .frame(maxWidth: .infinity, alignment: .topLeading)

                    GroupBox("Composite image settings") {
                        VStack(alignment: .leading, spacing: 14) {
                            Picker("White overlay channel", selection: $store.whiteChannelID) {
                                Text("None").tag(UUID?.none)
                                ForEach(store.channels) { channel in
                                    Text(channel.channelName).tag(Optional(channel.id))
                                }
                            }

                            HStack(spacing: 10) {
                                Text("White weight")
                                Slider(value: $store.whiteWeight, in: 0...1, step: 0.05)
                                    .frame(maxWidth: .infinity)
                                Text(store.whiteWeight, format: .number.precision(.fractionLength(2)))
                                    .foregroundStyle(.secondary)
                                    .monospacedDigit()
                                    .frame(width: 34, alignment: .trailing)
                            }

                            Text("Overlay channels are controlled by the checkboxes in the channel table above.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .padding(6)
                    }
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                }

                HStack {
                    Spacer()
                    Button {
                        store.saveConfiguration()
                    } label: {
                        Label("Save Configuration", systemImage: "square.and.arrow.down")
                    }
                    .spatialScopeProminentButtonStyle()
                }

                StatusBarView()
            }
            .padding(SpatialScopeDesign.contentPadding)
        }
    }
}

private struct ChannelHeaderRow: View {
    var body: some View {
        HStack(spacing: 12) {
            Text("Overlay").frame(width: 64, alignment: .leading)
            Text("CSV file").frame(maxWidth: .infinity, alignment: .leading)
            Text("Marker").frame(width: 220, alignment: .leading)
            Text("Color").frame(width: 96, alignment: .leading)
        }
        .font(.caption.weight(.semibold))
        .foregroundStyle(.secondary)
    }
}

private struct ChannelConfigRow: View {
    @Binding var channel: ChannelConfig

    var body: some View {
        HStack(spacing: 12) {
            Toggle("", isOn: $channel.overlayEnabled)
                .toggleStyle(.checkbox)
                .frame(width: 64, alignment: .leading)

            Text(channel.fileName)
                .font(.system(.body, design: .monospaced))
                .lineLimit(1)
                .truncationMode(.middle)
                .frame(maxWidth: .infinity, alignment: .leading)

            TextField("Marker", text: $channel.marker)
                .textFieldStyle(.roundedBorder)
                .frame(width: 220)

            ColorPicker(
                "",
                selection: colorBinding(
                    getHex: { channel.colorHex },
                    setHex: { channel.colorHex = $0 }
                ),
                supportsOpacity: false
            )
            .labelsHidden()
            .frame(width: 96, alignment: .leading)
        }
        .frame(minHeight: 34)
    }
}
