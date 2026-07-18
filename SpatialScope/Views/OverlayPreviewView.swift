import SwiftUI

struct OverlayPreviewView: View {
    @EnvironmentObject private var store: AppStore
    @State private var selectedPreview = "Overlay"

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Picker("", selection: $selectedPreview) {
                    Text("Overlay").tag("Overlay")
                    Text("Split Channels").tag("Split Channels")
                }
                .pickerStyle(.segmented)
                .frame(width: 260)

                Button {
                    store.generateOverlay()
                } label: {
                    Label("Load Inputs and Generate Overlay", systemImage: "play.fill")
                }
                .spatialScopeProminentButtonStyle()
                .disabled(!store.canGenerateOverlay)

                Button {
                    store.revealOutputFolder()
                } label: {
                    Label("Reveal Output", systemImage: "arrow.up.forward.app")
                }

                Spacer()
            }
            .padding(.horizontal, SpatialScopeDesign.contentPadding)
            .padding(.vertical, 14)
            .spatialScopeGlassSurface(cornerRadius: 0, tint: Color.accentColor.opacity(0.025))

            Divider()

            if selectedPreview == "Overlay" {
                ImagePreviewPane(title: "Overlay", image: store.overlayImage, backgroundColor: .black)
                    .padding(SpatialScopeDesign.contentPadding)
            } else {
                ImagePreviewPane(title: "Split channels", image: store.splitImage, backgroundColor: .black)
                    .padding(SpatialScopeDesign.contentPadding)
            }

            Divider()
            StatusBarView()
                .padding(.horizontal, SpatialScopeDesign.contentPadding)
                .padding(.bottom, 12)
        }
    }
}
