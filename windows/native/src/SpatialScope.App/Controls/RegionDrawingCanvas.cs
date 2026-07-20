using System.Collections.Specialized;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Input;
using System.Windows.Media;

namespace SpatialScope.Windows.Controls;

/// <summary>
/// Draws one or more regions over a uniformly fitted source image. All public
/// polygon coordinates are expressed in source-image pixels.
/// </summary>
public sealed class RegionDrawingCanvas : FrameworkElement
{
    private const double MinimumScreenPointSpacing = 2.0;
    private const double PointerArmLength = 10.0;
    private const double PointerGap = 3.0;
    private const double PointerRingRadius = 4.25;
    private static readonly Brush CanvasBrush = CreateBrush(Color.FromRgb(0, 0, 0));
    private static readonly Brush DrawingBrush = CreateBrush(Color.FromRgb(255, 138, 0));
    private static readonly Brush DrawingFillBrush = CreateBrush(Color.FromArgb(54, 255, 138, 0));
    private static readonly Brush VertexFillBrush = CreateBrush(Color.FromRgb(255, 181, 66));
    private static readonly Pen ContrastPen = CreatePen(Brushes.Black, 7.0);
    private static readonly Pen DrawingHaloPen = CreatePen(Brushes.White, 4.5);
    private static readonly Pen DrawingPen = CreatePen(DrawingBrush, 2.75);
    private static readonly Pen PreviewContrastPen = CreateDashedPen(Brushes.Black, 5.0);
    private static readonly Pen PreviewPen = CreateDashedPen(Brushes.White, 2.0);
    private static readonly Pen PointerContrastPen = CreatePen(Brushes.Black, 5.0);
    private static readonly Pen PointerPen = CreatePen(Brushes.White, 2.25);
    private static readonly Pen FocusPen = CreateFocusPen();

    public static readonly DependencyProperty BackgroundImageProperty = DependencyProperty.Register(
        nameof(BackgroundImage),
        typeof(ImageSource),
        typeof(RegionDrawingCanvas),
        new FrameworkPropertyMetadata(null, FrameworkPropertyMetadataOptions.AffectsRender));

    public static readonly DependencyProperty SourcePixelWidthProperty = DependencyProperty.Register(
        nameof(SourcePixelWidth),
        typeof(double),
        typeof(RegionDrawingCanvas),
        new FrameworkPropertyMetadata(
            0.0,
            FrameworkPropertyMetadataOptions.AffectsRender,
            null,
            CoerceSourceDimension));

    public static readonly DependencyProperty SourcePixelHeightProperty = DependencyProperty.Register(
        nameof(SourcePixelHeight),
        typeof(double),
        typeof(RegionDrawingCanvas),
        new FrameworkPropertyMetadata(
            0.0,
            FrameworkPropertyMetadataOptions.AffectsRender,
            null,
            CoerceSourceDimension));

    public static readonly DependencyProperty DrawingModeProperty = DependencyProperty.Register(
        nameof(DrawingMode),
        typeof(RegionDrawingMode),
        typeof(RegionDrawingCanvas),
        new FrameworkPropertyMetadata(
            RegionDrawingMode.Polygon,
            FrameworkPropertyMetadataOptions.AffectsRender,
            DrawingModeChanged));

    public static readonly DependencyProperty InitialPolygonsProperty = DependencyProperty.Register(
        nameof(InitialPolygons),
        typeof(IEnumerable<IEnumerable<Point>>),
        typeof(RegionDrawingCanvas),
        new FrameworkPropertyMetadata(null, InitialPolygonsChanged));

    private static readonly DependencyPropertyKey CurrentPointCountPropertyKey =
        DependencyProperty.RegisterReadOnly(
            nameof(CurrentPointCount),
            typeof(int),
            typeof(RegionDrawingCanvas),
            new PropertyMetadata(0));

