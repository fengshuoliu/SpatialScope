import AppKit
import SwiftUI

enum SpatialScopeDesign {
    static let contentPadding: CGFloat = 24
    static let sectionSpacing: CGFloat = 18
    static let panelRadius: CGFloat = 8

    static let canvas = Color(nsColor: .windowBackgroundColor)
    static let panel = Color(nsColor: .controlBackgroundColor)
    static let subtleFill = Color.secondary.opacity(0.055)
    static let panelBorder = Color.secondary.opacity(0.16)
    static let accentFill = Color.accentColor.opacity(0.11)
    static let spectralCyan = Color(red: 0.04, green: 0.68, blue: 0.78)
    static let spectralMagenta = Color(red: 0.86, green: 0.28, blue: 0.62)
    static let spectralGreen = Color(red: 0.16, green: 0.68, blue: 0.46)
    static let glassNeutralTint = Color.white.opacity(0.035)
    static let glassHighlight = Color.white.opacity(0.68)
    static let glassShadow = Color.black.opacity(0.16)
    static let glassAmbientShadow = Color.black.opacity(0.055)
}

struct SpatialScopeCanvasBackground: View {
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ZStack {
            SpatialScopeDesign.canvas

            LinearGradient(
                stops: [
                    .init(color: SpatialScopeDesign.spectralCyan.opacity(spectralOpacity), location: 0),
                    .init(color: Color.clear, location: 0.34),
                    .init(color: SpatialScopeDesign.spectralMagenta.opacity(spectralOpacity * 0.52), location: 0.69),
                    .init(color: SpatialScopeDesign.spectralGreen.opacity(spectralOpacity * 0.76), location: 1)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            LinearGradient(
                colors: [
                    Color.white.opacity(colorScheme == .dark ? 0.02 : 0.15),
                    Color.clear,
                    Color.black.opacity(colorScheme == .dark ? 0.10 : 0.025)
                ],
                startPoint: .top,
                endPoint: .bottom
            )
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
    }

    private var spectralOpacity: Double {
        colorScheme == .dark ? 0.14 : 0.09
    }
}

extension View {
    func spatialScopeWindowMaterial() -> some View {
        modifier(SpatialScopeWindowMaterialModifier())
    }

    func spatialScopeGlassSurface(
        cornerRadius: CGFloat = SpatialScopeDesign.panelRadius,
        tint: Color? = nil,
        interactive: Bool = false
    ) -> some View {
        modifier(
            SpatialScopeGlassSurfaceModifier(
                cornerRadius: cornerRadius,
                tint: tint,
                interactive: interactive
            )
        )
    }

    func spatialScopeGlassCapsule(tint: Color? = nil) -> some View {
        modifier(SpatialScopeGlassCapsuleModifier(tint: tint))
    }

    func spatialScopeSelectedGlass(isSelected: Bool, tint: Color) -> some View {
        modifier(SpatialScopeSelectedGlassModifier(isSelected: isSelected, tint: tint))
    }

    func spatialScopeProminentButtonStyle() -> some View {
        modifier(SpatialScopeProminentButtonModifier())
    }
}

private struct SpatialScopeWindowMaterialModifier: ViewModifier {
    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(macOS 15.0, *) {
            content.containerBackground(.regularMaterial, for: .window)
        } else {
            content
        }
    }
}

private struct SpatialScopeGlassSurfaceModifier: ViewModifier {
    var cornerRadius: CGFloat
    var tint: Color?
    var interactive: Bool
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @State private var isHovering = false

