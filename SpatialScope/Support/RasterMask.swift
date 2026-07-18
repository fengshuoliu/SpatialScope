import Foundation

struct RasterMask {
    var width: Int
    var height: Int
    var pixels: [Bool]

    init(width: Int, height: Int, fill: Bool = false) {
        self.width = max(1, width)
        self.height = max(1, height)
        self.pixels = Array(repeating: fill, count: self.width * self.height)
    }

    init(width: Int, height: Int, pixels: [Bool]) {
        self.width = max(1, width)
        self.height = max(1, height)
        if pixels.count == self.width * self.height {
            self.pixels = pixels
        } else {
            self.pixels = Array(repeating: false, count: self.width * self.height)
        }
    }

    init(width: Int, height: Int, runs: [MaskRun]) {
        self.init(width: width, height: height)
        for run in runs {
            guard run.y >= 0, run.y < self.height else { continue }
            let start = max(0, min(self.width, run.xStart))
            let end = max(start, min(self.width, run.xEnd))
            guard start < end else { continue }
            for x in start..<end {
                self[x, run.y] = true
            }
        }
    }

    subscript(x: Int, y: Int) -> Bool {
        get {
            guard x >= 0, x < width, y >= 0, y < height else { return false }
            return pixels[y * width + x]
        }
        set {
            guard x >= 0, x < width, y >= 0, y < height else { return }
            pixels[y * width + x] = newValue
        }
    }

    var area: Int {
        pixels.reduce(0) { $0 + ($1 ? 1 : 0) }
    }

    var isEmpty: Bool {
        !pixels.contains(true)
    }

    func union(_ other: RasterMask) -> RasterMask {
        combined(with: other) { $0 || $1 }
    }

    func intersecting(_ other: RasterMask) -> RasterMask {
        combined(with: other) { $0 && $1 }
    }

    func subtracting(_ other: RasterMask) -> RasterMask {
        combined(with: other) { $0 && !$1 }
    }

    func contains(x: Double, y: Double) -> Bool {
        let ix = min(max(Int(round(x)), 0), width - 1)
        let iy = min(max(Int(round(y)), 0), height - 1)
        return self[ix, iy]
    }

    func toRuns() -> [MaskRun] {
        var runs: [MaskRun] = []
        for y in 0..<height {
            var x = 0
            while x < width {
                while x < width && !self[x, y] { x += 1 }
                let start = x
                while x < width && self[x, y] { x += 1 }
                if start < x {
                    runs.append(MaskRun(y: y, xStart: start, xEnd: x))
                }
            }
        }
        return runs
    }

    func boundingBox() -> (x: Int, y: Int, width: Int, height: Int)? {
        var minX = width
        var minY = height
        var maxX = -1
        var maxY = -1
        for y in 0..<height {
            for x in 0..<width where self[x, y] {
                minX = min(minX, x)
                minY = min(minY, y)
                maxX = max(maxX, x)
                maxY = max(maxY, y)
            }
        }
        guard maxX >= minX, maxY >= minY else { return nil }
        return (minX, minY, maxX - minX + 1, maxY - minY + 1)
    }

    mutating func fillDisk(centerX: Double, centerY: Double, radius: Double) {
        let r = max(1.0, radius)
        let minX = max(0, Int(floor(centerX - r)))
        let maxX = min(width - 1, Int(ceil(centerX + r)))
        let minY = max(0, Int(floor(centerY - r)))
        let maxY = min(height - 1, Int(ceil(centerY + r)))
        let r2 = r * r
        for y in minY...maxY {
            for x in minX...maxX {
                let dx = Double(x) + 0.5 - centerX
                let dy = Double(y) + 0.5 - centerY
                if dx * dx + dy * dy <= r2 {
                    self[x, y] = true
                }
            }
        }
    }

