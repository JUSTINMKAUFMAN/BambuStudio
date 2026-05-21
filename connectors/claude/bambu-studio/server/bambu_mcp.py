#!/usr/bin/env python3
"""Claude Desktop MCPB entrypoint for the local Bambu Studio MCP server."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


DEFAULT_REPO = Path("/Users/justin/Documents/BambuStudio")


def main() -> int:
    repo = Path(os.environ.get("BAMBU_STUDIO_REPO", str(DEFAULT_REPO))).expanduser()
    server = repo / "plugins/bambu-studio/scripts/bambu_mcp.py"
    if not server.exists():
        sys.stderr.write(f"Bambu Studio MCP server not found: {server}\n")
        return 1
    sys.path.insert(0, str(server.parent))
    runpy.run_path(str(server), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
