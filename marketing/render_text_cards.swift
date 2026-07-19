import AppKit
import Foundation

private let canvas = NSSize(width: 1080, height: 1920)

struct MarketingCopy {
    let brandSubtitle: String
    let availability: String
    let tagline: String
    let stages: String
    let compositeTitle: String
    let compositeSubtitle: String
    let nucleiTitle: String
    let nucleiSubtitle: String
    let cellTypesTitle: String
    let cellTypesSubtitle: String
    let regionsTitle: String
    let regionsSubtitle: String
    let densityTitle: String
    let densitySubtitle: String
    let endBody: String
    let callToAction: String
    let compatibility: String
    let referencePrefix: String
}

private let english = MarketingCopy(
    brandSubtitle: "SPATIAL IMAGE ANALYSIS",
    availability: "NOW AVAILABLE",
    tagline: "Spatial image analysis, end to end.",
    stages: "Nine guided analysis stages",
    compositeTitle: "See every marker together.",
    compositeSubtitle: "Multiplex composite preview",
    nucleiTitle: "Segment nuclei with control.",
    nucleiSubtitle: "Parameter screening + final masks",
    cellTypesTitle: "Assign cell identities.",
    cellTypesSubtitle: "Rule-based, transparent, reproducible",
    regionsTitle: "Map spatial regions.",
    regionsSubtitle: "Computational and manual ROIs",
    densityTitle: "Quantify spatial organization.",
    densitySubtitle: "Density, neighborhoods, and distances",
    endBody: "From aligned marker matrices\nto publication-ready figures.",
    callToAction: "DOWNLOAD FREE",
    compatibility: "macOS 13+   |   Windows 10/11   |   English + Simplified Chinese",
    referencePrefix: "Reference"
)

private let simplifiedChinese = MarketingCopy(
    brandSubtitle: "空间图像分析",
    availability: "现已发布",
    tagline: "端到端空间图像分析。",
    stages: "九个引导式分析步骤",
    compositeTitle: "查看全部标记物。",
    compositeSubtitle: "多重标记合成预览",
    nucleiTitle: "精细控制细胞核分割。",
    nucleiSubtitle: "参数筛选 + 最终掩膜",
    cellTypesTitle: "分配细胞类型。",
    cellTypesSubtitle: "规则清晰、透明、可复现",
    regionsTitle: "绘制空间区域。",
    regionsSubtitle: "计算生成与手动绘制 ROI",
    densityTitle: "量化空间组织。",
    densitySubtitle: "密度、邻域与距离分析",
    endBody: "从配准后的标记物矩阵\n到可用于发表的图表。",
    callToAction: "免费下载",
    compatibility: "macOS 13+   |   Windows 10/11   |   英文 + 简体中文",
    referencePrefix: "参考文献"
)

private func color(_ red: CGFloat, _ green: CGFloat, _ blue: CGFloat, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: red / 255, green: green / 255, blue: blue / 255, alpha: alpha)
}

private func drawText(
    _ text: String,
    x: CGFloat,
    y: CGFloat,
    width: CGFloat,
    height: CGFloat,
    size: CGFloat,
    weight: NSFont.Weight,
    foreground: NSColor,
    alignment: NSTextAlignment = .left
) {
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = alignment
    paragraph.lineBreakMode = .byWordWrapping

    let attributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: size, weight: weight),
        .foregroundColor: foreground,
        .paragraphStyle: paragraph,
        .kern: 0,
    ]

    NSAttributedString(string: text, attributes: attributes).draw(
        with: NSRect(x: x, y: y, width: width, height: height),
        options: [.usesLineFragmentOrigin, .usesFontLeading]
    )
}

private func roundedBox(_ rect: NSRect, radius: CGFloat, fill: NSColor, stroke: NSColor? = nil) {
    let path = NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
    fill.setFill()
    path.fill()
    if let stroke {
        stroke.setStroke()
        path.lineWidth = 1
        path.stroke()
    }
}

private func render(_ name: String, outputDirectory: URL, drawing: @escaping () -> Void) throws {
    let image = NSImage(size: canvas, flipped: true) { _ in
        NSGraphicsContext.current?.imageInterpolation = .high
        drawing()
        return true
    }

    guard
        let tiff = image.tiffRepresentation,
        let bitmap = NSBitmapImageRep(data: tiff),
        let png = bitmap.representation(using: .png, properties: [:])
    else {
        throw NSError(domain: "SpatialScopeMarketing", code: 1)
    }

    try png.write(to: outputDirectory.appendingPathComponent(name))
}

private func drawBrand(copy: MarketingCopy) {
    drawText(
        "SpatialScope",
        x: 160, y: 88, width: 500, height: 60,
        size: 38, weight: .semibold, foreground: .white
    )
    drawText(
        copy.brandSubtitle,
        x: 160, y: 140, width: 500, height: 45,
        size: 24, weight: .semibold, foreground: color(120, 224, 209)
    )
}

