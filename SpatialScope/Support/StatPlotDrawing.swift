import AppKit
import Foundation

enum StatPlotDrawing {
    static func drawRotatedXAxisLabel(
        _ text: String,
        anchor: NSPoint,
        maxWidth: CGFloat,
        font: NSFont,
        color: NSColor = NSColor(calibratedWhite: 0.18, alpha: 1),
        angleDegrees: CGFloat = -45
    ) {
        guard !text.isEmpty else { return }

        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .right
        paragraph.lineBreakMode = .byTruncatingTail

        NSGraphicsContext.saveGraphicsState()
        let transform = NSAffineTransform()
        transform.translateX(by: anchor.x, yBy: anchor.y)
        transform.rotate(byDegrees: angleDegrees)
        transform.concat()

        (text as NSString).draw(
            in: NSRect(x: -maxWidth, y: -font.pointSize * 0.55, width: maxWidth, height: font.pointSize * 1.4),
            withAttributes: [
                .font: font,
                .foregroundColor: color,
                .paragraphStyle: paragraph
            ]
        )

        NSGraphicsContext.restoreGraphicsState()
    }
}
