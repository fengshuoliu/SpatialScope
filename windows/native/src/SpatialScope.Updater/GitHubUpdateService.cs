using System.Buffers;
using System.Globalization;
using System.IO.Compression;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace SpatialScope.Windows.Updates;

public sealed class GitHubUpdateService : IDisposable
{
    public const string InstallerAssetName = "SpatialScope-Windows-x64-Setup.exe";
    public const string ChecksumAssetName = "SHA256SUMS-Windows.txt";
    public static readonly Uri ReleasesApiUri = new(
        "https://api.github.com/repos/fengshuoliu/SpatialScope/releases?per_page=100");

    private const long MaximumReleaseListBytes = 5 * 1024 * 1024;
    private const long MaximumChecksumBytes = 64 * 1024;
    private const long MaximumInstallerBytes = 2L * 1024 * 1024 * 1024;
    private static readonly Regex WindowsTagPattern = new(
        "^windows-v(?<version>\\d+\\.\\d+\\.\\d+)$",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex Sha256Pattern = new(
        "^[0-9a-f]{64}$",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly HashSet<string> AllowedDownloadHosts = new(StringComparer.OrdinalIgnoreCase)
    {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
        "github-releases.githubusercontent.com",
    };

    private readonly HttpClient _httpClient;
    private readonly bool _ownsHttpClient;
    private readonly Version _currentVersion;

    public GitHubUpdateService(
        Version currentVersion,
        HttpClient? httpClient = null,
        string? updatesRoot = null)
    {
        _currentVersion = NormalizeVersion(currentVersion ?? throw new ArgumentNullException(nameof(currentVersion)));
        _ownsHttpClient = httpClient is null;
        _httpClient = httpClient ?? CreateHttpClient();
        UpdatesRoot = Path.GetFullPath(updatesRoot ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "SpatialScope",
            "Updates"));
    }

    public string UpdatesRoot { get; }

    public async Task<WindowsUpdateRelease?> CheckForUpdateAsync(CancellationToken cancellationToken = default)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(20));

        using var request = new HttpRequestMessage(HttpMethod.Get, ReleasesApiUri);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        request.Headers.UserAgent.ParseAdd($"SpatialScope-Windows/{FormatVersion(_currentVersion)}");
        request.Headers.TryAddWithoutValidation("X-GitHub-Api-Version", "2022-11-28");

        using var response = await _httpClient.SendAsync(
            request,
            HttpCompletionOption.ResponseHeadersRead,
            timeout.Token).ConfigureAwait(false);
        EnsureSuccessfulResponse(response, "GitHub release lookup");
        EnsureContentLength(response.Content.Headers.ContentLength, MaximumReleaseListBytes, "GitHub release list");

