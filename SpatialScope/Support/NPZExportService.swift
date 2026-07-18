import Compression
import Foundation

enum NPZExportService {
    struct Array2D {
        var name: String
        var descr: String
        var width: Int
        var height: Int
        var data: Data
    }

    struct Int16Array2D {
        var name: String
        var width: Int
        var height: Int
        var values: [Int16]
    }

    static func uint8(name: String, width: Int, height: Int, values: [UInt8]) throws -> Array2D {
        try validateDimensions(width: width, height: height, count: values.count)
        return Array2D(name: name, descr: "|u1", width: width, height: height, data: Data(values))
    }

    static func int16(name: String, width: Int, height: Int, values: [Int16]) throws -> Array2D {
        try validateDimensions(width: width, height: height, count: values.count)
        var data = Data(capacity: values.count * MemoryLayout<Int16>.stride)
        for value in values {
            data.appendLittleEndian(value)
        }
        return Array2D(name: name, descr: "<i2", width: width, height: height, data: data)
    }

    static func float32(name: String, width: Int, height: Int, values: [Float]) throws -> Array2D {
        try validateDimensions(width: width, height: height, count: values.count)
        var data = Data(capacity: values.count * MemoryLayout<Float>.stride)
        for value in values {
            data.appendLittleEndian(value.bitPattern)
        }
        return Array2D(name: name, descr: "<f4", width: width, height: height, data: data)
    }

    static func writeNPZ(arrays: [Array2D], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let entries = try arrays.map { array in
            let fileName = "\(array.name).npy"
            let payload = try npyData(for: array)
            let compressed = try deflate(payload)
            return ZipEntry(
                fileName: fileName,
                data: payload,
                compressedData: compressed,
                method: 8,
                crc32: crc32(payload)
            )
        }
        try zipData(entries: entries).write(to: url)
    }

    static func readInt16Array(named name: String, from url: URL) throws -> Int16Array2D {
        let archive = try Data(contentsOf: url)
        let entries = try readZipEntries(from: archive)
        let entryName = "\(name).npy"
        guard let npy = entries[entryName] else {
            throw SpatialScopeError.message("Could not read \(name) from \(url.lastPathComponent).")
        }
        let array = try readNPY(npy, expectedName: name)
        guard array.descr == "<i2" || array.descr == "|i2" else {
            throw SpatialScopeError.message("Could not read \(name) because dtype \(array.descr) is not int16.")
        }
        let expectedBytes = array.width * array.height * MemoryLayout<Int16>.stride
        guard array.data.count >= expectedBytes else {
            throw SpatialScopeError.message("Could not read \(name) because the NumPy payload is truncated.")
        }
        var values: [Int16] = []
        values.reserveCapacity(array.width * array.height)
        for offset in stride(from: 0, to: expectedBytes, by: 2) {
            let low = UInt16(array.data[offset])
            let high = UInt16(array.data[offset + 1]) << 8
            values.append(Int16(bitPattern: high | low))
        }
        return Int16Array2D(name: name, width: array.width, height: array.height, values: values)
    }

    private static func validateDimensions(width: Int, height: Int, count: Int) throws {
        guard width > 0, height > 0, width * height == count else {
            throw SpatialScopeError.message("Could not write NPZ because array dimensions do not match the data.")
        }
    }

    private static func npyData(for array: Array2D) throws -> Data {
        let headerDict = "{'descr': '\(array.descr)', 'fortran_order': False, 'shape': (\(array.height), \(array.width)), }"
        let preambleLength = 10
        let newlineLength = 1
        let padding = (16 - ((preambleLength + headerDict.utf8.count + newlineLength) % 16)) % 16
        let header = headerDict + String(repeating: " ", count: padding) + "\n"
        guard header.utf8.count <= UInt16.max else {
            throw SpatialScopeError.message("Could not write NPZ because a NumPy header is too large.")
        }

        var output = Data()
        output.append(0x93)
        output.append(contentsOf: Array("NUMPY".utf8))
        output.append(0x01)
        output.append(0x00)
        output.appendLittleEndian(UInt16(header.utf8.count))
        output.append(contentsOf: header.data(using: .ascii) ?? Data())
        output.append(array.data)
        return output
    }

    private struct ZipEntry {
        var fileName: String
        var data: Data
        var compressedData: Data
        var method: UInt16
        var crc32: UInt32
    }