    mutating func fillPolygon(_ points: [CellBoundaryPoint]) {
        guard points.count >= 3 else { return }
        let clipped = points.map {
            CellBoundaryPoint(
                x: min(max($0.x, 0.0), Double(width - 1)),
                y: min(max($0.y, 0.0), Double(height - 1))
            )
        }
        let minY = max(0, Int(floor(clipped.map(\.y).min() ?? 0)))
        let maxY = min(height - 1, Int(ceil(clipped.map(\.y).max() ?? 0)))
        guard minY <= maxY else { return }

        for y in minY...maxY {
            let scanY = Double(y) + 0.5
            var intersections: [Double] = []
            for index in clipped.indices {
                let p1 = clipped[index]
                let p2 = clipped[(index + 1) % clipped.count]
                let crosses = (p1.y <= scanY && p2.y > scanY) || (p2.y <= scanY && p1.y > scanY)
                guard crosses else { continue }
                let dy = p2.y - p1.y
                guard abs(dy) > 1e-9 else { continue }
                let t = (scanY - p1.y) / dy
                intersections.append(p1.x + t * (p2.x - p1.x))
            }
            intersections.sort()
            var i = 0
            while i + 1 < intersections.count {
                let start = max(0, Int(ceil(intersections[i])))
                let end = min(width - 1, Int(floor(intersections[i + 1])))
                if start <= end {
                    for x in start...end {
                        self[x, y] = true
                    }
                }
                i += 2
            }
        }
    }

    func dilated(radius: Int) -> RasterMask {
        let radius = max(0, radius)
        guard radius > 0 else { return self }
        let integral = integralCounts()
        var out = RasterMask(width: width, height: height)
        for y in 0..<height {
            let y0 = max(0, y - radius)
            let y1 = min(height, y + radius + 1)
            for x in 0..<width {
                let x0 = max(0, x - radius)
                let x1 = min(width, x + radius + 1)
                out[x, y] = sum(integral: integral, x0: x0, y0: y0, x1: x1, y1: y1) > 0
            }
        }
        return out
    }

    func eroded(radius: Int) -> RasterMask {
        let radius = max(0, radius)
        guard radius > 0 else { return self }
        let integral = integralCounts()
        var out = RasterMask(width: width, height: height)
        let windowArea = (radius * 2 + 1) * (radius * 2 + 1)
        guard width > radius * 2, height > radius * 2 else { return out }
        for y in radius..<(height - radius) {
            for x in radius..<(width - radius) {
                let x0 = x - radius
                let y0 = y - radius
                let x1 = x + radius + 1
                let y1 = y + radius + 1
                out[x, y] = sum(integral: integral, x0: x0, y0: y0, x1: x1, y1: y1) == windowArea
            }
        }
        return out
    }

    func closed(radius: Int) -> RasterMask {
        dilated(radius: radius).eroded(radius: radius)
    }

    func dilatedDisk(radius: Int) -> RasterMask {
        let radius = max(0, radius)
        guard radius > 0, !isEmpty else { return self }
        let radiusSquared = Double(radius * radius)
        let distances = squaredDistanceToValue(true, outsideIsTarget: false)
        return RasterMask(
            width: width,
            height: height,
            pixels: distances.map { $0 <= radiusSquared }
        )
    }

    func erodedDisk(radius: Int) -> RasterMask {
        let radius = max(0, radius)
        guard radius > 0, !isEmpty else { return self }
        let paddedWidth = width + 2
        let paddedHeight = height + 2
        var paddedBackground = RasterMask(width: paddedWidth, height: paddedHeight, fill: true)
        for y in 0..<height {
            for x in 0..<width {
                paddedBackground[x + 1, y + 1] = !self[x, y]
            }
        }
        let radiusSquared = Double(radius * radius)
        let dilatedBackground = paddedBackground.squaredDistanceToValue(true, outsideIsTarget: false)
        var out = RasterMask(width: width, height: height)
        for y in 0..<height {
            for x in 0..<width where self[x, y] {
                out[x, y] = dilatedBackground[(y + 1) * paddedWidth + (x + 1)] > radiusSquared
            }
        }
        return out
    }

