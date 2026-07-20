using Microsoft.Win32;
using SpatialScope.Windows.Updates;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows;
using System.Windows.Automation;

namespace SpatialScope.Windows;

public partial class MainWindow
{
    private static readonly TimeSpan AutomaticUpdateCheckInterval = TimeSpan.FromHours(24);
    private readonly Version _applicationVersion = GetCurrentApplicationVersion();
    private readonly GitHubUpdateService _updateService = new(GetCurrentApplicationVersion());
    private readonly UpdateCheckStateStore _updateCheckState = new();
    private readonly CancellationTokenSource _updateCancellation = new();
    private WindowsUpdateRelease? _availableUpdate;
    private bool _updateCheckInProgress;
    private bool _updateInstallInProgress;

    private static Version GetCurrentApplicationVersion() =>
        Assembly.GetExecutingAssembly().GetName().Version ?? new Version(1, 2, 5, 0);

    private void BeginAutomaticUpdateCheck()
    {
        if (_closeInProgress || UpdatesDisabledByEnvironment() || !IsInstallerOwnedCurrentCopy()) return;
        _ = Task.Run(() => _updateService.CleanupOldDownloads(_applicationVersion));
        if (!_updateCheckState.IsAutomaticCheckDue(DateTimeOffset.UtcNow, AutomaticUpdateCheckInterval)) return;
        _ = CheckForUpdatesAsync(isManual: false);
    }

    private async void CheckForUpdatesButton_Click(object sender, RoutedEventArgs e)
    {
        if (_isBusy || _updateInstallInProgress) return;
        if (_availableUpdate is not null)
        {
            await OfferAndInstallUpdateAsync(_availableUpdate);
            return;
        }
        await CheckForUpdatesAsync(isManual: true);
    }

    private async Task CheckForUpdatesAsync(bool isManual)
    {
        if (_closeInProgress || _updateCheckInProgress || _updateInstallInProgress) return;
        _updateCheckInProgress = true;
        RefreshUpdateControls();
        if (isManual) SetLocalizedStatus("CheckingForUpdates");

        try
        {
            var release = await _updateService.CheckForUpdateAsync(_updateCancellation.Token);
            TryRecordSuccessfulUpdateCheck();
            _availableUpdate = release;
            RefreshUpdateControls();

            if (release is null)
            {
                if (isManual)
                {
                    SetLocalizedStatus("ApplicationUpToDate");
                    MessageBox.Show(
                        this,
                        string.Format(_localization["ApplicationUpToDateMessage"], DisplayVersion(_applicationVersion)),
                        _localization["ApplicationUpToDateTitle"],
                        MessageBoxButton.OK,
                        MessageBoxImage.Information);
                }
                return;
            }

            if (_isBusy)
            {
                // Never interrupt an active scientific analysis. The footer
                // changes to an explicit install action when the run finishes.
                return;
            }
            await OfferAndInstallUpdateAsync(release);
        }
        catch (OperationCanceledException) when (_updateCancellation.IsCancellationRequested)
        {
            // Window shutdown cancels network work without affecting analysis state.
        }
        catch (Exception exception)
        {
            Debug.WriteLine($"SpatialScope update check failed: {exception}");
            if (isManual && !_closeInProgress)
            {
                SetLocalizedStatus("UpdateCheckFailed", isError: true);
                MessageBox.Show(
                    this,
                    string.Format(_localization["UpdateCheckFailedMessage"], exception.Message),
                    _localization["UpdateCheckFailedTitle"],
                    MessageBoxButton.OK,
                    MessageBoxImage.Warning);
            }
        }
        finally
        {
            _updateCheckInProgress = false;
            if (!_closeInProgress) RefreshUpdateControls();
        }
    }

