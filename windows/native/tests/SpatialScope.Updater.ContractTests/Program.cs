using SpatialScope.Windows.Updates;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

var tests = new List<(string Name, Func<Task> Run)>
{
    ("release selection isolates the stable Windows channel", ReleaseSelectionIsolatesWindowsChannelAsync),
    ("same and older versions are ignored", SameAndOlderVersionsAreIgnoredAsync),
    ("invalid release asset contracts fail closed", InvalidReleaseContractsFailClosedAsync),
    ("GitHub API request uses required headers", ApiRequestUsesRequiredHeadersAsync),
    ("verified installer downloads atomically", VerifiedInstallerDownloadsAtomicallyAsync),
    ("checksum mismatch removes partial download", ChecksumMismatchRemovesPartialDownloadAsync),
    ("verified cached installer is reused", VerifiedCachedInstallerIsReusedAsync),
    ("checksum parsing is strict", ChecksumParsingIsStrictAsync),
    ("automatic check state is throttled", AutomaticCheckStateIsThrottledAsync),
    ("installer launch arguments are structured and scoped", InstallerLaunchArgumentsAreStructuredAndScopedAsync),
    ("QA instance mutex override is isolated and validated", QaInstanceMutexOverrideIsIsolatedAndValidatedAsync),
};
if (args.Contains("--live-github", StringComparer.OrdinalIgnoreCase))
    tests.Add(("live GitHub feed matches the Windows release contract", LiveGitHubFeedMatchesContractAsync));

var failures = new List<string>();
foreach (var test in tests)
{
    try
    {
        await test.Run();
        Console.WriteLine($"PASS {test.Name}");
    }
    catch (Exception exception)
    {
        failures.Add($"FAIL {test.Name}: {exception}");
        Console.Error.WriteLine(failures[^1]);
    }
}

if (failures.Count > 0)
{
    Console.Error.WriteLine($"{failures.Count} updater contract test(s) failed.");
    return 1;
}

Console.WriteLine($"All {tests.Count} updater contract tests passed.");
return 0;

static async Task ReleaseSelectionIsolatesWindowsChannelAsync()
{
    const string hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    var releases = new object[]
    {
        Release("v9.0.0", "2026-08-10T00:00:00Z", 90, false, false,
            Asset("SpatialScope-macOS-universal.dmg", "v9.0.0", 100, hash)),
        Release("windows-v9.0.0", "2026-08-09T00:00:00Z", 89, true, false,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v9.0.0", 100, hash),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v9.0.0", 100, hash)),
        Release("windows-v8.0.0", "2026-08-08T00:00:00Z", 88, false, true,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v8.0.0", 100, hash),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v8.0.0", 100, hash)),
        Release("windows-v2.0.0", "2026-08-07T00:00:00Z", 87, false, false,
            Asset("SpatialScope-Windows-x64-Portable-2.0.0.zip", "windows-v2.0.0", 100, hash),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v2.0.0", 100, hash)),
        Release("windows-v1.3", "2026-08-07T00:00:00Z", 87, false, false,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v1.3", 100, hash),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v1.3", 100, hash)),
        Release("windows-v1.2.6", "2026-08-06T00:00:00Z", 86, false, false,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v1.2.6", 100, hash),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v1.2.6", 100, hash)),
        Release("windows-v1.2.10", "2026-08-05T00:00:00Z", 85, false, false,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v1.2.10", 100, hash),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v1.2.10", 100, hash)),
    };

    using var document = JsonDocument.Parse(JsonSerializer.Serialize(releases));
    var update = GitHubUpdateService.SelectUpdate(document.RootElement, new Version(1, 2, 5, 0));
    Assert(update is not null, "Expected a valid Windows update.");
    Assert(update!.Version == new Version(1, 2, 10, 0), "Numeric version ordering did not choose 1.2.10.");
    Assert(update.TagName == "windows-v1.2.10", "Selected the wrong platform release.");
    await Task.CompletedTask;
}

static async Task SameAndOlderVersionsAreIgnoredAsync()
{
    const string hash = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    var releases = new[]
    {
        Release("windows-v1.2.5", "2026-08-03T00:00:00Z", 2, false, false,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v1.2.5", 100, hash),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v1.2.5", 100, hash)),
        Release("windows-v1.2.4", "2026-08-02T00:00:00Z", 1, false, false,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v1.2.4", 100, hash),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v1.2.4", 100, hash)),
    };
    using var document = JsonDocument.Parse(JsonSerializer.Serialize(releases));
    Assert(
        GitHubUpdateService.SelectUpdate(document.RootElement, new Version(1, 2, 5, 0)) is null,
        "Same or older releases must not be offered.");
    await Task.CompletedTask;
}

