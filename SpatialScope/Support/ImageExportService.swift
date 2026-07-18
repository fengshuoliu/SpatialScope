import AppKit
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

enum ImageExportService {
    private struct RGBPixel: Equatable, Hashable {
        var r: UInt8
        var g: UInt8
        var b: UInt8

        var hex: String {
            String(format: "#%02x%02x%02x", r, g, b)
        }

        static let white = RGBPixel(r: 255, g: 255, b: 255)
    }

    static func makeCGImage(width: Int, height: Int, rgba: [UInt8]) throws -> CGImage {
        let data = Data(rgba)
        guard let provider = CGDataProvider(data: data as CFData) else {
            throw SpatialScopeError.message("Could not create image data provider.")
        }
        let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue)
        guard let image = CGImage(
            width: width,
            height: height,
            bitsPerComponent: 8,
            bitsPerPixel: 32,
            bytesPerRow: width * 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: bitmapInfo,
            provider: provider,
            decode: nil,
            shouldInterpolate: false,
            intent: .defaultIntent
        ) else {
            throw SpatialScopeError.message("Could not create CGImage.")
        }
        return image
    }

    static func nsImage(width: Int, height: Int, rgba: [UInt8]) throws -> NSImage {
        let cgImage = try makeCGImage(width: width, height: height, rgba: rgba)
        return NSImage(cgImage: cgImage, size: NSSize(width: width, height: height))
    }

    static func pngData(from image: NSImage, dpi: Int = 300) throws -> Data {
        guard let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff) else {
            throw SpatialScopeError.message("Could not encode PNG.")
        }
        bitmap.size = NSSize(
            width: CGFloat(bitmap.pixelsWide) * 72.0 / CGFloat(max(1, dpi)),
            height: CGFloat(bitmap.pixelsHigh) * 72.0 / CGFloat(max(1, dpi))
        )
        guard let png = bitmap.representation(using: .png, properties: [.compressionFactor: 1.0]) else {
            throw SpatialScopeError.message("Could not encode PNG.")
        }
        return png
    }

    static func writePNG(_ image: NSImage, to url: URL, dpi: Int = 300) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try pngData(from: image, dpi: dpi).write(to: url)
    }

    static func writeTIFF(_ image: NSImage, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard let data = image.tiffRepresentation else {
            throw SpatialScopeError.message("Could not encode TIFF.")
        }
        try data.write(to: url)
    }

    static func writeUInt8TIFF(width: Int, height: Int, values: [UInt8], to url: URL) throws {
        let width = max(1, width)
        let height = max(1, height)
        guard values.count == width * height else {
            throw SpatialScopeError.message("Could not encode TIFF because the mask dimensions do not match the data.")
        }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = Data(values)
        guard let provider = CGDataProvider(data: data as CFData),
              let image = CGImage(
                width: width,
                height: height,
                bitsPerComponent: 8,
                bitsPerPixel: 8,
                bytesPerRow: width,
                space: CGColorSpaceCreateDeviceGray(),
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                provider: provider,
                decode: nil,
                shouldInterpolate: false,
                intent: .defaultIntent
              ),
              let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.tiff.identifier as CFString, 1, nil) else {
            throw SpatialScopeError.message("Could not encode TIFF.")
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else {
            throw SpatialScopeError.message("Could not finalize TIFF.")
        }
    }

    static func writeUInt16TIFF(width: Int, height: Int, values: [UInt16], to url: URL) throws {
        let width = max(1, width)
        let height = max(1, height)
        guard values.count == width * height else {
            throw SpatialScopeError.message("Could not encode TIFF because the mask dimensions do not match the data.")
        }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let littleEndianValues = values.map(\.littleEndian)
        let data = littleEndianValues.withUnsafeBufferPointer { buffer in
            Data(bytes: buffer.baseAddress!, count: buffer.count * MemoryLayout<UInt16>.stride)
        }
        guard let provider = CGDataProvider(data: data as CFData),
              let image = CGImage(
                width: width,
                height: height,
                bitsPerComponent: 16,
                bitsPerPixel: 16,
                bytesPerRow: width * 2,
                space: CGColorSpaceCreateDeviceGray(),
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue | CGBitmapInfo.byteOrder16Little.rawValue),
                provider: provider,
                decode: nil,
                shouldInterpolate: false,
                intent: .defaultIntent
              ),
              let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.tiff.identifier as CFString, 1, nil) else {
            throw SpatialScopeError.message("Could not encode TIFF.")
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else {
            throw SpatialScopeError.message("Could not finalize TIFF.")
        }
    }

    static func writeUInt16RasterRaw(width: Int, height: Int, values: [UInt16], to url: URL) throws {
        let width = max(1, width)
        let height = max(1, height)
        guard values.count == width * height else {
            throw SpatialScopeError.message("Could not encode raw mask because the dimensions do not match the data.")
        }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)

        var data = Data("SSU16R1\n".utf8)
        var rawWidth = UInt32(width).littleEndian
        var rawHeight = UInt32(height).littleEndian
        withUnsafeBytes(of: &rawWidth) { data.append(contentsOf: $0) }
        withUnsafeBytes(of: &rawHeight) { data.append(contentsOf: $0) }

        let littleEndianValues = values.map(\.littleEndian)
        littleEndianValues.withUnsafeBufferPointer { buffer in
            if let baseAddress = buffer.baseAddress {
                data.append(Data(bytes: baseAddress, count: buffer.count * MemoryLayout<UInt16>.stride))
            }
        }
        try data.write(to: url)
    }

    static func loadUInt16RasterRaw(from url: URL) -> UInt16Raster? {
        guard let data = try? Data(contentsOf: url) else {
            return nil
        }
        let magic = Data("SSU16R1\n".utf8)
        let headerSize = magic.count + MemoryLayout<UInt32>.stride * 2
        guard data.count >= headerSize,
              data.prefix(magic.count) == magic else {
            return nil
        }

        func readUInt32(at offset: Int) -> UInt32 {
            data[offset..<offset + 4].enumerated().reduce(UInt32(0)) { partial, item in
                partial | (UInt32(item.element) << UInt32(item.offset * 8))
            }
        }

        let width = Int(readUInt32(at: magic.count))
        let height = Int(readUInt32(at: magic.count + 4))
        guard width > 0, height > 0 else {
            return nil
        }
        let expectedBytes = headerSize + width * height * MemoryLayout<UInt16>.stride
        guard data.count == expectedBytes else {
            return nil
        }

        var values = [UInt16](repeating: 0, count: width * height)
        data.withUnsafeBytes { rawBuffer in
            guard let bytes = rawBuffer.bindMemory(to: UInt8.self).baseAddress else { return }
            for index in values.indices {
                let offset = headerSize + index * 2
                values[index] = UInt16(bytes[offset]) | (UInt16(bytes[offset + 1]) << 8)
            }
        }
        return UInt16Raster(width: width, height: height, values: values)
    }

    static func loadUInt16TIFF(from url: URL) -> UInt16Raster? {
        guard let image = NSImage(contentsOf: url),
              let tiff = image.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff) else {
            return nil
        }
        let width = max(1, rep.pixelsWide)
        let height = max(1, rep.pixelsHigh)
        var values = [UInt16](repeating: 0, count: width * height)

        if rep.bitsPerSample == 16,
           rep.samplesPerPixel >= 1,
           let bitmapData = rep.bitmapData {
            let bytesPerRow = rep.bytesPerRow
            let bytesPerPixel = max(2, rep.bitsPerPixel / 8)
            let littleEndian = rep.bitmapFormat.contains(.sixteenBitLittleEndian)
            for y in 0..<height {
                for x in 0..<width {
                    let offset = y * bytesPerRow + x * bytesPerPixel
                    let first = UInt16(bitmapData[offset])
                    let second = UInt16(bitmapData[offset + 1])
                    values[y * width + x] = littleEndian
                        ? first | (second << 8)
                        : (first << 8) | second
                }
            }
            return UInt16Raster(width: width, height: height, values: values)
        }

        for y in 0..<height {
            for x in 0..<width {
                guard let color = rep.colorAt(x: x, y: y)?.usingColorSpace(.deviceGray) else {
                    continue
                }
                values[y * width + x] = UInt16(clamping: Int(round(color.whiteComponent * 65_535.0)))
            }
        }
        return UInt16Raster(width: width, height: height, values: values)
    }

    static func writeAI(_ image: NSImage, to url: URL, dpi: Int = 300) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let raster = try rasterPixels(from: image)
        var mediaBox = CGRect(x: 0, y: 0, width: raster.width, height: raster.height)
        guard let consumer = CGDataConsumer(url: url as CFURL),
              let context = CGContext(consumer: consumer, mediaBox: &mediaBox, nil) else {
            throw SpatialScopeError.message("Could not encode AI-compatible PDF.")
        }
        context.beginPDFPage(nil)
        drawVectorRuns(
            pixels: raster.pixels,
            width: raster.width,
            height: raster.height,
            background: raster.background,
            into: context
        )
        context.endPDFPage()
        context.closePDF()
    }

    static func writeSVGEmbeddingPNG(_ image: NSImage, title: String = "Image", to url: URL, dpi: Int = 300) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let png = try pngData(from: image, dpi: dpi)
        let base64 = png.base64EncodedString()
        let size = pixelSize(for: image)
        let widthPt = Double(size.width) * 72.0 / Double(max(1, dpi))
        let heightPt = Double(size.height) * 72.0 / Double(max(1, dpi))
        let escapedTitle = escapeXML(title)
        let svg = """
        <?xml version="1.0" encoding="utf-8" standalone="no"?>
        <!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"
          "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
        <svg xmlns:xlink="http://www.w3.org/1999/xlink" width="\(fmt(widthPt))pt" height="\(fmt(heightPt))pt" viewBox="0 0 \(fmt(widthPt)) \(fmt(heightPt))" xmlns="http://www.w3.org/2000/svg" version="1.1">
          <metadata>
            <rdf:RDF xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:cc="http://creativecommons.org/ns#" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
              <cc:Work>
                <dc:title>\(escapedTitle)</dc:title>
                <dc:format>image/svg+xml</dc:format>
              </cc:Work>
            </rdf:RDF>
          </metadata>
          <defs>
            <style type="text/css">*{stroke-linejoin: round; stroke-linecap: butt}</style>
          </defs>
          <g id="figure_1">
            <g id="patch_1">
              <path d="M 0 \(fmt(heightPt)) L \(fmt(widthPt)) \(fmt(heightPt)) L \(fmt(widthPt)) 0 L 0 0 z" style="fill: #ffffff"/>
            </g>
            <g id="axes_1">
              <image x="0" y="0" width="\(fmt(widthPt))" height="\(fmt(heightPt))" preserveAspectRatio="none" xlink:href="data:image/png;base64,\(base64)"/>
            </g>
          </g>
        </svg>
        """
        try svg.write(to: url, atomically: true, encoding: .utf8)
    }

    static func writeVectorSVG(_ image: NSImage, title: String, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let raster = try rasterPixels(from: image)
        let rects = vectorSVGRectElements(
            pixels: raster.pixels,
            width: raster.width,
            height: raster.height,
            background: raster.background,
            x: 0,
            y: 0,
            displayWidth: Double(raster.width),
            displayHeight: Double(raster.height)
        )
        let svg = """
        <?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="\(raster.width)" height="\(raster.height)" viewBox="0 0 \(raster.width) \(raster.height)">
          <title>\(escapeXML(title))</title>
          <rect id="background" x="0" y="0" width="\(raster.width)" height="\(raster.height)" fill="\(raster.background.hex)"/>
        \(rects)
        </svg>
        """
        try svg.write(to: url, atomically: true, encoding: .utf8)
    }

    static func vectorSVGRectElements(
        for image: NSImage,
        x: Double,
        y: Double,
        displayWidth: Double,
        displayHeight: Double
    ) throws -> String {
        let raster = try rasterPixels(from: image)
        return vectorSVGRectElements(
            pixels: raster.pixels,
            width: raster.width,
            height: raster.height,
            background: raster.background,
            x: x,
            y: y,
            displayWidth: displayWidth,
            displayHeight: displayHeight
        )
    }

    static func pixelSize(for image: NSImage) -> NSSize {
        if let rep = image.representations.first {
            return NSSize(width: rep.pixelsWide, height: rep.pixelsHigh)
        }
        return image.size
    }

    static func lightenedRGBA(from image: NSImage, width: Int, height: Int, blend: Double = 0.45) throws -> [UInt8] {
        guard let source = cgImage(from: image) else {
            throw SpatialScopeError.message("Could not read overlay image pixels.")
        }
        let width = max(1, width)
        let height = max(1, height)
        var rgba = [UInt8](repeating: 255, count: width * height * 4)
        let bitmapInfo = CGBitmapInfo(rawValue: CGBitmapInfo.byteOrder32Big.rawValue | CGImageAlphaInfo.premultipliedLast.rawValue)

        try rgba.withUnsafeMutableBytes { buffer in
            guard let context = CGContext(
                data: buffer.baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: bitmapInfo.rawValue
            ) else {
                throw SpatialScopeError.message("Could not create overlay bitmap context.")
            }
            context.setFillColor(gray: 0, alpha: 1)
            context.fill(CGRect(x: 0, y: 0, width: width, height: height))
            context.interpolationQuality = .none
            context.draw(source, in: CGRect(x: 0, y: 0, width: width, height: height))
        }

        let amount = max(0.0, min(1.0, blend))
        let inverse = 1.0 - amount
        for offset in stride(from: 0, to: rgba.count, by: 4) {
            rgba[offset] = UInt8(max(0, min(255, Int(round(Double(rgba[offset]) * amount + 255.0 * inverse)))))
            rgba[offset + 1] = UInt8(max(0, min(255, Int(round(Double(rgba[offset + 1]) * amount + 255.0 * inverse)))))
            rgba[offset + 2] = UInt8(max(0, min(255, Int(round(Double(rgba[offset + 2]) * amount + 255.0 * inverse)))))
            rgba[offset + 3] = 255
        }
        return rgba
    }

    private static func cgImage(from image: NSImage) -> CGImage? {
        var rect = NSRect(origin: .zero, size: image.size)
        if let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) {
            return cgImage
        }
        guard let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff) else {
            return nil
        }
        return bitmap.cgImage
    }

    private static func rasterPixels(from image: NSImage) throws -> (width: Int, height: Int, pixels: [RGBPixel], background: RGBPixel) {
        guard let source = cgImage(from: image) else {
            throw SpatialScopeError.message("Could not read image pixels for vector export.")
        }
        let width = max(1, source.width)
        let height = max(1, source.height)
        var rgba = [UInt8](repeating: 0, count: width * height * 4)
        let bitmapInfo = CGBitmapInfo(rawValue: CGBitmapInfo.byteOrder32Big.rawValue | CGImageAlphaInfo.premultipliedLast.rawValue)

        try rgba.withUnsafeMutableBytes { buffer in
            guard let context = CGContext(
                data: buffer.baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: bitmapInfo.rawValue
            ) else {
                throw SpatialScopeError.message("Could not create vector export bitmap context.")
            }
            context.setFillColor(gray: 1, alpha: 1)
            context.fill(CGRect(x: 0, y: 0, width: width, height: height))
            context.interpolationQuality = .none
            context.draw(source, in: CGRect(x: 0, y: 0, width: width, height: height))
        }

        var pixels: [RGBPixel] = []
        pixels.reserveCapacity(width * height)

        for offset in stride(from: 0, to: rgba.count, by: 4) {
            pixels.append(RGBPixel(r: rgba[offset], g: rgba[offset + 1], b: rgba[offset + 2]))
        }

        let corners = [
            pixels[0],
            pixels[max(0, width - 1)],
            pixels[max(0, (height - 1) * width)],
            pixels[max(0, height * width - 1)]
        ]
        let background = Dictionary(grouping: corners, by: { $0 })
            .max { $0.value.count < $1.value.count }?
            .key ?? corners[0]
        return (width, height, pixels, background)
    }

    private static func drawVectorRuns(
        pixels: [RGBPixel],
        width: Int,
        height: Int,
        background: RGBPixel,
        into context: CGContext
    ) {
        context.setFillColor(red: CGFloat(background.r) / 255, green: CGFloat(background.g) / 255, blue: CGFloat(background.b) / 255, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))

        var currentColor: RGBPixel?
        for y in 0..<height {
            var x = 0
            while x < width {
                let color = pixels[y * width + x]
                var runEnd = x + 1
                while runEnd < width, pixels[y * width + runEnd] == color {
                    runEnd += 1
                }
                if color != background {
                    if currentColor != color {
                        context.setFillColor(red: CGFloat(color.r) / 255, green: CGFloat(color.g) / 255, blue: CGFloat(color.b) / 255, alpha: 1)
                        currentColor = color
                    }
                    context.fill(CGRect(x: x, y: height - y - 1, width: runEnd - x, height: 1))
                }
                x = runEnd
            }
        }
    }

    private static func vectorSVGRectElements(
        pixels: [RGBPixel],
        width: Int,
        height: Int,
        background: RGBPixel,
        x originX: Double,
        y originY: Double,
        displayWidth: Double,
        displayHeight: Double
    ) -> String {
        let scaleX = displayWidth / Double(max(1, width))
        let scaleY = displayHeight / Double(max(1, height))
        var body = ""
        body.reserveCapacity(width * height / 2)
        for y in 0..<height {
            var x = 0
            while x < width {
                let color = pixels[y * width + x]
                var runEnd = x + 1
                while runEnd < width, pixels[y * width + runEnd] == color {
                    runEnd += 1
                }
                if color != background {
                    body += """

          <rect x="\(fmt(originX + Double(x) * scaleX))" y="\(fmt(originY + Double(y) * scaleY))" width="\(fmt(Double(runEnd - x) * scaleX))" height="\(fmt(scaleY))" fill="\(color.hex)"/>
"""
                }
                x = runEnd
            }
        }
        return body
    }

    private static func fmt(_ value: Double) -> String {
        String(format: "%.4f", value)
    }

    private static func escapeXML(_ text: String) -> String {
        text
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "\"", with: "&quot;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
    }
}
