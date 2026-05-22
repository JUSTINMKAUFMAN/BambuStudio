#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_PATH="${1:-"$ROOT_DIR/build/arm64/BambuStudio/BambuStudio.app"}"
ENTITLEMENTS="${BAMBU_CODESIGN_ENTITLEMENTS:-"$ROOT_DIR/script/macos-agent-entitlements.plist"}"
TIMESTAMP_ARG="${BAMBU_CODESIGN_TIMESTAMP:---timestamp=none}"

if [[ ! -d "$APP_PATH" ]]; then
    echo "App bundle not found: $APP_PATH" >&2
    exit 1
fi

IDENTITY="${BAMBU_CODESIGN_IDENTITY:-}"
if [[ -z "$IDENTITY" ]]; then
    IDENTITY="$(
        security find-identity -p codesigning -v |
        awk -F'"' '/Apple Development: Justin Kaufman/ { print $2; exit }'
    )"
fi

if [[ -z "$IDENTITY" ]]; then
    echo "No signing identity found. Set BAMBU_CODESIGN_IDENTITY." >&2
    security find-identity -p codesigning -v >&2
    exit 1
fi

rm -rf "$APP_PATH/Contents/Resources/agent/__pycache__"

codesign \
    --force \
    --deep \
    --options runtime \
    "$TIMESTAMP_ARG" \
    --entitlements "$ENTITLEMENTS" \
    --sign "$IDENTITY" \
    "$APP_PATH"

codesign --verify --deep --strict --verbose=2 "$APP_PATH"
codesign -dvvv --entitlements :- "$APP_PATH"