static async Task InvalidReleaseContractsFailClosedAsync()
{
    const string hash = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
    var releases = new object[]
    {
        Release("windows-v1.3.0", "2026-08-09T00:00:00Z", 5, false, false,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v1.3.0", 100, null),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v1.3.0", 100, hash)),
        Release("windows-v1.2.9", "2026-08-08T00:00:00Z", 4, false, false,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v1.2.9", 100, hash, "https://example.com/update.exe"),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v1.2.9", 100, hash)),
        Release("windows-v1.2.8", "2026-08-07T00:00:00Z", 3, false, false,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v1.2.8", 100, hash),
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v1.2.8", 100, hash),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v1.2.8", 100, hash)),
        Release("windows-v1.2.7", "2026-08-06T00:00:00Z", 2, false, false,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v1.2.7", 100, hash)),
        Release("windows-v1.2.6-beta", "2026-08-05T00:00:00Z", 1, false, false,
            Asset(GitHubUpdateService.InstallerAssetName, "windows-v1.2.6-beta", 100, hash),
            Asset(GitHubUpdateService.ChecksumAssetName, "windows-v1.2.6-beta", 100, hash)),
    };
    using var document = JsonDocument.Parse(JsonSerializer.Serialize(releases));
    Assert(
        GitHubUpdateService.SelectUpdate(document.RootElement, new Version(1, 2, 5, 0)) is null,
        "A malformed, unsigned, duplicated, or incomplete release must fail closed.");
    await Task.CompletedTask;
}

static async Task ApiRequestUsesRequiredHeadersAsync()
{
    HttpRequestMessage? captured = null;
    var handler = new StubHttpMessageHandler(request =>
    {
        captured = request;
        return JsonResponse("[]");
    });
    using var client = new HttpClient(handler);
    using var service = new GitHubUpdateService(new Version(1, 2, 5, 0), client);
    var update = await service.CheckForUpdateAsync();

    Assert(update is null, "An empty release list should produce no update.");
    Assert(captured?.RequestUri == GitHubUpdateService.ReleasesApiUri, "The updater used the wrong GitHub endpoint.");
    Assert(captured!.Headers.UserAgent.ToString().StartsWith("SpatialScope-Windows/1.2.5", StringComparison.Ordinal), "Missing updater User-Agent.");
    Assert(captured.Headers.Accept.Any(item => item.MediaType == "application/vnd.github+json"), "Missing GitHub API Accept header.");
    Assert(captured.Headers.TryGetValues("X-GitHub-Api-Version", out var values)
           && values.Single() == "2022-11-28", "Missing GitHub API version header.");
}

static async Task VerifiedInstallerDownloadsAtomicallyAsync()
{
    var installerBytes = Encoding.UTF8.GetBytes("verified SpatialScope installer bytes");
    var hash = Convert.ToHexStringLower(SHA256.HashData(installerBytes));
    var release = CreateDownloadRelease(new Version(1, 2, 6, 0), hash, installerBytes.Length);
    var requests = new List<string>();
    var handler = new StubHttpMessageHandler(request =>
    {
        requests.Add(request.RequestUri!.AbsolutePath);
        if (request.RequestUri.AbsolutePath.EndsWith(GitHubUpdateService.ChecksumAssetName, StringComparison.Ordinal))
            return TextResponse($"{hash}  {GitHubUpdateService.InstallerAssetName}\r\n");
        return BytesResponse(installerBytes);
    });

    await WithTemporaryDirectoryAsync(async root =>
    {
        using var client = new HttpClient(handler);
        using var service = new GitHubUpdateService(new Version(1, 2, 5, 0), client, root);
        var progressValues = new List<UpdateDownloadProgress>();
        var prepared = await service.DownloadAndVerifyAsync(
            release,
            new SynchronousProgress<UpdateDownloadProgress>(progressValues.Add));

        Assert(File.Exists(prepared.InstallerPath), "Verified installer was not atomically promoted.");
        Assert(!File.Exists(prepared.InstallerPath + ".partial"), "Partial installer remained after success.");
        Assert(File.ReadAllBytes(prepared.InstallerPath).SequenceEqual(installerBytes), "Installer bytes changed during staging.");
        Assert(prepared.Sha256 == hash, "Prepared installer reported the wrong hash.");
        Assert(requests.Count == 2, "Expected exactly one checksum and one installer request.");
        Assert(progressValues.Count > 0 && progressValues[^1].BytesReceived == installerBytes.Length, "Download progress did not reach the complete byte count.");
    });
}

