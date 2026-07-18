import AppKit
import Foundation

enum AssignmentScanPlotRenderer {
    static func render(records: [AssignmentScanRecord], selectedCombo: Int? = nil) -> NSImage? {
        guard !records.isEmpty else { return nil }
        let sorted = records.sorted { $0.comboIndex < $1.comboIndex }
        let width = 1120
        let height = 560
        let marginLeft: CGFloat = 116
        let marginRight: CGFloat = 40
        let marginTop: CGFloat = 64
        let marginBottom: CGFloat = 120
        let plotWidth = CGFloat(width) - marginLeft - marginRight
        let plotHeight = CGFloat(height) - marginTop - marginBottom
        let unresolved = sorted.map(\.unresolvedCount)
        let maxUnresolved = max(1, unresolved.max() ?? 1)
        let minUnresolved = unresolved.min() ?? 0
        let best = sorted.min {
            if $0.unresolvedCount == $1.unresolvedCount { return $0.assignedCount > $1.assignedCount }
            return $0.unresolvedCount < $1.unresolvedCount
        }
        let selectedIndex = selectedCombo.flatMap { combo in
            sorted.firstIndex { $0.comboIndex == combo }
        }

        let image = NSImage(size: NSSize(width: width, height: height))
        image.lockFocus()
        NSColor.white.setFill()
        NSRect(x: 0, y: 0, width: width, height: height).fill()

        NSColor.labelColor.setStroke()
        let axes = NSBezierPath()
        axes.move(to: NSPoint(x: marginLeft, y: marginBottom))
        axes.line(to: NSPoint(x: marginLeft, y: marginBottom + plotHeight))
        axes.move(to: NSPoint(x: marginLeft, y: marginBottom))
        axes.line(to: NSPoint(x: marginLeft + plotWidth, y: marginBottom))
        axes.lineWidth = 1.2
        axes.stroke()

        let barWidth = max(2, plotWidth / CGFloat(sorted.count) * 0.72)
        for (idx, record) in sorted.enumerated() {
            let x = marginLeft + CGFloat(idx) / CGFloat(max(1, sorted.count - 1)) * plotWidth
            let h = CGFloat(record.unresolvedCount) / CGFloat(maxUnresolved) * plotHeight
            let rect = NSRect(x: x - barWidth / 2, y: marginBottom, width: barWidth, height: h)
            let color = colorForUnresolved(record.unresolvedCount, minCount: minUnresolved, maxCount: maxUnresolved)
                .withAlphaComponent(record.stage.hasPrefix("refine") ? 0.92 : 0.72)
            color.setFill()
            rect.fill()
        }

        if let selectedIndex {
            let x = marginLeft + CGFloat(selectedIndex) / CGFloat(max(1, sorted.count - 1)) * plotWidth
            NSColor.systemOrange.setStroke()
            let rule = NSBezierPath()
            rule.move(to: NSPoint(x: x, y: marginBottom))
            rule.line(to: NSPoint(x: x, y: marginBottom + plotHeight))
            rule.lineWidth = 2
            rule.stroke()
        }

        for idx in tickIndexes(count: sorted.count, selectedIndex: selectedIndex) {
            let x = marginLeft + CGFloat(idx) / CGFloat(max(1, sorted.count - 1)) * plotWidth
            NSColor.black.withAlphaComponent(0.28).setStroke()
            NSBezierPath.strokeLine(
                from: NSPoint(x: x, y: marginBottom),
                to: NSPoint(x: x, y: marginBottom - 6)
            )
            StatPlotDrawing.drawRotatedXAxisLabel(
                "\(sorted[idx].comboIndex)",
                anchor: NSPoint(x: x + 4, y: marginBottom - 12),
                maxWidth: 68,
                font: NSFont.systemFont(ofSize: 16)
            )
        }

        "Cell-type assignment parameter screening".draw(
            in: NSRect(x: marginLeft, y: CGFloat(height) - marginTop + 10, width: plotWidth, height: 36),
            withAttributes: [.font: NSFont.systemFont(ofSize: 30, weight: .semibold), .foregroundColor: NSColor.labelColor]
        )
        "Parameter combination #".draw(
            in: NSRect(x: marginLeft + plotWidth / 2 - 170, y: 18, width: 340, height: 30),
            withAttributes: [.font: NSFont.systemFont(ofSize: 22), .foregroundColor: NSColor.secondaryLabelColor]
        )

        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        "Ambiguous + unassigned".draw(
            in: NSRect(x: 4, y: marginBottom + plotHeight / 2 - 48, width: 116, height: 62),
            withAttributes: [.font: NSFont.systemFont(ofSize: 22), .foregroundColor: NSColor.secondaryLabelColor, .paragraphStyle: paragraph]
        )

        if let best {
            let text = "Suggested: combo \(best.comboIndex), \(best.unresolvedCount) unresolved cells"
            text.draw(
                in: NSRect(x: marginLeft, y: CGFloat(height) - marginTop - 28, width: plotWidth, height: 28),
                withAttributes: [.font: NSFont.systemFont(ofSize: 20, weight: .medium), .foregroundColor: NSColor.systemOrange]
            )
        }

        image.unlockFocus()
        return image
    }

    private static func colorForUnresolved(_ count: Int, minCount: Int, maxCount: Int) -> NSColor {
        let denominator = max(1, maxCount - minCount)
        let normalized = CGFloat(count - minCount) / CGFloat(denominator)
        let hue = 0.34 - (0.34 * normalized)
        return NSColor(calibratedHue: hue, saturation: 0.82, brightness: 0.92, alpha: 1)
    }

    private static func tickIndexes(count: Int, selectedIndex: Int?) -> [Int] {
        guard count > 0 else { return [] }
        let step = max(1, Int(ceil(Double(count) / 8.0)))
        var indexes = Set(stride(from: 0, to: count, by: step))
        indexes.insert(count - 1)
        if let selectedIndex {
            indexes.insert(selectedIndex)
        }
        return indexes.sorted()
    }
}
