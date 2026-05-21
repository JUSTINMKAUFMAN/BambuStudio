---
name: bambu-studio-agent
description: Use when working with Bambu Studio, Bambu Lab printers, H2D, 3MF files, slicer plates, model cutting, dovetail connectors, auto-arrange/orient flows, or the local Bambu Studio agent API.
---

# Bambu Studio Agent Workflow

Use this plugin before editing Bambu Studio 3MF files by hand. Prefer the
structured MCP tools from the `bambu-studio` server. If the MCP tools are not
loaded in the current thread, use the CLI fallback:

```bash
python3 /Users/justin/Documents/BambuStudio/plugins/bambu-studio/scripts/bambu_cli.py status
```

## Hard Rules

- Do not claim a generated 3MF has the intended plate count or arrangement based
  only on `Metadata/model_settings.config`. Structural validation is useful, but
  Bambu Studio can still render a different result.
- Before saying a print-prep output is ready for the user to inspect, open the
  file in the local custom Bambu Studio build and visually verify the plate list,
  object count, orientation, and arrangement.
- Use explicit absolute paths for all 3MF inputs and outputs.
- Keep physical-printer actions separate from file-prep actions. Do not send,
  pause, resume, or cancel a real printer job unless the user explicitly asks
  for that physical action in the active thread.

## Normal 3MF Prep Flow

1. Run `bambu_status` or `bambu_cli.py status` to confirm the repo, app, and
   agent entrypoint are available.
2. Inspect the source file with `bambu_project_inspect` or `bambu_cli.py
   inspect`.
3. Run the requested agent method with `bambu_agent_run`, or use
   `bambu_project_partition_for_printer` for the dovetail partition method.
   Use `bambu_project_add_reassembly_plate` when the user asks to copy generated
   pieces onto a new plate and arrange them back into the source model shape.
4. Run `bambu_validate_3mf_layout` or `bambu_cli.py validate` on the output.
   Treat failures as blockers. Treat success as structural evidence only.
5. Open the output through `bambu_open_in_studio` or the custom app executable
   and visually confirm the result in the Bambu Studio UI.

## Current Agent API

The local fork documents the app bridge in:

```text
/Users/justin/Documents/BambuStudio/docs/agent-api/README.md
```

The current request shape is:

```json
{
  "schema_version": 1,
  "request_id": "inspect",
  "method": "project.inspect",
  "params": {
    "input": "/absolute/path/to/project.3mf"
  }
}
```

The current partition method is:

```json
{
  "schema_version": 1,
  "request_id": "boat-dovetail",
  "method": "project.auto_partition_for_printer",
  "params": {
    "input": "/absolute/path/to/input.3mf",
    "output": "/absolute/path/to/output.3mf",
    "added_subpieces": 5
  }
}
```

The current reassembly-plate method is:

```json
{
  "schema_version": 1,
  "request_id": "boat-reassembly-plate",
  "method": "project.add_reassembly_plate",
  "params": {
    "input": "/absolute/path/to/generated.3mf",
    "output": "/absolute/path/to/generated.3mf",
    "source": "/absolute/path/to/original-source.3mf"
  }
}
```

Printer send/control and live camera monitoring should be routed through future
explicit printer tools once they exist. Until then, this plugin is the file-prep
and local app-verification path.