private func drawFeatureCard(copy: MarketingCopy, title: String, subtitle: String) {
    drawBrand(copy: copy)
    roundedBox(
        NSRect(x: 52, y: 612, width: 976, height: 660),
        radius: 28,
        fill: color(255, 255, 255, 0.075),
        stroke: color(255, 255, 255, 0.14)
    )
    drawText(
        title,
        x: 60, y: 292, width: 960, height: 90,
        size: 54, weight: .bold, foreground: .white
    )
    drawText(
        subtitle,
        x: 60, y: 410, width: 960, height: 60,
        size: 31, weight: .medium, foreground: color(217, 228, 231)
    )
}

guard CommandLine.arguments.count == 3 else {
    fputs("Usage: render_text_cards.swift <output-directory> <en|zh-Hans>\n", stderr)
    exit(2)
}

let outputDirectory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let copy: MarketingCopy
switch CommandLine.arguments[2] {
case "en":
    copy = english
case "zh-Hans":
    copy = simplifiedChinese
default:
    fputs("Unsupported language. Use en or zh-Hans.\n", stderr)
    exit(2)
}
try FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)

try render("intro.png", outputDirectory: outputDirectory) {
    drawText(
        copy.availability,
        x: 0, y: 238, width: 1080, height: 55,
        size: 34, weight: .bold, foreground: color(120, 224, 209), alignment: .center
    )
    drawText(
        "SpatialScope",
        x: 0, y: 320, width: 1080, height: 115,
        size: 88, weight: .bold, foreground: .white, alignment: .center
    )
    drawText(
        copy.tagline,
        x: 0, y: 452, width: 1080, height: 70,
        size: 37, weight: .medium, foreground: color(217, 228, 231), alignment: .center
    )
    roundedBox(
        NSRect(x: 340, y: 1268, width: 400, height: 72),
        radius: 24,
        fill: color(12, 17, 24, 0.82),
        stroke: color(255, 255, 255, 0.16)
    )
    drawText(
        "macOS  +  Windows",
        x: 340, y: 1283, width: 400, height: 48,
        size: 31, weight: .semibold, foreground: .white, alignment: .center
    )
    drawText(
        copy.stages,
        x: 0, y: 1390, width: 1080, height: 55,
        size: 29, weight: .medium, foreground: color(175, 194, 199), alignment: .center
    )
}

try render("composite.png", outputDirectory: outputDirectory) {
    drawFeatureCard(copy: copy, title: copy.compositeTitle, subtitle: copy.compositeSubtitle)
}

try render("nuclei.png", outputDirectory: outputDirectory) {
    drawFeatureCard(copy: copy, title: copy.nucleiTitle, subtitle: copy.nucleiSubtitle)
}

try render("cell-types.png", outputDirectory: outputDirectory) {
    drawFeatureCard(copy: copy, title: copy.cellTypesTitle, subtitle: copy.cellTypesSubtitle)
}

try render("regions.png", outputDirectory: outputDirectory) {
    drawFeatureCard(copy: copy, title: copy.regionsTitle, subtitle: copy.regionsSubtitle)
}

try render("density.png", outputDirectory: outputDirectory) {
    drawFeatureCard(copy: copy, title: copy.densityTitle, subtitle: copy.densitySubtitle)
}

try render("end.png", outputDirectory: outputDirectory) {
    drawText(
        "SpatialScope",
        x: 0, y: 535, width: 1080, height: 105,
        size: 76, weight: .bold, foreground: .white, alignment: .center
    )
    drawText(
        copy.endBody,
        x: 80, y: 670, width: 920, height: 125,
        size: 35, weight: .medium, foreground: color(217, 228, 231), alignment: .center
    )
    roundedBox(
        NSRect(x: 244, y: 865, width: 592, height: 92),
        radius: 28,
        fill: color(11, 163, 154, 0.96)
    )
    drawText(
        copy.callToAction,
        x: 244, y: 886, width: 592, height: 55,
        size: 37, weight: .bold, foreground: .white, alignment: .center
    )
    drawText(
        "github.com/fengshuoliu/SpatialScope",
        x: 0, y: 1025, width: 1080, height: 60,
        size: 32, weight: .semibold, foreground: .white, alignment: .center
    )
    drawText(
        copy.compatibility,
        x: 40, y: 1110, width: 1000, height: 50,
        size: 25, weight: .medium, foreground: color(183, 201, 206), alignment: .center
    )
    drawText(
        "\(copy.referencePrefix): Cell (2026)   |   10.1016/j.cell.2026.04.009",
        x: 40, y: 1495, width: 1000, height: 50,
        size: 23, weight: .regular, foreground: color(143, 165, 171), alignment: .center
    )
}

print("Rendered marketing text cards in \(outputDirectory.path)")