    func closedDisk(radius: Int) -> RasterMask {
        dilatedDisk(radius: radius).erodedDisk(radius: radius)
    }

    func filledHoles() -> RasterMask {
        var exterior = RasterMask(width: width, height: height)
        var queue: [(Int, Int)] = []
        func enqueue(_ x: Int, _ y: Int, exterior: inout RasterMask, queue: inout [(Int, Int)]) {
            guard x >= 0, x < width, y >= 0, y < height else { return }
            guard !self[x, y], !exterior[x, y] else { return }
            exterior[x, y] = true
            queue.append((x, y))
        }

        for x in 0..<width {
            enqueue(x, 0, exterior: &exterior, queue: &queue)
            enqueue(x, height - 1, exterior: &exterior, queue: &queue)
        }
        for y in 0..<height {
            enqueue(0, y, exterior: &exterior, queue: &queue)
            enqueue(width - 1, y, exterior: &exterior, queue: &queue)
        }

        var cursor = 0
        let neighbors = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        while cursor < queue.count {
            let (x, y) = queue[cursor]
            cursor += 1
            for neighbor in neighbors {
                enqueue(x + neighbor.0, y + neighbor.1, exterior: &exterior, queue: &queue)
            }
        }

        var out = self
        for y in 0..<height {
            for x in 0..<width where !self[x, y] && !exterior[x, y] {
                out[x, y] = true
            }
        }
        return out
    }

    func removingSmallObjects(minSize: Int) -> RasterMask {
        guard minSize > 1 else { return self }
        var out = RasterMask(width: width, height: height)
        for component in components() where component.count >= minSize {
            for index in component {
                out.pixels[index] = true
            }
        }
        return out
    }

    func components() -> [[Int]] {
        var visited = Array(repeating: false, count: pixels.count)
        var result: [[Int]] = []
        let neighbors = [
            (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1)
        ]

        for index in pixels.indices where pixels[index] && !visited[index] {
            visited[index] = true
            var stack = [index]
            var component = [index]
            while let current = stack.popLast() {
                let x = current % width
                let y = current / width
                for neighbor in neighbors {
                    let nx = x + neighbor.0
                    let ny = y + neighbor.1
                    guard nx >= 0, nx < width, ny >= 0, ny < height else { continue }
                    let next = ny * width + nx
                    if pixels[next] && !visited[next] {
                        visited[next] = true
                        stack.append(next)
                        component.append(next)
                    }
                }
            }
            result.append(component)
        }
        return result
    }

    func keepingComponents(containingCentroids centroids: [(Double, Double)], minimumHits: Int) -> RasterMask {
        let components = components()
        guard !components.isEmpty else { return RasterMask(width: width, height: height) }
        var labelByIndex: [Int: Int] = [:]
        for (label, component) in components.enumerated() {
            for index in component {
                labelByIndex[index] = label + 1
            }
        }
        var hits = Array(repeating: 0, count: components.count + 1)
        for centroid in centroids {
            let x = min(max(Int(round(centroid.0)), 0), width - 1)
            let y = min(max(Int(round(centroid.1)), 0), height - 1)
            let label = labelByIndex[y * width + x] ?? 0
            if label > 0 { hits[label] += 1 }
        }
        let keep = Set(hits.indices.filter { $0 > 0 && hits[$0] >= minimumHits })
        var out = RasterMask(width: width, height: height)
        for (label, component) in components.enumerated() where keep.contains(label + 1) {
            for index in component {
                out.pixels[index] = true
            }
        }
        return out
    }

    func boundary(thickness: Int = 1) -> RasterMask {
        var out = RasterMask(width: width, height: height)
        let neighbors = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
        for y in 0..<height {
            for x in 0..<width where self[x, y] {
                if neighbors.contains(where: { !self[x + $0.0, y + $0.1] }) {
                    out[x, y] = true
                }
            }
        }
        return thickness > 1 ? out.dilated(radius: thickness - 1) : out
    }