    private static func zipData(entries: [ZipEntry]) throws -> Data {
        var archive = Data()
        var centralDirectory = Data()
        var localOffsets: [UInt32] = []

        for entry in entries {
            guard let fileNameData = entry.fileName.data(using: .utf8),
                  fileNameData.count <= UInt16.max,
                  entry.data.count <= UInt32.max,
                  entry.compressedData.count <= UInt32.max,
                  archive.count <= UInt32.max else {
                throw SpatialScopeError.message("Could not write NPZ because a ZIP entry is too large.")
            }
            let zip64Extra = zip64ExtraField(
                uncompressedSize: UInt64(entry.data.count),
                compressedSize: UInt64(entry.compressedData.count)
            )
            localOffsets.append(UInt32(archive.count))
            archive.appendLittleEndian(UInt32(0x04034b50))
            archive.appendLittleEndian(UInt16(45))
            archive.appendLittleEndian(UInt16(0))
            archive.appendLittleEndian(entry.method)
            archive.appendLittleEndian(UInt16(0))
            archive.appendLittleEndian(UInt16(0))
            archive.appendLittleEndian(entry.crc32)
            archive.appendLittleEndian(UInt32.max)
            archive.appendLittleEndian(UInt32.max)
            archive.appendLittleEndian(UInt16(fileNameData.count))
            archive.appendLittleEndian(UInt16(zip64Extra.count))
            archive.append(fileNameData)
            archive.append(zip64Extra)
            archive.append(entry.compressedData)
        }

        for (index, entry) in entries.enumerated() {
            guard let fileNameData = entry.fileName.data(using: .utf8) else {
                throw SpatialScopeError.message("Could not write NPZ because a ZIP file name is invalid.")
            }
            centralDirectory.appendLittleEndian(UInt32(0x02014b50))
            centralDirectory.appendLittleEndian(UInt16(45))
            centralDirectory.appendLittleEndian(UInt16(45))
            centralDirectory.appendLittleEndian(UInt16(0))
            centralDirectory.appendLittleEndian(entry.method)
            centralDirectory.appendLittleEndian(UInt16(0))
            centralDirectory.appendLittleEndian(UInt16(0))
            centralDirectory.appendLittleEndian(entry.crc32)
            centralDirectory.appendLittleEndian(UInt32(entry.compressedData.count))
            centralDirectory.appendLittleEndian(UInt32(entry.data.count))
            centralDirectory.appendLittleEndian(UInt16(fileNameData.count))
            centralDirectory.appendLittleEndian(UInt16(0))
            centralDirectory.appendLittleEndian(UInt16(0))
            centralDirectory.appendLittleEndian(UInt16(0))
            centralDirectory.appendLittleEndian(UInt16(0))
            centralDirectory.appendLittleEndian(UInt32(0x0180_0000))
            centralDirectory.appendLittleEndian(localOffsets[index])
            centralDirectory.append(fileNameData)
        }

        guard entries.count <= UInt16.max,
              archive.count <= UInt32.max,
              centralDirectory.count <= UInt32.max else {
            throw SpatialScopeError.message("Could not write NPZ because the ZIP directory is too large.")
        }
        let centralOffset = UInt32(archive.count)
        archive.append(centralDirectory)
        archive.appendLittleEndian(UInt32(0x06054b50))
        archive.appendLittleEndian(UInt16(0))
        archive.appendLittleEndian(UInt16(0))
        archive.appendLittleEndian(UInt16(entries.count))
        archive.appendLittleEndian(UInt16(entries.count))
        archive.appendLittleEndian(UInt32(centralDirectory.count))
        archive.appendLittleEndian(centralOffset)
        archive.appendLittleEndian(UInt16(0))
        return archive
    }

    private static func deflate(_ data: Data) throws -> Data {
        var capacity = max(64, data.count / 2)
        while capacity <= max(128, data.count + 4096) {
            var output = [UInt8](repeating: 0, count: capacity)
            let encoded = data.withUnsafeBytes { inputBuffer in
                compression_encode_buffer(
                    &output,
                    capacity,
                    inputBuffer.bindMemory(to: UInt8.self).baseAddress!,
                    data.count,
                    nil,
                    COMPRESSION_ZLIB
                )
            }
            if encoded > 0 {
                return Data(output.prefix(encoded))
            }
            capacity *= 2
        }
        throw SpatialScopeError.message("Could not compress NPZ data.")
    }

    private static func zip64ExtraField(uncompressedSize: UInt64, compressedSize: UInt64) -> Data {
        var extra = Data()
        extra.appendLittleEndian(UInt16(0x0001))
        extra.appendLittleEndian(UInt16(16))
        extra.appendLittleEndian(uncompressedSize)
        extra.appendLittleEndian(compressedSize)
        return extra
    }

