using SpatialScope.Windows.Updates;
using System.Threading;
using System.Windows;

namespace SpatialScope.Windows;

public partial class App : Application
{
    private Mutex? _instanceMutex;
    private bool _ownsInstanceMutex;

    protected override void OnStartup(StartupEventArgs e)
    {
        try
        {
            _instanceMutex = new Mutex(
                initiallyOwned: true,
                UpdateInstallerLauncher.ResolveInstanceMutexName(e.Args, AppContext.BaseDirectory),
                out var createdNew);
            if (!createdNew)
            {
                _instanceMutex.Dispose();
                _instanceMutex = null;
                Shutdown();
                return;
            }
            _ownsInstanceMutex = true;
        }
        catch (UnauthorizedAccessException)
        {
            Shutdown(1);
            return;
        }

        base.OnStartup(e);
    }

    protected override void OnExit(ExitEventArgs e)
    {
        if (_ownsInstanceMutex)
        {
            try
            {
                _instanceMutex?.ReleaseMutex();
            }
            catch (ApplicationException)
            {
                // Process teardown also releases an abandoned mutex safely.
            }
        }
        _instanceMutex?.Dispose();
        base.OnExit(e);
    }
}
