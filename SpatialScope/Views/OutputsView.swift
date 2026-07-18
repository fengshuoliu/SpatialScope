import SwiftUI

struct OutputsView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: "tray.full")
                    .foregroundStyle(Color.accentColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Output directory")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(store.outputFolder.path)
                        .font(.system(.body, design: .monospaced))
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .textSelection(.enabled)
                }
                Spacer()
                Button {
                    store.refreshOutputs()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                Button {
                    store.revealOutputFolder()
                } label: {
                    Label("Reveal in Finder", systemImage: "arrow.up.forward.app")
                }
            }
            .padding(.horizontal, SpatialScopeDesign.contentPadding)
            .padding(.vertical, 14)
            .spatialScopeGlassSurface(cornerRadius: 0, tint: Color.accentColor.opacity(0.025))

            Divider()

            if store.outputFiles.isEmpty {
                EmptyStateView(
                    systemImage: "tray",
                    title: "No generated outputs",
                    message: "Save configuration or generate an overlay to populate the selected output folder."
                )
            } else {
                Table(store.outputFiles) {
                    TableColumn("Name", value: \.name)
                    TableColumn("Relative path", value: \.relativePath)
                    TableColumn("Size") { file in
                        Text(file.formattedSize)
                    }
                    .width(90)
                }
                .padding(SpatialScopeDesign.contentPadding)
            }
        }
    }
}
