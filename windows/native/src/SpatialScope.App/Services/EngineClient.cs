using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO;
using System.Text.Json;

namespace SpatialScope.Windows.Services;

public sealed record EngineProgress(string RequestId, double Value, string Message);

public sealed class EngineClient : IAsyncDisposable
{
    private static readonly TimeSpan ShutdownWriteTimeout = TimeSpan.FromMilliseconds(500);
    private static readonly TimeSpan GracefulShutdownTimeout = TimeSpan.FromSeconds(2);
    private static readonly TimeSpan ForcedShutdownTimeout = TimeSpan.FromSeconds(1);

    private readonly ConcurrentDictionary<string, TaskCompletionSource<JsonElement>> _pending = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private Process? _process;
    private StreamWriter? _stdin;
    private Task? _readLoop;
    private int _disposeState;

    public event EventHandler<EngineProgress>? ProgressChanged;
    public event EventHandler<string>? EngineMessage;

    public Process? Process => _process is { HasExited: false } ? _process : null;
    public bool IsRunning => Process is not null;
    public int DefaultCpuWorkers { get; private set; } = Math.Max(1, Environment.ProcessorCount);
    public IReadOnlyList<string> DetectedGpus { get; private set; } = [];
    public string? AnalysisGpuBackend { get; private set; }