    private static func readZipEntries(from data: Data) throws -> [String: Data] {
        var entries: [String: Data] = [:]
        var offset = 0
        while offset + 30 <= data.count {
            let signature = readUInt32(data, offset: offset)
            if signature == 0x02014b50 || signature == 0x06054b50 { break }
            guard signature == 0x04034b50 else {
                throw SpatialScopeError.message("Could not read NPZ because the ZIP local header is invalid.")
            }
            let flags = readUInt16(data, offset: offset + 6)
            let method = readUInt16(data, offset: offset + 8)
            let compressedSizeRaw = readUInt32(data, offset: offset + 18)
            let uncompressedSizeRaw = readUInt32(data, offset: offset + 22)
            let fileNameLength = Int(readUInt16(data, offset: offset + 26))
            let extraLength = Int(readUInt16(data, offset: offset + 28))
            guard flags & 0x0008 == 0 else {
                throw SpatialScopeError.message("Could not read NPZ because ZIP data descriptors are not supported.")
            }
            let nameStart = offset + 30
            let extraStart = nameStart + fileNameLength
            let payloadStart = extraStart + extraLength
            guard extraStart <= data.count, payloadStart <= data.count else {
                throw SpatialScopeError.message("Could not read NPZ because a ZIP entry is truncated.")
            }
            let nameData = Data(data[nameStart..<(nameStart + fileNameLength)])
            guard let name = String(data: nameData, encoding: .utf8) else {
                throw SpatialScopeError.message("Could not read NPZ because a ZIP entry name is invalid.")
            }
            let extraData = Data(data[extraStart..<payloadStart])
            let zip64Sizes = zip64ExtraSizes(
                extraData,
                needsUncompressed: uncompressedSizeRaw == UInt32.max,
                needsCompressed: compressedSizeRaw == UInt32.max
            )
            let compressedSize64 = compressedSizeRaw == UInt32.max ? zip64Sizes.compressed : UInt64(compressedSizeRaw)
            let uncompressedSize64 = uncompressedSizeRaw == UInt32.max ? zip64Sizes.uncompressed : UInt64(uncompressedSizeRaw)
            guard let compressedSize = compressedSize64.flatMap({ $0 <= UInt64(Int.max) ? Int($0) : nil }),
                  let uncompressedSize = uncompressedSize64.flatMap({ $0 <= UInt64(Int.max) ? Int($0) : nil }) else {
                throw SpatialScopeError.message("Could not read NPZ because a ZIP64 entry is too large.")
            }
            let payloadEnd = payloadStart + compressedSize
            guard payloadEnd <= data.count else {
                throw SpatialScopeError.message("Could not read NPZ because a ZIP entry is truncated.")
            }
            let compressedPayload = Data(data[payloadStart..<payloadEnd])
            let payload: Data
            switch method {
            case 0:
                payload = compressedPayload
            case 8:
                payload = try inflateRawDeflate(compressedPayload, uncompressedSize: uncompressedSize)
            default:
                throw SpatialScopeError.message("Could not read NPZ because ZIP compression method \(method) is unsupported.")
            }
            entries[name] = payload
            offset = payloadEnd
        }
        return entries
    }

    private static func zip64ExtraSizes(
        _ extra: Data,
        needsUncompressed: Bool,
        needsCompressed: Bool
    ) -> (uncompressed: UInt64?, compressed: UInt64?) {
        var offset = 0
        while offset + 4 <= extra.count {
            let headerID = readUInt16(extra, offset: offset)
            let dataSize = Int(readUInt16(extra, offset: offset + 2))
            let dataStart = offset + 4
            let dataEnd = dataStart + dataSize
            guard dataEnd <= extra.count else { break }
            if headerID == 0x0001 {
                var cursor = dataStart
                var uncompressed: UInt64?
                var compressed: UInt64?
                if needsUncompressed, cursor + 8 <= dataEnd {
                    uncompressed = readUInt64(extra, offset: cursor)
                    cursor += 8
                }
                if needsCompressed, cursor + 8 <= dataEnd {
                    compressed = readUInt64(extra, offset: cursor)
                }
                return (uncompressed, compressed)
            }
            offset = dataEnd
        }
        return (nil, nil)
    }

    private static func inflateRawDeflate(_ data: Data, uncompressedSize: Int) throws -> Data {
        let capacity = max(1, uncompressedSize)
        var output = [UInt8](repeating: 0, count: capacity)
        let decoded = data.withUnsafeBytes { inputBuffer in
            compression_decode_buffer(
                &output,
                capacity,
                inputBuffer.bindMemory(to: UInt8.self).baseAddress!,
                data.count,
                nil,
                COMPRESSION_ZLIB
            )
        }
        guard decoded > 0 else {
            throw SpatialScopeError.message("Could not inflate compressed NPZ data.")
        }
        return Data(output.prefix(decoded))
    }

