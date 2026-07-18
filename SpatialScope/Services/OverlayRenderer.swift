import AppKit
import CoreGraphics
import Dispatch
import Foundation

struct OverlayRenderResult {
    var overlayImage: NSImage
    var overlayBaseImage: NSImage
    var splitImage: NSImage
    var splitTiles: [SplitChannelTile]
    var overlayChannels: [ChannelConfig]
    var matrices: [CSVMatrix]
}

struct SplitChannelTile {
    var channelName: String
    var colorHex: String
    var image: NSImage
}

enum OverlayRenderer {
    private struct RenderChannel {
        var matrix: CSVMatrix
        var high: Double
        var red: Double
        var green: Double
        var blue: Double
    }

    static func render(
        matrices: [CSVMatrix],
        channels: [ChannelConfig],
        whiteChannelID: UUID?,
        whiteWeight: Double,
        pixelSizeXUm: Double?,
        cpuAllocationPercent: Double = 100
    ) throws -> OverlayRenderResult {
        let workerCount = workerCount(for: cpuAllocationPercent)
        let matrixByFile = Dictionary(uniqueKeysWithValues: matrices.map { ($0.fileName, $0) })
        let selectedChannels = channels.filter(\.overlayEnabled)
        let overlayChannels = selectedChannels.isEmpty ? channels : selectedChannels
        guard let firstChannel = overlayChannels.first,
              let firstMatrix = matrixByFile[firstChannel.fileName] else {
            throw SpatialScopeError.message("No CSV channels are selected for overlay rendering.")
        }

        for channel in overlayChannels {
            guard let matrix = matrixByFile[channel.fileName] else {
                throw SpatialScopeError.message("Missing matrix for \(channel.fileName).")
            }
            if matrix.width != firstMatrix.width || matrix.height != firstMatrix.height {
                throw SpatialScopeError.message("\(channel.fileName) has shape \(matrix.width)x\(matrix.height), expected \(firstMatrix.width)x\(firstMatrix.height).")
            }
        }

        let overlayBase = try makeOverlayCGImage(
            channels: overlayChannels,
            matrixByFile: matrixByFile,
            width: firstMatrix.width,
            height: firstMatrix.height,
            whiteChannelID: whiteChannelID,
            whiteWeight: whiteWeight,
            workerCount: workerCount
        )
        let overlayBaseImage = NSImage(
            cgImage: overlayBase,
            size: NSSize(width: firstMatrix.width, height: firstMatrix.height)
        )
        let overlayImage = drawAnnotations(
            base: overlayBase,
            labels: overlayChannels.map { ($0.channelName, $0.colorHex) },
            pixelSizeXUm: pixelSizeXUm
        )
        let splitImage = try renderSplitPanels(channels: channels, matrixByFile: matrixByFile, workerCount: workerCount)
        let splitTiles = try renderSplitTiles(channels: channels, matrixByFile: matrixByFile, workerCount: workerCount)
        return OverlayRenderResult(
            overlayImage: overlayImage,
            overlayBaseImage: overlayBaseImage,
            splitImage: splitImage,
            splitTiles: splitTiles,
            overlayChannels: overlayChannels,
            matrices: matrices
        )
    }

    private static func makeOverlayCGImage(
        channels: [ChannelConfig],
        matrixByFile: [String: CSVMatrix],
        width: Int,
        height: Int,
        whiteChannelID: UUID?,
        whiteWeight: Double,
        workerCount: Int
    ) throws -> CGImage {
        let renderChannels = channels.compactMap { channel -> RenderChannel? in
            guard let matrix = matrixByFile[channel.fileName],
                  let color = NSColor(hex: channel.colorHex) else { return nil }
            let (red, green, blue) = color.rgbComponents01
            return RenderChannel(
                matrix: matrix,
                high: max(0.000_001, matrix.percentile(99.8)),
                red: red,
                green: green,
                blue: blue
            )
        }

        let whiteChannel = channels.first { $0.id == whiteChannelID }
        let whiteMatrix = whiteChannel.flatMap { matrixByFile[$0.fileName] }
        let whiteHigh = whiteMatrix.map { max(0.000_001, $0.percentile(99.8)) } ?? 1.0

        let rgba = parallelRGBAByRows(width: width, height: height, workerCount: workerCount) { rowRange in
            var local = [UInt8]()
            local.reserveCapacity(rowRange.count * width * 4)

            for y in rowRange {
                for x in 0..<width {
                    var r = 0.0
                    var g = 0.0
                    var b = 0.0

                    for channel in renderChannels {
                        let normalized = min(max(channel.matrix[x, y] / channel.high, 0), 1)
                        r = 1.0 - (1.0 - r) * (1.0 - normalized * channel.red)
                        g = 1.0 - (1.0 - g) * (1.0 - normalized * channel.green)
                        b = 1.0 - (1.0 - b) * (1.0 - normalized * channel.blue)
                    }

                    if let whiteMatrix, whiteWeight > 0 {
                        let normalized = min(max(whiteMatrix[x, y] / whiteHigh, 0), 1) * min(max(whiteWeight, 0), 1)
                        r = 1.0 - (1.0 - r) * (1.0 - normalized)
                        g = 1.0 - (1.0 - g) * (1.0 - normalized)
                        b = 1.0 - (1.0 - b) * (1.0 - normalized)
                    }

                    local.append(UInt8(min(max(r, 0), 1) * 255))
                    local.append(UInt8(min(max(g, 0), 1) * 255))
                    local.append(UInt8(min(max(b, 0), 1) * 255))
                    local.append(255)
                }
            }

            return local
        }

        return try ImageExportService.makeCGImage(width: width, height: height, rgba: rgba)
    }