    @ViewBuilder
    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        if reduceTransparency {
            content
                .background(SpatialScopeDesign.panel, in: shape)
                .overlay {
                    shape.stroke(SpatialScopeDesign.panelBorder, lineWidth: 1)
                }
                .shadow(color: SpatialScopeDesign.glassAmbientShadow, radius: 10, y: 4)
        } else if #available(macOS 26.0, *) {
            if interactive {
                content
                    .glassEffect(.regular.tint(tint ?? SpatialScopeDesign.glassNeutralTint).interactive(), in: shape)
                    .overlay {
                        SpatialScopeOpticalEdge(shape: shape, tint: tint, intensity: isHovering ? 1.12 : 0.92)
                    }
                    .shadow(color: SpatialScopeDesign.glassShadow, radius: isHovering ? 18 : 14, y: isHovering ? 7 : 5)
                    .shadow(color: SpatialScopeDesign.glassAmbientShadow, radius: 4, y: -1)
                    .scaleEffect(isHovering ? 1.004 : 1)
                    .animation(.spring(response: 0.24, dampingFraction: 0.78), value: isHovering)
                    .onHover { hovering in
                        isHovering = hovering
                    }
            } else {
                content
                    .glassEffect(.regular.tint(tint ?? SpatialScopeDesign.glassNeutralTint), in: shape)
                    .overlay {
                        SpatialScopeOpticalEdge(shape: shape, tint: tint, intensity: 0.82)
                    }
                    .shadow(color: SpatialScopeDesign.glassShadow, radius: 15, y: 6)
                    .shadow(color: SpatialScopeDesign.glassAmbientShadow, radius: 4, y: -1)
            }
        } else {
            content
                .background(.ultraThinMaterial, in: shape)
                .overlay {
                    SpatialScopeOpticalEdge(shape: shape, tint: tint, intensity: 0.82)
                }
                .shadow(color: SpatialScopeDesign.glassShadow, radius: 15, y: 6)
                .shadow(color: SpatialScopeDesign.glassAmbientShadow, radius: 4, y: -1)
        }
    }
}

private struct SpatialScopeGlassCapsuleModifier: ViewModifier {
    var tint: Color?
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    @ViewBuilder
    func body(content: Content) -> some View {
        if reduceTransparency {
            content
                .background(SpatialScopeDesign.panel, in: Capsule())
                .overlay {
                    Capsule().stroke(SpatialScopeDesign.panelBorder, lineWidth: 1)
                }
        } else if #available(macOS 26.0, *) {
            content
                .glassEffect(.regular.tint(tint ?? SpatialScopeDesign.glassNeutralTint), in: Capsule())
                .overlay {
                    SpatialScopeOpticalEdge(shape: Capsule(), tint: tint, intensity: 0.88)
                }
                .shadow(color: SpatialScopeDesign.glassAmbientShadow, radius: 7, y: 3)
        } else {
            content
                .background(.ultraThinMaterial, in: Capsule())
                .overlay {
                    SpatialScopeOpticalEdge(shape: Capsule(), tint: tint, intensity: 0.88)
                }
                .shadow(color: SpatialScopeDesign.glassAmbientShadow, radius: 7, y: 3)
        }
    }
}

private struct SpatialScopeSelectedGlassModifier: ViewModifier {
    var isSelected: Bool
    var tint: Color

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(macOS 26.0, *), isSelected {
            let shape = RoundedRectangle(cornerRadius: 6, style: .continuous)
            content
                .glassEffect(
                    .regular.tint(tint.opacity(0.18)).interactive(),
                    in: shape
                )
                .overlay {
                    SpatialScopeOpticalEdge(shape: shape, tint: tint, intensity: 1.1)
                }
                .shadow(color: tint.opacity(0.12), radius: 10, y: 4)
        } else {
            content
        }
    }
}

private struct SpatialScopeProminentButtonModifier: ViewModifier {
    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(macOS 26.0, *) {
            content
                .buttonStyle(.glassProminent)
                .buttonBorderShape(.capsule)
        } else {
            content.buttonStyle(.borderedProminent)
        }
    }
}

private struct SpatialScopeOpticalEdge<S: InsettableShape>: View {
    var shape: S
    var tint: Color?
    var intensity: Double

