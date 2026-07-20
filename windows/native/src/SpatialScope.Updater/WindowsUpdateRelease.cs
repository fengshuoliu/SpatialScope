namespace SpatialScope.Windows.Updates;

public sealed record WindowsUpdateRelease(
    Version Version,
    string TagName,
    DateTimeOffset PublishedAt,
    long ReleaseId,
    long InstallerSize,
    Uri InstallerUri,
    Uri ChecksumUri,
    Uri ReleasePageUri,
    string? GitHubInstallerDigest);

public sealed record PreparedWindowsUpdate(
    WindowsUpdateRelease Release,
    string InstallerPath,
    string Sha256);

public readonly record struct UpdateDownloadProgress(long BytesReceived, long? TotalBytes)
{
    public double? Fraction => TotalBytes is > 0
        ? Math.Clamp((double)BytesReceived / TotalBytes.Value, 0, 1)
        : null;
}
