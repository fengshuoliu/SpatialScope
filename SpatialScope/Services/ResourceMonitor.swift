import Darwin
import Combine
import Foundation
import Metal

@MainActor
final class ResourceMonitor: ObservableObject {
    @Published private(set) var snapshot: ResourceSnapshot

    private var previousTicks: [UInt32]?
    private var timer: Timer?
    private var gpuSampleInFlight = false
    private var lastGPUSampleTime = Date.distantPast

    init() {
        let gpus = Self.detectGPUNames()
        snapshot = ResourceSnapshot(
            cpuCoreCount: ProcessInfo.processInfo.processorCount,
            activeCPUCoreCount: ProcessInfo.processInfo.activeProcessorCount,
            gpuCount: gpus.count,
            gpuNames: gpus,
            cpuUsagePercent: 0,
            gpuUsagePercent: gpus.isEmpty ? 0 : nil
        )
    }

    func start() {
        sample()
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let monitor = self else { return }
            Task { @MainActor in
                monitor.sample()
            }
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func sample() {
        let ticks = Self.cpuTicks()
        let usage: Double
        if let previousTicks {
            let deltas = zip(ticks, previousTicks).map { current, previous in
                current >= previous ? current - previous : 0
            }
            let total = deltas.reduce(UInt32(0), +)
            let idle = deltas.count > CPU_STATE_IDLE ? deltas[Int(CPU_STATE_IDLE)] : 0
            usage = total == 0 ? snapshot.cpuUsagePercent : (100.0 * Double(total - idle) / Double(total))
        } else {
            usage = snapshot.cpuUsagePercent
        }
        previousTicks = ticks
        snapshot.cpuUsagePercent = min(max(usage, 0), 100)
        snapshot.timestamp = Date()
        requestGPUSampleIfNeeded()
    }

    private static func cpuTicks() -> [UInt32] {
        var info = host_cpu_load_info()
        var count = mach_msg_type_number_t(MemoryLayout<host_cpu_load_info_data_t>.stride / MemoryLayout<integer_t>.stride)
        let result = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { rebound in
                host_statistics(mach_host_self(), HOST_CPU_LOAD_INFO, rebound, &count)
            }
        }
        guard result == KERN_SUCCESS else { return [0, 0, 0, 0] }
        return [info.cpu_ticks.0, info.cpu_ticks.1, info.cpu_ticks.2, info.cpu_ticks.3]
    }

    private static func detectGPUNames() -> [String] {
        if #available(macOS 10.13, *) {
            return MTLCopyAllDevices().map(\.name)
        }
        return MTLCreateSystemDefaultDevice().map { [$0.name] } ?? []
    }

    private func requestGPUSampleIfNeeded() {
        guard snapshot.gpuCount > 0,
              !gpuSampleInFlight,
              Date().timeIntervalSince(lastGPUSampleTime) >= 1.0 else {
            return
        }

        gpuSampleInFlight = true
        Task.detached(priority: .utility) {
            let utilization = Self.gpuUtilizationPercentFromIORegistry()
            await MainActor.run {
                if let utilization {
                    self.snapshot.gpuUsagePercent = utilization
                    self.snapshot.timestamp = Date()
                }
                self.lastGPUSampleTime = Date()
                self.gpuSampleInFlight = false
            }
        }
    }

    nonisolated private static func gpuUtilizationPercentFromIORegistry() -> Double? {
        for className in ["AGXAccelerator", "IOAccelerator"] {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/sbin/ioreg")
            process.arguments = ["-r", "-d", "1", "-c", className]

            let outputPipe = Pipe()
            process.standardOutput = outputPipe
            process.standardError = Pipe()

            do {
                try process.run()
                let data = outputPipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                guard process.terminationStatus == 0,
                      let text = String(data: data, encoding: .utf8),
                      let utilization = parseDeviceUtilizationPercent(from: text) else {
                    continue
                }
                return utilization
            } catch {
                continue
            }
        }
        return nil
    }

    nonisolated private static func parseDeviceUtilizationPercent(from text: String) -> Double? {
        let pattern = #""Device Utilization %"\s*=\s*([0-9]+(?:\.[0-9]+)?)"#
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
              match.numberOfRanges > 1,
              let range = Range(match.range(at: 1), in: text),
              let value = Double(text[range]) else {
            return nil
        }
        return min(max(value, 0), 100)
    }
}