        await using var stream = await response.Content.ReadAsStreamAsync(timeout.Token).ConfigureAwait(false);
        using var bounded = new BoundedReadStream(stream, MaximumReleaseListBytes);
        using var document = await JsonDocument.ParseAsync(bounded, cancellationToken: timeout.Token).ConfigureAwait(false);
        return SelectUpdate(document.RootElement, _currentVersion);
    }

    public async Task<PreparedWindowsUpdate> DownloadAndVerifyAsync(
        WindowsUpdateRelease release,
        IProgress<UpdateDownloadProgress>? progress = null,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(release);
        if (release.Version <= _currentVersion)
            throw new InvalidOperationException("The selected release is not newer than this application.");

        ValidateReleaseAssetUri(release.ChecksumUri, release.TagName, ChecksumAssetName);
        ValidateReleaseAssetUri(release.InstallerUri, release.TagName, InstallerAssetName);

        Directory.CreateDirectory(UpdatesRoot);
        var releaseDirectory = GetReleaseDirectory(release.Version);
        Directory.CreateDirectory(releaseDirectory);
        var installerPath = Path.Combine(releaseDirectory, InstallerAssetName);
        var partialPath = installerPath + ".partial";

        var manifest = await DownloadSmallTextAsync(
            release.ChecksumUri,
            MaximumChecksumBytes,
            cancellationToken).ConfigureAwait(false);
        var expectedHash = ParseInstallerChecksum(manifest);
        if (release.GitHubInstallerDigest is { Length: > 0 }
            && !CryptographicOperations.FixedTimeEquals(
                Encoding.ASCII.GetBytes(expectedHash),
                Encoding.ASCII.GetBytes(release.GitHubInstallerDigest)))
        {
            throw new InvalidDataException("The GitHub asset digest does not match the published checksum manifest.");
        }

        if (File.Exists(installerPath))
        {
            if (new FileInfo(installerPath).Length == release.InstallerSize)
            {
                var cachedHash = await ComputeSha256Async(installerPath, cancellationToken).ConfigureAwait(false);
                if (FixedTimeHashEquals(cachedHash, expectedHash))
                    return new PreparedWindowsUpdate(release, installerPath, cachedHash);
            }
            File.Delete(installerPath);
        }

        TryDeleteFile(partialPath);
        try
        {
            var downloadedHash = await DownloadInstallerAsync(
                release.InstallerUri,
                partialPath,
                release.InstallerSize,
                progress,
                cancellationToken).ConfigureAwait(false);
            if (!FixedTimeHashEquals(downloadedHash, expectedHash))
                throw new InvalidDataException("The downloaded installer failed SHA-256 verification.");

            File.Move(partialPath, installerPath, overwrite: true);
            return new PreparedWindowsUpdate(release, installerPath, downloadedHash);
        }
        catch
        {
            TryDeleteFile(partialPath);
            throw;
        }
    }

    public void CleanupOldDownloads(Version currentVersion)
    {
        try
        {
            if (!Directory.Exists(UpdatesRoot)) return;
            foreach (var directory in Directory.EnumerateDirectories(UpdatesRoot))
            {
                var name = Path.GetFileName(directory);
                if (!Version.TryParse(name, out var version) || NormalizeVersion(version) <= NormalizeVersion(currentVersion))
                {
                    try
                    {
                        Directory.Delete(directory, recursive: true);
                    }
                    catch (IOException)
                    {
                        // A helper from the just-completed update may still be exiting.
                    }
                    catch (UnauthorizedAccessException)
                    {
                        // Cleanup is optional and must never block application startup.
                    }
                }
            }
        }
        catch (IOException)
        {
            // Root enumeration is also best-effort startup cleanup.
        }
        catch (UnauthorizedAccessException)
        {
            // Root enumeration is also best-effort startup cleanup.
        }
    }

    public void Dispose()
    {
        if (_ownsHttpClient) _httpClient.Dispose();
    }

    internal static WindowsUpdateRelease? SelectUpdate(JsonElement root, Version currentVersion)
    {
        if (root.ValueKind != JsonValueKind.Array)
            throw new InvalidDataException("GitHub returned an invalid release list.");

        var candidates = new List<WindowsUpdateRelease>();
        foreach (var item in root.EnumerateArray())
        {
            if (TryParseCandidate(item, currentVersion, out var candidate)) candidates.Add(candidate);
        }

        return candidates
            .OrderByDescending(item => item.Version)
            .ThenByDescending(item => item.PublishedAt)
            .ThenByDescending(item => item.ReleaseId)
            .FirstOrDefault();
    }

    internal static string ParseInstallerChecksum(string manifest)
    {
        var matches = new List<string>();
        foreach (var rawLine in manifest.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries))
        {
            var line = rawLine.Trim();
            var separator = line.IndexOfAny([' ', '\t']);
            if (separator <= 0) continue;

            var hash = line[..separator];
            var fileName = line[separator..].TrimStart(' ', '\t');
            if (fileName.StartsWith('*')) fileName = fileName[1..];
            if (string.Equals(fileName, InstallerAssetName, StringComparison.Ordinal)
                && Sha256Pattern.IsMatch(hash))
            {
                matches.Add(hash.ToLowerInvariant());
            }
        }

        return matches.Count == 1
            ? matches[0]
            : throw new InvalidDataException(
                $"The checksum manifest must contain exactly one SHA-256 entry for {InstallerAssetName}.");
    }

    internal string GetReleaseDirectory(Version version)
    {
        var directory = Path.GetFullPath(Path.Combine(UpdatesRoot, FormatVersion(version)));
        EnsureChildPath(directory, UpdatesRoot);
        return directory;
    }

    public static string FormatVersion(Version version)
    {
        var normalized = NormalizeVersion(version);
        return normalized.Revision == 0 ? normalized.ToString(3) : normalized.ToString(4);
    }

    private static HttpClient CreateHttpClient()
    {
        var handler = new HttpClientHandler
        {
            AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate | DecompressionMethods.Brotli,
            AllowAutoRedirect = true,
            MaxAutomaticRedirections = 5,
        };
        return new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
    }

    private static bool TryParseCandidate(
        JsonElement item,
        Version currentVersion,
        out WindowsUpdateRelease candidate)
    {
        candidate = null!;
        if (item.ValueKind != JsonValueKind.Object
            || GetBoolean(item, "draft")
            || GetBoolean(item, "prerelease")
            || !TryGetString(item, "tag_name", out var tagName))
            return false;

        var tagMatch = WindowsTagPattern.Match(tagName);
        if (!tagMatch.Success
            || !Version.TryParse(tagMatch.Groups["version"].Value, out var parsedVersion)
            || !TryGetDateTimeOffset(item, "published_at", out var publishedAt)
            || !item.TryGetProperty("assets", out var assets)
            || assets.ValueKind != JsonValueKind.Array)
            return false;

        var version = NormalizeVersion(parsedVersion);
        if (version <= NormalizeVersion(currentVersion)) return false;

        var installerAssets = FindAssets(assets, InstallerAssetName, tagName).ToList();
        var checksumAssets = FindAssets(assets, ChecksumAssetName, tagName).ToList();
        if (installerAssets.Count != 1 || checksumAssets.Count != 1) return false;

        var installer = installerAssets[0];
        var checksum = checksumAssets[0];
        var installerDigest = TryGetSha256Digest(installer.Element);
        if (installerDigest is null) return false;
        var releaseId = item.TryGetProperty("id", out var idElement) && idElement.TryGetInt64(out var id)
            ? id
            : 0;
        var releasePage = new Uri(
            $"https://github.com/fengshuoliu/SpatialScope/releases/tag/{Uri.EscapeDataString(tagName)}");
        candidate = new WindowsUpdateRelease(
            version,
            tagName,
            publishedAt,
            releaseId,
            installer.Size,
            installer.Uri,
            checksum.Uri,
            releasePage,
            installerDigest);
        return true;
    }

    private static IEnumerable<(JsonElement Element, Uri Uri, long Size)> FindAssets(
        JsonElement assets,
        string expectedName,
        string tagName)
    {
        foreach (var asset in assets.EnumerateArray())
        {
            if (asset.ValueKind != JsonValueKind.Object
                || !TryGetString(asset, "name", out var name)
                || !string.Equals(name, expectedName, StringComparison.Ordinal)
                || !TryGetString(asset, "state", out var state)
                || !string.Equals(state, "uploaded", StringComparison.Ordinal)
                || !asset.TryGetProperty("size", out var sizeElement)
                || !sizeElement.TryGetInt64(out var size)
                || size <= 0
                || !TryGetString(asset, "browser_download_url", out var rawUrl)
                || !Uri.TryCreate(rawUrl, UriKind.Absolute, out var uri))
                continue;

            if (IsValidReleaseAssetUri(uri, tagName, expectedName)) yield return (asset, uri, size);
        }
    }

    private static async Task<string> ComputeSha256Async(string path, CancellationToken cancellationToken)
    {
        await using var stream = new FileStream(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read,
            bufferSize: 1024 * 1024,
            FileOptions.Asynchronous | FileOptions.SequentialScan);
        using var sha256 = SHA256.Create();
        var hash = await sha256.ComputeHashAsync(stream, cancellationToken).ConfigureAwait(false);
        return Convert.ToHexStringLower(hash);
    }

    private async Task<string> DownloadSmallTextAsync(
        Uri uri,
        long maximumBytes,
        CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, uri);
        using var response = await _httpClient.SendAsync(
            request,
            HttpCompletionOption.ResponseHeadersRead,
            cancellationToken).ConfigureAwait(false);
        EnsureSuccessfulResponse(response, "Checksum download");
        ValidateResolvedDownloadUri(response.RequestMessage?.RequestUri);
        EnsureContentLength(response.Content.Headers.ContentLength, maximumBytes, "Checksum file");

        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        using var bounded = new BoundedReadStream(stream, maximumBytes);
        using var reader = new StreamReader(bounded, Encoding.ASCII, detectEncodingFromByteOrderMarks: true);
        return await reader.ReadToEndAsync(cancellationToken).ConfigureAwait(false);
    }

    private async Task<string> DownloadInstallerAsync(
        Uri uri,
        string destinationPath,
        long expectedSize,
        IProgress<UpdateDownloadProgress>? progress,
        CancellationToken cancellationToken)
    {
        if (expectedSize <= 0 || expectedSize > MaximumInstallerBytes)
            throw new InvalidDataException("The GitHub release declared an unexpected installer size.");

        using var request = new HttpRequestMessage(HttpMethod.Get, uri);
        using var response = await _httpClient.SendAsync(
            request,
            HttpCompletionOption.ResponseHeadersRead,
            cancellationToken).ConfigureAwait(false);
        EnsureSuccessfulResponse(response, "Installer download");
        ValidateResolvedDownloadUri(response.RequestMessage?.RequestUri);
        var totalBytes = response.Content.Headers.ContentLength;
        EnsureContentLength(totalBytes, MaximumInstallerBytes, "Windows installer");
        if (totalBytes.HasValue && totalBytes.Value != expectedSize)
            throw new InvalidDataException("The installer response size does not match the GitHub release metadata.");

        await using var source = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        await using var destination = new FileStream(
            destinationPath,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.None,
            bufferSize: 1024 * 1024,
            FileOptions.Asynchronous | FileOptions.SequentialScan);
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = ArrayPool<byte>.Shared.Rent(1024 * 1024);
        long received = 0;
        try
        {
            while (true)
            {
                var count = await source.ReadAsync(buffer.AsMemory(0, buffer.Length), cancellationToken).ConfigureAwait(false);
                if (count == 0) break;
                received = checked(received + count);
                if (received > MaximumInstallerBytes)
                    throw new InvalidDataException("The Windows installer exceeded the maximum permitted size.");
                await destination.WriteAsync(buffer.AsMemory(0, count), cancellationToken).ConfigureAwait(false);
                hash.AppendData(buffer, 0, count);
                progress?.Report(new UpdateDownloadProgress(received, totalBytes));
            }
            await destination.FlushAsync(cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            ArrayPool<byte>.Shared.Return(buffer);
        }

        if (received != expectedSize)
            throw new EndOfStreamException("The Windows installer size does not match the GitHub release metadata.");
        return Convert.ToHexStringLower(hash.GetHashAndReset());
    }

    private static void ValidateReleaseAssetUri(Uri uri, string tagName, string assetName)
    {
        var expectedPath = $"/fengshuoliu/SpatialScope/releases/download/{Uri.EscapeDataString(tagName)}/{Uri.EscapeDataString(assetName)}";
        if (!uri.IsAbsoluteUri
            || !string.Equals(uri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase)
            || !string.Equals(uri.Host, "github.com", StringComparison.OrdinalIgnoreCase)
            || !string.Equals(uri.AbsolutePath, expectedPath, StringComparison.Ordinal)
            || !string.IsNullOrEmpty(uri.Query)
            || !string.IsNullOrEmpty(uri.Fragment))
        {
            throw new InvalidDataException("GitHub returned an unexpected release asset address.");
        }
    }

    private static bool IsValidReleaseAssetUri(Uri uri, string tagName, string assetName)
    {
        try
        {
            ValidateReleaseAssetUri(uri, tagName, assetName);
            return true;
        }
        catch (InvalidDataException)
        {
            return false;
        }
    }

    private static void ValidateResolvedDownloadUri(Uri? uri)
    {
        if (uri is null
            || !uri.IsAbsoluteUri
            || !string.Equals(uri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase)
            || !AllowedDownloadHosts.Contains(uri.Host))
        {
            throw new InvalidDataException("The release download redirected to an untrusted address.");
        }
    }

    private static string? TryGetSha256Digest(JsonElement asset)
    {
        if (!TryGetString(asset, "digest", out var digest)) return null;
        const string prefix = "sha256:";
        if (!digest.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return null;
        var hash = digest[prefix.Length..];
        return Sha256Pattern.IsMatch(hash) ? hash.ToLowerInvariant() : null;
    }

    private static bool FixedTimeHashEquals(string left, string right) =>
        left.Length == right.Length
        && CryptographicOperations.FixedTimeEquals(
            Encoding.ASCII.GetBytes(left.ToLowerInvariant()),
            Encoding.ASCII.GetBytes(right.ToLowerInvariant()));

    private static Version NormalizeVersion(Version version) => new(
        version.Major,
        version.Minor,
        Math.Max(0, version.Build),
        Math.Max(0, version.Revision));

    private static bool GetBoolean(JsonElement element, string propertyName) =>
        element.TryGetProperty(propertyName, out var value) && value.ValueKind == JsonValueKind.True;

    private static bool TryGetString(JsonElement element, string propertyName, out string value)
    {
        value = string.Empty;
        if (!element.TryGetProperty(propertyName, out var property) || property.ValueKind != JsonValueKind.String)
            return false;
        value = property.GetString() ?? string.Empty;
        return !string.IsNullOrWhiteSpace(value);
    }

    private static bool TryGetDateTimeOffset(JsonElement element, string propertyName, out DateTimeOffset value)
    {
        value = default;
        return TryGetString(element, propertyName, out var raw)
            && DateTimeOffset.TryParse(raw, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out value);
    }

    private static void EnsureSuccessfulResponse(HttpResponseMessage response, string operation)
    {
        if (response.IsSuccessStatusCode) return;
        throw new HttpRequestException(
            $"{operation} failed with HTTP {(int)response.StatusCode} ({response.ReasonPhrase}).",
            null,
            response.StatusCode);
    }

    private static void EnsureContentLength(long? contentLength, long maximumBytes, string label)
    {
        if (contentLength.HasValue && (contentLength.Value < 0 || contentLength.Value > maximumBytes))
            throw new InvalidDataException($"{label} has an unexpected size.");
    }

    private static void EnsureChildPath(string candidate, string expectedParent)
    {
        var relative = Path.GetRelativePath(Path.GetFullPath(expectedParent), Path.GetFullPath(candidate));
        if (relative is "." or ".."
            || relative.StartsWith($"..{Path.DirectorySeparatorChar}", StringComparison.Ordinal)
            || Path.IsPathRooted(relative))
        {
            throw new InvalidOperationException("The updater path escaped its private update directory.");
        }
    }

    private static void TryDeleteFile(string path)
    {
        try
        {
            File.Delete(path);
        }
        catch (IOException)
        {
            // Preserve the original exception when cleanup cannot complete.
        }
        catch (UnauthorizedAccessException)
        {
            // Preserve the original exception when cleanup cannot complete.
        }
    }

    private sealed class BoundedReadStream(Stream inner, long maximumBytes) : Stream
    {
        private long _bytesRead;

        public override bool CanRead => inner.CanRead;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => _bytesRead; set => throw new NotSupportedException(); }
        public override void Flush() => throw new NotSupportedException();
        public override int Read(byte[] buffer, int offset, int count)
        {
            var read = inner.Read(buffer, offset, count);
            Track(read);
            return read;
        }
        public override async ValueTask<int> ReadAsync(
            Memory<byte> buffer,
            CancellationToken cancellationToken = default)
        {
            var read = await inner.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
            Track(read);
            return read;
        }
        public override int ReadByte()
        {
            var value = inner.ReadByte();
            if (value >= 0) Track(1);
            return value;
        }
        protected override void Dispose(bool disposing)
        {
            if (disposing) inner.Dispose();
            base.Dispose(disposing);
        }
        public override ValueTask DisposeAsync() => inner.DisposeAsync();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

        private void Track(int count)
        {
            _bytesRead = checked(_bytesRead + count);
            if (_bytesRead > maximumBytes)
                throw new InvalidDataException("The downloaded response exceeded its maximum permitted size.");
        }
    }
}
