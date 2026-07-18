import AppKit
import Foundation

enum EditableSVGWriter {
    static func writeOverlaySVG(
        baseImage: NSImage,
        channels: [ChannelConfig],
        pixelSizeXUm: Double?,
        to url: URL
    ) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let size = ImageExportService.pixelSize(for: baseImage)
        let width = max(1, Int(size.width))
        let height = max(1, Int(size.height))
        let vectorRects = try ImageExportService.vectorSVGRectElements(
            for: baseImage,
            x: 0,
            y: 0,
            displayWidth: Double(width),
            displayHeight: Double(height)
        )

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(width)" height="\(height)" viewBox="0 0 \(width) \(height)">
          <title>Overlay preview</title>
          <desc>Editable vectorized multiplex image with channel labels and scalebar. No linked raster files are used.</desc>
          <rect id="black-background" x="0" y="0" width="\(width)" height="\(height)" fill="#000000"/>
        \(vectorRects)
          <g id="channel-labels" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="\(max(12, min(22, width / 36)))" font-weight="700" text-anchor="end">
        """

        var y = max(22, min(34, height / 24))
        let dy = max(18, min(30, height / 52))
        for channel in channels.prefix(18) {
            body += """

            <text x="\(width - 14)" y="\(y)" fill="\(escape(channel.colorHex))">\(escape(channel.channelName))</text>
            """
            y += dy
        }

        body += """

          </g>
        """

        if let pixelSizeXUm, pixelSizeXUm > 0 {
            let barUm = 20.0
            let barPixels = max(1, Int(round(barUm / pixelSizeXUm)))
            let x2 = Int(Double(width) * 0.94)
            let x1 = max(Int(Double(width) * 0.06), x2 - barPixels)
            let yBar = Int(Double(height) * 0.07)
            body += """

            <g id="scalebar" stroke="#ffffff" stroke-width="\(max(3, width / 160))" stroke-linecap="butt">
              <line x1="\(x1)" y1="\(height - yBar)" x2="\(x2)" y2="\(height - yBar)"/>
            </g>
            """
        }

        body += """

        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeSplitChannelsSVG(tiles: [SplitChannelTile], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard !tiles.isEmpty else {
            try emptySVG(title: "Split channels").write(to: url, atomically: true, encoding: .utf8)
            return
        }

        let columns = tiles.count <= 4 ? 2 : 3
        let rows = Int(ceil(Double(tiles.count) / Double(columns)))
        let tileMax = 340.0
        let labelHeight = 28.0
        let gap = 14.0
        let firstSize = ImageExportService.pixelSize(for: tiles[0].image)
        let scale = min(tileMax / max(1.0, Double(firstSize.width)), tileMax / max(1.0, Double(firstSize.height)), 1.0)
        let tileWidth = max(80.0, Double(firstSize.width) * scale)
        let tileHeight = max(80.0, Double(firstSize.height) * scale)
        let canvasWidth = Double(columns) * tileWidth + Double(columns + 1) * gap
        let canvasHeight = Double(rows) * (tileHeight + labelHeight) + Double(rows + 1) * gap

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(canvasWidth))" height="\(Int(canvasHeight))" viewBox="0 0 \(Int(canvasWidth)) \(Int(canvasHeight))">
          <title>Split channels</title>
          <desc>Editable vectorized split-channel tiles with channel labels. No linked raster files are used.</desc>
          <rect id="black-background" x="0" y="0" width="\(Int(canvasWidth))" height="\(Int(canvasHeight))" fill="#000000"/>
          <g id="split-channel-tiles" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="13" font-weight="600">
        """

        for (index, tile) in tiles.enumerated() {
            let col = index % columns
            let row = index / columns
            let x = gap + Double(col) * (tileWidth + gap)
            let y = gap + Double(row) * (tileHeight + labelHeight + gap)
            let vectorRects = try ImageExportService.vectorSVGRectElements(
                for: tile.image,
                x: x,
                y: y + labelHeight,
                displayWidth: tileWidth,
                displayHeight: tileHeight
            )
            body += """