static async Task ChecksumMismatchRemovesPartialDownloadAsync()
{
    var installerBytes = Encoding.UTF8.GetBytes("tampered installer bytes");
    var expectedHash = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes("expected installer bytes")));
    var release = CreateDownloadRelease(new Version(1, 2, 6, 0), expectedHash, installerBytes.Length);
    var handler = new StubHttpMessageHandler(request =>
        request.RequestUri!.AbsolutePath.EndsWith(GitHubUpdateService.ChecksumAssetName, StringComparison.Ordinal)
            ? TextResponse($"{expectedHash}  {GitHubUpdateService.InstallerAssetName}\n")
            : BytesResponse(installerBytes));

    await WithTemporaryDirectoryAsync(async root =>
    {
        using var client = new HttpClient(handler);
        using var service = new GitHubUpdateService(new Version(1, 2, 5, 0), client, root);
        await AssertThrowsAsync<InvalidDataException>(() => service.DownloadAndVerifyAsync(release));
        var releaseDirectory = service.GetReleaseDirectory(release.Version);
        Assert(!File.Exists(Path.Combine(releaseDirectory, GitHubUpdateService.InstallerAssetName)), "Unverified installer remained on disk.");
        Assert(!File.Exists(Path.Combine(releaseDirectory, GitHubUpdateService.InstallerAssetName) + ".partial"), "Partial installer was not removed.");
    });
}

static async Task VerifiedCachedInstallerIsReusedAsync()
{
    var installerBytes = Encoding.UTF8.GetBytes("cached verified installer");
    var hash = Convert.ToHexStringLower(SHA256.HashData(installerBytes));
    var release = CreateDownloadRelease(new Version(1, 2, 6, 0), hash, installerBytes.Length);
    var installerRequests = 0;
    var handler = new StubHttpMessageHandler(request =>
    {
        if (request.RequestUri!.AbsolutePath.EndsWith(GitHubUpdateService.ChecksumAssetName, StringComparison.Ordinal))
            return TextResponse($"{hash}  {GitHubUpdateService.InstallerAssetName}\n");
        installerRequests++;
        return BytesResponse(installerBytes);
    });

    await WithTemporaryDirectoryAsync(async root =>
    {
        using var client = new HttpClient(handler);
        using var service = new GitHubUpdateService(new Version(1, 2, 5, 0), client, root);
        var directory = service.GetReleaseDirectory(release.Version);
        Directory.CreateDirectory(directory);
        await File.WriteAllBytesAsync(Path.Combine(directory, GitHubUpdateService.InstallerAssetName), installerBytes);
        var prepared = await service.DownloadAndVerifyAsync(release);
        Assert(File.Exists(prepared.InstallerPath), "Cached installer was not reused.");
        Assert(installerRequests == 0, "A valid cached installer was downloaded again.");
    });
}

static async Task ChecksumParsingIsStrictAsync()
{
    const string hash = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd";
    Assert(
        GitHubUpdateService.ParseInstallerChecksum($"{hash.ToUpperInvariant()} *{GitHubUpdateService.InstallerAssetName}\r\n") == hash,
        "Uppercase checksum or binary marker was not accepted.");
    AssertThrows<InvalidDataException>(() => GitHubUpdateService.ParseInstallerChecksum(
        $"{hash}  other.exe\n"));
    AssertThrows<InvalidDataException>(() => GitHubUpdateService.ParseInstallerChecksum(
        $"{hash}  {GitHubUpdateService.InstallerAssetName}\n{hash}  {GitHubUpdateService.InstallerAssetName}\n"));
    AssertThrows<InvalidDataException>(() => GitHubUpdateService.ParseInstallerChecksum(
        $"abcd  {GitHubUpdateService.InstallerAssetName}\n"));
    await Task.CompletedTask;
}

