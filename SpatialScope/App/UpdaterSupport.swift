import Combine
import Sparkle
import SwiftUI

@MainActor
final class CheckForUpdatesViewModel: ObservableObject {
    @Published var canCheckForUpdates = false

    init(updater: SPUUpdater) {
        updater.publisher(for: \.canCheckForUpdates)
            .assign(to: &$canCheckForUpdates)
    }
}

struct CheckForUpdatesView: View {
    @ObservedObject private var viewModel: CheckForUpdatesViewModel
    private let updater: SPUUpdater
    private let language: AppLanguage

    @MainActor
    init(updater: SPUUpdater, language: AppLanguage) {
        self.updater = updater
        self.language = language
        viewModel = CheckForUpdatesViewModel(updater: updater)
    }

    var body: some View {
        Button(language.localized("Check for Updates..."), action: updater.checkForUpdates)
            .disabled(!viewModel.canCheckForUpdates)
    }
}
