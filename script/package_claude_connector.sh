#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONNECTOR_DIR="$ROOT_DIR/connectors/claude/bambu-studio"
DIST_DIR="$ROOT_DIR/dist"
OUTPUT="$DIST_DIR/bambu-studio-claude.mcpb"

mkdir -p "$DIST_DIR"
rm -f "$OUTPUT"

(
  cd "$CONNECTOR_DIR"
  zip -qr "$OUTPUT" manifest.json README.md claude-code-mcp.json server \
    -x '*/__pycache__/*' -x '*/.DS_Store'
)

echo "$OUTPUT"
