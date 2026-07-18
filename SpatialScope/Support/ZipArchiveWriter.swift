import Foundation

enum ZipArchiveWriter {
    struct Entry {
        var fileName: String
        var data: Data

        init(fileName: String, data: Data) {
            self.fileName = fileName
            self.data = data
        }
    }

    static func write(entries: [Entry], to url: URL, errorContext: String = "ZIP") throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try zipData(entries: entries, errorContext: errorContext).write(to: url)
    }

    static func zipData(entries: [Entry], errorContext: String = "ZIP") throws -> Data {
        var archive = Data()
        var centralDirectory = Data()
        var localOffsets: [UInt32] = []

        for entry in entries {
            guard let fileNameData = entry.fileName.data(using: .utf8),
                  fileNameData.count <= UInt16.max,
                  entry.data.count <= UInt32.max,
                  archive.count <= UInt32.max else {
                throw SpatialScopeError.message("Could not write \(errorContext) because an archive entry is too large.")
            }
            let crc = crc32(entry.data)
            localOffsets.append(UInt32(archive.count))
            appendLE(UInt32(0x04034b50), to: &archive)
            appendLE(UInt16(20), to: &archive)
            appendLE(UInt16(0x0800), to: &archive)
            appendLE(UInt16(0), to: &archive)
            appendLE(UInt16(0), to: &archive)
            appendLE(UInt16(0), to: &archive)
            appendLE(crc, to: &archive)
            appendLE(UInt32(entry.data.count), to: &archive)
            appendLE(UInt32(entry.data.count), to: &archive)
            appendLE(UInt16(fileNameData.count), to: &archive)
            appendLE(UInt16(0), to: &archive)
            archive.append(fileNameData)
            archive.append(entry.data)
        }

        for (index, entry) in entries.enumerated() {
            guard let fileNameData = entry.fileName.data(using: .utf8) else {
                throw SpatialScopeError.message("Could not write \(errorContext) because an archive file name is invalid.")
            }
            let crc = crc32(entry.data)
            appendLE(UInt32(0x02014b50), to: &centralDirectory)
            appendLE(UInt16(20), to: &centralDirectory)
            appendLE(UInt16(20), to: &centralDirectory)
            appendLE(UInt16(0x0800), to: &centralDirectory)
            appendLE(UInt16(0), to: &centralDirectory)
            appendLE(UInt16(0), to: &centralDirectory)
            appendLE(UInt16(0), to: &centralDirectory)
            appendLE(crc, to: &centralDirectory)
            appendLE(UInt32(entry.data.count), to: &centralDirectory)
            appendLE(UInt32(entry.data.count), to: &centralDirectory)
            appendLE(UInt16(fileNameData.count), to: &centralDirectory)
            appendLE(UInt16(0), to: &centralDirectory)
            appendLE(UInt16(0), to: &centralDirectory)
            appendLE(UInt16(0), to: &centralDirectory)
            appendLE(UInt16(0), to: &centralDirectory)
            appendLE(UInt32(0), to: &centralDirectory)
            appendLE(localOffsets[index], to: &centralDirectory)
            centralDirectory.append(fileNameData)
        }

        guard entries.count <= UInt16.max,
              archive.count <= UInt32.max,
              centralDirectory.count <= UInt32.max else {
            throw SpatialScopeError.message("Could not write \(errorContext) because the archive directory is too large.")
        }
        let centralOffset = UInt32(archive.count)
        archive.append(centralDirectory)
        appendLE(UInt32(0x06054b50), to: &archive)
        appendLE(UInt16(0), to: &archive)
        appendLE(UInt16(0), to: &archive)
        appendLE(UInt16(entries.count), to: &archive)
        appendLE(UInt16(entries.count), to: &archive)
        appendLE(UInt32(centralDirectory.count), to: &archive)
        appendLE(centralOffset, to: &archive)
        appendLE(UInt16(0), to: &archive)
        return archive
    }

    private static func appendLE(_ value: UInt16, to data: inout Data) {
        var little = value.littleEndian
        data.append(Data(bytes: &little, count: MemoryLayout<UInt16>.stride))
    }

    private static func appendLE(_ value: UInt32, to data: inout Data) {
        var little = value.littleEndian
        data.append(Data(bytes: &little, count: MemoryLayout<UInt32>.stride))
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
