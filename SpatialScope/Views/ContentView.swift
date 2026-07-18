import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        NavigationSplitView {
            SidebarView()
        } detail: {
            ZStack {
                SpatialScopeCanvasBackground()

                VStack(spacing: 0) {
                    DetailHeaderView(section: store.selectedSection)
                        .zIndex(1)
                    Divider()
                        .zIndex(1)
                    glassContainedDetailContent
                        .clipped()
                        .zIndex(0)
                }
            }
        }
        .spatialScopeWindowMaterial()
        .toolbar {
            ToolbarItemGroup {
                Button {
                    store.chooseInputFolder()
                } label: {
                    Label("Input", systemImage: "folder")
                }
                .help("Choose input folder")

                Button {
                    store.revealOutputFolder()
                } label: {
                    Label("Output", systemImage: "arrow.up.forward.app")
                }
                .help("Reveal output folder in Finder")
            }
        }
    }

    @ViewBuilder
    private var glassContainedDetailContent: some View {
        if #available(macOS 26.0, *) {
            GlassEffectContainer(spacing: SpatialScopeDesign.sectionSpacing) {
                styledDetailContent
            }
        } else {
            styledDetailContent
        }
    }

    private var styledDetailContent: some View {
        detailContent
            .groupBoxStyle(FunctionalPanelGroupBoxStyle())
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .id(store.selectedSection)
    }

    @ViewBuilder
    private var detailContent: some View {
        switch store.selectedSection {
        case .inputs:
            InputsConfigView()
        case .overlay:
            OverlayPreviewView()
        case .nuclei:
            NucleiSegmentationView()
        case .cellTypes:
            CellTypeAssignmentsView()
        case .neighborhood:
            NeighborhoodAnalysisView()
        case .region:
            RegionAnalysisView()
        case .cellDistribution:
            CellDistributionView()
        case .distance:
            DistanceAnalysisView()
        case .outputs:
            OutputsView()
        }
    }
}

private struct DetailHeaderView: View {
    @EnvironmentObject private var store: AppStore
    var section: AnalysisSection

    var body: some View {
        HStack(spacing: 15) {
            Image(systemName: section.systemImage)
                .font(.system(size: 21, weight: .semibold))
                .foregroundStyle(Color.accentColor)
                .frame(width: 44, height: 44)
                .spatialScopeGlassSurface(cornerRadius: 8, tint: Color.accentColor.opacity(0.12))

            VStack(alignment: .leading, spacing: 3) {
                Text("STEP \(section.stepNumber) OF \(AnalysisSection.allCases.count)")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(Color.accentColor)
                Text(section.title)
                    .font(.system(size: 25, weight: .semibold))
                Text(section.subtitle)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            WorkflowStatusBadge(status: workflowStatus(for: section, store: store))
        }
        .padding(.horizontal, SpatialScopeDesign.contentPadding)
        .padding(.vertical, 17)
        .spatialScopeGlassSurface(cornerRadius: 0, tint: Color.accentColor.opacity(0.035))
    }
}
