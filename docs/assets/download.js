(function configureSpatialScopeDownload(global) {
  "use strict";

  const releasesApi = "https://api.github.com/repos/fengshuoliu/SpatialScope/releases?per_page=100";
  const releasePathPrefix = "/fengshuoliu/SpatialScope/releases/download/";
  const platformConfiguration = Object.freeze({
    macos: Object.freeze({
      displayName: "macOS",
      tagPattern: /^v\d+(?:\.\d+){1,2}$/,
      assetName: "SpatialScope-macOS-universal.dmg",
    }),
    windows: Object.freeze({
      displayName: "Windows",
      tagPattern: /^windows-v\d+(?:\.\d+){1,2}$/,
      assetName: "SpatialScope-Windows-x64-Setup.exe",
    }),
  });

  function publishedTime(release) {
    const parsed = Date.parse(release?.published_at || "");
    return Number.isFinite(parsed) ? parsed : null;
  }

  function selectLatestAsset(releases, platform) {
    const configuration = platformConfiguration[platform];
    if (!configuration) throw new Error("Choose macOS or Windows to continue.");
    if (!Array.isArray(releases)) throw new Error("GitHub returned an invalid release list.");

    const candidates = releases
      .filter((release) =>
        release
        && !release.draft
        && !release.prerelease
        && publishedTime(release) !== null
        && typeof release.tag_name === "string"
        && configuration.tagPattern.test(release.tag_name))
      .sort((left, right) =>
        publishedTime(right) - publishedTime(left)
        || Number(right.id || 0) - Number(left.id || 0));

    for (const release of candidates) {
      const matchingAssets = Array.isArray(release.assets)
        ? release.assets.filter((item) =>
          item
          && item.name === configuration.assetName
          && item.state === "uploaded"
          && Number(item.size) > 0)
        : [];
      if (matchingAssets.length === 1) return { configuration, release, asset: matchingAssets[0] };
    }

    throw new Error(`No published ${configuration.displayName} download is currently available.`);
  }

  function validatedDownloadUrl(rawUrl, release, configuration) {
    const url = new URL(rawUrl);
    const expectedPath = `${releasePathPrefix}${encodeURIComponent(release.tag_name)}/${encodeURIComponent(configuration.assetName)}`;
    if (url.protocol !== "https:"
        || url.hostname !== "github.com"
        || url.pathname !== expectedPath
        || url.search
        || url.hash) {
      throw new Error("GitHub returned an unexpected download address.");
    }
    return url.href;
  }

  async function routeDownload() {
    const status = global.document.getElementById("download-status");
    const fallback = global.document.getElementById("download-fallback");
    const platform = global.document.documentElement.dataset.platform;
    const abortController = new AbortController();
    const timeout = global.setTimeout(() => abortController.abort(), 10000);

    try {
      const response = await global.fetch(releasesApi, {
        cache: "no-store",
        credentials: "omit",
        signal: abortController.signal,
        headers: {
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
        },
      });
      if (!response.ok) throw new Error(`GitHub release lookup failed (${response.status}).`);

      const selected = selectLatestAsset(await response.json(), platform);
      const downloadUrl = validatedDownloadUrl(
        selected.asset.browser_download_url,
        selected.release,
        selected.configuration);
      fallback.href = `https://github.com/fengshuoliu/SpatialScope/releases/tag/${encodeURIComponent(selected.release.tag_name)}`;
      fallback.textContent = `View SpatialScope ${selected.release.tag_name}`;
      status.textContent = `Starting the latest ${selected.configuration.displayName} download…`;
      global.location.replace(downloadUrl);
    } catch (error) {
      status.textContent = error instanceof Error
        ? `${error.message} Use “View all releases” to choose a download manually.`
        : "The automatic download could not start. Use “View all releases” to choose a download manually.";
    } finally {
      global.clearTimeout(timeout);
    }
  }

  global.SpatialScopeDownloadRouter = Object.freeze({
    platformConfiguration,
    selectLatestAsset,
    validatedDownloadUrl,
  });

  if (global.document) global.addEventListener("DOMContentLoaded", routeDownload);
}(typeof window === "undefined" ? globalThis : window));