    var body: some View {
        let semanticTint = tint ?? Color.clear
        ZStack {
            shape.stroke(
                LinearGradient(
                    stops: [
                        .init(color: Color.white.opacity(0.82 * intensity), location: 0),
                        .init(color: SpatialScopeDesign.spectralCyan.opacity(0.40 * intensity), location: 0.18),
                        .init(color: Color.white.opacity(0.14 * intensity), location: 0.45),
                        .init(color: semanticTint.opacity(0.34 * intensity), location: 0.67),
                        .init(color: SpatialScopeDesign.spectralMagenta.opacity(0.22 * intensity), location: 0.82),
                        .init(color: Color.white.opacity(0.58 * intensity), location: 1)
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ),
                lineWidth: 1
            )

            shape
                .inset(by: 1)
                .stroke(Color.white.opacity(0.15 * intensity), lineWidth: 0.75)
        }
        .allowsHitTesting(false)
    }
}

enum WorkflowSectionStatus {
    case notStarted
    case ready
    case running
    case finished
    case error

    var title: String {
        switch self {
        case .notStarted: "Not started"
        case .ready: "Ready"
        case .running: "Running"
        case .finished: "Complete"
        case .error: "Needs attention"
        }
    }

    var foregroundColor: Color {
        switch self {
        case .notStarted: .secondary
        case .ready: .accentColor
        case .running: .orange
        case .finished: .green
        case .error: .red
        }
    }

    var backgroundColor: Color {
        switch self {
        case .notStarted: Color.secondary.opacity(0.045)
        case .ready: Color.accentColor.opacity(0.11)
        case .running: Color.orange.opacity(0.14)
        case .finished: Color.green.opacity(0.10)
        case .error: Color.red.opacity(0.12)
        }
    }
}

@MainActor
func workflowStatus(for section: AnalysisSection, store: AppStore) -> WorkflowSectionStatus {
    if store.runningSection == section {
        return .running
    }
    if workflowHasCurrentError(for: section, store: store) {
        return .error
    }
    if workflowIsFinished(section, store: store) {
        return .finished
    }
    if workflowIsReady(section, store: store) {
        return .ready
    }
    return .notStarted
}

@MainActor
func workflowIsReady(_ section: AnalysisSection, store: AppStore) -> Bool {
    guard !workflowIsFinished(section, store: store) else { return false }
    if section == .inputs { return true }
    guard let index = AnalysisSection.allCases.firstIndex(of: section), index > 0 else { return false }
    return workflowIsFinished(AnalysisSection.allCases[index - 1], store: store)
}

@MainActor
func workflowIsFinished(_ section: AnalysisSection, store: AppStore) -> Bool {
    switch section {
    case .inputs:
        !store.channels.isEmpty
    case .overlay:
        store.overlayImage != nil || store.splitImage != nil
    case .nuclei:
        store.nucleiResult != nil
    case .cellTypes:
        store.cellTypeAssignmentResult != nil
    case .neighborhood:
        store.neighborhoodAnalysisResult != nil
    case .region:
        store.regionAnalysisResult != nil
    case .cellDistribution:
        store.cellDistributionResult != nil
    case .distance:
        store.distanceAnalysisResult != nil
    case .outputs:
        !store.outputFiles.isEmpty
    }
}

@MainActor
private func workflowHasCurrentError(for section: AnalysisSection, store: AppStore) -> Bool {
    guard store.selectedSection == section else { return false }
    let message = store.statusMessage.lowercased()
    guard !message.isEmpty else { return false }
    return message.contains("failed")
        || message.contains("error")
        || message.contains("not found")
        || message.contains("choose ")
        || message.contains("select ")
        || message.contains("set figure")
        || (message.contains("run ") && message.contains(" before"))
}

struct WorkflowStatusBadge: View {
    let status: WorkflowSectionStatus

    var body: some View {
        Text(status.title)
            .font(.caption.weight(.semibold))
            .foregroundStyle(status.foregroundColor)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .spatialScopeGlassCapsule(tint: status.foregroundColor.opacity(0.16))
            .accessibilityLabel("Section status: \(status.title)")
    }
}
