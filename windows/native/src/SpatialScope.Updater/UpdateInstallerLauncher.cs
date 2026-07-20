using System.Diagnostics;
using System.Globalization;

namespace SpatialScope.Windows.Updates;

public static class UpdateInstallerLauncher
{
    public const string InstanceMutexName = @"Local\SpatialScope.Windows.Application";

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