    private async Task OfferAndInstallUpdateAsync(WindowsUpdateRelease release)
    {
        if (_isBusy || _updateInstallInProgress || _closeInProgress) return;
        var version = DisplayVersion(release.Version);
        if (!IsInstallerOwnedCurrentCopy())
        {
            SetStatus(string.Format(_localization["UpdateAvailableStatus"], version));
            MessageBox.Show(
                this,
                _localization["UpdateManualInstallRequiredMessage"],
                _localization["UpdateManualInstallRequiredTitle"],
                MessageBoxButton.OK,
                MessageBoxImage.Information);
            RefreshUpdateControls();
            return;
        }
        var choice = MessageBox.Show(
            this,
            string.Format(_localization["UpdateAvailableMessage"], version),
            _localization["UpdateAvailableTitle"],
            MessageBoxButton.YesNo,
            MessageBoxImage.Information,
            MessageBoxResult.No);
        if (choice != MessageBoxResult.Yes)
        {
            SetStatus(string.Format(_localization["UpdateAvailableStatus"], version));
            RefreshUpdateControls();
            return;
        }

        _updateInstallInProgress = true;
        SetInteractionBusy(true);
        OperationProgress.Value = 0;
        OperationProgress.Visibility = Visibility.Visible;
        SetStatus(string.Format(_localization["DownloadingUpdate"], version));
        ApplyStatusTone("Running", isError: false);
        RefreshUpdateControls();

        try
        {
            var progress = new Progress<UpdateDownloadProgress>(value =>
            {
                if (_closeInProgress) return;
                if (value.Fraction is { } fraction)
                {
                    OperationProgress.Value = fraction;
                    SetStatus(string.Format(_localization["DownloadingUpdateProgress"], version, fraction));
                    ApplyStatusTone("Running", isError: false);
                }
                else
                {
                    SetStatus(string.Format(_localization["DownloadingUpdate"], version));
                    ApplyStatusTone("Running", isError: false);
                }
            });
            var prepared = await _updateService.DownloadAndVerifyAsync(
                release,
                progress,
                _updateCancellation.Token);
            SetLocalizedStatus("UpdateVerified");

            // NSIS receives this PID and waits until MainWindow_Closing has
            // disposed the analysis engine and this process has fully exited.
            UpdateInstallerLauncher.Start(prepared, _updateService.UpdatesRoot, Environment.ProcessId);
            Close();
        }
        catch (OperationCanceledException) when (_updateCancellation.IsCancellationRequested)
        {
            // User-initiated window closure cancels the staged download.
        }
        catch (Exception exception)
        {
            Debug.WriteLine($"SpatialScope update installation failed: {exception}");
            if (!_closeInProgress)
            {
                SetLocalizedStatus("UpdateInstallFailed", isError: true);
                MessageBox.Show(
                    this,
                    string.Format(_localization["UpdateInstallFailedMessage"], exception.Message),
                    _localization["UpdateInstallFailedTitle"],
                    MessageBoxButton.OK,
                    MessageBoxImage.Error);
            }
        }
        finally
        {
            _updateInstallInProgress = false;
            if (!_closeInProgress)
            {
                OperationProgress.Visibility = Visibility.Collapsed;
                SetInteractionBusy(false);
                RefreshUpdateControls();
            }
        }
    }

    private void RefreshUpdateControls()
    {
        if (!IsInitialized || CheckForUpdatesButton is null || VersionText is null) return;
        VersionText.Text = string.Format(_localization["VersionFormat"], DisplayVersion(_applicationVersion));
        CheckForUpdatesButton.Content = _availableUpdate is null
            ? _localization["CheckForUpdates"]
            : string.Format(_localization["InstallUpdateVersion"], DisplayVersion(_availableUpdate.Version));
        CheckForUpdatesButton.IsEnabled = !_isBusy && !_updateCheckInProgress && !_updateInstallInProgress;
        AutomationProperties.SetName(CheckForUpdatesButton, CheckForUpdatesButton.Content?.ToString() ?? string.Empty);
    }

    private void CancelUpdateOperations() => _updateCancellation.Cancel();

    private void TryRecordSuccessfulUpdateCheck()
    {
        try
        {
            _updateCheckState.RecordSuccessfulCheck(DateTimeOffset.UtcNow);
        }
        catch (IOException exception)
        {
            Debug.WriteLine($"SpatialScope could not save update-check state: {exception.Message}");
        }
        catch (UnauthorizedAccessException exception)
        {
            Debug.WriteLine($"SpatialScope could not save update-check state: {exception.Message}");
        }
    }

    private static bool UpdatesDisabledByEnvironment()
    {
        var value = Environment.GetEnvironmentVariable("SPATIALSCOPE_DISABLE_UPDATE_CHECK");
        return value is not null
            && (value.Equals("1", StringComparison.OrdinalIgnoreCase)
                || value.Equals("true", StringComparison.OrdinalIgnoreCase)
                || value.Equals("yes", StringComparison.OrdinalIgnoreCase));
    }

    private static bool IsInstallerOwnedCurrentCopy()
    {
        try
        {
            var executablePath = Environment.ProcessPath;
            var executableDirectory = executablePath is null ? null : Path.GetDirectoryName(executablePath);
            if (string.IsNullOrWhiteSpace(executableDirectory)
                || !File.Exists(Path.Combine(executableDirectory, ".spatialscope-install")))
                return false;

            using var key = Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Uninstall\SpatialScope",
                writable: false);
            var registeredDirectory = key?.GetValue("InstallLocation") as string;
            if (string.IsNullOrWhiteSpace(registeredDirectory)) return false;

            var current = Path.TrimEndingDirectorySeparator(Path.GetFullPath(executableDirectory));
            var registered = Path.TrimEndingDirectorySeparator(Path.GetFullPath(registeredDirectory));
            return string.Equals(current, registered, StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception exception) when (
            exception is IOException
            or UnauthorizedAccessException
            or ArgumentException
            or System.Security.SecurityException)
        {
            return false;
        }
    }

    private static string DisplayVersion(Version version) => GitHubUpdateService.FormatVersion(version);
}
