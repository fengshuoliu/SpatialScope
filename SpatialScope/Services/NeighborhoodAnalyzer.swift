import AppKit
import Foundation

enum NeighborhoodAnalyzer {
    private static let excludedClusterTypes: Set<String> = ["Unassigned", "Ambiguous"]

    static func run(
        assignments: [CellTypeAssignment],
        gridSizeUm: Double,
        pixelSize: (Double, Double)?,
        canvasWidth: Int,
        canvasHeight: Int
    ) throws -> NeighborhoodAnalysisResult {
        guard !assignments.isEmpty else {
            throw SpatialScopeError.message("Run cell-type assignment before neighborhood analysis.")
        }
        let width = max(1, canvasWidth)
        let height = max(1, canvasHeight)
        let pixelSizeX = max(1e-12, pixelSize?.0 ?? 1.0)
        let pixelSizeY = max(1e-12, pixelSize?.1 ?? 1.0)
        let gridWidthPx = Double(max(1, Int(round(gridSizeUm / pixelSizeX))))
        let gridHeightPx = Double(max(1, Int(round(gridSizeUm / pixelSizeY))))
        let gridPx = sqrt(gridWidthPx * gridHeightPx)
        let columns = max(1, Int(ceil(Double(width) / gridWidthPx)))
        let rows = max(1, Int(ceil(Double(height) / gridHeightPx)))
        let validAssignments = assignments.filter { !excludedClusterTypes.contains($0.assignedType) }

        var buckets: [String: [CellTypeAssignment]] = [:]
        for assignment in validAssignments {
            let column = min(max(Int(assignment.centroidX / gridWidthPx), 0), columns - 1)
            let row = min(max(Int(assignment.centroidY / gridHeightPx), 0), rows - 1)
            buckets["\(row)-\(column)", default: []].append(assignment)
        }

        let tiles = buckets.compactMap { key, cells -> NeighborhoodTile? in
            guard !cells.isEmpty else { return nil }
            let parts = key.split(separator: "-")
            guard parts.count == 2,
                  let row = Int(parts[0]),
                  let column = Int(parts[1]) else {
                return nil
            }
            let counts = Dictionary(grouping: cells, by: \.assignedType).mapValues(\.count)
            let dominant = counts.sorted {
                if $0.value == $1.value {
                    return $0.key.localizedStandardCompare($1.key) == .orderedAscending
                }
                return $0.value > $1.value
            }.first
            let dominantName = dominant?.key ?? "Unassigned"
            let color = cells.first { $0.assignedType == dominantName }?.colorHex ?? "#777777"
            let clusterKey = NeighborhoodTile.makeClusterKey(from: counts)
            let clusterLabel = NeighborhoodTile.makeClusterLabel(from: counts)
            let x = Double(column) * gridWidthPx
            let y = Double(row) * gridHeightPx
            return NeighborhoodTile(
                row: row,
                column: column,
                xPx: x,
                yPx: y,
                widthPx: min(gridWidthPx, Double(width) - x),
                heightPx: min(gridHeightPx, Double(height) - y),
                dominantType: dominantName,
                colorHex: color,
                totalCells: cells.count,
                assignedCells: cells.filter { $0.assignedType != "Unassigned" && $0.assignedType != "Ambiguous" }.count,
                countsByType: counts,
                clusterID: nil,
                clusterKey: clusterKey,
                clusterLabel: clusterLabel
            )
        }
        .sorted {
            if $0.row == $1.row { return $0.column < $1.column }
            return $0.row < $1.row
        }

        let clusterCounts = makeClusterCounts(tiles: tiles, totalGridTileCount: rows * columns)
        let clusterByKey = Dictionary(uniqueKeysWithValues: clusterCounts.map { ($0.clusterKey, $0) })
        let coloredTiles = tiles.map { tile in
            var copy = tile
            if let cluster = clusterByKey[tile.effectiveClusterKey] {
                copy.clusterID = cluster.clusterID
                copy.clusterKey = cluster.clusterKey
                copy.clusterLabel = cluster.clusterLabel
                copy.colorHex = cluster.colorHex
            }
            return copy
        }
        let dominantCounts = makeDominantCounts(tiles: tiles)
        let image = renderNeighborhoodMap(
            tiles: coloredTiles,
            clusterCounts: clusterCounts,
            gridSizeUm: gridSizeUm,
            width: width,
            height: height
        )
        let clusterKeyImage = renderClusterKeyImage(counts: clusterCounts)
        let statsImage = renderClusterCountsPlot(counts: clusterCounts)

        return NeighborhoodAnalysisResult(
            tiles: coloredTiles,
            dominantCounts: dominantCounts,
            clusterCounts: clusterCounts,
            gridSizeUm: gridSizeUm,
            gridSizePx: gridPx,
            gridWidthPx: gridWidthPx,
            gridHeightPx: gridHeightPx,
            image: image,
            clusterKeyImage: clusterKeyImage,
            statsImage: statsImage,
            width: width,
            height: height
        )
    }