    private static func renderSplitPanels(
        channels: [ChannelConfig],
        matrixByFile: [String: CSVMatrix],
        workerCount: Int
    ) throws -> NSImage {
        let channelMatrices = channels.compactMap { channel -> (ChannelConfig, CSVMatrix)? in
            guard let matrix = matrixByFile[channel.fileName] else { return nil }
            return (channel, matrix)
        }
        guard !channelMatrices.isEmpty else {
            throw SpatialScopeError.message("No matrices are loaded.")
        }

        let columns = channelMatrices.count <= 4 ? 2 : 3
        let rows = Int(ceil(Double(channelMatrices.count) / Double(columns)))
        let tileMax = 340.0
        let labelHeight = 28.0
        let gap = 14.0
        let first = channelMatrices[0].1
        let scale = min(tileMax / Double(first.width), tileMax / Double(first.height), 1.0)
        let tileWidth = max(80.0, Double(first.width) * scale)
        let tileHeight = max(80.0, Double(first.height) * scale)
        let canvasWidth = Double(columns) * tileWidth + Double(columns + 1) * gap
        let canvasHeight = Double(rows) * (tileHeight + labelHeight) + Double(rows + 1) * gap
        let image = NSImage(size: NSSize(width: canvasWidth, height: canvasHeight))

        image.lockFocus()
        NSColor.black.setFill()
        NSRect(origin: .zero, size: image.size).fill()

        for (index, pair) in channelMatrices.enumerated() {
            let (channel, matrix) = pair
            let col = index % columns
            let row = index / columns
            let originX = gap + Double(col) * (tileWidth + gap)
            let originY = canvasHeight - gap - Double(row + 1) * (tileHeight + labelHeight) - Double(row) * gap
            let cgImage = try makeSingleChannelCGImage(matrix: matrix, colorHex: channel.colorHex, workerCount: workerCount)
            NSGraphicsContext.current?.imageInterpolation = .none
            NSImage(cgImage: cgImage, size: NSSize(width: matrix.width, height: matrix.height))
                .draw(in: NSRect(x: originX, y: originY, width: tileWidth, height: tileHeight))

            let label = channel.channelName
            let color = NSColor(hex: channel.colorHex) ?? .white
            label.draw(
                in: NSRect(x: originX, y: originY + tileHeight + 6, width: tileWidth, height: labelHeight),
                withAttributes: [
                    .font: NSFont.systemFont(ofSize: 13, weight: .semibold),
                    .foregroundColor: color
                ]
            )
        }

        image.unlockFocus()
        return image
    }

    private static func renderSplitTiles(
        channels: [ChannelConfig],
        matrixByFile: [String: CSVMatrix],
        workerCount: Int
    ) throws -> [SplitChannelTile] {
        try channels.compactMap { channel -> SplitChannelTile? in
            guard let matrix = matrixByFile[channel.fileName] else { return nil }
            let cgImage = try makeSingleChannelCGImage(matrix: matrix, colorHex: channel.colorHex, workerCount: workerCount)
            let image = NSImage(
                cgImage: cgImage,
                size: NSSize(width: matrix.width, height: matrix.height)
            )
            return SplitChannelTile(
                channelName: channel.channelName,
                colorHex: channel.colorHex,
                image: image
            )
        }
    }

