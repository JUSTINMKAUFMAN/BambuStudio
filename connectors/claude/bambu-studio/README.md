# Bambu Studio Claude Desktop Connector

This is the Claude Desktop MCP Bundle source for the local custom Bambu Studio
agent API. The bundle uses Claude Desktop's bundled Node runtime as its entry
point, then launches the local Python MCP server internally. It exposes the same
MCP tools as the Codex plugin:

- `bambu_status`
- `bambu_project_inspect`
- `bambu_agent_run`
- `bambu_project_partition_for_printer`
- `bambu_project_add_reassembly_plate`
- `bambu_validate_3mf_layout`
- `bambu_open_in_studio`

The packaged extension is written to:

```text
/Users/justin/Documents/BambuStudio/dist/bambu-studio-claude.mcpb
```

Install it by double-clicking the `.mcpb` file or by opening Claude Desktop and
choosing Settings -> Extensions -> Advanced settings -> Install Extension.

The connector defaults to this installed app path:

```text
/Applications/BambuStudio.app/Contents/MacOS/BambuStudio
```

It does not require a repo checkout. The MCP server uses the agent script
bundled inside the app.

After installation, Claude Desktop can call the local MCP server. Claude Code can
then import installed Claude Desktop MCP servers with:

```bash
claude mcp add-from-claude-desktop --scope user
```

Or add the same local server to Claude Code directly:

```bash
claude mcp add-json bambu-studio --scope user "$(cat /Users/justin/Documents/BambuStudio/connectors/claude/bambu-studio/claude-code-mcp.json)"
```

Custom remote connectors for Claude.ai/Cowork are a separate cloud-hosted MCP
mechanism. This package is intentionally local because it needs access to the
local Bambu Studio fork, 3MF files, and macOS app.
