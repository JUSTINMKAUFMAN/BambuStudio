# Bambu Studio Codex Plugin

This plugin makes the local custom Bambu Studio fork easier for Codex to use.
It provides:

- A local MCP server with structured tools for 3MF inspection, agent API calls,
  dovetail partitioning, reassembly-plate generation, structural plate
  validation, and opening output files.
- A small CLI wrapper for the same workflows when MCP tools are not loaded in a
  thread.
- A Codex skill that tells future agents how to use the interface without
  repeating the earlier mistake of trusting XML plate counts as UI proof.

## Install In Codex

This repo includes a local marketplace at:

```text
/Users/justin/Documents/BambuStudio/.agents/plugins/marketplace.json
```

Open this Codex deeplink on this machine:

```text
codex://plugins/bambu-studio?marketplacePath=%2FUsers%2Fjustin%2FDocuments%2FBambuStudio%2F.agents%2Fplugins%2Fmarketplace.json
```

After installation, new Codex threads can discover the `bambu-studio` MCP server
and the `bambu-studio-agent` skill.

## CLI Fallback

Use the bundled CLI wrapper directly from this repo:

```bash
python3 /Users/justin/Documents/BambuStudio/plugins/bambu-studio/scripts/bambu_cli.py status
python3 /Users/justin/Documents/BambuStudio/plugins/bambu-studio/scripts/bambu_cli.py inspect /Users/justin/Documents/BambuStudio/BoatSmall.3mf
python3 /Users/justin/Documents/BambuStudio/plugins/bambu-studio/scripts/bambu_cli.py validate /Users/justin/Documents/BambuStudio/BoatSmall.3mf
```

To run the current agent partition method:

```bash
python3 /Users/justin/Documents/BambuStudio/plugins/bambu-studio/scripts/bambu_cli.py partition \
  /Users/justin/Documents/BambuStudio/BoatSmall.3mf \
  /Users/justin/Documents/BambuStudio/BoatSmall_agent_dovetail_8plates.3mf \
  --added-subpieces 5
```

To add a ninth plate that copies the eight generated pieces and reassembles
them into the original boat shape:

```bash
python3 /Users/justin/Documents/BambuStudio/plugins/bambu-studio/scripts/bambu_cli.py reassemble \
  /Users/justin/Documents/BambuStudio/BoatSmall_agent_dovetail_8plates.3mf \
  --source /Users/justin/Documents/BambuStudio/BoatSmall.3mf
```

To validate the current nine-plate test file structurally:

```bash
python3 /Users/justin/Documents/BambuStudio/plugins/bambu-studio/scripts/bambu_cli.py validate \
  /Users/justin/Documents/BambuStudio/BoatSmall_agent_dovetail_8plates.3mf \
  --required-plate-count 9 \
  --required-build-item-count 16 \
  --plate-instance-count 9=8
```

Validation is structural only. A thread must still open the output in Bambu
Studio and visually verify the plate list and arrangement before claiming the
file is ready.

## Claude Desktop Connector

The comparable Claude Desktop extension source lives at:

```text
/Users/justin/Documents/BambuStudio/connectors/claude/bambu-studio
```

The packaged MCP Bundle is:

```text
/Users/justin/Documents/BambuStudio/dist/bambu-studio-claude.mcpb
```

Double-clicking that `.mcpb` installs the local MCP server into Claude Desktop.