static async Task AutomaticCheckStateIsThrottledAsync()
{
    await WithTemporaryDirectoryAsync(async root =>
    {
        var path = Path.Combine(root, "state", "update-state.json");
        var store = new UpdateCheckStateStore(path);
        var now = new DateTimeOffset(2026, 8, 1, 12, 0, 0, TimeSpan.Zero);
        Assert(store.IsAutomaticCheckDue(now, TimeSpan.FromHours(24)), "A first automatic check should be due.");
        store.RecordSuccessfulCheck(now);
        Assert(!store.IsAutomaticCheckDue(now.AddHours(23), TimeSpan.FromHours(24)), "Automatic check was not throttled.");
        Assert(store.IsAutomaticCheckDue(now.AddHours(24), TimeSpan.FromHours(24)), "Automatic check did not become due after the interval.");
        Assert(store.IsAutomaticCheckDue(now.AddHours(-1), TimeSpan.FromHours(24)), "A future timestamp should not suppress checks indefinitely.");
        await Task.CompletedTask;
    });
}

static async Task InstallerLaunchArgumentsAreStructuredAndScopedAsync()
{
    const string hash = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
    var release = CreateDownloadRelease(new Version(1, 2, 6, 0), hash, 3);
    await WithTemporaryDirectoryAsync(async root =>
    {
        var versionDirectory = Path.Combine(root, "1.2.6");
        Directory.CreateDirectory(versionDirectory);
        var installer = Path.Combine(versionDirectory, GitHubUpdateService.InstallerAssetName);
        await File.WriteAllBytesAsync(installer, [1, 2, 3]);
        var prepared = new PreparedWindowsUpdate(release, installer, hash);
        var startInfo = UpdateInstallerLauncher.CreateStartInfo(prepared, root, 4321);
        Assert(startInfo.UseShellExecute, "Installer should use the Windows shell.");
        Assert(startInfo.FileName == Path.GetFullPath(installer), "Installer path was not absolute.");
        Assert(startInfo.ArgumentList.SequenceEqual(["/S", "/UPDATEPID=4321"]), "Updater arguments were not structured correctly.");

        var escapedDirectory = Path.Combine(Path.GetDirectoryName(root)!, Path.GetFileName(root) + "-outside");
        Directory.CreateDirectory(escapedDirectory);
        try
        {
            var escapedInstaller = Path.Combine(escapedDirectory, GitHubUpdateService.InstallerAssetName);
            await File.WriteAllBytesAsync(escapedInstaller, [1]);
            var escaped = new PreparedWindowsUpdate(release, escapedInstaller, hash);
            AssertThrows<InvalidOperationException>(() => UpdateInstallerLauncher.CreateStartInfo(escaped, root, 4321));
        }
        finally
        {
            Directory.Delete(escapedDirectory, recursive: true);
        }
    });
}

static async Task QaInstanceMutexOverrideIsIsolatedAndValidatedAsync()
{
    var variable = UpdateInstallerLauncher.QaInstanceMutexEnvironmentVariable;
    var previous = Environment.GetEnvironmentVariable(variable);
    await WithTemporaryDirectoryAsync(async root =>
    {
        var isolated = $@"Local\SpatialScope.Windows.Application.QA.{Guid.NewGuid():N}";
        var markerPath = Path.Combine(root, UpdateInstallerLauncher.QaSmokeMarkerFileName);
        await File.WriteAllTextAsync(markerPath, "installer smoke test");

        try
        {
            Environment.SetEnvironmentVariable(variable, isolated);
            Assert(
                UpdateInstallerLauncher.ResolveInstanceMutexName(
                    [UpdateInstallerLauncher.QaSmokeLaunchArgument],
                    root) == isolated,
                "A marked, explicitly launched QA app did not select its isolated mutex.");
            Assert(
                UpdateInstallerLauncher.ResolveInstanceMutexName([], root)
                    == UpdateInstallerLauncher.InstanceMutexName,
                "A QA marker and inherited environment variable bypassed the explicit launch gate.");

            File.Delete(markerPath);
            Assert(
                UpdateInstallerLauncher.ResolveInstanceMutexName(
                    [UpdateInstallerLauncher.QaSmokeLaunchArgument],
                    root) == UpdateInstallerLauncher.InstanceMutexName,
                "A launch argument and inherited environment variable bypassed the installer marker gate.");

            await File.WriteAllTextAsync(markerPath, "installer smoke test");
            foreach (var invalid in new[]
                     {
                         @"Local\Unrelated.Application",
                         @"Local\SpatialScope.Windows.Application.QA.0123456789abcdef0123456789abcde\",
                         @"Local\SpatialScope.Windows.Application.QA.0123456789abcdef0123456789abcdef0",
                     })
            {
                Environment.SetEnvironmentVariable(variable, invalid);
                Assert(
                    UpdateInstallerLauncher.ResolveInstanceMutexName(
                        [UpdateInstallerLauncher.QaSmokeLaunchArgument],
                        root) == UpdateInstallerLauncher.InstanceMutexName,
                    $"An invalid QA mutex name was accepted: {invalid}");
                Assert(
                    !UpdateInstallerLauncher.IsValidQaInstanceMutexName(invalid),
                    $"QA mutex validation accepted an invalid name: {invalid}");
            }
        }
        finally
        {
            Environment.SetEnvironmentVariable(variable, previous);
        }
    });
}

