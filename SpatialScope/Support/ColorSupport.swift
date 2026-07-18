import AppKit
import SwiftUI

enum ColorPalette {
    static let commonFirst = [
        "#dc0000", "#00ff00", "#0008e5", "#ffffff", "#ff00ff", "#00ffff", "#ffff00",
        "#f05c4f", "#4fc36b", "#4f82f0", "#f0c24f", "#b85cf6", "#14b8a6",
        "#ef4444", "#22c55e", "#3b82f6", "#eab308", "#a855f7", "#06b6d4"
    ]

    static let segmentation = commonFirst.filter { $0.lowercased() != "#ffffff" }
    static let clusters = commonFirst.filter {
        let lower = $0.lowercased()
        return lower != "#ffffff" && lower != "#000000"
    }

    static func color(at index: Int) -> String {
        commonFirst[index % commonFirst.count]
    }

    static func clusterColor(at index: Int, offset: Int = 0) -> String {
        let paletteIndex = index + offset
        if offset == 0, index < clusters.count {
            return clusters[index]
        }
        let hue = CGFloat((0.07 + Double(paletteIndex) * 0.618_033_988_75).truncatingRemainder(dividingBy: 1.0))
        let saturation = CGFloat(0.68 + 0.10 * Double(paletteIndex % 3))
        let brightness = CGFloat(0.82 + 0.08 * Double((paletteIndex / 3) % 2))
        return NSColor(calibratedHue: hue, saturation: saturation, brightness: brightness, alpha: 1.0).hexString
    }
}

extension NSColor {
    convenience init?(hex: String) {
        var text = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("#") {
            text.removeFirst()
        }
        guard text.count == 6, let raw = Int(text, radix: 16) else { return nil }
        let r = CGFloat((raw >> 16) & 0xff) / 255.0
        let g = CGFloat((raw >> 8) & 0xff) / 255.0
        let b = CGFloat(raw & 0xff) / 255.0
        self.init(srgbRed: r, green: g, blue: b, alpha: 1.0)
    }

    var hexString: String {
        let rgb = usingColorSpace(.sRGB) ?? self
        let r = max(0, min(255, Int(round(rgb.redComponent * 255.0))))
        let g = max(0, min(255, Int(round(rgb.greenComponent * 255.0))))
        let b = max(0, min(255, Int(round(rgb.blueComponent * 255.0))))
        return String(format: "#%02x%02x%02x", r, g, b)
    }

    var rgbComponents01: (Double, Double, Double) {
        let rgb = usingColorSpace(.sRGB) ?? self
        return (Double(rgb.redComponent), Double(rgb.greenComponent), Double(rgb.blueComponent))
    }
}

extension Color {
    init(hex: String) {
        self.init(nsColor: NSColor(hex: hex) ?? .systemBlue)
    }
}
