import Foundation

enum XLSXExportService {
    struct Sheet {
        var name: String
        var rows: [[String]]

        init(name: String, rows: [[String]]) {
            self.name = name
            self.rows = rows
        }
    }

    static func writeWorkbook(sheets: [Sheet], to url: URL) throws {
        let normalizedSheets = normalizeSheets(sheets)
        guard !normalizedSheets.isEmpty else {
            throw SpatialScopeError.message("Could not write XLSX because there are no worksheets.")
        }

        var entries: [ZipArchiveWriter.Entry] = []
        entries.append(.init(fileName: "[Content_Types].xml", data: xmlData(contentTypesXML(sheetCount: normalizedSheets.count))))
        entries.append(.init(fileName: "_rels/.rels", data: xmlData(rootRelationshipsXML())))
        entries.append(.init(fileName: "xl/workbook.xml", data: xmlData(workbookXML(sheets: normalizedSheets))))
        entries.append(.init(fileName: "xl/_rels/workbook.xml.rels", data: xmlData(workbookRelationshipsXML(sheetCount: normalizedSheets.count))))
        entries.append(.init(fileName: "xl/styles.xml", data: xmlData(stylesXML())))
        for (index, sheet) in normalizedSheets.enumerated() {
            entries.append(.init(
                fileName: "xl/worksheets/sheet\(index + 1).xml",
                data: xmlData(worksheetXML(rows: sheet.rows))
            ))
        }
        try ZipArchiveWriter.write(entries: entries, to: url, errorContext: "XLSX")
    }

    private static func normalizeSheets(_ sheets: [Sheet]) -> [Sheet] {
        var usedNames: Set<String> = []
        return sheets.enumerated().map { index, sheet in
            var base = sanitizedSheetName(sheet.name)
            if base.isEmpty {
                base = "Sheet\(index + 1)"
            }
            var candidate = base
            var suffix = 2
            while usedNames.contains(candidate.lowercased()) {
                let suffixText = " \(suffix)"
                let maxBaseLength = max(1, 31 - suffixText.count)
                candidate = String(base.prefix(maxBaseLength)) + suffixText
                suffix += 1
            }
            usedNames.insert(candidate.lowercased())
            return Sheet(name: candidate, rows: sheet.rows)
        }
    }

    private static func sanitizedSheetName(_ name: String) -> String {
        let forbidden = CharacterSet(charactersIn: "[]:*?/\\")
        let filtered = name.unicodeScalars.map { forbidden.contains($0) ? "_" : String($0) }.joined()
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return String(filtered.prefix(31))
    }

    private static func contentTypesXML(sheetCount: Int) -> String {
        var overrides = """
        <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
        <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
        """
        for index in 1...sheetCount {
            overrides += "\n<Override PartName=\"/xl/worksheets/sheet\(index).xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>"
        }
        return """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
        <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
        <Default Extension="xml" ContentType="application/xml"/>
        \(overrides)
        </Types>
        """
    }

    private static func rootRelationshipsXML() -> String {
        """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
        </Relationships>
        """
    }

    private static func workbookXML(sheets: [Sheet]) -> String {
        let sheetXML = sheets.enumerated().map { index, sheet in
            "<sheet name=\"\(escapeXML(sheet.name))\" sheetId=\"\(index + 1)\" r:id=\"rId\(index + 1)\"/>"
        }.joined(separator: "\n")
        return """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <sheets>
        \(sheetXML)
        </sheets>
        </workbook>
        """
    }

    private static func workbookRelationshipsXML(sheetCount: Int) -> String {
        var relationships = ""
        for index in 1...sheetCount {
            relationships += "<Relationship Id=\"rId\(index)\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet\(index).xml\"/>\n"
        }
        relationships += "<Relationship Id=\"rId\(sheetCount + 1)\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles\" Target=\"styles.xml\"/>"
        return """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        \(relationships)
        </Relationships>
        """
    }

    private static func stylesXML() -> String {
        """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <fonts count="1"><font><sz val="11"/><color theme="1"/><name val="Helvetica Neue"/><family val="2"/></font></fonts>
        <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
        <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
        <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
        <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
        <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
        </styleSheet>
        """
    }

    private static func worksheetXML(rows: [[String]]) -> String {
        let maxColumn = rows.map(\.count).max() ?? 0
        let dimension = maxColumn > 0 && !rows.isEmpty ? "A1:\(columnName(maxColumn))\(rows.count)" : "A1"
        let rowXML = rows.enumerated().map { rowIndex, row in
            let rowNumber = rowIndex + 1
            let cellXML = row.enumerated().compactMap { columnIndex, value -> String? in
                guard !value.isEmpty else { return nil }
                let reference = "\(columnName(columnIndex + 1))\(rowNumber)"
                if let number = numericValue(value) {
                    return "<c r=\"\(reference)\"><v>\(number)</v></c>"
                }
                return "<c r=\"\(reference)\" t=\"inlineStr\"><is><t>\(escapeXML(value))</t></is></c>"
            }.joined()
            return "<row r=\"\(rowNumber)\">\(cellXML)</row>"
        }.joined(separator: "\n")
        return """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <dimension ref="\(dimension)"/>
        <sheetViews><sheetView workbookViewId="0"/></sheetViews>
        <sheetFormatPr defaultRowHeight="15"/>
        <sheetData>
        \(rowXML)
        </sheetData>
        </worksheet>
        """
    }

    private static func numericValue(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              trimmed.rangeOfCharacter(from: CharacterSet.decimalDigits) != nil,
              trimmed.rangeOfCharacter(from: CharacterSet(charactersIn: "0123456789.+-eE").inverted) == nil,
              let number = Double(trimmed),
              number.isFinite else {
            return nil
        }
        return trimmed
    }

    private static func columnName(_ oneBasedIndex: Int) -> String {
        var value = oneBasedIndex
        var name = ""
        while value > 0 {
            value -= 1
            let scalar = UnicodeScalar(65 + (value % 26))!
            name.insert(Character(scalar), at: name.startIndex)
            value /= 26
        }
        return name
    }

    private static func escapeXML(_ value: String) -> String {
        value
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
            .replacingOccurrences(of: "\"", with: "&quot;")
            .replacingOccurrences(of: "'", with: "&apos;")
    }

    private static func xmlData(_ xml: String) -> Data {
        Data(xml.utf8)
    }
}