static async Task LiveGitHubFeedMatchesContractAsync()
{
    using var service = new GitHubUpdateService(new Version(1, 2, 3, 0));
    var release = await service.CheckForUpdateAsync();
    Assert(release is not null, "The live feed did not return a stable Windows update newer than 1.2.3.");
    Assert(release!.TagName.StartsWith("windows-v", StringComparison.Ordinal), "The live feed selected a non-Windows tag.");
    Assert(release.Version >= new Version(1, 2, 4, 0), "The live feed selected an unexpectedly old Windows release.");
    Assert(release.InstallerSize > 0, "The live installer has no declared size.");
    Assert(release.GitHubInstallerDigest is { Length: 64 }, "The live installer has no valid GitHub SHA-256 digest.");
    Assert(release.InstallerUri.Host == "github.com", "The live installer URL has an unexpected host.");
}

static object Release(string tag, string publishedAt, long id, bool draft, bool prerelease, params object[] assets) => new
{
    id,
    tag_name = tag,
    published_at = publishedAt,
    draft,
    prerelease,
    assets,
};

static object Asset(string name, string tag, long size, string? hash, string? url = null) => new
{
    name,
    state = "uploaded",
    size,
    browser_download_url = url ?? $"https://github.com/fengshuoliu/SpatialScope/releases/download/{tag}/{name}",
    digest = hash is null ? null : $"sha256:{hash}",
};

static WindowsUpdateRelease CreateDownloadRelease(Version version, string hash, long installerSize)
{
    var tag = $"windows-v{GitHubUpdateService.FormatVersion(version)}";
    var root = $"https://github.com/fengshuoliu/SpatialScope/releases";
    return new WindowsUpdateRelease(
        version,
        tag,
        DateTimeOffset.UtcNow,
        1,
        installerSize,
        new Uri($"{root}/download/{tag}/{GitHubUpdateService.InstallerAssetName}"),
        new Uri($"{root}/download/{tag}/{GitHubUpdateService.ChecksumAssetName}"),
        new Uri($"{root}/tag/{tag}"),
        hash);
}

static HttpResponseMessage JsonResponse(string json) => new(HttpStatusCode.OK)
{
    Content = new StringContent(json, Encoding.UTF8, "application/json"),
};

static HttpResponseMessage TextResponse(string text) => new(HttpStatusCode.OK)
{
    Content = new StringContent(text, Encoding.ASCII, "text/plain"),
};

static HttpResponseMessage BytesResponse(byte[] bytes) => new(HttpStatusCode.OK)
{
    Content = new ByteArrayContent(bytes),
};

static async Task WithTemporaryDirectoryAsync(Func<string, Task> action)
{
    var root = Path.Combine(Path.GetTempPath(), $"SpatialScope-updater-tests-{Guid.NewGuid():N}");
    Directory.CreateDirectory(root);
    try
    {
        await action(root);
    }
    finally
    {
        if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
    }
}

static void Assert(bool condition, string message)
{
    if (!condition) throw new InvalidOperationException(message);
}

static void AssertThrows<TException>(Action action) where TException : Exception
{
    try
    {
        action();
    }
    catch (TException)
    {
        return;
    }
    throw new InvalidOperationException($"Expected {typeof(TException).Name}.");
}

static async Task AssertThrowsAsync<TException>(Func<Task> action) where TException : Exception
{
    try
    {
        await action();
    }
    catch (TException)
    {
        return;
    }
    throw new InvalidOperationException($"Expected {typeof(TException).Name}.");
}

sealed class StubHttpMessageHandler(Func<HttpRequestMessage, HttpResponseMessage> responseFactory) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var response = responseFactory(request);
        response.RequestMessage ??= request;
        return Task.FromResult(response);
    }
}

sealed class SynchronousProgress<T>(Action<T> report) : IProgress<T>
{
    public void Report(T value) => report(value);
}
