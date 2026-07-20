using System.Windows;

namespace SpatialScope.Windows.Controls;

public enum RegionDrawingMode
{
    Polygon,
    FreeDraw,
}

public sealed class RegionDrawingChangedEventArgs(
    IReadOnlyList<IReadOnlyList<Point>> completedPolygons,
    int currentPointCount,
    int totalPointCount) : EventArgs
{
    public IReadOnlyList<IReadOnlyList<Point>> CompletedPolygons { get; } = completedPolygons;

    public int CurrentPointCount { get; } = currentPointCount;

    public int TotalPointCount { get; } = totalPointCount;
}