    public static readonly DependencyProperty CurrentPointCountProperty =
        CurrentPointCountPropertyKey.DependencyProperty;

    private static readonly DependencyPropertyKey TotalPointCountPropertyKey =
        DependencyProperty.RegisterReadOnly(
            nameof(TotalPointCount),
            typeof(int),
            typeof(RegionDrawingCanvas),
            new PropertyMetadata(0));

    public static readonly DependencyProperty TotalPointCountProperty =
        TotalPointCountPropertyKey.DependencyProperty;

    private readonly List<List<Point>> _completedPolygons = [];
    private readonly List<Point> _currentPoints = [];
    private IReadOnlyList<IReadOnlyList<Point>> _completedPolygonSnapshot = [];
    private INotifyCollectionChanged? _observedInitialPolygons;
    private bool _freeDrawActive;
    private Point? _keyboardCursor;
    private Point? _mousePointer;

    public RegionDrawingCanvas()
    {
        Focusable = true;
        ClipToBounds = true;
        SnapsToDevicePixels = true;
        // The stock Windows cross cursor is black and disappears on dark
        // microscopy imagery. Hide it over the active canvas and paint a
        // DPI-aware white crosshair with a black outline in OnRender instead.
        Cursor = Cursors.None;
        IsEnabledChanged += RegionDrawingCanvasIsEnabledChanged;
    }

    public ImageSource? BackgroundImage
    {
        get => (ImageSource?)GetValue(BackgroundImageProperty);
        set => SetValue(BackgroundImageProperty, value);
    }

    public double SourcePixelWidth
    {
        get => (double)GetValue(SourcePixelWidthProperty);
        set => SetValue(SourcePixelWidthProperty, value);
    }

    public double SourcePixelHeight
    {
        get => (double)GetValue(SourcePixelHeightProperty);
        set => SetValue(SourcePixelHeightProperty, value);
    }

    public RegionDrawingMode DrawingMode
    {
        get => (RegionDrawingMode)GetValue(DrawingModeProperty);
        set => SetValue(DrawingModeProperty, value);
    }

    public IEnumerable<IEnumerable<Point>>? InitialPolygons
    {
        get => (IEnumerable<IEnumerable<Point>>?)GetValue(InitialPolygonsProperty);
        set => SetValue(InitialPolygonsProperty, value);
    }

    public IReadOnlyList<IReadOnlyList<Point>> CompletedPolygons => _completedPolygonSnapshot;

    public int CurrentPointCount => (int)GetValue(CurrentPointCountProperty);

    public int TotalPointCount => (int)GetValue(TotalPointCountProperty);

    public event EventHandler<RegionDrawingChangedEventArgs>? DrawingChanged;

    /// <summary>
    /// Closes the current polygon or free-draw point set. Returns false when
    /// fewer than three non-collinear points are available.
    /// </summary>
    public bool CloseCurrentArea()
    {
        if (_freeDrawActive)
        {
            _freeDrawActive = false;
            if (IsMouseCaptured) ReleaseMouseCapture();
        }

        var closed = RemoveConsecutiveDuplicates(_currentPoints.Where(IsFinite));

        if (closed.Count < 3 || Math.Abs(SignedArea(closed)) < double.Epsilon)
            return false;

        _completedPolygons.Add(closed);
        _currentPoints.Clear();
        PublishDrawingChange();
        return true;
    }

    public void ResetDrawing()
    {
        _freeDrawActive = false;
        if (IsMouseCaptured) ReleaseMouseCapture();
        _completedPolygons.Clear();
        _currentPoints.Clear();
        PublishDrawingChange();
    }

    protected override AutomationPeer OnCreateAutomationPeer() =>
        new RegionDrawingCanvasAutomationPeer(this);

