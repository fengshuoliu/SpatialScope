using System.Text.Json;

namespace SpatialScope.Windows.Updates;

public sealed class UpdateCheckStateStore
{
    private readonly string _path;

    public UpdateCheckStateStore(string? path = null)
    {
        _path = Path.GetFullPath(path ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "SpatialScope",
            "update-state.json"));
    }

    public bool IsAutomaticCheckDue(DateTimeOffset now, TimeSpan interval)
    {
        if (interval <= TimeSpan.Zero) return true;
        var lastCheck = LoadLastSuccessfulCheck();
        return !lastCheck.HasValue || now - lastCheck.Value >= interval || now < lastCheck.Value;
    }

    public void RecordSuccessfulCheck(DateTimeOffset checkedAt)
    {
        var directory = Path.GetDirectoryName(_path)!;
        Directory.CreateDirectory(directory);
        var temporaryPath = _path + ".tmp";
        var payload = JsonSerializer.Serialize(new UpdateState(checkedAt));
        File.WriteAllText(temporaryPath, payload);
        File.Move(temporaryPath, _path, overwrite: true);
    }

    private DateTimeOffset? LoadLastSuccessfulCheck()
    {
        try
        {
            if (!File.Exists(_path)) return null;
            var state = JsonSerializer.Deserialize<UpdateState>(File.ReadAllText(_path));
            return state?.LastSuccessfulCheckUtc;
        }
        catch (IOException)
        {
            return null;
        }
        catch (UnauthorizedAccessException)
        {
            return null;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private sealed record UpdateState(DateTimeOffset LastSuccessfulCheckUtc);
}