    static func makeClusterCounts(tiles: [NeighborhoodTile], totalGridTileCount: Int? = nil) -> [NeighborhoodClusterCount] {
        let displayableTiles = tiles.filter(isDisplayableTile)
        let grouped = Dictionary(grouping: displayableTiles, by: \.effectiveClusterKey)
        let ordered = grouped
            .map { key, rows -> (key: String, label: String, tileCount: Int, cellCount: Int, typeCount: Int) in
                let label = rows.first?.effectiveClusterLabel ?? key.replacingOccurrences(of: "|", with: " + ")
                let typeCount = key.split(separator: "|").count
                return (
                    key: key,
                    label: label,
                    tileCount: rows.count,
                    cellCount: rows.reduce(0) { $0 + $1.assignedCells },
                    typeCount: typeCount
                )
            }
            .sorted {
                if $0.typeCount == $1.typeCount {
                    return $0.label.localizedStandardCompare($1.label) == .orderedAscending
                }
                return $0.typeCount < $1.typeCount
            }
        let denominator = Double(max(1, totalGridTileCount ?? displayableTiles.count))
        return ordered.enumerated().map { index, row in
            NeighborhoodClusterCount(
                clusterID: index + 1,
                clusterKey: row.key,
                clusterLabel: row.label,
                tileCount: row.tileCount,
                cellCount: row.cellCount,
                tileFraction: Double(row.tileCount) / denominator,
                colorHex: ColorPalette.clusterColor(at: index)
            )
        }
    }

    private static func makeDominantCounts(tiles: [NeighborhoodTile]) -> [NeighborhoodTypeCount] {
        let grouped = Dictionary(grouping: tiles.filter(isDisplayableTile), by: \.dominantType)
        return grouped.map { name, rows in
            NeighborhoodTypeCount(
                name: name,
                count: rows.count,
                colorHex: rows.first?.colorHex ?? "#777777"
            )
        }
        .sorted {
            if $0.count == $1.count {
                return $0.name.localizedStandardCompare($1.name) == .orderedAscending
            }
            return $0.count > $1.count
        }
    }