    protected override void OnRender(DrawingContext drawingContext)
    {
        base.OnRender(drawingContext);
        drawingContext.DrawRectangle(CanvasBrush, null, new Rect(RenderSize));

        var imageRect = GetDisplayedImageRect();
        if (imageRect.IsEmpty) return;

        drawingContext.PushClip(new RectangleGeometry(imageRect));
        if (BackgroundImage is not null)
            drawingContext.DrawImage(BackgroundImage, imageRect);

        foreach (var polygon in _completedPolygons)
            DrawPolygon(drawingContext, polygon, imageRect, isClosed: true, showVertices: true);

        if (_currentPoints.Count > 0)
        {
            var previewClosed = DrawingMode == RegionDrawingMode.Polygon && _currentPoints.Count >= 3;
            DrawPolygon(drawingContext, _currentPoints, imageRect, previewClosed, showVertices: true);

            if (DrawingMode == RegionDrawingMode.Polygon
                && _mousePointer is Point pointer
                && imageRect.Contains(pointer))
            {
                var lastPoint = MapToControl(_currentPoints[^1], imageRect);
                drawingContext.DrawLine(PreviewContrastPen, lastPoint, pointer);
                drawingContext.DrawLine(PreviewPen, lastPoint, pointer);
            }
        }
        drawingContext.Pop();

        if (IsKeyboardFocused)
            drawingContext.DrawRectangle(null, FocusPen, imageRect);

        var visiblePointer = _mousePointer is Point mousePointer
            ? mousePointer
            : IsKeyboardFocused && _keyboardCursor is Point keyboardCursor
                ? MapToControl(keyboardCursor, imageRect)
                : (Point?)null;
        if (visiblePointer is Point pointerPosition)
            DrawHighContrastPointer(drawingContext, pointerPosition, new Rect(RenderSize));
    }

    private void RegionDrawingCanvasIsEnabledChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        Cursor = IsEnabled ? Cursors.None : Cursors.Arrow;
        if (!IsEnabled)
            _mousePointer = null;
        else if (IsMouseOver)
            _mousePointer = Mouse.GetPosition(this);
        InvalidateVisual();
    }

    protected override void OnMouseEnter(MouseEventArgs e)
    {
        base.OnMouseEnter(e);
        TrackMousePointer(e.GetPosition(this));
    }

    protected override void OnMouseLeave(MouseEventArgs e)
    {
        base.OnMouseLeave(e);
        if (IsMouseCaptured)
        {
            Cursor = Cursors.Arrow;
            return;
        }
        _mousePointer = null;
        InvalidateVisual();
    }

    protected override void OnMouseLeftButtonDown(MouseButtonEventArgs e)
    {
        base.OnMouseLeftButtonDown(e);
        var controlPoint = e.GetPosition(this);
        TrackMousePointer(controlPoint);
        if (!TryMapToSource(controlPoint, out var sourcePoint)) return;

        Focus();
        _keyboardCursor = sourcePoint;
        if (DrawingMode == RegionDrawingMode.Polygon)
        {
            if (e.ClickCount >= 2)
            {
                CloseCurrentArea();
            }
            else
            {
                AppendPoint(sourcePoint, requireMinimumSpacing: false);
            }
        }
        else
        {
            _currentPoints.Clear();
            _currentPoints.Add(sourcePoint);
            _freeDrawActive = true;
            CaptureMouse();
            PublishDrawingChange();
        }
        e.Handled = true;
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        base.OnMouseMove(e);
        TrackMousePointer(e.GetPosition(this));
        if (!_freeDrawActive || e.LeftButton != MouseButtonState.Pressed) return;
        if (!TryMapToSource(_mousePointer!.Value, out var sourcePoint)) return;
        if (AppendPoint(sourcePoint, requireMinimumSpacing: true)) e.Handled = true;
    }

    protected override void OnMouseLeftButtonUp(MouseButtonEventArgs e)
    {
        base.OnMouseLeftButtonUp(e);
        TrackMousePointer(e.GetPosition(this));
        if (!_freeDrawActive) return;

        if (TryMapToSource(_mousePointer!.Value, out var sourcePoint))
            AppendPoint(sourcePoint, requireMinimumSpacing: true, publish: false);

        _freeDrawActive = false;
        if (IsMouseCaptured) ReleaseMouseCapture();
        CloseCurrentArea();
        if (_currentPoints.Count > 0)
        {
            _currentPoints.Clear();
            PublishDrawingChange();
        }
        e.Handled = true;
    }

    protected override void OnMouseRightButtonDown(MouseButtonEventArgs e)
    {
        base.OnMouseRightButtonDown(e);
        TrackMousePointer(e.GetPosition(this));
        Focus();
        if (CloseCurrentArea()) e.Handled = true;
    }

    protected override void OnKeyDown(KeyEventArgs e)
    {
        base.OnKeyDown(e);
        if (e.Key is Key.Enter or Key.Return)
        {
            if (CloseCurrentArea()) e.Handled = true;
            return;
        }
        if (DrawingMode != RegionDrawingMode.Polygon
            || EffectiveSourceWidth <= 0
            || EffectiveSourceHeight <= 0)
        {
            return;
        }

        if (e.Key == Key.Space)
        {
            AppendPoint(EnsureKeyboardCursor(), requireMinimumSpacing: false);
            e.Handled = true;
            return;
        }
        if (e.Key is Key.Back or Key.Delete)
        {
            if (_currentPoints.Count > 0)
            {
                _currentPoints.RemoveAt(_currentPoints.Count - 1);
                PublishDrawingChange();
            }
            e.Handled = true;
            return;
        }
        if (e.Key == Key.Escape)
        {
            if (_currentPoints.Count > 0)
            {
                _currentPoints.Clear();
                PublishDrawingChange();
            }
            e.Handled = true;
            return;
        }
        if (e.Key is not (Key.Left or Key.Right or Key.Up or Key.Down)) return;

        var cursor = EnsureKeyboardCursor();
        var step = Keyboard.Modifiers.HasFlag(ModifierKeys.Shift)
            ? 1.0
            : Math.Max(1.0, Math.Min(EffectiveSourceWidth, EffectiveSourceHeight) / 100.0);
        if (e.Key == Key.Left) cursor.X -= step;
        if (e.Key == Key.Right) cursor.X += step;
        if (e.Key == Key.Up) cursor.Y -= step;
        if (e.Key == Key.Down) cursor.Y += step;
        _keyboardCursor = new Point(
            Math.Clamp(cursor.X, 0, Math.Max(0, EffectiveSourceWidth - 1)),
            Math.Clamp(cursor.Y, 0, Math.Max(0, EffectiveSourceHeight - 1)));
        InvalidateVisual();
        e.Handled = true;
    }

    protected override void OnLostMouseCapture(MouseEventArgs e)
    {
        base.OnLostMouseCapture(e);
        Cursor = IsMouseOver && IsEnabled ? Cursors.None : Cursors.Arrow;
        if (!IsMouseOver) _mousePointer = null;
        if (!_freeDrawActive) return;
        _freeDrawActive = false;
        CloseCurrentArea();
        if (_currentPoints.Count > 0)
        {
            _currentPoints.Clear();
            PublishDrawingChange();
        }
    }

    protected override void OnGotKeyboardFocus(KeyboardFocusChangedEventArgs e)
    {
        base.OnGotKeyboardFocus(e);
        EnsureKeyboardCursor();
        InvalidateVisual();
    }

    protected override void OnLostKeyboardFocus(KeyboardFocusChangedEventArgs e)
    {
        base.OnLostKeyboardFocus(e);
        InvalidateVisual();
    }

    private static object CoerceSourceDimension(DependencyObject sender, object value)
    {
        var dimension = (double)value;
        return double.IsFinite(dimension) && dimension > 0 ? dimension : 0.0;
    }

    private static void DrawingModeChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        var canvas = (RegionDrawingCanvas)sender;
        canvas._freeDrawActive = false;
        if (canvas.IsMouseCaptured) canvas.ReleaseMouseCapture();
        if (canvas._currentPoints.Count == 0) return;
        canvas._currentPoints.Clear();
        canvas.PublishDrawingChange();
    }

    private Point EnsureKeyboardCursor()
    {
        if (_keyboardCursor is null)
        {
            _keyboardCursor = new Point(
                Math.Max(0, (EffectiveSourceWidth - 1) / 2.0),
                Math.Max(0, (EffectiveSourceHeight - 1) / 2.0));
        }
        return _keyboardCursor.Value;
    }

    private static void InitialPolygonsChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        var canvas = (RegionDrawingCanvas)sender;
        canvas.ObserveInitialPolygons(args.OldValue, args.NewValue);
        canvas.LoadInitialPolygons();
    }

    private void ObserveInitialPolygons(object? oldValue, object? newValue)
    {
        if (_observedInitialPolygons is not null)
            _observedInitialPolygons.CollectionChanged -= InitialPolygonsCollectionChanged;
        _observedInitialPolygons = newValue as INotifyCollectionChanged;
        if (_observedInitialPolygons is not null)
            _observedInitialPolygons.CollectionChanged += InitialPolygonsCollectionChanged;
    }

    private void InitialPolygonsCollectionChanged(object? sender, NotifyCollectionChangedEventArgs e) =>
        LoadInitialPolygons();

    private void LoadInitialPolygons()
    {
        _freeDrawActive = false;
        if (IsMouseCaptured) ReleaseMouseCapture();
        _completedPolygons.Clear();
        _currentPoints.Clear();
        if (InitialPolygons is not null)
        {
            foreach (var polygon in InitialPolygons)
            {
                var clean = RemoveConsecutiveDuplicates(polygon.Where(IsFinite));
                if (clean.Count >= 3 && Math.Abs(SignedArea(clean)) >= double.Epsilon)
                    _completedPolygons.Add(clean);
            }
        }
        PublishDrawingChange();
    }

    private bool AppendPoint(Point sourcePoint, bool requireMinimumSpacing, bool publish = true)
    {
        if (requireMinimumSpacing && _currentPoints.Count > 0)
        {
            var scale = GetImageScale();
            var minimumSourceSpacing = scale > 0 ? MinimumScreenPointSpacing / scale : MinimumScreenPointSpacing;
            var delta = sourcePoint - _currentPoints[^1];
            if (delta.LengthSquared < minimumSourceSpacing * minimumSourceSpacing) return false;
        }

        _currentPoints.Add(sourcePoint);
        if (publish) PublishDrawingChange();
        return true;
    }

    private void TrackMousePointer(Point controlPoint)
    {
        Cursor = IsEnabled && new Rect(RenderSize).Contains(controlPoint)
            ? Cursors.None
            : Cursors.Arrow;
        if (_mousePointer == controlPoint) return;
        _mousePointer = controlPoint;
        InvalidateVisual();
    }

    private void PublishDrawingChange()
    {
        _completedPolygonSnapshot = _completedPolygons
            .Select(points => (IReadOnlyList<Point>)Array.AsReadOnly(points.ToArray()))
            .ToArray();
        SetValue(CurrentPointCountPropertyKey, _currentPoints.Count);
        SetValue(TotalPointCountPropertyKey, _currentPoints.Count + _completedPolygons.Sum(points => points.Count));
        InvalidateVisual();
        DrawingChanged?.Invoke(
            this,
            new RegionDrawingChangedEventArgs(
                _completedPolygonSnapshot,
                CurrentPointCount,
                TotalPointCount));
    }

    private Rect GetDisplayedImageRect()
    {
        var sourceWidth = EffectiveSourceWidth;
        var sourceHeight = EffectiveSourceHeight;
        if (sourceWidth <= 0 || sourceHeight <= 0 || ActualWidth <= 0 || ActualHeight <= 0)
            return Rect.Empty;

        var scale = Math.Min(ActualWidth / sourceWidth, ActualHeight / sourceHeight);
        if (!double.IsFinite(scale) || scale <= 0) return Rect.Empty;
        var width = sourceWidth * scale;
        var height = sourceHeight * scale;
        return new Rect((ActualWidth - width) / 2.0, (ActualHeight - height) / 2.0, width, height);
    }

    private double GetImageScale()
    {
        var imageRect = GetDisplayedImageRect();
        return imageRect.IsEmpty || EffectiveSourceWidth <= 0
            ? 0
            : imageRect.Width / EffectiveSourceWidth;
    }

    private double EffectiveSourceWidth => ResolveSourceDimension(SourcePixelWidth, BackgroundImage?.Width ?? 0);

    private double EffectiveSourceHeight => ResolveSourceDimension(SourcePixelHeight, BackgroundImage?.Height ?? 0);

    private static double ResolveSourceDimension(double requested, double fallback) =>
        double.IsFinite(requested) && requested > 0
            ? requested
            : double.IsFinite(fallback) && fallback > 0
                ? fallback
                : 0;

    private bool TryMapToSource(Point controlPoint, out Point sourcePoint)
    {
        sourcePoint = default;
        var imageRect = GetDisplayedImageRect();
        var sourceWidth = EffectiveSourceWidth;
        var sourceHeight = EffectiveSourceHeight;
        if (imageRect.IsEmpty
            || controlPoint.X < imageRect.Left
            || controlPoint.X > imageRect.Right
            || controlPoint.Y < imageRect.Top
            || controlPoint.Y > imageRect.Bottom)
            return false;

        var scale = imageRect.Width / sourceWidth;
        sourcePoint = new Point(
            Math.Clamp((controlPoint.X - imageRect.Left) / scale, 0, Math.Max(0, sourceWidth - 1)),
            Math.Clamp((controlPoint.Y - imageRect.Top) / scale, 0, Math.Max(0, sourceHeight - 1)));
        return true;
    }

    private Point MapToControl(Point sourcePoint, Rect imageRect)
    {
        var scale = imageRect.Width / EffectiveSourceWidth;
        return new Point(imageRect.Left + sourcePoint.X * scale, imageRect.Top + sourcePoint.Y * scale);
    }

    private void DrawPolygon(
        DrawingContext drawingContext,
        IReadOnlyList<Point> sourcePoints,
        Rect imageRect,
        bool isClosed,
        bool showVertices)
    {
        if (sourcePoints.Count == 0) return;
        var points = sourcePoints.Select(point => MapToControl(point, imageRect)).ToArray();

        if (isClosed && points.Length >= 3)
        {
            var fillGeometry = CreateGeometry(points, isClosed: true, isFilled: true);
            drawingContext.DrawGeometry(DrawingFillBrush, null, fillGeometry);
        }

        if (points.Length >= 2)
        {
            var lineGeometry = CreateGeometry(points, isClosed, isFilled: false);
            drawingContext.DrawGeometry(null, ContrastPen, lineGeometry);
            drawingContext.DrawGeometry(null, DrawingHaloPen, lineGeometry);
            drawingContext.DrawGeometry(null, DrawingPen, lineGeometry);
        }

        if (!showVertices) return;
        foreach (var point in points)
        {
            drawingContext.DrawEllipse(Brushes.Black, null, point, 6.0, 6.0);
            drawingContext.DrawEllipse(Brushes.White, null, point, 4.75, 4.75);
            drawingContext.DrawEllipse(VertexFillBrush, null, point, 3.25, 3.25);
        }
    }

    private static void DrawHighContrastPointer(
        DrawingContext drawingContext,
        Point center,
        Rect imageRect)
    {
        drawingContext.PushClip(new RectangleGeometry(imageRect));

        DrawPointerLine(
            drawingContext,
            new Point(center.X - PointerArmLength, center.Y),
            new Point(center.X - PointerGap, center.Y));
        DrawPointerLine(
            drawingContext,
            new Point(center.X + PointerGap, center.Y),
            new Point(center.X + PointerArmLength, center.Y));
        DrawPointerLine(
            drawingContext,
            new Point(center.X, center.Y - PointerArmLength),
            new Point(center.X, center.Y - PointerGap));
        DrawPointerLine(
            drawingContext,
            new Point(center.X, center.Y + PointerGap),
            new Point(center.X, center.Y + PointerArmLength));

        drawingContext.DrawEllipse(null, PointerContrastPen, center, PointerRingRadius, PointerRingRadius);
        drawingContext.DrawEllipse(null, PointerPen, center, PointerRingRadius, PointerRingRadius);
        drawingContext.Pop();
    }

    private static void DrawPointerLine(DrawingContext drawingContext, Point start, Point end)
    {
        drawingContext.DrawLine(PointerContrastPen, start, end);
        drawingContext.DrawLine(PointerPen, start, end);
    }

    private static StreamGeometry CreateGeometry(IReadOnlyList<Point> points, bool isClosed, bool isFilled)
    {
        var geometry = new StreamGeometry { FillRule = FillRule.EvenOdd };
        using (var context = geometry.Open())
        {
            context.BeginFigure(points[0], isFilled, isClosed);
            if (points.Count > 1)
                context.PolyLineTo(points.Skip(1).ToArray(), isStroked: true, isSmoothJoin: true);
        }
        geometry.Freeze();
        return geometry;
    }

    private static List<Point> RemoveConsecutiveDuplicates(IEnumerable<Point> points)
    {
        var result = new List<Point>();
        foreach (var point in points)
        {
            if (result.Count == 0 || point != result[^1]) result.Add(point);
        }
        if (result.Count > 1 && result[0] == result[^1]) result.RemoveAt(result.Count - 1);
        return result;
    }

    private static double SignedArea(IReadOnlyList<Point> polygon)
    {
        var twiceArea = 0.0;
        for (var index = 0; index < polygon.Count; index++)
        {
            var next = (index + 1) % polygon.Count;
            twiceArea += polygon[index].X * polygon[next].Y - polygon[next].X * polygon[index].Y;
        }
        return twiceArea / 2.0;
    }

    private static bool IsFinite(Point point) => double.IsFinite(point.X) && double.IsFinite(point.Y);

    private static Brush CreateBrush(Color color)
    {
        var brush = new SolidColorBrush(color);
        brush.Freeze();
        return brush;
    }

    private static Pen CreatePen(Brush brush, double thickness)
    {
        var pen = new Pen(brush, thickness)
        {
            LineJoin = PenLineJoin.Round,
            StartLineCap = PenLineCap.Round,
            EndLineCap = PenLineCap.Round,
        };
        pen.Freeze();
        return pen;
    }

    private static Pen CreateDashedPen(Brush brush, double thickness)
    {
        var pen = new Pen(brush, thickness)
        {
            DashStyle = new DashStyle([3.0, 2.0], 0),
            LineJoin = PenLineJoin.Round,
            StartLineCap = PenLineCap.Round,
            EndLineCap = PenLineCap.Round,
        };
        pen.Freeze();
        return pen;
    }

    private static Pen CreateFocusPen()
    {
        var pen = new Pen(DrawingBrush, 1.5) { DashStyle = DashStyles.Dash };
        pen.Freeze();
        return pen;
    }

    private sealed class RegionDrawingCanvasAutomationPeer(RegionDrawingCanvas owner)
        : FrameworkElementAutomationPeer(owner)
    {
        protected override string GetClassNameCore() => nameof(RegionDrawingCanvas);

        protected override AutomationControlType GetAutomationControlTypeCore() => AutomationControlType.Custom;
    }
}