    private static func readNPY(_ data: Data, expectedName: String) throws -> Array2D {
        guard data.count >= 10,
              data[0] == 0x93,
              String(data: Data(data[1..<6]), encoding: .ascii) == "NUMPY" else {
            throw SpatialScopeError.message("Could not read \(expectedName) because the NumPy header is invalid.")
        }
        let major = data[6]
        let headerStart: Int
        let headerLength: Int
        if major == 1 {
            headerLength = Int(readUInt16(data, offset: 8))
            headerStart = 10
        } else if major == 2 || major == 3 {
            headerLength = Int(readUInt32(data, offset: 8))
            headerStart = 12
        } else {
            throw SpatialScopeError.message("Could not read \(expectedName) because NumPy format \(major) is unsupported.")
        }
        let headerEnd = headerStart + headerLength
        guard headerEnd <= data.count else {
            throw SpatialScopeError.message("Could not read \(expectedName) because the NumPy header is truncated.")
        }
        guard let header = String(data: Data(data[headerStart..<headerEnd]), encoding: .ascii) else {
            throw SpatialScopeError.message("Could not read \(expectedName) because the NumPy header is not ASCII.")
        }
        let descr = npyHeaderValue("descr", in: header)
        guard let shape = npyShape(in: header), shape.count == 2 else {
            throw SpatialScopeError.message("Could not read \(expectedName) because only 2D arrays are supported.")
        }
        let height = shape[0]
        let width = shape[1]
        guard width > 0, height > 0 else {
            throw SpatialScopeError.message("Could not read \(expectedName) because array dimensions are invalid.")
        }
        return Array2D(
            name: expectedName,
            descr: descr ?? "",
            width: width,
            height: height,
            data: Data(data[headerEnd..<data.count])
        )
    }

    private static func npyHeaderValue(_ key: String, in header: String) -> String? {
        let pattern = "'\(key)'"
        guard let keyRange = header.range(of: pattern),
              let colon = header[keyRange.upperBound...].firstIndex(of: ":") else {
            return nil
        }
        let tail = header[header.index(after: colon)...]
        guard let quote = tail.firstIndex(where: { $0 == "'" || $0 == "\"" }) else { return nil }
        let quoteChar = tail[quote]
        let valueStart = tail.index(after: quote)
        guard let valueEnd = tail[valueStart...].firstIndex(of: quoteChar) else { return nil }
        return String(tail[valueStart..<valueEnd])
    }

    private static func npyShape(in header: String) -> [Int]? {
        guard let shapeRange = header.range(of: "'shape'"),
              let open = header[shapeRange.upperBound...].firstIndex(of: "("),
              let close = header[open...].firstIndex(of: ")") else {
            return nil
        }
        return header[header.index(after: open)..<close]
            .split(separator: ",")
            .compactMap { Int($0.trimmingCharacters(in: .whitespacesAndNewlines)) }
    }

    private static func readUInt16(_ data: Data, offset: Int) -> UInt16 {
        UInt16(data[offset]) | (UInt16(data[offset + 1]) << 8)
    }

    private static func readUInt32(_ data: Data, offset: Int) -> UInt32 {
        UInt32(data[offset])
            | (UInt32(data[offset + 1]) << 8)
            | (UInt32(data[offset + 2]) << 16)
            | (UInt32(data[offset + 3]) << 24)
    }

    private static func readUInt64(_ data: Data, offset: Int) -> UInt64 {
        var value: UInt64 = 0
        for index in 0..<8 {
            value |= UInt64(data[offset + index]) << UInt64(index * 8)
        }
        return value
    }

    private static func crc32(_ data: Data) -> UInt32 {
        var crc: UInt32 = 0xffff_ffff
        for byte in data {
            crc = (crc >> 8) ^ crcTable[Int((crc ^ UInt32(byte)) & 0xff)]
        }
        return crc ^ 0xffff_ffff
    }

    private static let crcTable: [UInt32] = (0..<256).map { value in
        var crc = UInt32(value)
        for _ in 0..<8 {
            if crc & 1 == 1 {
                crc = (crc >> 1) ^ 0xedb8_8320
            } else {
                crc >>= 1
            }
        }
        return crc
    }
}

private extension Data {
    mutating func appendLittleEndian(_ value: UInt16) {
        var little = value.littleEndian
        append(Data(bytes: &little, count: MemoryLayout<UInt16>.stride))
    }

    mutating func appendLittleEndian(_ value: UInt32) {
        var little = value.littleEndian
        append(Data(bytes: &little, count: MemoryLayout<UInt32>.stride))
    }

    mutating func appendLittleEndian(_ value: UInt64) {
        var little = value.littleEndian
        append(Data(bytes: &little, count: MemoryLayout<UInt64>.stride))
    }

    mutating func appendLittleEndian(_ value: Int16) {
        var little = value.littleEndian
        append(Data(bytes: &little, count: MemoryLayout<Int16>.stride))
    }
}
