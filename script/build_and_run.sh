#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
PROJECT_NAME="SpatialScope"
SCHEME_NAME="SpatialScope"
CONFIGURATION="${CONFIGURATION:-Debug}"
DESTINATION="${DESTINATION:-platform=macOS}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

pkill -x "$PROJECT_NAME" >/dev/null 2>&1 || true

XCODEBUILD=(env -u AR -u CC -u CXX -u LD -u SDKROOT -u LDFLAGS -u LDFLAGS_LD /usr/bin/xcodebuild)

"${XCODEBUILD[@]}" \
  -project "$PROJECT_NAME.xcodeproj" \
  -scheme "$SCHEME_NAME" \
  -configuration "$CONFIGURATION" \
  -destination "$DESTINATION" \
  build

SETTINGS="$(
  "${XCODEBUILD[@]}" \
    -project "$PROJECT_NAME.xcodeproj" \
    -scheme "$SCHEME_NAME" \
    -configuration "$CONFIGURATION" \
    -destination "$DESTINATION" \
    -showBuildSettings
)"
TARGET_BUILD_DIR="$(printf '%s\n' "$SETTINGS" | awk -F ' = ' '/TARGET_BUILD_DIR =/ { print $2; exit }')"
FULL_PRODUCT_NAME="$(printf '%s\n' "$SETTINGS" | awk -F ' = ' '/FULL_PRODUCT_NAME =/ { print $2; exit }')"
APP_BUNDLE="$TARGET_BUILD_DIR/$FULL_PRODUCT_NAME"
APP_BINARY="$APP_BUNDLE/Contents/MacOS/$PROJECT_NAME"

case "$MODE" in
  run)
    /usr/bin/open -n "$APP_BUNDLE"
    ;;
  --verify|verify)
    /usr/bin/open -n "$APP_BUNDLE"
    sleep 1
    pgrep -x "$PROJECT_NAME" >/dev/null
    ;;
  --debug|debug)
    lldb -- "$APP_BINARY"
    ;;
  --logs|logs)
    /usr/bin/open -n "$APP_BUNDLE"
    /usr/bin/log stream --info --style compact --predicate "process == \"$PROJECT_NAME\""
    ;;
  *)
    echo "usage: $0 [run|--verify|--debug|--logs]" >&2
    exit 2
    ;;
esac