    func interfaceBoundary(includeImageEdges: Bool = true) -> RasterMask {
        var out = RasterMask(width: width, height: height)
        let neighbors = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
        for y in 0..<height {
            for x in 0..<width {
                let value = self[x, y]
                if neighbors.contains(where: { neighbor in
                    let nx = x + neighbor.0
                    let ny = y + neighbor.1
                    if nx < 0 || nx >= width || ny < 0 || ny >= height {
                        return includeImageEdges && value
                    }
                    return self[nx, ny] != value
                }) {
                    out[x, y] = true
                }
            }
        }
        return out
    }

    func cardinalInterfaceBoundary(includeImageEdges: Bool = true) -> RasterMask {
        var out = RasterMask(width: width, height: height)
        let neighbors = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        for y in 0..<height {
            for x in 0..<width {
                let value = self[x, y]
                if neighbors.contains(where: { neighbor in
                    let nx = x + neighbor.0
                    let ny = y + neighbor.1
                    if nx < 0 || nx >= width || ny < 0 || ny >= height {
                        return includeImageEdges && value
                    }
                    return self[nx, ny] != value
                }) {
                    out[x, y] = true
                }
            }
        }
        return out
    }

    func euclideanDistanceToInterface(xScale: Double, yScale: Double) -> [Double] {
        euclideanDistance(to: interfaceBoundary(), xScale: xScale, yScale: yScale)
    }

    func euclideanDistance(to seed: RasterMask, xScale: Double, yScale: Double) -> [Double] {
        let inf = Double.greatestFiniteMagnitude / 16.0
        var xPass = Array(repeating: inf, count: width * height)
        for y in 0..<height {
            var row = Array(repeating: inf, count: width)
            for x in 0..<width where seed[x, y] {
                row[x] = 0.0
            }
            let transformed = Self.distanceTransform1D(row, scale: max(1e-9, xScale))
            for x in 0..<width {
                xPass[y * width + x] = transformed[x]
            }
        }

        var output = Array(repeating: inf, count: width * height)
        for x in 0..<width {
            var column = Array(repeating: inf, count: height)
            for y in 0..<height {
                column[y] = xPass[y * width + x]
            }
            let transformed = Self.distanceTransform1D(column, scale: max(1e-9, yScale))
            for y in 0..<height {
                output[y * width + x] = sqrt(max(0.0, transformed[y]))
            }
        }
        return output
    }

    func distanceStepsFromBoundary() -> [Int] {
        let seed = boundary(thickness: 1)
        var distances = Array(repeating: Int.max, count: pixels.count)
        var queue: [Int] = []
        for index in seed.pixels.indices where seed.pixels[index] {
            distances[index] = 0
            queue.append(index)
        }
        var cursor = 0
        let neighbors = [
            (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1)
        ]
        while cursor < queue.count {
            let current = queue[cursor]
            cursor += 1
            let x = current % width
            let y = current / width
            let nextDistance = distances[current] + 1
            for neighbor in neighbors {
                let nx = x + neighbor.0
                let ny = y + neighbor.1
                guard nx >= 0, nx < width, ny >= 0, ny < height else { continue }
                let next = ny * width + nx
                if nextDistance < distances[next] {
                    distances[next] = nextDistance
                    queue.append(next)
                }
            }
        }
        return distances
    }

    static func diskOffsets(radius: Int) -> [(x: Int, y: Int)] {
        let radius = max(0, radius)
        let r2 = radius * radius
        var offsets: [(Int, Int)] = []
        for y in -radius...radius {
            for x in -radius...radius where x * x + y * y <= r2 {
                offsets.append((x, y))
            }
        }
        return offsets
    }

