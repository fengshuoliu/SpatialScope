#!/usr/bin/env bash
set -euo pipefail

# Keep Conda/Miniforge toolchains from shadowing Apple's linker and utilities.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DERIVED_DATA="$ROOT_DIR/build/DerivedData-Release"
RUNTIME_ROOT="$ROOT_DIR/build/cell-distribution-runtime"
CONFIGURATION="Release"

cd "$ROOT_DIR"

if [[ ! -x "$RUNTIME_ROOT/arm64/cell_distribution_exporter" || ! -x "$RUNTIME_ROOT/x86_64/cell_distribution_exporter" ]]; then
    "$ROOT_DIR/script/build_cell_distribution_runtime.sh"
fi

env -u AR -u AS -u CC -u CFLAGS -u CPP -u CPPFLAGS -u CXX -u CXXFLAGS \
    -u LD -u LDFLAGS -u LDFLAGS_LD -u LIPO -u NM -u OTOOL -u RANLIB \
    -u SDKROOT -u STRIP \
    /usr/bin/xcodebuild \
    -project SpatialScope.xcodeproj \
    -scheme SpatialScope \
    -configuration "$CONFIGURATION" \
    -destination 'generic/platform=macOS' \
    -derivedDataPath "$DERIVED_DATA" \
    ARCHS='arm64 x86_64' \
    ONLY_ACTIVE_ARCH=NO \
    CODE_SIGNING_ALLOWED=NO \
    ENABLE_HARDENED_RUNTIME=NO \
    build

BUILT_APP="$DERIVED_DATA/Build/Products/$CONFIGURATION/SpatialScope.app"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$BUILT_APP/Contents/Info.plist")"
WORK_DIR="$(mktemp -d "/tmp/spatialscope-package-$VERSION.XXXXXX")"
APP="$WORK_DIR/SpatialScope.app"
RELEASE_DIR="$ROOT_DIR/build/release/v$VERSION"

mkdir -p "$RELEASE_DIR"
/usr/bin/ditto --noextattr --norsrc "$BUILT_APP" "$APP"
mkdir -p "$APP/Contents/Resources/CellDistributionRuntime"
/usr/bin/ditto --noextattr --norsrc "$RUNTIME_ROOT/arm64" "$APP/Contents/Resources/CellDistributionRuntime/arm64"
/usr/bin/ditto --noextattr --norsrc "$RUNTIME_ROOT/x86_64" "$APP/Contents/Resources/CellDistributionRuntime/x86_64"
/usr/bin/xattr -cr "$APP"
/usr/bin/find "$APP" -name '.DS_Store' -delete

while IFS= read -r -d '' candidate; do
    if /usr/bin/file "$candidate" | /usr/bin/grep -q 'Mach-O'; then
        if ! /usr/bin/codesign --force --sign - "$candidate" >/dev/null 2>&1; then
            echo "Could not sign bundled runtime component: $candidate" >&2
            exit 1
        fi
    fi
done < <(/usr/bin/find "$APP/Contents/Resources/CellDistributionRuntime" -type f -print0)

/usr/bin/codesign --force --deep --sign - "$APP" >/dev/null
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP"

ARCHITECTURES="$(/usr/bin/lipo -archs "$APP/Contents/MacOS/SpatialScope")"
if [[ "$ARCHITECTURES" != *arm64* || "$ARCHITECTURES" != *x86_64* ]]; then
    echo "Expected a universal app, found: $ARCHITECTURES" >&2
    exit 1
fi

ZIP_PATH="$RELEASE_DIR/SpatialScope-macOS-universal.zip"
DMG_PATH="$RELEASE_DIR/SpatialScope-macOS-universal.dmg"
DMG_STAGE="$WORK_DIR/dmg"
mkdir -p "$DMG_STAGE"
/usr/bin/ditto --noextattr --norsrc "$APP" "$DMG_STAGE/SpatialScope.app"
/bin/ln -s /Applications "$DMG_STAGE/Applications"

COPYFILE_DISABLE=1 /usr/bin/ditto -c -k --noextattr --norsrc --noqtn --keepParent "$APP" "$ZIP_PATH"
/usr/bin/hdiutil create -volname "SpatialScope" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG_PATH"

(
    cd "$RELEASE_DIR"
    /usr/bin/shasum -a 256 SpatialScope-macOS-universal.dmg SpatialScope-macOS-universal.zip > SHA256SUMS.txt
)

echo "Release $VERSION is ready in $RELEASE_DIR"
