using System.Diagnostics;
using System.Globalization;

namespace SpatialScope.Windows.Updates;

public static class UpdateInstallerLauncher
{
    public const string InstanceMutexName = @"Local\SpatialScope.Windows.Application";
    public const string QaInstanceMutexEnvironmentVariable = "SPATIALSCOPE_QA_INSTANCE_MUTEX";
    public const string QaSmokeLaunchArgument = "--qa-smoke";
    public const string QaSmokeMarkerFileName = ".spatialscope-smoke";
    private const string QaInstanceMutexPrefix = @"Local\SpatialScope.Windows.Application.QA.";
    private const int QaInstanceMutexTokenLength = 32;

    public static string ResolveInstanceMutexName(
        IEnumerable<string> launchArguments,
        string applicationDirectory)
    {
        ArgumentNullException.ThrowIfNull(launchArguments);
        ArgumentException.ThrowIfNullOrWhiteSpace(applicationDirectory);

        if (!launchArguments.Contains(QaSmokeLaunchArgument, StringComparer.Ordinal))
            return InstanceMutexName;

        var requested = Environment.GetEnvironmentVariable(QaInstanceMutexEnvironmentVariable);
        if (!IsValidQaInstanceMutexName(requested))
            return InstanceMutexName;

        try
        {
            var markerPath = Path.Combine(
                Path.GetFullPath(applicationDirectory),
                QaSmokeMarkerFileName);
            return File.Exists(markerPath) ? requested! : InstanceMutexName;
        }
        catch (Exception exception) when (exception is ArgumentException or IOException or NotSupportedException)
        {
            return InstanceMutexName;
        }
    }

    public static bool IsValidQaInstanceMutexName(string? requested)
    {
        if (requested is null
            || requested.Length != QaInstanceMutexPrefix.Length + QaInstanceMutexTokenLength
            || !requested.StartsWith(QaInstanceMutexPrefix, StringComparison.Ordinal))
        {
            return false;
        }

        return requested.AsSpan(QaInstanceMutexPrefix.Length).IndexOfAnyExcept(
            "0123456789abcdefABCDEF") < 0;
    }

    public static Process Start(
        PreparedWindowsUpdate update,
        string updatesRoot,
        int parentProcessId)
    {
        var startInfo = CreateStartInfo(update, updatesRoot, parentProcessId);
        return Process.Start(startInfo)
            ?? throw new InvalidOperationException("Windows did not start the SpatialScope update installer.");
    }

    internal static ProcessStartInfo CreateStartInfo(
        PreparedWindowsUpdate update,
        string updatesRoot,
        int parentProcessId)
    {
        ArgumentNullException.ThrowIfNull(update);
        if (parentProcessId <= 0) throw new ArgumentOutOfRangeException(nameof(parentProcessId));

        var installerPath = Path.GetFullPath(update.InstallerPath);
        var root = Path.GetFullPath(updatesRoot);
        EnsureChildPath(installerPath, root);
        if (!string.Equals(Path.GetFileName(installerPath), GitHubUpdateService.InstallerAssetName, StringComparison.Ordinal))
            throw new InvalidOperationException("The verified update does not have the expected installer name.");
        if (!File.Exists(installerPath)) throw new FileNotFoundException("The verified update installer is missing.", installerPath);

        var startInfo = new ProcessStartInfo
        {
            FileName = installerPath,
            UseShellExecute = true,
            WorkingDirectory = Path.GetDirectoryName(installerPath)!,
        };
        startInfo.ArgumentList.Add("/S");
        startInfo.ArgumentList.Add($"/UPDATEPID={parentProcessId.ToString(CultureInfo.InvariantCulture)}");
        return startInfo;
    }

    private static void EnsureChildPath(string candidate, string expectedParent)
    {
        var relative = Path.GetRelativePath(expectedParent, candidate);
        if (relative is "." or ".."
            || relative.StartsWith($"..{Path.DirectorySeparatorChar}", StringComparison.Ordinal)
            || Path.IsPathRooted(relative))
        {
            throw new InvalidOperationException("The installer path escaped the private SpatialScope update directory.");
        }
    }
}
