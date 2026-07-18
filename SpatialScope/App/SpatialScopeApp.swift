import AppKit
import Sparkle
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}

@main
struct SpatialScopeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store: AppStore
    private let updaterController: SPUStandardUpdaterController

    init() {
        if CommandLine.arguments.contains("--refresh-celltype-mask") {
            do {
                try SmokeTestRunner.refreshCellTypeMaskFromSavedAssignment()
                exit(0)
            } catch {
                fputs("Cell-type mask refresh failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-overlay") {
            do {
                try SmokeTestRunner.runOverlaySmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-nuclei") {
            do {
                try SmokeTestRunner.runNucleiSmokeTest(
                    runScan: false,
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent(),
                    combinationBudget: Self.smokeCombinationBudget()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-nuclei-scan") {
            do {
                try SmokeTestRunner.runNucleiSmokeTest(
                    runScan: true,
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent(),
                    combinationBudget: Self.smokeCombinationBudget()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-staged") {
            do {
                try SmokeTestRunner.runStagedAnalysisSmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-celltypes") {
            do {
                try SmokeTestRunner.runCellTypeAssignmentSmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-celltype-screening") {
            do {
                try SmokeTestRunner.runCellTypeAssignmentScreeningSmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent(),
                    combinationBudget: Self.smokeCombinationBudget()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-neighborhood") {
            do {
                try SmokeTestRunner.runNeighborhoodSmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-region") {
            do {
                try SmokeTestRunner.runRegionSmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-region-customized") {
            do {
                try SmokeTestRunner.runRegionCustomizedDisplaySmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-region-manual") {
            do {
                try SmokeTestRunner.runRegionManualAdjustmentSmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-cell-distribution") {
            do {
                try SmokeTestRunner.runCellDistributionSmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-cell-distribution-cluster") {
            do {
                try SmokeTestRunner.runCellDistributionClusterSmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-cell-distribution-load") {
            do {
                try SmokeTestRunner.runCellDistributionLoadSmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        if CommandLine.arguments.contains("--smoke-distance") {
            do {
                try SmokeTestRunner.runDistanceSmokeTest(
                    cpuAllocationPercent: Self.smokeCPUAllocationPercent()
                )
                exit(0)
            } catch {
                fputs("Smoke test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
        updaterController = SPUStandardUpdaterController(
            startingUpdater: true,
            updaterDelegate: nil,
            userDriverDelegate: nil
        )
        _store = StateObject(wrappedValue: AppStore())
    }

    var body: some Scene {
        WindowGroup("SpatialScope", id: "main") {
            ContentView()
                .environmentObject(store)
                .environment(\.locale, store.uiLanguage.locale)
                .tint(Color.accentColor)
                .frame(minWidth: 1180, minHeight: 780)
        }
        .defaultSize(width: 1440, height: 920)
        .windowToolbarStyle(.unified(showsTitle: true))
        .commands {
            CommandGroup(after: .appInfo) {
                CheckForUpdatesView(
                    updater: updaterController.updater,
                    language: store.uiLanguage
                )
            }

            CommandGroup(after: .newItem) {
                Button(store.uiLanguage.localized("Choose Input Folder...")) {
                    store.chooseInputFolder()
                }
                .keyboardShortcut("o", modifiers: [.command])

                Button(store.uiLanguage.localized("Generate Overlay")) {
                    store.generateOverlay()
                }
                .keyboardShortcut("r", modifiers: [.command])
                .disabled(!store.canGenerateOverlay)
            }
        }
    }

    private static func smokeCPUAllocationPercent() -> Double {
        for argument in CommandLine.arguments where argument.hasPrefix("--smoke-cpu=") {
            let valueText = argument.replacingOccurrences(of: "--smoke-cpu=", with: "")
            if let value = Double(valueText) {
                return min(max(value, 10), 100)
            }
        }
        return 100
    }

    private static func smokeCombinationBudget() -> Int {
        for argument in CommandLine.arguments where argument.hasPrefix("--smoke-combos=") {
            let valueText = argument.replacingOccurrences(of: "--smoke-combos=", with: "")
            if let value = Int(valueText) {
                return min(max(value, 10), NucleiSegmenter.advancedSearchSpaceSize)
            }
        }
        return 160
    }
}
