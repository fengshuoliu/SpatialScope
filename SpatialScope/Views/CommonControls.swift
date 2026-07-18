import AppKit
import SwiftUI

struct FunctionalPanelGroupBoxStyle: GroupBoxStyle {
    func makeBody(configuration: Configuration) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            configuration.label
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(.primary)

            configuration.content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .spatialScopeGlassSurface()
    }
}

struct StatusBarView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        HStack(spacing: 9) {
            if store.isBusy {
                ProgressView()
                    .controlSize(.small)
            } else {
                Image(systemName: statusSymbol)
                    .foregroundStyle(statusColor)
            }
            Text(
                store.uiLanguage.localizedStatusMessage(
                    store.statusMessage.isEmpty ? "Ready" : store.statusMessage
                )
            )
                .foregroundStyle(.primary)
                .lineLimit(2)
            Spacer()
        }
        .font(.callout)
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .spatialScopeGlassSurface(cornerRadius: 7, tint: statusColor.opacity(0.06))
    }

    private var statusColor: Color {
        let message = store.statusMessage.lowercased()
        return statusIsError(message) ? .red : .green
    }

    private var statusSymbol: String {
        statusIsError(store.statusMessage.lowercased())
            ? "exclamationmark.triangle.fill"
            : "checkmark.circle.fill"
    }

    private func statusIsError(_ message: String) -> Bool {
        message.contains("failed")
            || message.contains("error")
            || message.contains("not found")
            || message.contains("choose ")
            || message.contains("select ")
            || message.contains("set figure")
    }
}

struct ResourceAllocationControl: View {
    @EnvironmentObject private var store: AppStore
    var contextLabel: String = "Analysis"

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) {
                    cpuAllocationControls
                    Spacer()
                }
                VStack(alignment: .leading, spacing: 8) {
                    cpuAllocationControls
                }
            }

            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "cpu")
                    .foregroundStyle(Color.accentColor)
                Text(
                    String(
                        format: store.uiLanguage.localized(
                            "CPU limit is enforced as a maximum worker count. %@ can use up to %lld of %lld active cores."
                        ),
                        store.uiLanguage.localized(contextLabel),
                        store.configuredCPUWorkerCount,
                        store.resourceSnapshot.activeCPUCoreCount
                    )
                )
                    .fixedSize(horizontal: false, vertical: true)
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "gauge")
                    .foregroundStyle(.secondary)
                Text(gpuStatusText)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .font(.caption)

            Label("Requested CPU workers and observed CPU/GPU activity are recorded in resource_metadata.json.", systemImage: "doc.badge.gearshape")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var cpuAllocationControls: some View {
        Text("CPU allocation")
            .frame(minWidth: 120, alignment: .leading)
        Slider(value: $store.cpuAllocationPercent, in: 10...100, step: 5)
            .frame(minWidth: 180, maxWidth: 340)
        Text("\(store.cpuAllocationPercent, format: .number.precision(.fractionLength(0)))%")
            .font(.system(.caption, design: .monospaced))
            .foregroundStyle(.secondary)
            .frame(width: 50, alignment: .trailing)
        Text("\(store.configuredCPUWorkerCount) workers")
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
    }

    private var gpuStatusText: String {
        guard store.hasGPU else {
            return store.uiLanguage.localized("No Metal GPU detected.")
        }
        let names = store.resourceSnapshot.gpuNames.joined(separator: ", ")
        let usage = store.resourceSnapshot.gpuUsagePercent ?? 0
        return String(
            format: store.uiLanguage.localized(
                "Metal GPU monitoring: %@, currently %@%%. Nuclei and cell-type methods remain CPU-only so their numerical method is unchanged."
            ),
            names,
            usage.formatted(.number.precision(.fractionLength(0)))
        )
    }
}

