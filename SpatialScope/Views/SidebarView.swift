import AppKit
import SwiftUI

struct SidebarView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 13) {
                    Image(nsImage: NSApplication.shared.applicationIconImage)
                        .resizable()
                        .scaledToFit()
                        .frame(width: 54, height: 54)

                    VStack(alignment: .leading, spacing: 3) {
                        Text("SpatialScope")
                            .font(.system(size: 25, weight: .bold))
                        Text("Spatial image analysis")
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(.secondary)
                    }
                }

                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Workflow progress")
                        Spacer()
                        Text("\(completedSectionCount) of \(AnalysisSection.allCases.count)")
                            .monospacedDigit()
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)

                    ProgressView(
                        value: Double(completedSectionCount),
                        total: Double(AnalysisSection.allCases.count)
                    )
                    .tint(Color.accentColor)
                }

                HStack(spacing: 10) {
                    Label {
                        Text(verbatim: "Language/语言")
                    } icon: {
                        Image(systemName: "globe")
                    }
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Spacer()
                    Picker("", selection: $store.uiLanguage) {
                        ForEach(AppLanguage.allCases) { language in
                            Text(language.displayName(in: store.uiLanguage)).tag(language)
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)
                    .frame(width: 132)
                    .accessibilityLabel(Text(verbatim: "Language/语言"))
                }
            }
            .padding(.horizontal, 18)
            .padding(.top, 20)
            .padding(.bottom, 14)

            ScrollView(.vertical) {
                LazyVStack(spacing: 0) {
                    ForEach(Array(AnalysisSection.allCases.enumerated()), id: \.element.id) { index, section in
                        let status = workflowStatus(for: section, store: store)
                        Button {
                            store.selectedSection = section
                        } label: {
                            SidebarRow(
                                index: index + 1,
                                section: section,
                                isSelected: store.selectedSection == section,
                                status: status
                            )
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 4)
                    }
                }
            }

            Divider()

            VStack(alignment: .leading, spacing: 9) {
                sidebarMetadataRow(
                    icon: "square.grid.3x3",
                    title: "Dataset",
                    value: "\(store.channels.count) channels"
                )
                sidebarMetadataRow(icon: "ruler", title: "Scale", value: sidebarScaleText)
                sidebarMetadataRow(
                    icon: "cpu",
                    title: "Compute",
                    value: sidebarCPUUsageText
                )
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
        }
        .navigationSplitViewColumnWidth(min: 330, ideal: 350, max: 380)
    }

    private var completedSectionCount: Int {
        AnalysisSection.allCases.filter { workflowIsFinished($0, store: store) }.count
    }

    private var sidebarScaleText: LocalizedStringKey {
        guard store.xUm > 0, store.yUm > 0, store.xPx > 0, store.yPx > 0 else {
            return "Not set"
        }
        let x = store.xUm.formatted(.number.precision(.fractionLength(0...2)))
        let y = store.yUm.formatted(.number.precision(.fractionLength(0...2)))
        return "\(x) x \(y) um"
    }

    private var sidebarCPUUsageText: LocalizedStringKey {
        let usage = min(max(store.resourceSnapshot.cpuUsagePercent, 0), 100)
        let percentage = usage.formatted(.number.precision(.fractionLength(1)))
        return "\(percentage)% CPU"
    }

    private func sidebarMetadataRow(
        icon: String,
        title: LocalizedStringKey,
        value: LocalizedStringKey
    ) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .frame(width: 15)
            Text(title)
                .frame(width: 50, alignment: .leading)
            Text(value)
                .foregroundStyle(.primary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }
}

private struct SidebarRow: View {
    var index: Int
    var section: AnalysisSection
    var isSelected: Bool
    var status: WorkflowSectionStatus

    var body: some View {
        HStack(spacing: 11) {
            Image(systemName: section.systemImage)
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(isSelected ? Color.accentColor : Color.secondary)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 0) {
                    Text("\(String(format: "%02d", index))  ")
                    Text(LocalizedStringKey(section.title))
                }
                .font(.system(size: 15, weight: .semibold))
                .lineLimit(1)
                .foregroundStyle(.primary)
                Text(LocalizedStringKey(section.subtitle))
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer(minLength: 4)
        }
        .padding(.horizontal, 11)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            if !isSelected {
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(status.backgroundColor)
            }
        }
        .spatialScopeSelectedGlass(isSelected: isSelected, tint: status.foregroundColor)
        .overlay {
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .stroke(
                    isSelected ? Color.accentColor.opacity(0.55) : Color.white.opacity(0.30),
                    lineWidth: 1
                )
        }
        .help(Text(LocalizedStringKey(status.title)))
    }
}
