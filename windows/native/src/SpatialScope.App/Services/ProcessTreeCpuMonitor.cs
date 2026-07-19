using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows.Threading;

namespace SpatialScope.Windows.Services;

public sealed class ProcessTreeCpuMonitor : INotifyPropertyChanged, IDisposable
{
    private const uint SnapshotProcesses = 0x00000002;
    private readonly DispatcherTimer _timer;
    private readonly int _rootProcessId;
    private readonly Dictionary<int, TimeSpan> _previousCpu = new();
    private DateTime _previousSample = DateTime.UtcNow;
    private double _cpuPercent;

    public ProcessTreeCpuMonitor(int rootProcessId)
    {
        _rootProcessId = rootProcessId;
        _timer = new DispatcherTimer(DispatcherPriority.Background)
        {
            Interval = TimeSpan.FromSeconds(1),
        };
        _timer.Tick += (_, _) => Sample();
    }

    public double CpuPercent
    {
        get => _cpuPercent;
        private set
        {
            if (Math.Abs(_cpuPercent - value) < 0.05) return;
            _cpuPercent = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(CpuPercent)));
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public void Start()
    {
        Sample();
        _timer.Start();
    }

    public void Dispose() => _timer.Stop();

    private void Sample()
    {
        var now = DateTime.UtcNow;
        var elapsed = (now - _previousSample).TotalMilliseconds;
        if (elapsed <= 0) return;

        var ids = EnumerateTree(_rootProcessId);
        var current = new Dictionary<int, TimeSpan>();
        double cpuMilliseconds = 0;
        foreach (var id in ids)
        {
            try
            {
                using var process = Process.GetProcessById(id);
                var total = process.TotalProcessorTime;
                current[id] = total;
                if (_previousCpu.TryGetValue(id, out var previous))
                    cpuMilliseconds += Math.Max(0, (total - previous).TotalMilliseconds);
            }
            catch
            {
                // A short-lived worker may exit between enumeration and sampling.
            }
        }

        _previousCpu.Clear();
        foreach (var pair in current) _previousCpu[pair.Key] = pair.Value;
        _previousSample = now;
        CpuPercent = Math.Clamp(cpuMilliseconds / (elapsed * Environment.ProcessorCount) * 100.0, 0, 100);
    }

    private static HashSet<int> EnumerateTree(int rootProcessId)
    {
        var parents = new Dictionary<int, List<int>>();
        var snapshot = CreateToolhelp32Snapshot(SnapshotProcesses, 0);
        if (snapshot == IntPtr.Zero || snapshot == new IntPtr(-1)) return [rootProcessId];

        try
        {
            var entry = new ProcessEntry32 { Size = (uint)Marshal.SizeOf<ProcessEntry32>() };
            if (Process32First(snapshot, ref entry))
            {
                do
                {
                    var parent = unchecked((int)entry.ParentProcessId);
                    var child = unchecked((int)entry.ProcessId);
                    if (!parents.TryGetValue(parent, out var children)) parents[parent] = children = [];
                    children.Add(child);
                }
                while (Process32Next(snapshot, ref entry));
            }
        }
        finally
        {
            CloseHandle(snapshot);
        }

        var result = new HashSet<int> { rootProcessId };
        var queue = new Queue<int>();
        queue.Enqueue(rootProcessId);
        while (queue.Count > 0)
        {
            var parent = queue.Dequeue();
            if (!parents.TryGetValue(parent, out var children)) continue;
            foreach (var child in children.Where(result.Add)) queue.Enqueue(child);
        }
        return result;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct ProcessEntry32
    {
        public uint Size;
        public uint Usage;
        public uint ProcessId;
        public IntPtr DefaultHeapId;
        public uint ModuleId;
        public uint Threads;
        public uint ParentProcessId;
        public int PriorityClassBase;
        public uint Flags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string ExeFile;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint processId);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool Process32First(IntPtr snapshot, ref ProcessEntry32 entry);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool Process32Next(IntPtr snapshot, ref ProcessEntry32 entry);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr handle);
}