struct FolderRow: View {
    var title: String
    var url: URL
    var systemImage: String
    var action: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Label(LocalizedStringKey(title), systemImage: systemImage)
                .font(.headline)
                .frame(width: 150, alignment: .leading)
            Text(url.path)
                .font(.system(.body, design: .monospaced))
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
            Spacer()
            Button(action: action) {
                Label("Choose", systemImage: "folder.badge.plus")
            }
            .help(Text("Choose folder"))
        }
    }
}

struct EmptyStateView: View {
    var systemImage: String
    var title: String
    var message: String

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: systemImage)
                .font(.system(size: 30, weight: .medium))
                .foregroundStyle(Color.accentColor)
                .frame(width: 62, height: 62)
                .spatialScopeGlassSurface(cornerRadius: 8, tint: Color.accentColor.opacity(0.12))
            Text(LocalizedStringKey(title))
                .font(.title3.weight(.semibold))
            Text(LocalizedStringKey(message))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 420)
        }
        .padding(34)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct ImagePreviewPane: View {
    var title: String
    var image: NSImage?
    var backgroundColor: NSColor = .textBackgroundColor

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(LocalizedStringKey(title), systemImage: "photo.on.rectangle.angled")
                .font(.headline.weight(.semibold))
                .foregroundStyle(.secondary)
            if let image {
                ZoomableImageView(image: image, backgroundColor: backgroundColor)
                    .frame(minHeight: 320)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .overlay {
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(SpatialScopeDesign.panelBorder, lineWidth: 1)
                    }
            } else {
                EmptyStateView(
                    systemImage: "photo",
                    title: "No preview",
                    message: "Generate an overlay from the input CSV folder."
                )
                .frame(minHeight: 320)
            }
        }
    }
}

struct ZoomableImageView: View {
    var image: NSImage
    var backgroundColor: NSColor = .textBackgroundColor
    var outerBackgroundColor: NSColor = .textBackgroundColor
    var minMagnification: CGFloat = 0.08
    var maxMagnification: CGFloat = 16
    @State private var baseMagnification: CGFloat = 1
    @State private var gestureMagnification: CGFloat = 1

    var body: some View {
        GeometryReader { proxy in
            let sourceSize = ImageExportService.pixelSize(for: image)
            let fitSize = fittedSize(
                source: CGSize(width: max(1, sourceSize.width), height: max(1, sourceSize.height)),
                container: proxy.size
            )
            let currentMagnification = clampedMagnification(baseMagnification * gestureMagnification)
            ZStack {
                Color(nsColor: outerBackgroundColor)
                ScrollView([.horizontal, .vertical]) {
                    Image(nsImage: image)
                        .resizable()
                        .interpolation(.high)
                        .antialiased(true)
                        .scaledToFit()
                        .frame(
                            width: fitSize.width * currentMagnification,
                            height: fitSize.height * currentMagnification,
                            alignment: .center
                        )
                        .background(Color(nsColor: backgroundColor))
                        .frame(minWidth: proxy.size.width, minHeight: proxy.size.height, alignment: .center)
                }
                .gesture(
                    MagnificationGesture()
                        .onChanged { value in
                            gestureMagnification = value
                        }
                        .onEnded { value in
                            baseMagnification = clampedMagnification(baseMagnification * value)
                            gestureMagnification = 1
                        }
                )
                .onTapGesture(count: 2) {
                    baseMagnification = 1
                    gestureMagnification = 1
                }
            }
        }
    }

    private func fittedSize(source: CGSize, container: CGSize) -> CGSize {
        guard source.width > 0, source.height > 0, container.width > 0, container.height > 0 else {
            return .zero
        }
        let scale = min(container.width / source.width, container.height / source.height)
        return CGSize(width: source.width * scale, height: source.height * scale)
    }

    private func clampedMagnification(_ value: CGFloat) -> CGFloat {
        min(max(value, minMagnification), maxMagnification)
    }
}

func colorBinding(getHex: @escaping () -> String, setHex: @escaping (String) -> Void) -> Binding<Color> {
    Binding<Color>(
        get: { Color(hex: getHex()) },
        set: { newColor in
            setHex(NSColor(newColor).hexString)
        }
    )
}
