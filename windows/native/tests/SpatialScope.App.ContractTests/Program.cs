using SpatialScope.Windows.Services;
using System.IO;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SpatialScope.App.ContractTests;

internal static class Program
{
    [STAThread]
    private static int Main()
    {
        var failures = new List<string>();
        Run(
            "same-path preview reload returns the latest pixels",
            SamePathPreviewReloadReturnsLatestPixels,
            failures);

        if (failures.Count > 0)
        {
            Console.Error.WriteLine($"{failures.Count} app contract test(s) failed.");
            return 1;
        }

        Console.WriteLine("All app contract tests passed.");
        return 0;
    }

    private static void Run(string name, Action test, ICollection<string> failures)
    {
        try
        {
            test();
            Console.WriteLine($"PASS {name}");
        }
        catch (Exception exception)
        {
            var failure = $"FAIL {name}: {exception}";
            failures.Add(failure);
            Console.Error.WriteLine(failure);
        }
    }

    private static void SamePathPreviewReloadReturnsLatestPixels()
    {
        var root = Path.Combine(Path.GetTempPath(), $"SpatialScope-app-tests-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var previewPath = Path.Combine(root, "region_filtered_preview__manual_editor.png");
        try
        {
            WriteSolidPng(previewPath, Colors.Red);
            var first = ReadFirstPixel(PreviewBitmapLoader.LoadFresh(previewPath, 2));

            WriteSolidPng(previewPath, Colors.Blue);
            var second = ReadFirstPixel(PreviewBitmapLoader.LoadFresh(previewPath, 2));

            Assert(first == Colors.Red, $"First preview pixel was {first}, expected red.");
            Assert(second == Colors.Blue, $"Reloaded preview pixel was {second}, expected blue.");

            File.Delete(previewPath);
            Assert(!File.Exists(previewPath), "OnLoad did not release the preview file.");
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static void WriteSolidPng(string path, Color color)
    {
        var bitmap = new WriteableBitmap(2, 2, 96, 96, PixelFormats.Bgra32, null);
        var pixels = Enumerable.Range(0, 4)
            .SelectMany(_ => new[] { color.B, color.G, color.R, color.A })
            .ToArray();
        bitmap.WritePixels(new Int32Rect(0, 0, 2, 2), pixels, 8, 0);

        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var stream = File.Open(path, FileMode.Create, FileAccess.Write, FileShare.None);
        encoder.Save(stream);
    }

    private static Color ReadFirstPixel(BitmapSource source)
    {
        var converted = new FormatConvertedBitmap(source, PixelFormats.Bgra32, null, 0);
        var pixels = new byte[4];
        converted.CopyPixels(new Int32Rect(0, 0, 1, 1), pixels, 4, 0);
        return Color.FromArgb(pixels[3], pixels[2], pixels[1], pixels[0]);
    }

    private static void Assert(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