            <g id="channel-\(index + 1)">
              <text x="\(fmt(x))" y="\(fmt(y + 16))" fill="\(escape(tile.colorHex))">\(escape(tile.channelName))</text>
            \(vectorRects)
            </g>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeNucleiSegmentationSVG(result: NucleiSegmentationResult, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let size = ImageExportService.pixelSize(for: result.image)
        let width = max(1, Int(size.width))
        let height = max(1, Int(size.height))
        let palette = ColorPalette.segmentation

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(width)" height="\(height)" viewBox="0 0 \(width) \(height)">
          <title>Nuclei segmentation - \(escape(result.channelName))</title>
          <desc>Editable vector nuclei mask-like shapes. Each nucleus stores centroid, approximate area, and label metadata.</desc>
          <rect id="black-background" x="0" y="0" width="\(width)" height="\(height)" fill="#000000"/>
          <g id="nuclei">
        """

        for detection in result.detections {
            let radius = max(1.5, sqrt(Double(max(1, detection.areaPx)) / Double.pi))
            let color = palette[(detection.id - 1) % palette.count]
            let points = maskLikePolygonPoints(
                x: detection.centroidX,
                y: detection.centroidY,
                radius: radius,
                seed: detection.id
            )
            body += """

            <polygon id="nucleus-\(detection.id)" points="\(points)" fill="\(color)" fill-opacity="0.82" data-centroid-x="\(fmt(detection.centroidX))" data-centroid-y="\(fmt(detection.centroidY))" data-area-px="\(detection.areaPx)" data-mean-intensity="\(fmt(detection.meanIntensity))">
              <title>Nucleus \(detection.id): area \(detection.areaPx) px, mean \(fmt(detection.meanIntensity))</title>
            </polygon>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeNucleiScanSVG(records: [NucleiScanRecord], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let sorted = records.sorted { $0.comboIndex < $1.comboIndex }
        guard !sorted.isEmpty else {
            try emptySVG(title: "Nuclei parameter scan").write(to: url, atomically: true, encoding: .utf8)
            return
        }

        let width = 1400.0
        let height = 720.0
        let left = 90.0
        let right = 44.0
        let top = 62.0
        let bottom = 92.0
        let plotWidth = width - left - right
        let plotHeight = height - top - bottom
        let maxCount = max(1, sorted.map(\.count).max() ?? 1)
        let best = sorted.max {
            if $0.count == $1.count { return $0.comboIndex > $1.comboIndex }
            return $0.count < $1.count
        }
        let barWidth = max(2.0, plotWidth / Double(sorted.count) * 0.72)

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(fmt(width))" height="\(fmt(height))" viewBox="0 0 \(fmt(width)) \(fmt(height))">
          <title>Nuclei parameter scan</title>
          <desc>Editable bar chart. Each bar contains parameter metadata and detected nuclei count.</desc>
          <g id="axes" fill="none" stroke="#222222" stroke-width="1.2">
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(left))" y2="\(fmt(top))"/>
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(left + plotWidth))" y2="\(fmt(height - bottom))"/>
          </g>
          <text x="\(fmt(left))" y="32" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="28" font-weight="600" fill="#222222">Nuclei parameter scan</text>
        """

        if let best {
            body += """

              <text x="\(fmt(left))" y="56" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="18" fill="#d62728">Recommended: combo \(best.comboIndex), \(best.count) nuclei</text>
            """
        }

        body += """

          <text x="\(fmt(left + plotWidth / 2))" y="\(fmt(height - 28))" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="18" fill="#555555">Parameter setting / combo #</text>
          <text x="26" y="\(fmt(top + plotHeight / 2))" transform="rotate(-90 26 \(fmt(top + plotHeight / 2)))" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="18" fill="#555555">Nuclei count</text>
          <g id="bars">
        """

        for (idx, record) in sorted.enumerated() {
            let xCenter = left + (Double(idx) / Double(max(1, sorted.count - 1))) * plotWidth
            let barHeight = Double(record.count) / Double(maxCount) * plotHeight
            let x = xCenter - barWidth / 2.0
            let y = height - bottom - barHeight
            let fill = record.comboIndex == best?.comboIndex ? "#d62728" : (record.stage.hasPrefix("refine") ? "#1f77b4" : "#8fbce6")
            let p = record.params
            body += """

