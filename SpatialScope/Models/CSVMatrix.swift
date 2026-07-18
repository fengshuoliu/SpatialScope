import Foundation

struct CSVMatrix {
    var channelName: String
    var fileName: String
    var width: Int
    var height: Int
    var values: [Double]

    subscript(x: Int, y: Int) -> Double {
        values[(y * width) + x]
    }

    func percentile(_ percentile: Double) -> Double {
        let finite = values.filter { $0.isFinite }.sorted()
        guard !finite.isEmpty else { return 0 }
        if finite.count == 1 { return finite[0] }

        let clamped = min(max(percentile, 0), 100)
        let position = (clamped / 100.0) * Double(finite.count - 1)
        let lower = Int(position.rounded(.down))
        let upper = Int(position.rounded(.up))
        if lower == upper { return finite[lower] }
        let weight = position - Double(lower)
        return finite[lower] * (1.0 - weight) + finite[upper] * weight
    }
}

struct OutputFileInfo: Identifiable, Equatable {
    var id: String { relativePath }
    var name: String
    var relativePath: String
    var sizeBytes: Int64

    var formattedSize: String {
        ByteCountFormatter.string(fromByteCount: sizeBytes, countStyle: .file)
    }
}
