import Foundation

enum CSVImageLoader {
    static func discoverCSVFiles(in folder: URL) throws -> [URL] {
        let urls = try FileManager.default.contentsOfDirectory(
            at: folder,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        )
        return urls
            .filter { $0.pathExtension.lowercased() == "csv" }
            .sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }
    }

    static func loadMatrix(from url: URL, channelName: String) throws -> CSVMatrix {
        let text = try String(contentsOf: url, encoding: .utf8)
        var rows: [[Double]] = []
        var maxWidth = 0

        for rawLine in text.split(whereSeparator: \.isNewline) {
            let line = String(rawLine).trimmingCharacters(in: .whitespacesAndNewlines)
            if line.isEmpty { continue }
            let tokens = splitTokens(line)
            if tokens.isEmpty { continue }
            let row = tokens.map { token -> Double in
                let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !trimmed.isEmpty else { return 0 }
                return Double(trimmed) ?? 0
            }
            maxWidth = max(maxWidth, row.count)
            rows.append(row)
        }

        guard !rows.isEmpty, maxWidth > 0 else {
            throw SpatialScopeError.message("\(url.lastPathComponent) did not contain a numeric matrix.")
        }

        var values: [Double] = []
        values.reserveCapacity(rows.count * maxWidth)
        for row in rows {
            values.append(contentsOf: row)
            if row.count < maxWidth {
                values.append(contentsOf: Array(repeating: 0, count: maxWidth - row.count))
            }
        }

        return CSVMatrix(
            channelName: channelName,
            fileName: url.lastPathComponent,
            width: maxWidth,
            height: rows.count,
            values: values
        )
    }

    static func loadMatrices(inputFolder: URL, channels: [ChannelConfig]) throws -> [CSVMatrix] {
        try channels.map { channel in
            try loadMatrix(
                from: inputFolder.appendingPathComponent(channel.fileName),
                channelName: channel.channelName
            )
        }
    }

    private static func splitTokens(_ line: String) -> [String] {
        if line.contains(",") {
            return line.split(separator: ",", omittingEmptySubsequences: false).map(String.init)
        }
        if line.contains("\t") {
            return line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
        }
        if line.contains(";") {
            return line.split(separator: ";", omittingEmptySubsequences: false).map(String.init)
        }
        return line.split(whereSeparator: \.isWhitespace).map(String.init)
    }
}