            <rect id="combo-\(record.comboIndex)" x="\(fmt(x))" y="\(fmt(y))" width="\(fmt(barWidth))" height="\(fmt(barHeight))" fill="\(fill)" fill-opacity="0.82" data-stage="\(escape(record.stage))" data-count="\(record.count)" data-min-diam-um="\(fmt(p.minDiamUm))" data-max-diam-um="\(fmt(p.maxDiamUm))" data-tophat-radius-um="\(fmt(p.tophatRadiusUm))" data-gauss-sigma-um="\(fmt(p.gaussSigmaUm))" data-local-win-um="\(fmt(p.localWinUm))" data-local-offset="\(fmt(p.localOffset))" data-h-maxima-um="\(fmt(p.hMaximaUm))" data-seed-min-dist-um="\(fmt(p.seedMinDistUm))" data-watershed-compactness="\(fmt(p.watershedCompactness))" data-post-resplit-mult="\(fmt(p.postResplitMult))">
              <title>Combo \(record.comboIndex): \(record.count) nuclei; min \(fmt(p.minDiamUm)), max \(fmt(p.maxDiamUm)), top \(fmt(p.tophatRadiusUm)), sigma \(fmt(p.gaussSigmaUm)), win \(fmt(p.localWinUm)), offset \(fmt(p.localOffset)), h \(fmt(p.hMaximaUm)), seed \(fmt(p.seedMinDistUm)), compact \(fmt(p.watershedCompactness)), split \(fmt(p.postResplitMult))</title>
            </rect>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeCellTypeAssignmentMapSVG(result: CellTypeAssignmentResult, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let width = max(1, result.width)
        let height = max(1, result.height)

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(width)" height="\(height)" viewBox="0 0 \(width) \(height)">
          <title>Cell type assignment map</title>
          <desc>Editable vector cell assignments on a black imaging background. Each cell region follows marker-signal support for the assigned cell type.</desc>
          <rect id="black-background" x="0" y="0" width="\(width)" height="\(height)" fill="#000000"/>
          <g id="cell-type-assignments">
        """

        for assignment in result.assignments {
            guard assignment.assignedType != "Unassigned", assignment.assignedType != "Ambiguous" else {
                continue
            }
            let radius = max(2.0, sqrt(Double(max(1, assignment.areaPx)) / Double.pi))
            let boundaryPoints = (assignment.cellBoundaryPoints?.count ?? 0) >= 3
                ? polygonPoints(assignment.cellBoundaryPoints ?? [])
                : maskLikePolygonPoints(
                    x: assignment.centroidX,
                    y: assignment.centroidY,
                    radius: radius + 1.5,
                    seed: assignment.nucleusID + 7_919
                )
            let signalOpacity = "0.90"
            let markerMeans = assignment.markerMeans
                .sorted { $0.key.localizedStandardCompare($1.key) == .orderedAscending }
                .prefix(24)
                .map { "\($0.key)=\(fmt($0.value))" }
                .joined(separator: "; ")
            body += """

            <g id="cell-\(assignment.nucleusID)" data-cell-type="\(escape(assignment.assignedType))" data-score="\(fmt(assignment.score))" data-probability="\(fmt(assignment.probability))">
              <title>Cell \(assignment.nucleusID): \(escape(assignment.assignedType)); score \(fmt(assignment.score)); probability \(fmt(assignment.probability)); \(escape(markerMeans))</title>
              <polygon id="cell-signal-region-\(assignment.nucleusID)" points="\(boundaryPoints)" fill="\(escape(assignment.colorHex))" fill-opacity="\(signalOpacity)"/>
            </g>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeCellTypeCountsSVG(counts: [CellTypeCount], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard !counts.isEmpty else {
            try emptySVG(title: "Cell type counts").write(to: url, atomically: true, encoding: .utf8)
            return
        }

        let width = 760.0
        let height = 430.0
        let left = 82.0
        let right = 34.0
        let top = 54.0
        let bottom = 126.0
        let plotWidth = width - left - right
        let plotHeight = height - top - bottom
        let maxCount = max(1, counts.map(\.count).max() ?? 1)
        let step = plotWidth / Double(max(1, counts.count))
        let barWidth = min(72.0, step * 0.62)

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(width))" height="\(Int(height))" viewBox="0 0 \(Int(width)) \(Int(height))">
          <title>Cell type counts</title>
          <desc>Editable statistical bar chart of assigned cell type counts.</desc>
          <rect id="white-background" x="0" y="0" width="\(Int(width))" height="\(Int(height))" fill="#ffffff"/>
          <g id="axes" fill="none" stroke="#222222" stroke-width="1.2">
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(left))" y2="\(fmt(top))"/>
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(width - right))" y2="\(fmt(height - bottom))"/>
          </g>
          <text x="\(fmt(left))" y="32" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="28" font-weight="600" fill="#222222">Cell type counts</text>
          <g id="bars" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif">
        """

        for (index, row) in counts.enumerated() {
            let barHeight = Double(row.count) / Double(maxCount) * plotHeight
            let x = left + Double(index) * step + (step - barWidth) / 2.0
            let y = height - bottom - barHeight
            body += """

            <rect id="count-\(index + 1)" x="\(fmt(x))" y="\(fmt(y))" width="\(fmt(barWidth))" height="\(fmt(barHeight))" fill="\(escape(row.colorHex))" data-cell-type="\(escape(row.name))" data-count="\(row.count)">
              <title>\(escape(row.name)): \(row.count)</title>
            </rect>
            <text x="\(fmt(x + barWidth / 2))" y="\(fmt(y - 8))" text-anchor="middle" font-size="17" font-weight="600" fill="#222222">\(row.count)</text>
            <text x="\(fmt(x + barWidth / 2))" y="\(fmt(height - bottom + 20))" text-anchor="end" transform="rotate(-45 \(fmt(x + barWidth / 2)) \(fmt(height - bottom + 20)))" font-size="18" fill="#333333">\(escape(row.name))</text>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeNeighborhoodMapSVG(result: NeighborhoodAnalysisResult, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let sourceWidth = max(1, result.width)
        let sourceHeight = max(1, result.height)
        let mapTargetWidth = 2_120.0
        let mapTargetHeight = 1_430.0
        var scale = min(3.0, mapTargetWidth / Double(sourceWidth), mapTargetHeight / Double(sourceHeight))
        if Double(sourceWidth) * scale < 760.0 {
            scale = min(3.0, 760.0 / Double(sourceWidth))
        }
        scale = max(0.05, scale)
        let mapWidth = Double(sourceWidth) * scale
        let mapHeight = Double(sourceHeight) * scale
        let legendRowHeight = 31
        let legendTop = 92
        let legendBottom = 34
        let maxRowsForMap = max(8, Int(floor(max(180.0, mapHeight - Double(legendTop) - Double(legendBottom)) / Double(legendRowHeight))))
        let columnsNeededForMap = Int(ceil(Double(result.clusterCounts.count) / Double(max(1, maxRowsForMap))))
        let baselineColumns = result.clusterCounts.count > 80 ? 3 : (result.clusterCounts.count > 42 ? 2 : 1)
        let legendColumns = min(4, max(1, max(baselineColumns, columnsNeededForMap)))
        let legendRows = Int(ceil(Double(result.clusterCounts.count) / Double(max(1, legendColumns))))
        let maxDigits = max(2, "\(max(1, result.clusterCounts.count))".count)
        let legendColumnWidth = max(118.0, 60.0 + Double(maxDigits) * 9.0)
        let legendWidth = max(520.0, legendColumnWidth * Double(legendColumns) + 34.0)
        let legendHeight = Double(legendTop + legendBottom) + Double(legendRows * legendRowHeight)
        let width = Int(ceil(mapWidth + legendWidth))
        let height = Int(ceil(max(mapHeight, legendHeight)))
        let mapY = (Double(height) - mapHeight) / 2.0
        let legendX = mapWidth
        let title = "Neighborhood clusters (\(String(format: "%.1f", result.gridSizeUm)) um grid)"
        let titleFontSize = 38.0
        let estimatedTitleWidth = min(mapWidth - 28.0, max(420.0, Double(title.count) * titleFontSize * 0.56))
        let titleX = max(14.0, (mapWidth - estimatedTitleWidth) / 2.0)
        let titleY = mapY + 48.0

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(width)" height="\(height)" viewBox="0 0 \(width) \(height)">
          <title>Neighborhood analysis map</title>
          <desc>Editable neighborhood cluster map with a black tile background and a separate white cluster-number legend. Each non-empty tile stores cluster composition metadata.</desc>
          <rect id="white-background" x="0" y="0" width="\(width)" height="\(height)" fill="#ffffff"/>
          <rect id="black-map-background" x="0" y="\(fmt(mapY))" width="\(fmt(mapWidth))" height="\(fmt(mapHeight))" fill="#000000"/>
          <g id="neighborhood-tiles" transform="translate(0 \(fmt(mapY))) scale(\(fmt(scale)))" shape-rendering="crispEdges">
        """

        for tile in result.tiles where isDisplayableNeighborhoodTile(tile) {
            let composition = tile.countsByType
                .sorted { $0.key.localizedStandardCompare($1.key) == .orderedAscending }
                .map { "\($0.key)=\($0.value)" }
                .joined(separator: "; ")
            let clusterID = tile.clusterID.map(String.init) ?? ""
            body += """

            <rect id="tile-r\(tile.row)-c\(tile.column)" x="\(fmt(tile.xPx - 0.12))" y="\(fmt(tile.yPx - 0.12))" width="\(fmt(tile.widthPx + 0.24))" height="\(fmt(tile.heightPx + 0.24))" fill="\(escape(tile.colorHex))" data-cluster-id="\(escape(clusterID))" data-cluster-key="\(escape(tile.effectiveClusterKey))" data-cluster-label="\(escape(tile.effectiveClusterLabel))" data-dominant-type="\(escape(tile.dominantType))" data-total-cells="\(tile.totalCells)" data-assigned-cells="\(tile.assignedCells)">
              <title>Row \(tile.row), column \(tile.column): \(escape(tile.effectiveClusterLabel)); total \(tile.totalCells); assigned \(tile.assignedCells); \(escape(composition))</title>
            </rect>
            """
        }

        body += """

          </g>
          <g id="map-title" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif">
            <rect x="\(fmt(titleX - 16.0))" y="\(fmt(titleY - titleFontSize + 5.0))" width="\(fmt(estimatedTitleWidth + 32.0))" height="\(fmt(titleFontSize + 12.0))" fill="#000000" fill-opacity="0.36"/>
            <text x="\(fmt(mapWidth / 2.0))" y="\(fmt(titleY))" text-anchor="middle" font-size="\(fmt(titleFontSize))" font-weight="700" fill="#ffffff">\(escape(title))</text>
          </g>
          <rect id="legend-background" x="\(fmt(legendX))" y="0" width="\(fmt(legendWidth))" height="\(height)" fill="#ffffff"/>
          <line id="legend-divider" x1="\(fmt(legendX))" y1="0" x2="\(fmt(legendX))" y2="\(height)" stroke="#d6d6d6" stroke-width="1"/>
          <g id="cluster-legend" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif">
            <text x="\(fmt(legendX + 34.0))" y="44" font-size="26" font-weight="500" fill="#1a1a1a">Clusters</text>
            <text x="\(fmt(legendX + 34.0))" y="72" font-size="15" fill="#474747">\(result.clusterCounts.count) types, \(fmt(result.gridSizeUm)) um grid; see key for names</text>
        """

        for (index, row) in result.clusterCounts.enumerated() {
            let column = index / max(1, legendRows)
            let rowIndex = index % max(1, legendRows)
            let x = legendX + 34.0 + Double(column) * legendColumnWidth
            let y = 92.0 + Double(rowIndex * legendRowHeight)
            body += """

            <rect id="legend-cluster-\(row.clusterID)-swatch" x="\(fmt(x))" y="\(fmt(y - 14.0))" width="23" height="18" fill="\(escape(row.colorHex))" stroke="#b8b8b8" stroke-width="0.8"/>
            <text x="\(fmt(x + 33.0))" y="\(fmt(y))" font-size="16" font-weight="500" fill="#1e1e1e">\(row.clusterID)</text>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeNeighborhoodClusterKeySVG(counts: [NeighborhoodClusterCount], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard !counts.isEmpty else {
            try emptySVG(title: "Neighborhood cluster key").write(to: url, atomically: true, encoding: .utf8)
            return
        }

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

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(width))" height="\(Int(height))" viewBox="0 0 \(Int(width)) \(Int(height))">
          <title>Neighborhood cluster key</title>
          <desc>Editable lookup table mapping neighborhood map numbers to cluster compositions.</desc>
          <rect id="white-background" x="0" y="0" width="\(Int(width))" height="\(Int(height))" fill="#ffffff"/>
          <text x="\(fmt(side))" y="38" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="28" font-weight="600" fill="#222222">Number-to-cluster ID key</text>
          <text x="\(fmt(side))" y="61" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="15" fill="#5c5c5c">Cluster numbers used in the map legend; full mapping is also saved as CSV and TXT.</text>
          <g id="cluster-key-rows" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif">
        """

        for (index, row) in counts.enumerated() {
            let column = index / max(1, rowCount)
            let rowIndex = index % max(1, rowCount)
            let x = side + Double(column) * (columnWidth + columnGap)
            let y = height - top - Double(rowIndex) * rowHeight
            if rowIndex % 2 == 0 {
                body += """

            <rect id="cluster-key-row-\(row.clusterID)-background" x="\(fmt(x - 6))" y="\(fmt(y - 20))" width="\(fmt(columnWidth + 12))" height="\(fmt(rowHeight))" fill="#f6f6f6"/>
            """
            }
            body += """

            <rect id="cluster-key-\(row.clusterID)-swatch" x="\(fmt(x))" y="\(fmt(y - 14))" width="13" height="13" fill="\(escape(row.colorHex))" stroke="#aaaaaa" stroke-width="0.8"/>
            <text x="\(fmt(x + 20))" y="\(fmt(y - 3))" font-size="14" font-weight="700" fill="#1a1a1a">\(row.clusterID)</text>
            <text x="\(fmt(x + 72))" y="\(fmt(y - 3))" font-size="14" fill="#242424">\(escape(row.clusterLabel))</text>
            <text x="\(fmt(x + columnWidth))" y="\(fmt(y - 3))" font-size="12" fill="#666666" text-anchor="end">\(row.tileCount) tiles, \(row.cellCount) cells</text>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    private static func isDisplayableNeighborhoodTile(_ tile: NeighborhoodTile) -> Bool {
        tile.assignedCells > 0
            && !tile.effectiveClusterKey.isEmpty
    }

    static func writeNeighborhoodClusterSummarySVG(counts: [NeighborhoodClusterCount], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard !counts.isEmpty else {
            try emptySVG(title: "Neighborhood cluster counts").write(to: url, atomically: true, encoding: .utf8)
            return
        }

        let width = 1120.0
        let rowHeight = 34.0
        let height = max(520.0, 118.0 + Double(counts.count) * rowHeight)
        let left = 390.0
        let right = 96.0
        let top = 78.0
        let bottom = 48.0
        let plotWidth = width - left - right
        let maxCount = max(1, counts.map(\.tileCount).max() ?? 1)

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(width))" height="\(Int(height))" viewBox="0 0 \(Int(width)) \(Int(height))">
          <title>Neighborhood cluster counts</title>
          <desc>Editable statistical bar chart of neighborhood cluster tile counts.</desc>
          <rect id="white-background" x="0" y="0" width="\(Int(width))" height="\(Int(height))" fill="#ffffff"/>
          <g id="axes" fill="none" stroke="#222222" stroke-width="1.2">
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(left))" y2="\(fmt(top))"/>
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(width - right))" y2="\(fmt(height - bottom))"/>
          </g>
          <text x="\(fmt(left))" y="34" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="28" font-weight="600" fill="#222222">Neighborhood cluster counts</text>
          <g id="bars" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif">
        """

        for (index, row) in counts.enumerated() {
            let centerY = height - top - Double(index) * rowHeight - rowHeight / 2.0
            let barHeight = 19.0
            let barWidth = Double(row.tileCount) / Double(maxCount) * plotWidth
            let x = left
            let y = centerY - barHeight / 2.0
            body += """

            <rect id="cluster-\(row.clusterID)" x="\(fmt(x))" y="\(fmt(y))" width="\(fmt(max(1.0, barWidth)))" height="\(fmt(barHeight))" fill="\(escape(row.colorHex))" data-cluster-key="\(escape(row.clusterKey))" data-cluster-label="\(escape(row.clusterLabel))" data-tile-count="\(row.tileCount)" data-cell-count="\(row.cellCount)">
              <title>\(row.clusterID) \(escape(row.clusterLabel)): \(row.tileCount) tiles, \(row.cellCount) cells</title>
            </rect>
            <text x="28" y="\(fmt(y + 14))" font-size="15" fill="#333333">\(row.clusterID) \(escape(row.clusterLabel))</text>
            <text x="\(fmt(x + max(1.0, barWidth) + 8.0))" y="\(fmt(y + 14))" font-size="17" font-weight="600" fill="#222222">\(row.tileCount)</text>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeRegionMapSVG(
        result: RegionAnalysisResult,
        assignments: [CellTypeAssignment] = [],
        to url: URL
    ) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let width = max(1, result.width)
        let height = max(1, result.height)

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(width)" height="\(height)" viewBox="0 0 \(width) \(height)">
          <title>Region analysis map</title>
          <desc>Editable computational ROI map on a black imaging background. Cells and true ROI mask boundaries are separate vector layers with no linked raster files.</desc>
          <rect id="black-background" x="0" y="0" width="\(width)" height="\(height)" fill="#000000"/>
          <g id="region-cell-background" opacity="0.80">
        """

        for assignment in assignments where assignment.assignedType != "Unassigned" && assignment.assignedType != "Ambiguous" {
            let markerMeans = assignment.markerMeans
                .sorted { $0.key.localizedStandardCompare($1.key) == .orderedAscending }
                .prefix(24)
                .map { "\($0.key)=\(fmt($0.value))" }
                .joined(separator: "; ")
            if let points = assignment.cellBoundaryPoints, points.count >= 3 {
                body += """

            <polygon id="region-cell-\(assignment.nucleusID)" points="\(polygonPoints(points))" fill="\(escape(assignment.colorHex))" data-cell-type="\(escape(assignment.assignedType))">
              <title>Cell \(assignment.nucleusID): \(escape(assignment.assignedType)); \(escape(markerMeans))</title>
            </polygon>
            """
            } else {
                let radius = max(1.8, min(8.0, sqrt(Double(max(1, assignment.areaPx)) / Double.pi)))
                body += """

            <circle id="region-cell-\(assignment.nucleusID)" cx="\(fmt(assignment.centroidX))" cy="\(fmt(assignment.centroidY))" r="\(fmt(radius))" fill="\(escape(assignment.colorHex))" data-cell-type="\(escape(assignment.assignedType))">
              <title>Cell \(assignment.nucleusID): \(escape(assignment.assignedType)); \(escape(markerMeans))</title>
            </circle>
            """
            }
        }

        body += """

          </g>
          <g id="region-boundaries" stroke="none">
        """

        for region in result.regions {
            let composition = region.countsByType
                .sorted { $0.key.localizedStandardCompare($1.key) == .orderedAscending }
                .map { "\($0.key)=\($0.value)" }
                .joined(separator: "; ")
            let mask = RegionAnalyzer.mask(for: region, width: width, height: height)
            let boundary = mask.boundary(thickness: max(1, Int(round(result.parameters.lineWidth))))
            body += """

            <g id="region-\(region.id)" fill="\(escape(region.colorHex))" data-dominant-type="\(escape(region.dominantType))" data-cell-count="\(region.cellCount)" data-assigned-cell-count="\(region.assignedCellCount)" data-area-um2="\(fmt(region.areaUm2))" data-source-type="\(escape(region.sourceType ?? region.dominantType))">
              <title>Region \(region.id): \(escape(region.dominantType)); cells \(region.cellCount); assigned \(region.assignedCellCount); area \(fmt(region.areaUm2)) um2; \(escape(composition))</title>
            """
            for run in boundary.toRuns() {
                body += """

              <rect id="region-\(region.id)-boundary-y\(run.y)-x\(run.xStart)" x="\(run.xStart)" y="\(run.y)" width="\(max(1, run.xEnd - run.xStart))" height="1"/>
            """
            }
            body += """

            </g>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeRegionCountsSVG(counts: [RegionTypeCount], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard !counts.isEmpty else {
            try emptySVG(title: "Region dominant types").write(to: url, atomically: true, encoding: .utf8)
            return
        }

        let width = 760.0
        let height = 430.0
        let left = 82.0
        let right = 34.0
        let top = 54.0
        let bottom = 126.0
        let plotWidth = width - left - right
        let plotHeight = height - top - bottom
        let maxCount = max(1, counts.map(\.count).max() ?? 1)
        let step = plotWidth / Double(max(1, counts.count))
        let barWidth = min(72.0, step * 0.62)

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(width))" height="\(Int(height))" viewBox="0 0 \(Int(width)) \(Int(height))">
          <title>Region dominant types</title>
          <desc>Editable statistical bar chart of dominant type counts across computational ROIs.</desc>
          <rect id="white-background" x="0" y="0" width="\(Int(width))" height="\(Int(height))" fill="#ffffff"/>
          <g id="axes" fill="none" stroke="#222222" stroke-width="1.2">
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(left))" y2="\(fmt(top))"/>
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(width - right))" y2="\(fmt(height - bottom))"/>
          </g>
          <text x="\(fmt(left))" y="32" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="28" font-weight="600" fill="#222222">Region dominant types</text>
          <g id="bars" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif">
        """

        for (index, row) in counts.enumerated() {
            let barHeight = Double(row.count) / Double(maxCount) * plotHeight
            let x = left + Double(index) * step + (step - barWidth) / 2.0
            let y = height - bottom - barHeight
            body += """

            <rect id="region-dominant-\(index + 1)" x="\(fmt(x))" y="\(fmt(y))" width="\(fmt(barWidth))" height="\(fmt(barHeight))" fill="\(escape(row.colorHex))" data-region-type="\(escape(row.name))" data-count="\(row.count)">
              <title>\(escape(row.name)): \(row.count) regions</title>
            </rect>
            <text x="\(fmt(x + barWidth / 2))" y="\(fmt(y - 8))" text-anchor="middle" font-size="17" font-weight="600" fill="#222222">\(row.count)</text>
            <text x="\(fmt(x + barWidth / 2))" y="\(fmt(height - bottom + 20))" text-anchor="end" transform="rotate(-45 \(fmt(x + barWidth / 2)) \(fmt(height - bottom + 20)))" font-size="18" fill="#333333">\(escape(row.name))</text>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeDistanceMapSVG(result: DistanceAnalysisResult, regions: [RegionROI], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let width = max(1, result.width)
        let height = max(1, result.height)
        let cells = result.nearestDistances
        var cellByID: [Int: NearestNeighborDistance] = [:]
        for cell in cells where cellByID[cell.nucleusID] == nil {
            cellByID[cell.nucleusID] = cell
        }

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(width)" height="\(height)" viewBox="0 0 \(width) \(height)">
          <title>Distance analysis map</title>
          <desc>Editable nearest-neighbor and ROI boundary distance map on a black imaging background.</desc>
          <rect id="black-background" x="0" y="0" width="\(width)" height="\(height)" fill="#000000"/>
          <g id="distance-regions" fill-opacity="0.16" stroke-width="1.2">
        """

        for region in regions {
            body += """

            <rect id="distance-region-\(region.id)" x="\(fmt(region.xPx))" y="\(fmt(region.yPx))" width="\(fmt(region.widthPx))" height="\(fmt(region.heightPx))" fill="\(escape(region.colorHex))" stroke="\(escape(region.colorHex))" data-dominant-type="\(escape(region.dominantType))" data-cell-count="\(region.cellCount)">
              <title>Region \(region.id): \(escape(region.dominantType)); cells \(region.cellCount)</title>
            </rect>
            """
        }

        body += """

          </g>
          <g id="nearest-neighbor-links" fill="none" stroke="#ffffff" stroke-opacity="0.18" stroke-width="0.6">
        """

        for row in cells {
            guard let nearestID = row.nearestNucleusID,
                  row.nucleusID < nearestID,
                  let nearest = cellByID[nearestID] else {
                continue
            }
            body += """

            <line id="nearest-link-\(row.nucleusID)-\(nearestID)" x1="\(fmt(row.centroidX))" y1="\(fmt(row.centroidY))" x2="\(fmt(nearest.centroidX))" y2="\(fmt(nearest.centroidY))" data-distance-um="\(fmt(row.nearestDistanceUm))">
              <title>Cell \(row.nucleusID) to \(nearestID): \(fmt(row.nearestDistanceUm)) um</title>
            </line>
            """
        }

        body += """

          </g>
          <g id="distance-cells" stroke-width="0.8">
        """

        for row in cells {
            let radius = max(2.0, min(5.0, 2.0 + row.nearestDistancePx / 40.0))
            body += """

            <circle id="distance-cell-\(row.nucleusID)" cx="\(fmt(row.centroidX))" cy="\(fmt(row.centroidY))" r="\(fmt(radius))" fill="\(escape(row.colorHex))" stroke="\(escape(row.colorHex))" data-cell-type="\(escape(row.assignedType))" data-nearest-nucleus-id="\(row.nearestNucleusID.map(String.init) ?? "")" data-nearest-distance-um="\(fmt(row.nearestDistanceUm))">
              <title>Cell \(row.nucleusID): \(escape(row.assignedType)); nearest \(row.nearestNucleusID.map(String.init) ?? "none"); distance \(fmt(row.nearestDistanceUm)) um</title>
            </circle>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeDistanceHistogramSVG(title: String, values: [Double], fillHex: String, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let filtered = values.filter(\.isFinite).sorted()
        guard !filtered.isEmpty else {
            try emptySVG(title: title).write(to: url, atomically: true, encoding: .utf8)
            return
        }

        let width = 780.0
        let height = 430.0
        let left = 82.0
        let right = 34.0
        let top = 54.0
        let bottom = 82.0
        let plotWidth = width - left - right
        let plotHeight = height - top - bottom
        let maxValue = max(1.0, filtered.last ?? 1.0)
        let binCount = min(32, max(8, Int(sqrt(Double(max(1, filtered.count))))))
        var bins = Array(repeating: 0, count: binCount)
        for value in filtered {
            let index = min(binCount - 1, max(0, Int((value / maxValue) * Double(binCount))))
            bins[index] += 1
        }
        let maxBin = max(1, bins.max() ?? 1)
        let step = plotWidth / Double(binCount)
        let barWidth = max(1.0, step * 0.86)

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(width))" height="\(Int(height))" viewBox="0 0 \(Int(width)) \(Int(height))">
          <title>\(escape(title))</title>
          <desc>Editable statistical histogram of distance measurements.</desc>
          <rect id="white-background" x="0" y="0" width="\(Int(width))" height="\(Int(height))" fill="#ffffff"/>
          <g id="axes" fill="none" stroke="#222222" stroke-width="1.2">
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(left))" y2="\(fmt(top))"/>
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(width - right))" y2="\(fmt(height - bottom))"/>
          </g>
          <text x="\(fmt(left))" y="32" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="28" font-weight="600" fill="#222222">\(escape(title))</text>
          <text x="\(fmt(width - right - 70))" y="\(fmt(height - 22))" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="17" fill="#333333">max \(fmt(maxValue)) um</text>
          <g id="histogram-bars" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif">
        """

        for (index, count) in bins.enumerated() {
            let lower = Double(index) / Double(binCount) * maxValue
            let upper = Double(index + 1) / Double(binCount) * maxValue
            let barHeight = Double(count) / Double(maxBin) * plotHeight
            let x = left + Double(index) * step + (step - barWidth) / 2.0
            let y = height - bottom - barHeight
            body += """

            <rect id="distance-bin-\(index + 1)" x="\(fmt(x))" y="\(fmt(y))" width="\(fmt(barWidth))" height="\(fmt(barHeight))" fill="\(escape(fillHex))" data-count="\(count)" data-lower-um="\(fmt(lower))" data-upper-um="\(fmt(upper))">
              <title>\(fmt(lower))-\(fmt(upper)) um: \(count) cells</title>
            </rect>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    private static func writeSimpleBarSVG(
        title: String,
        xLabelPrefix: String,
        rows: [(label: String, value: Double, colorHex: String, tooltip: String)],
        to url: URL
    ) throws {
        guard !rows.isEmpty else {
            try emptySVG(title: title).write(to: url, atomically: true, encoding: .utf8)
            return
        }

        let width = 780.0
        let height = 430.0
        let left = 82.0
        let right = 34.0
        let top = 54.0
        let bottom = 126.0
        let plotWidth = width - left - right
        let plotHeight = height - top - bottom
        let maxValue = max(1.0, rows.map(\.value).max() ?? 1.0)
        let step = plotWidth / Double(max(1, rows.count))
        let barWidth = min(72.0, step * 0.62)

        var body = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(width))" height="\(Int(height))" viewBox="0 0 \(Int(width)) \(Int(height))">
          <title>\(escape(title))</title>
          <desc>Editable statistical bar chart.</desc>
          <rect id="white-background" x="0" y="0" width="\(Int(width))" height="\(Int(height))" fill="#ffffff"/>
          <g id="axes" fill="none" stroke="#222222" stroke-width="1.2">
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(left))" y2="\(fmt(top))"/>
            <line x1="\(fmt(left))" y1="\(fmt(height - bottom))" x2="\(fmt(width - right))" y2="\(fmt(height - bottom))"/>
          </g>
          <text x="\(fmt(left))" y="32" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif" font-size="28" font-weight="600" fill="#222222">\(escape(title))</text>
          <g id="bars" font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif">
        """

        for (index, row) in rows.enumerated() {
            let barHeight = row.value / maxValue * plotHeight
            let x = left + Double(index) * step + (step - barWidth) / 2.0
            let y = height - bottom - barHeight
            body += """

            <rect id="bar-\(index + 1)" x="\(fmt(x))" y="\(fmt(y))" width="\(fmt(barWidth))" height="\(fmt(barHeight))" fill="\(escape(row.colorHex))" data-label="\(escape(row.label))" data-value="\(fmt(row.value))">
              <title>\(escape(row.tooltip))</title>
            </rect>
            <text x="\(fmt(x + barWidth / 2))" y="\(fmt(y - 8))" text-anchor="middle" font-size="17" font-weight="600" fill="#222222">\(fmt(row.value))</text>
            <text x="\(fmt(x + barWidth / 2))" y="\(fmt(height - bottom + 20))" text-anchor="end" transform="rotate(-45 \(fmt(x + barWidth / 2)) \(fmt(height - bottom + 20)))" font-size="18" fill="#333333">\(escape(xLabelPrefix.isEmpty ? row.label : row.label))</text>
            """
        }

        body += """

          </g>
        </svg>
        """
        try body.write(to: url, atomically: true, encoding: .utf8)
    }

    private static func niceTicks(minValue: Double, maxValue: Double, targetCount: Int) -> [Double] {
        let span = max(1e-9, maxValue - minValue)
        let rawStep = span / Double(max(1, targetCount))
        let magnitude = pow(10.0, floor(log10(rawStep)))
        let normalized = rawStep / magnitude
        let niceNormalized: Double
        if normalized <= 1.0 {
            niceNormalized = 1.0
        } else if normalized <= 2.0 {
            niceNormalized = 2.0
        } else if normalized <= 5.0 {
            niceNormalized = 5.0
        } else {
            niceNormalized = 10.0
        }
        let step = niceNormalized * magnitude
        let first = ceil(minValue / step) * step
        let last = floor(maxValue / step) * step
        var values: [Double] = []
        var value = first
        while value <= last + step * 0.5 {
            values.append(value)
            value += step
        }
        if values.first != minValue, minValue == 0 {
            values.insert(0, at: 0)
        }
        return values.isEmpty ? [minValue, maxValue] : values
    }

    private static func tickText(_ value: Double) -> String {
        if abs(value) >= 100 {
            return String(format: "%.0f", value)
        }
        if abs(value) >= 10 {
            return String(format: "%.1f", value)
        }
        return String(format: "%.2f", value)
    }

    private static func emptySVG(title: String) -> String {
        """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="800" height="400" viewBox="0 0 800 400">
          <title>\(escape(title))</title>
        </svg>
        """
    }

    private static func fmt(_ value: Double) -> String {
        String(format: "%.4f", value)
    }

    private static func maskLikePolygonPoints(x: Double, y: Double, radius: Double, seed: Int) -> String {
        (0..<12).map { index in
            let angle = (Double(index) / 12.0) * Double.pi * 2.0
            let wobble = 0.78 + 0.28 * Double(((seed * 31 + index * 17) % 11)) / 10.0
            let px = x + cos(angle) * radius * wobble
            let py = y + sin(angle) * radius * wobble
            return "\(fmt(px)),\(fmt(py))"
        }
        .joined(separator: " ")
    }

    private static func polygonPoints(_ points: [CellBoundaryPoint]) -> String {
        points.map { "\(fmt($0.x)),\(fmt($0.y))" }
            .joined(separator: " ")
    }

    private static func escape(_ text: String) -> String {
        text
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "\"", with: "&quot;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
    }
}
