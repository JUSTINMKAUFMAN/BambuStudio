#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="${ARCH:-$(uname -m)}"
BUILD_CONFIG="${BUILD_CONFIG:-RelWithDebInfo}"
APP_NAME="BambuStudio"
APP_BUNDLE="$ROOT_DIR/build/$ARCH/BambuStudio/BambuStudio.app"
APP_BINARY="$APP_BUNDLE/Contents/MacOS/BambuStudio"

export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH"

usage() {
  echo "usage: $0 [run|--debug|--logs|--telemetry|--verify|--agent REQUEST RESPONSE]" >&2
}

build_app() {
  "$ROOT_DIR/BuildMac.sh" -a "$ARCH" -x -s -c "$BUILD_CONFIG"
}

launch_app() {
  pkill -x "$APP_NAME" >/dev/null 2>&1 || true
  /usr/bin/open -n "$APP_BUNDLE"
}

case "$MODE" in
  run)
    build_app
    launch_app
    ;;
  --verify|verify)
    build_app
    launch_app
    sleep 2
    pgrep -x "$APP_NAME" >/dev/null
    ;;
  --debug|debug)
    build_app
    lldb -- "$APP_BINARY"
    ;;
  --logs|logs)
    build_app
    launch_app
    /usr/bin/log stream --info --style compact --predicate "process == \"$APP_NAME\""
    ;;
  --telemetry|telemetry)
    build_app
    launch_app
    /usr/bin/log stream --info --style compact --predicate "process == \"$APP_NAME\""
    ;;
  --agent|agent)
    if [ "$#" -ne 3 ]; then
      usage
      exit 2
    fi
    build_app
    "$APP_BINARY" --agent-run "$2" --agent-out "$3"
    ;;
  *)
    usage
    exit 2
    ;;
esac