    private static func makeSingleChannelCGImage(matrix: CSVMatrix, colorHex: String, workerCount: Int) throws -> CGImage {
        let high = max(0.000_001, matrix.percentile(99.8))
        let color = NSColor(hex: colorHex) ?? .white
        let (cr, cg, cb) = color.rgbComponents01

        let rgba = parallelRGBAByRows(width: matrix.width, height: matrix.height, workerCount: workerCount) { rowRange in
            var local = [UInt8]()
            local.reserveCapacity(rowRange.count * matrix.width * 4)

            for y in rowRange {
                for x in 0..<matrix.width {
                    let normalized = min(max(matrix[x, y] / high, 0), 1)
                    local.append(UInt8(normalized * cr * 255))
                    local.append(UInt8(normalized * cg * 255))
                    local.append(UInt8(normalized * cb * 255))
                    local.append(255)
                }
            }

            return local
        }

        return try ImageExportService.makeCGImage(width: matrix.width, height: matrix.height, rgba: rgba)
    }

    private static func drawAnnotations(
        base: CGImage,
        labels: [(String, String)],
        pixelSizeXUm: Double?
    ) -> NSImage {
        let width = base.width
        let height = base.height
        let image = NSImage(size: NSSize(width: width, height: height))
        image.lockFocus()
        NSColor.black.setFill()
        NSRect(origin: .zero, size: image.size).fill()
        NSGraphicsContext.current?.imageInterpolation = .none
        NSImage(cgImage: base, size: NSSize(width: width, height: height))
            .draw(in: NSRect(x: 0, y: 0, width: width, height: height))

        let labelSize = CGFloat(max(11, min(18, width / 34)))
        let labelFont = NSFont.systemFont(ofSize: labelSize, weight: .bold)
        var y = CGFloat(height) - 26
        for (name, hex) in labels.prefix(14) {
            let color = NSColor(hex: hex) ?? .white
            let paragraph = NSMutableParagraphStyle()
            paragraph.alignment = .right
            name.draw(
                in: NSRect(x: 8, y: y, width: CGFloat(width) - 16, height: 22),
                withAttributes: [
                    .font: labelFont,
                    .foregroundColor: color,
                    .paragraphStyle: paragraph
                ]
            )
            y -= 22
        }

        if let pixelSizeXUm, pixelSizeXUm > 0 {
            let barUm = 20.0
            let barPixels = max(1, Int(round(barUm / pixelSizeXUm)))
            let xEnd = CGFloat(width) * 0.94
            let xStart = max(CGFloat(width) * 0.06, xEnd - CGFloat(barPixels))
            let yBar = CGFloat(height) * 0.07
            let path = NSBezierPath()
            path.move(to: NSPoint(x: xStart, y: yBar))
            path.line(to: NSPoint(x: xEnd, y: yBar))
            path.lineWidth = max(3, CGFloat(width) / 160)
            NSColor.white.setStroke()
            path.stroke()
        }

        image.unlockFocus()
        return image
    }

    private static func workerCount(for cpuAllocationPercent: Double) -> Int {
        let activeCores = max(1, ProcessInfo.processInfo.activeProcessorCount)
        let clamped = min(max(cpuAllocationPercent, 10), 100)
        let requested = Int((Double(activeCores) * clamped / 100.0).rounded(.toNearestOrAwayFromZero))
        return min(activeCores, max(1, requested))
    }

    private static func effectiveRowWorkerCount(height: Int, workerCount: Int) -> Int {
        guard height >= 64 else { return 1 }
        return min(max(1, workerCount), max(1, height))
    }

    private static func parallelRGBAByRows(
        width: Int,
        height: Int,
        workerCount: Int,
        body: @escaping (Range<Int>) -> [UInt8]
    ) -> [UInt8] {
        guard width > 0, height > 0 else { return [] }
        let workers = effectiveRowWorkerCount(height: height, workerCount: workerCount)
        guard workers > 1 else {
            return body(0..<height)
        }

        let lock = NSLock()
        var parts: [(Int, [UInt8])] = []
        parts.reserveCapacity(workers)

        DispatchQueue.concurrentPerform(iterations: workers) { workerIndex in
            let start = workerIndex * height / workers
            let end = (workerIndex + 1) * height / workers
            guard start < end else { return }
            let rows = body(start..<end)
            lock.lock()
            parts.append((workerIndex, rows))
            lock.unlock()
        }

        return parts.sorted { $0.0 < $1.0 }.flatMap(\.1)
    }
}
