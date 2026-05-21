#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
VERSION="${1:-$(git -C "$ROOT_DIR" rev-parse --short HEAD)}"
APP_SRC="$ROOT_DIR/build/arm64/BambuStudio/BambuStudio.app"

APP_ZIP="$DIST_DIR/BambuStudio-Agent-macOS-arm64-${VERSION}.zip"
APP_PART_PREFIX="$APP_ZIP.part-"
CODEX_ZIP="$DIST_DIR/bambu-studio-codex-plugin-${VERSION}.zip"
CLAUDE_MCPB_VERSIONED="$DIST_DIR/bambu-studio-claude-${VERSION}.mcpb"
CHECKSUMS="$DIST_DIR/checksums-${VERSION}.txt"

mkdir -p "$DIST_DIR"

if [[ ! -d "$APP_SRC" ]]; then
  echo "missing app bundle: $APP_SRC" >&2
  exit 1
fi

rm -f "$APP_ZIP" "$APP_PART_PREFIX"* "$CODEX_ZIP" "$CLAUDE_MCPB_VERSIONED" "$CHECKSUMS"

ditto -c -k --sequesterRsrc --keepParent "$APP_SRC" "$APP_ZIP"
# GitHub release uploads for this repo are reliable with small app chunks.
split -b 3m -d -a 3 "$APP_ZIP" "$APP_PART_PREFIX"

"$ROOT_DIR/script/package_claude_connector.sh" >/dev/null
cp "$DIST_DIR/bambu-studio-claude.mcpb" "$CLAUDE_MCPB_VERSIONED"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

CODEX_ROOT="$TMP_DIR/bambu-studio-codex-plugin"
mkdir -p "$CODEX_ROOT/plugins" "$CODEX_ROOT/.agents/plugins"
ditto "$ROOT_DIR/plugins/bambu-studio" "$CODEX_ROOT/plugins/bambu-studio"
find "$CODEX_ROOT" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$CODEX_ROOT" -name '*.pyc' -delete

cat > "$CODEX_ROOT/.agents/plugins/marketplace.json" <<'JSON'
{
  "name": "bambu-studio-local",
  "interface": {
    "displayName": "Local Bambu Studio"
  },
  "plugins": [
    {
      "name": "bambu-studio",
      "source": {
        "source": "local",
        "path": "./plugins/bambu-studio"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Engineering"
    }
  ]
}
JSON

cat > "$CODEX_ROOT/install-codex-plugin.command" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKETPLACE="$ROOT_DIR/.agents/plugins/marketplace.json"
URL_ENCODED="$(python3 - "$MARKETPLACE" <<'PY'
import sys
from urllib.parse import quote
print(quote(sys.argv[1], safe=""))
PY
)"
open "codex://plugins/bambu-studio?marketplacePath=${URL_ENCODED}"
SH
chmod +x "$CODEX_ROOT/install-codex-plugin.command"

cat > "$CODEX_ROOT/README-INSTALL.md" <<'MD'
# Bambu Studio Codex Plugin

1. Install `BambuStudio.app` from the release app ZIP into `/Applications`.
2. Double-click `install-codex-plugin.command`.
3. In Codex, install the `bambu-studio` plugin from the opened local marketplace.

The MCP server looks for the app in `/Applications/BambuStudio.app` by default.
Set `BAMBU_STUDIO_APP` if you install the app somewhere else.
MD

(
  cd "$TMP_DIR"
  zip -qr "$CODEX_ZIP" bambu-studio-codex-plugin
)

(
  cd "$DIST_DIR"
  shasum -a 256 "$(basename "$APP_ZIP")" "$(basename "$APP_PART_PREFIX")"* "$(basename "$CODEX_ZIP")" "$(basename "$CLAUDE_MCPB_VERSIONED")" > "$CHECKSUMS"
)

printf '%s\n%s\n%s\n%s\n' "$APP_ZIP" "$CODEX_ZIP" "$CLAUDE_MCPB_VERSIONED" "$CHECKSUMS"
printf '%s\n' "$APP_PART_PREFIX"*
