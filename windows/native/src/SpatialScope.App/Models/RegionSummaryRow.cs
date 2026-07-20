using System.Collections.ObjectModel;

namespace SpatialScope.Windows.Models;

public sealed class RegionSummaryRow
{
    public string Id { get; init; } = string.Empty;
    public string Label { get; init; } = string.Empty;
    public string SourceType { get; init; } = string.Empty;
    public string DominantType { get; init; } = string.Empty;
    public int CellCount { get; init; }
    public double AreaUm2 { get; init; }
    public string ColorHex { get; init; } = "#A1D99B";
    public IReadOnlyDictionary<string, int> CountsByType { get; init; } =
        new ReadOnlyDictionary<string, int>(new Dictionary<string, int>());

    public string DisplayId => Id.StartsWith("roi_", StringComparison.Ordinal) && Id.Length > 12
        ? Id[4..12]
        : Id;
    public string AreaDisplay => $"{AreaUm2:N0}";
}

public sealed class RegionDominantCountRow
{
    public string Name { get; init; } = string.Empty;
    public int Count { get; init; }
    public string ColorHex { get; init; } = "#A1D99B";
}