    static func renderNeighborhoodMap(
        tiles: [NeighborhoodTile],
        clusterCounts: [NeighborhoodClusterCount],
        gridSizeUm: Double,
        width: Int,
        height: Int
    ) -> NSImage {
        let sourceWidth = max(1, width)
        let sourceHeight = max(1, height)
        let outerMargin = 0.0
        let mapTargetWidth = 2_120.0
        let mapTargetHeight = 1_430.0
        var scale = min(3.0, mapTargetWidth / Double(sourceWidth), mapTargetHeight / Double(sourceHeight))
        if Double(sourceWidth) * scale < 760.0 {
            scale = min(3.0, 760.0 / Double(sourceWidth))
        }
        scale = max(0.05, scale)
        let mapWidth = Double(sourceWidth) * scale
        let mapHeight = Double(sourceHeight) * scale
        let legendRowHeight = 31.0
        let legendTop = 92.0
        let legendBottom = 34.0
        let maxRowsForMap = max(8, Int(floor(max(180.0, mapHeight - legendTop - legendBottom) / legendRowHeight)))
        let columnsNeededForMap = Int(ceil(Double(clusterCounts.count) / Double(max(1, maxRowsForMap))))
        let baselineColumns = clusterCounts.count > 80 ? 3 : (clusterCounts.count > 42 ? 2 : 1)
        let legendColumns = min(4, max(1, max(baselineColumns, columnsNeededForMap)))
        let legendRows = Int(ceil(Double(clusterCounts.count) / Double(max(1, legendColumns))))
        let maxDigits = max(2, "\(max(1, clusterCounts.count))".count)
        let legendColumnWidth = max(118.0, 60.0 + Double(maxDigits) * 9.0)
        let legendWidth = max(520.0, legendColumnWidth * Double(legendColumns) + 34.0)
        let legendHeight = legendTop + Double(legendRows) * legendRowHeight + legendBottom
        let figureWidth = ceil(outerMargin * 2.0 + mapWidth + legendWidth)
        let figureHeight = ceil(max(mapHeight, legendHeight))
        let mapX = outerMargin
        let mapY = (figureHeight - mapHeight) / 2.0
        let legendX = mapX + mapWidth
        let image = NSImage(size: NSSize(width: figureWidth, height: figureHeight))
        image.lockFocus()
        NSColor.white.setFill()
        NSRect(x: 0, y: 0, width: figureWidth, height: figureHeight).fill()
        NSColor.black.setFill()
        NSRect(x: mapX, y: mapY, width: mapWidth, height: mapHeight).fill()

        NSGraphicsContext.current?.cgContext.setShouldAntialias(false)
        for tile in tiles where isDisplayableTile(tile) {
            let color = NSColor(hex: tile.colorHex) ?? .systemGray
            let rect = NSRect(
                x: mapX + tile.xPx * scale,
                y: mapY + (Double(sourceHeight) - tile.yPx - tile.heightPx) * scale,
                width: max(1.0, tile.widthPx * scale),
                height: max(1.0, tile.heightPx * scale)
            ).insetBy(dx: -0.35, dy: -0.35)
            color.setFill()
            rect.fill()
        }
        NSGraphicsContext.current?.cgContext.setShouldAntialias(true)

        let title = "Neighborhood clusters (\(String(format: "%.1f", gridSizeUm)) um grid)"
        let titleAttributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 38, weight: .bold),
            .foregroundColor: NSColor.white
        ]
        let titleSize = title.size(withAttributes: titleAttributes)
        let titleX = mapX + max(14.0, (mapWidth - titleSize.width) / 2.0)
        let titleY = mapY + mapHeight - titleSize.height - 12.0
        NSColor.black.withAlphaComponent(0.36).setFill()
        NSBezierPath(roundedRect: NSRect(x: titleX - 16.0, y: titleY - 4.0, width: titleSize.width + 32.0, height: titleSize.height + 8.0), xRadius: 4, yRadius: 4).fill()
        title.draw(at: NSPoint(x: titleX, y: titleY), withAttributes: titleAttributes)

        NSColor.white.setFill()
        NSRect(x: legendX, y: 0, width: legendWidth, height: figureHeight).fill()
        NSColor(calibratedWhite: 0.84, alpha: 1).setStroke()
        NSBezierPath.strokeLine(from: NSPoint(x: legendX, y: 0), to: NSPoint(x: legendX, y: figureHeight))

        let legendTitleAttributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 26, weight: .medium),
            .foregroundColor: NSColor(calibratedWhite: 0.10, alpha: 1)
        ]
        let subheadAttributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 15, weight: .regular),
            .foregroundColor: NSColor(calibratedWhite: 0.28, alpha: 1)
        ]
        "Clusters".draw(
            at: NSPoint(x: legendX + 34.0, y: figureHeight - 44.0),
            withAttributes: legendTitleAttributes
        )
        "\(clusterCounts.count) types, \(String(format: "%.1f", gridSizeUm)) um grid; see key for names".draw(
            at: NSPoint(x: legendX + 34.0, y: figureHeight - 72.0),
            withAttributes: subheadAttributes
        )

        let numberAttributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 16, weight: .medium),
            .foregroundColor: NSColor(calibratedWhite: 0.12, alpha: 1)
        ]
        let legendStartY = figureHeight - legendTop
        for (index, cluster) in clusterCounts.enumerated() {
            let column = index / max(1, legendRows)
            let row = index % max(1, legendRows)
            let x = legendX + 34.0 + Double(column) * legendColumnWidth
            let y = legendStartY - Double(row) * legendRowHeight
            let swatch = NSRect(x: x, y: y - 14.0, width: 23.0, height: 18.0)
            let color = NSColor(hex: cluster.colorHex) ?? .systemGray
            color.setFill()
            swatch.fill()
            NSColor(calibratedWhite: 0.72, alpha: 1).setStroke()
            NSBezierPath(rect: swatch).stroke()
            "\(cluster.clusterID)".draw(
                at: NSPoint(x: x + 33.0, y: y - 15.0),
                withAttributes: numberAttributes
            )
        }

        image.unlockFocus()
        return image
    }

    private static func isDisplayableTile(_ tile: NeighborhoodTile) -> Bool {
        tile.assignedCells > 0
            && !tile.effectiveClusterKey.isEmpty
            && !excludedClusterTypes.contains(tile.effectiveClusterKey)
    }

    static func renderClusterKeyImage(counts: [NeighborhoodClusterCount]) -> NSImage {
        let width = 1320.0
        let columnCount = counts.count > 18 ? 2 : 1
        let rowCount = Int(ceil(Double(counts.count) / Double(columnCount)))
        let rowHeight = 31.0
        let top = 74.0
        let bottom = 34.0
        let columnGap = 28.0
        let side = 34.0
        let columnWidth = (width - side * 2.0 - columnGap * Double(columnCount - 1)) / Double(columnCount)
        let height = max(420.0, top + bottom + Double(rowCount) * rowHeight)
        let image = NSImage(size: NSSize(width: width, height: height))

        image.lockFocus()
        NSColor.white.setFill()
        NSRect(x: 0, y: 0, width: width, height: height).fill()

        "Number-to-cluster ID key".draw(
            at: NSPoint(x: side, y: height - 38.0),
            withAttributes: [
                .font: NSFont.systemFont(ofSize: 28, weight: .semibold),
                .foregroundColor: NSColor(calibratedWhite: 0.12, alpha: 1)
            ]
        )
        "Cluster numbers used in the map legend; full mapping is also saved as CSV and TXT.".draw(
            at: NSPoint(x: side, y: height - 61.0),
            withAttributes: [
                .font: NSFont.systemFont(ofSize: 15, weight: .regular),
                .foregroundColor: NSColor(calibratedWhite: 0.36, alpha: 1)
            ]
        )

        let labelAttributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 14, weight: .regular),
            .foregroundColor: NSColor(calibratedWhite: 0.14, alpha: 1)
        ]
        let metaAttributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .regular),
            .foregroundColor: NSColor(calibratedWhite: 0.42, alpha: 1)
        ]
        let idAttributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 14, weight: .semibold),
            .foregroundColor: NSColor(calibratedWhite: 0.1, alpha: 1)
        ]

        for (index, row) in counts.enumerated() {
            let column = index / max(1, rowCount)
            let rowIndex = index % max(1, rowCount)
            let x = side + Double(column) * (columnWidth + columnGap)
            let y = height - top - Double(rowIndex) * rowHeight
            if rowIndex % 2 == 0 {
                NSColor(calibratedWhite: 0.965, alpha: 1).setFill()
                NSRect(x: x - 6.0, y: y - 20.0, width: columnWidth + 12.0, height: rowHeight).fill()
            }
            let color = NSColor(hex: row.colorHex) ?? .systemGray
            color.setFill()
            NSRect(x: x, y: y - 14.0, width: 13.0, height: 13.0).fill()
            NSColor(calibratedWhite: 0.7, alpha: 1).setStroke()
            NSBezierPath(rect: NSRect(x: x, y: y - 14.0, width: 13.0, height: 13.0)).stroke()
            "\(row.clusterID)".draw(
                at: NSPoint(x: x + 20.0, y: y - 17.0),
                withAttributes: idAttributes
            )
            row.clusterLabel.draw(
                in: NSRect(x: x + 72.0, y: y - 18.0, width: columnWidth - 190.0, height: 24.0),
                withAttributes: labelAttributes
            )
            "\(row.tileCount) tiles, \(row.cellCount) cells".draw(
                in: NSRect(x: x + columnWidth - 118.0, y: y - 17.0, width: 118.0, height: 22.0),
                withAttributes: metaAttributes
            )
        }

        image.unlockFocus()
        return image
    }

    static func renderClusterCountsPlot(counts: [NeighborhoodClusterCount]) -> NSImage {
        let width = 960.0
        let rowHeight = 32.0
        let height = max(440.0, 110.0 + Double(counts.count) * rowHeight)
        let left = 340.0
        let right = 86.0
        let top = 70.0
        let bottom = 44.0
        let plotWidth = width - left - right
        let maxCount = max(1, counts.map(\.tileCount).max() ?? 1)
        let image = NSImage(size: NSSize(width: width, height: height))

        image.lockFocus()
        NSColor.white.setFill()
        NSRect(x: 0, y: 0, width: width, height: height).fill()
        NSColor(calibratedWhite: 0.12, alpha: 1).setStroke()
        NSBezierPath.strokeLine(from: NSPoint(x: left, y: bottom), to: NSPoint(x: left, y: height - top))
        NSBezierPath.strokeLine(from: NSPoint(x: left, y: bottom), to: NSPoint(x: width - right, y: bottom))

        "Neighborhood cluster counts".draw(
            at: NSPoint(x: left, y: height - 32),
            withAttributes: [
                .font: NSFont.systemFont(ofSize: 28, weight: .semibold),
                .foregroundColor: NSColor(calibratedWhite: 0.12, alpha: 1)
            ]
        )

        for (index, row) in counts.enumerated() {
            let centerY = height - top - Double(index) * rowHeight - rowHeight / 2.0
            let barHeight = 19.0
            let barWidth = Double(row.tileCount) / Double(maxCount) * plotWidth
            let x = left
            let y = centerY - barHeight / 2.0
            let color = NSColor(hex: row.colorHex) ?? .systemBlue
            color.setFill()
            NSRect(x: x, y: y, width: max(1.0, barWidth), height: barHeight).fill()
            "\(row.tileCount)".draw(
                at: NSPoint(x: x + max(1.0, barWidth) + 8.0, y: y - 1.0),
                withAttributes: [
                    .font: NSFont.systemFont(ofSize: 18, weight: .medium),
                    .foregroundColor: NSColor(calibratedWhite: 0.12, alpha: 1)
                ]
            )
            "\(row.clusterID)  \(row.clusterLabel)".draw(
                in: NSRect(x: 28.0, y: y - 4.0, width: left - 42.0, height: rowHeight),
                withAttributes: [
                    .font: NSFont.systemFont(ofSize: 15, weight: .regular),
                    .foregroundColor: NSColor(calibratedWhite: 0.18, alpha: 1)
                ]
            )
        }

        image.unlockFocus()
        return image
    }

}