    public async Task StartAsync(CancellationToken cancellationToken = default)
    {
        ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposeState) != 0, this);
        if (IsRunning) return;

        var (executable, arguments, workingDirectory) = ResolveLaunchCommand();
        var startInfo = new ProcessStartInfo
        {
            FileName = executable,
            Arguments = arguments,
            WorkingDirectory = workingDirectory,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        startInfo.Environment["PYTHONUTF8"] = "1";
        startInfo.Environment["MPLBACKEND"] = "Agg";
        var cpuCount = Math.Max(1, Environment.ProcessorCount).ToString();
        startInfo.Environment["SPATIALSCOPE_CPU_WORKERS"] = cpuCount;
        startInfo.Environment["SPATIALSCOPE_GPU_MODE"] = "auto";
        startInfo.Environment["OMP_NUM_THREADS"] = cpuCount;
        startInfo.Environment["MKL_NUM_THREADS"] = cpuCount;
        startInfo.Environment["OPENBLAS_NUM_THREADS"] = cpuCount;
        startInfo.Environment["NUMEXPR_NUM_THREADS"] = cpuCount;
        startInfo.Environment["NUMBA_NUM_THREADS"] = cpuCount;
        startInfo.Environment["NUMBA_THREADING_LAYER"] = "omp";

        _process = Process.Start(startInfo) ?? throw new InvalidOperationException("Could not start the SpatialScope analysis engine.");
        if (Volatile.Read(ref _disposeState) != 0)
        {
            try { _process.Kill(entireProcessTree: true); } catch { }
            try { _process.Dispose(); } catch { }
            _process = null;
            throw new ObjectDisposedException(nameof(EngineClient));
        }
        _stdin = _process.StandardInput;
        _stdin.AutoFlush = true;
        _readLoop = ReadLoopAsync(_process.StandardOutput, cancellationToken);
        _ = ReadErrorsAsync(_process.StandardError, cancellationToken);

        var hello = await SendAsync("hello", new { }, cancellationToken);
        if (!hello.TryGetProperty("protocolVersion", out _))
        {
            throw new InvalidDataException("The analysis engine returned an invalid handshake.");
        }
        if (hello.TryGetProperty("compute", out var compute) && compute.ValueKind == JsonValueKind.Object)
        {
            if (compute.TryGetProperty("defaultCpuWorkers", out var workers) && workers.TryGetInt32(out var cpuWorkers))
                DefaultCpuWorkers = Math.Max(1, cpuWorkers);
            if (compute.TryGetProperty("detectedGpus", out var gpus) && gpus.ValueKind == JsonValueKind.Array)
                DetectedGpus = gpus.EnumerateArray().Select(item => item.GetString() ?? string.Empty).Where(item => item.Length > 0).ToArray();
            if (compute.TryGetProperty("analysisGpuBackend", out var gpuBackend) && gpuBackend.ValueKind == JsonValueKind.String)
                AnalysisGpuBackend = gpuBackend.GetString();
        }
    }

    public async Task<JsonElement> SendAsync(string command, object payload, CancellationToken cancellationToken = default)
    {
        ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposeState) != 0, this);
        if (!IsRunning) await StartAsync(cancellationToken);

        var requestId = Guid.NewGuid().ToString("N");
        var completion = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!_pending.TryAdd(requestId, completion)) throw new InvalidOperationException("Could not allocate an engine request.");

        var request = JsonSerializer.Serialize(new { id = requestId, command, payload });
        await _writeLock.WaitAsync(cancellationToken);
        try
        {
            await _stdin!.WriteLineAsync(request.AsMemory(), cancellationToken);
        }
        finally
        {
            _writeLock.Release();
        }

        using var registration = cancellationToken.Register(() => completion.TrySetCanceled(cancellationToken));
        try
        {
            return await completion.Task;
        }
        finally
        {
            _pending.TryRemove(requestId, out _);
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref _disposeState, 1) != 0) return;

        var process = _process;
        try
        {
            if (IsProcessRunning(process))
            {
                await TryRequestShutdownAsync(process!);
                await WaitForExitAsync(process!, GracefulShutdownTimeout);
            }
        }
        catch
        {
            // Shutdown is best effort. The owned process tree is terminated below.
        }
        finally
        {
            await TerminateProcessTreeAsync(process);

            var stopped = new ObjectDisposedException(nameof(EngineClient), "The analysis engine was stopped because SpatialScope is closing.");
            foreach (var completion in _pending.Values)
                completion.TrySetException(stopped);
            _pending.Clear();

            if (_readLoop is not null)
            {
                try
                {
                    await _readLoop.WaitAsync(ForcedShutdownTimeout);
                }
                catch
                {
                    // The process exit can race a redirected-stream read during disposal.
                }
            }

            try { _stdin?.Dispose(); } catch { }
            try { process?.Dispose(); } catch { }
            _stdin = null;
            _process = null;
            _readLoop = null;
            _writeLock.Dispose();
        }
    }

    private async Task TryRequestShutdownAsync(Process process)
    {
        using var timeout = new CancellationTokenSource(ShutdownWriteTimeout);
        var lockTaken = false;
        try
        {
            await _writeLock.WaitAsync(timeout.Token);
            lockTaken = true;

            if (!IsProcessRunning(process) || _stdin is null) return;
            var request = JsonSerializer.Serialize(new
            {
                id = Guid.NewGuid().ToString("N"),
                command = "shutdown",
                payload = new { },
            });
            await _stdin.WriteLineAsync(request.AsMemory(), timeout.Token);
            await _stdin.FlushAsync(timeout.Token);
        }
        catch
        {
            // A busy or broken pipe must not delay application shutdown.
        }
        finally
        {
            if (lockTaken) _writeLock.Release();
        }
    }

    private static bool IsProcessRunning(Process? process)
    {
        if (process is null) return false;
        try { return !process.HasExited; }
        catch { return false; }
    }

    private static async Task<bool> WaitForExitAsync(Process process, TimeSpan timeout)
    {
        if (!IsProcessRunning(process)) return true;
        try
        {
            await process.WaitForExitAsync().WaitAsync(timeout);
            return true;
        }
        catch
        {
            return !IsProcessRunning(process);
        }
    }

    private static async Task TerminateProcessTreeAsync(Process? process)
    {
        if (!IsProcessRunning(process)) return;

        try { process!.Kill(entireProcessTree: true); } catch { }
        await WaitForExitAsync(process!, ForcedShutdownTimeout);
    }

    private async Task ReadLoopAsync(StreamReader reader, CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            var line = await reader.ReadLineAsync(cancellationToken);
            if (line is null) break;
            if (string.IsNullOrWhiteSpace(line)) continue;

            try
            {
                using var document = JsonDocument.Parse(line);
                var root = document.RootElement;
                var type = root.GetProperty("type").GetString();
                var requestId = root.TryGetProperty("id", out var idElement) ? idElement.GetString() ?? string.Empty : string.Empty;
                switch (type)
                {
                    case "progress":
                        ProgressChanged?.Invoke(this, new EngineProgress(
                            requestId,
                            root.TryGetProperty("value", out var value) ? value.GetDouble() : 0,
                            root.TryGetProperty("message", out var message) ? message.GetString() ?? string.Empty : string.Empty));
                        break;
                    case "result":
                        if (_pending.TryGetValue(requestId, out var resultCompletion))
                            resultCompletion.TrySetResult(root.GetProperty("data").Clone());
                        break;
                    case "error":
                        if (_pending.TryGetValue(requestId, out var errorCompletion))
                        {
                            var error = root.TryGetProperty("message", out var errorMessage)
                                ? errorMessage.GetString() ?? "Analysis failed."
                                : "Analysis failed.";
                            errorCompletion.TrySetException(new InvalidOperationException(error));
                        }
                        break;
                    case "event":
                        EngineMessage?.Invoke(this, root.TryGetProperty("message", out var eventMessage)
                            ? eventMessage.GetString() ?? string.Empty
                            : string.Empty);
                        break;
                }
            }
            catch (Exception exception)
            {
                EngineMessage?.Invoke(this, $"Engine protocol warning: {exception.Message}");
            }
        }

        var exitCode = _process is { HasExited: true } ? _process.ExitCode : -1;
        foreach (var completion in _pending.Values)
            completion.TrySetException(new InvalidOperationException($"The analysis engine stopped unexpectedly (exit code {exitCode})."));
    }

    private async Task ReadErrorsAsync(StreamReader reader, CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            var line = await reader.ReadLineAsync(cancellationToken);
            if (line is null) return;
            if (!string.IsNullOrWhiteSpace(line)) EngineMessage?.Invoke(this, line);
        }
    }

    private static (string Executable, string Arguments, string WorkingDirectory) ResolveLaunchCommand()
    {
        var baseDirectory = AppContext.BaseDirectory;
        var packagedCandidates = new[]
        {
            Path.Combine(baseDirectory, "engine", "SpatialScopeEngine.exe"),
            Path.Combine(baseDirectory, "SpatialScopeEngine.exe"),
        };
        var packaged = packagedCandidates.FirstOrDefault(File.Exists);
        if (packaged is not null) return (packaged, "--json-lines", Path.GetDirectoryName(packaged)!);

        var cursor = new DirectoryInfo(baseDirectory);
        for (var depth = 0; depth < 10 && cursor is not null; depth++, cursor = cursor.Parent)
        {
            var script = Path.Combine(cursor.FullName, "windows", "backend", "native_engine.py");
            if (!File.Exists(script)) continue;
            var venvPython = Path.Combine(cursor.FullName, "windows", ".venv", "Scripts", "python.exe");
            var python = File.Exists(venvPython) ? venvPython : "python";
            return (python, $"\"{script}\" --json-lines", Path.GetDirectoryName(script)!);
        }

        var configuredEngine = Environment.GetEnvironmentVariable("SPATIALSCOPE_ENGINE_PATH");
        if (!string.IsNullOrWhiteSpace(configuredEngine) && File.Exists(configuredEngine))
            return (configuredEngine, "--json-lines", Path.GetDirectoryName(configuredEngine)!);

        throw new FileNotFoundException("SpatialScopeEngine.exe or windows/backend/native_engine.py was not found.");
    }
}