    private func integralCounts() -> [Int] {
        var integral = Array(repeating: 0, count: (width + 1) * (height + 1))
        let stride = width + 1
        for y in 0..<height {
            var rowSum = 0
            for x in 0..<width {
                if self[x, y] {
                    rowSum += 1
                }
                integral[(y + 1) * stride + x + 1] = integral[y * stride + x + 1] + rowSum
            }
        }
        return integral
    }

    private func sum(integral: [Int], x0: Int, y0: Int, x1: Int, y1: Int) -> Int {
        let stride = width + 1
        return integral[y1 * stride + x1]
            - integral[y0 * stride + x1]
            - integral[y1 * stride + x0]
            + integral[y0 * stride + x0]
    }

    private func combined(with other: RasterMask, op: (Bool, Bool) -> Bool) -> RasterMask {
        var out = RasterMask(width: width, height: height)
        let sharedWidth = min(width, other.width)
        let sharedHeight = min(height, other.height)
        guard sharedWidth > 0, sharedHeight > 0 else { return out }
        for y in 0..<sharedHeight {
            for x in 0..<sharedWidth {
                out[x, y] = op(self[x, y], other[x, y])
            }
        }
        return out
    }

    private func squaredDistanceToValue(_ target: Bool, outsideIsTarget: Bool) -> [Double] {
        let inf = Double.greatestFiniteMagnitude / 16.0
        var xPass = Array(repeating: inf, count: width * height)
        for y in 0..<height {
            var row = Array(repeating: inf, count: width)
            for x in 0..<width where self[x, y] == target {
                row[x] = 0.0
            }
            if outsideIsTarget {
                row[0] = min(row[0], 1.0)
                row[width - 1] = min(row[width - 1], 1.0)
            }
            let transformed = Self.distanceTransform1D(row, scale: 1.0)
            for x in 0..<width {
                xPass[y * width + x] = transformed[x]
            }
        }

        var output = Array(repeating: inf, count: width * height)
        for x in 0..<width {
            var column = Array(repeating: inf, count: height)
            for y in 0..<height {
                column[y] = xPass[y * width + x]
            }
            if outsideIsTarget {
                column[0] = min(column[0], 1.0)
                column[height - 1] = min(column[height - 1], 1.0)
            }
            let transformed = Self.distanceTransform1D(column, scale: 1.0)
            for y in 0..<height {
                output[y * width + x] = transformed[y]
            }
        }
        return output
    }

    private static func distanceTransform1D(_ values: [Double], scale: Double) -> [Double] {
        let n = values.count
        guard n > 0 else { return [] }
        let inf = Double.greatestFiniteMagnitude / 16.0
        let sites = (0..<n).filter { values[$0].isFinite && values[$0] < inf * 0.5 }
        guard !sites.isEmpty else { return Array(repeating: inf, count: n) }

        var v = Array(repeating: 0, count: sites.count)
        var z = Array(repeating: 0.0, count: sites.count + 1)
        var k = 0
        v[0] = sites[0]
        z[0] = -inf
        z[1] = inf
        let s2 = scale * scale

        if sites.count > 1 {
            for q in sites.dropFirst() {
                var intersection = -inf
                repeat {
                    let candidate = v[k]
                    let numerator = (values[q] + s2 * Double(q) * Double(q)) - (values[candidate] + s2 * Double(candidate) * Double(candidate))
                    let denominator = 2.0 * s2 * Double(q - candidate)
                    intersection = denominator == 0 ? inf : numerator / denominator
                    if intersection <= z[k], k > 0 {
                        k -= 1
                    } else {
                        break
                    }
                } while true
                k += 1
                v[k] = q
                z[k] = intersection
                z[k + 1] = inf
            }
        }

        k = 0
        var output = Array(repeating: 0.0, count: n)
        for q in 0..<n {
            while z[k + 1] < Double(q) {
                k += 1
            }
            let dx = Double(q - v[k]) * scale
            output[q] = dx * dx + values[v[k]]
        }
        return output
    }
}
